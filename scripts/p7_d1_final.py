#!/usr/bin/env python
"""P7 D-1 최종 재검증 (2026-07-04). create-anomaly evidence 강제.

newParameter1(required objectSet) 제거 후 스키마 기준. 근거는 단일
observations(object, required). write 최소화: 기존 Observation을 근거로
재사용해 Anomaly 1건만 생성 → traverse → delete. before==after 확인.
시크릿 미출력.
"""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
c = foundry_sdk.FoundryClient(
    auth=foundry_sdk.UserTokenAuth(os.environ["FOUNDRY_TOKEN"]),
    hostname=os.environ["FOUNDRY_HOSTNAME"],
)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject
LO = c.ontologies.LinkedObject
VALIDATE = ApplyActionRequestOptions(mode="VALIDATE_ONLY")
EXECUTE = ApplyActionRequestOptions(mode="VALIDATE_AND_EXECUTE", return_edits="ALL")
T = int(time.time())

# 재사용할 기존 근거 Observation (baseline: Observation 3건 중 하나)
EVID_OBS = "847114-1783136694"


def now():
    return datetime.now(timezone.utc).isoformat()


def _get(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def count(t):
    return len(list(OO.list(ONT, t)))


def val(action, params):
    try:
        resp = A.apply(ONT, action, parameters=params, options=VALIDATE)
        v = getattr(resp, "validation", None)
        return str(getattr(v, "result", "?"))
    except Exception as e:
        return f"EXC:{type(e).__name__}:{str(e).replace(chr(10), ' ')[:130]}"


def ex(action, params):
    """EXECUTE. (valid_bool, result, pk, err)"""
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return (
            False,
            "EXC",
            None,
            f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:200]}",
        )
    v = getattr(resp, "validation", None)
    result = str(getattr(v, "result", "?"))
    pk = None
    edits = getattr(resp, "edits", None)
    mod = getattr(edits, "edits", None) if edits is not None else None
    if mod:
        for ed in mod:
            got = getattr(ed, "primary_key", None)
            if got:
                pk = got
                break
    return (result == "VALID"), result, pk, None


def traverse(otype, pk, link):
    try:
        objs = list(LO.list_linked_objects(ONT, otype, pk, link))
        out = []
        for o in objs:
            p = _get(o, "__primaryKey") or _get(o, "obsId") or _get(o, "icao24")
            out.append(p)
        return out
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:80]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main():
    sec("0. baseline counts")
    c0 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly", "Region")}
    print(c0)
    print(f"근거 재사용 Observation = {EVID_OBS!r}")
    ev = OO.get(ONT, "Observation", EVID_OBS)
    print(
        f"  존재 확인: obsId={ev.get('obsId')!r} aircraftIcao24={ev.get('aircraftIcao24')!r}"
    )

    an_pk = f"p7d1-an-{T}"
    cleanup_an = None

    # ---- 1a. evidence(observations) 없이 → 거부되나? ----
    sec("1a. [거부검증] observations(근거) 생략 → VALIDATE")
    r_noev = val(
        "create-anomaly",
        {
            "type": "emergency_squawk",
            "ts": now(),
            "lat": 36.5,
            "lon": 124.5,
            "newParameter": an_pk,
        },
    )
    print(f"  observations 생략 → {r_noev}")
    print(
        "  (INVALID = 근거 없는 Anomaly 거부 = provenance 강제 ✓ / VALID면 강제 실패)"
    )

    # ---- 1b. evidence(observations) 포함 → EXECUTE 깔끔 성공? ----
    sec("1b. [핵심] observations=근거 포함 create-anomaly EXECUTE")
    params = {
        "type": "emergency_squawk",
        "ts": now(),
        "lat": 36.5,
        "lon": 124.5,
        "confidence": 0.9,
        "status": "candidate",
        "explanation": "P7D1 obs-grounded anomaly",
        "observations": EVID_OBS,
        "newParameter": an_pk,
    }
    r_val = val("create-anomaly", params)
    print(f"  VALIDATE(pre-exec) → {r_val}")
    okv, r, pk, err = ex("create-anomaly", params)
    print(f"  EXECUTE → valid={okv} result={r} pk={pk!r}")
    print(f"  err={err}")
    print(f"  ★ ApplyActionFailed 재발? {'예 (잔존)' if err else '아니오 (깔끔 성공)'}")

    # PK 확정: edits에서 못 얻으면 목록 diff
    if pk is None:
        anos = [_get(o, "anomalyId") for o in OO.list(ONT, "Anomaly")]
        if an_pk in anos:
            pk = an_pk
    if pk:
        cleanup_an = pk
        d = OO.get(ONT, "Anomaly", pk)
        print(
            f"  read-back: anomalyId={d.get('anomalyId')!r} status={d.get('status')!r} "
            f"conf={d.get('confidence')!r} type={d.get('type')!r} expl={d.get('explanation')!r}"
        )
        # ---- 1c. evidenced_by → Observation traverse ----
        sec("1c. [핵심] evidenced_by → Observation traverse")
        tv = traverse("Anomaly", pk, "observations")
        print(f"  Anomaly.observations → {tv}")
        formed = isinstance(tv, list) and EVID_OBS in tv
        print(
            f"  ★ evidenced_by 엣지 형성? {'OK (근거≡' + EVID_OBS + ')' if formed else '실패 (빈/불일치)'}"
        )
        # 역방향도 확인
        rev = traverse("Observation", EVID_OBS, "anomalies")
        print(
            f"  역방향 Observation.anomalies → {rev} (anomaly 포함? {isinstance(rev, list) and pk in rev})"
        )
    else:
        print("  Anomaly PK 미확정 — 생성 안 됨/에러")

    # ---- 정리 ----
    sec("CLEANUP")
    if cleanup_an:
        okv, r, _, err = ex("delete-anomaly", {"Anomaly": cleanup_an})
        print(
            f"  delete-anomaly({cleanup_an!r}) → {'삭제' if okv else 'FAIL:' + str(err)[:80]}"
        )
        # half-anomaly 잔존 여부: 혹시 pk 외 다른 신규 anomaly 있나
    # 최종 잔여 anomaly 확인 (half-anomaly 청소)
    leftover = [_get(o, "anomalyId") for o in OO.list(ONT, "Anomaly")]
    stray = [a for a in leftover if a and str(a).startswith("p7d1-")]
    for s in stray:
        okv, r, _, err = ex("delete-anomaly", {"Anomaly": s})
        print(f"  [stray cleanup] delete-anomaly({s!r}) → {'삭제' if okv else 'FAIL'}")

    sec("after counts / delta")
    c1 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly", "Region")}
    print("before:", c0)
    print("after :", c1)
    print("delta :", {t: c1[t] - c0[t] for t in c0})
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
