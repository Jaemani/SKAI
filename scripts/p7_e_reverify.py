#!/usr/bin/env python
"""P7 E부 재검증 (2026-07-04). create-anomaly 클린 실행 + E-2/E-3 반영 판정.

사용자가 create-anomaly 규칙 수정(§12 가짜 에러 원인 = 링크가 신규 객체가 아닌 `anomalies`
입력 파라미터에 연결되던 것) + E부(리네임·correlatedWith·createdAt·explainerBackend 등) 반영 +
OSDK 0.7.0 재발행을 마친 뒤의 실측 재검증.

검증 항목:
  1a. observations(근거) 생략 → 거부(INVALID) 유지?
  1b. observations + correlatedWith 포함 create-anomaly EXECUTE → **ApplyActionFailed 없이 깔끔 성공?**
  1c. evidenced_by(→Observation) / involves(→Aircraft) traverse
  1d. correlatedWith required 여부 + placeholder("none") 수용성 + 실 ref 시 correlatedWith 엣지 traverse
  1e. optional 링크(newsEvents/orbitPasses/aircraft) 생략 가능?
저수준 foundry_sdk. write 최소·매건 delete 정리·before==after. 시크릿 미출력.
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

EVID_OBS = "a748bc-1783151576"  # 재사용 근거 Observation
INVOLVES_AC = "a748bc"  # involves 대상 Aircraft (근거 obs의 항공기)
ABSENT = "none"  # correlatedWith placeholder (non-existent Anomaly ref)


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
        return f"EXC:{type(e).__name__}:{str(e).replace(chr(10), ' ')[:150]}"


def ex(action, params):
    """EXECUTE. 반환 (valid_bool, result, pk, err)."""
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return (
            False,
            "EXC",
            None,
            f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:220]}",
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
            p = (
                _get(o, "__primaryKey")
                or _get(o, "anomalyId")
                or _get(o, "obsId")
                or _get(o, "icao24")
            )
            out.append(p)
        return out
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:80]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def base_params(pk, correlated):
    return {
        "type": "emergency_squawk",
        "ts": now(),
        "lat": 36.5,
        "lon": 124.5,
        "confidence": 0.9,
        "status": "candidate",
        "explanation": "P7E obs-grounded anomaly",
        "createdAt": now(),  # E-3 신규 속성
        "explainerBackend": "template",  # E-3 신규 속성
        "observations": EVID_OBS,  # required 근거 (evidenced_by)
        "correlatedWith": correlated,  # required (E-2.3, 실측 required)
        "anomalyId": pk,  # E-4 리네임 (구 newParameter)
    }


def main():
    sec("0. baseline")
    c0 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly", "Region")}
    print(c0)
    ev = OO.get(ONT, "Observation", EVID_OBS)
    print(f"  근거 obs 존재: obsId={ev.get('obsId')!r} ac={ev.get('aircraftIcao24')!r}")

    created = []

    # 1a. 근거 생략 → 거부 유지?
    sec("1a. [거부검증] observations 생략 → VALIDATE")
    p = base_params(f"p7e-noev-{T}", ABSENT)
    del p["observations"]
    print(f"  observations 생략 → {val('create-anomaly', p)}")
    print("  (INVALID = 근거 없는 Anomaly 거부 = provenance 강제 유지 ✓)")

    # 1d-i. correlatedWith 생략 → required 여부
    sec("1d-i. correlatedWith 생략 → VALIDATE (required 여부)")
    p = base_params(f"p7e-nocorr-{T}", ABSENT)
    del p["correlatedWith"]
    print(f"  correlatedWith 생략 → {val('create-anomaly', p)}")
    print("  (INVALID = required / VALID = optional)")

    # 1b. 핵심: 근거+correlatedWith(placeholder) 포함 EXECUTE → 깔끔?
    sec("1b. [핵심] create-anomaly EXECUTE (근거+correlatedWith=placeholder)")
    an1 = f"p7e-an1-{T}"
    params = base_params(an1, ABSENT)
    print(f"  VALIDATE(pre) → {val('create-anomaly', params)}")
    okv, r, pk, err = ex("create-anomaly", params)
    print(f"  EXECUTE → valid={okv} result={r} pk={pk!r}")
    print(f"  err={err}")
    print(f"  ★ ApplyActionFailed? {'예 (잔존)' if err else '아니오 (깔끔 성공)'}")
    if pk is None and an1 in [_get(o, "anomalyId") for o in OO.list(ONT, "Anomaly")]:
        pk = an1
    if pk:
        created.append(pk)
        d = OO.get(ONT, "Anomaly", pk)
        print(
            f"  read-back: id={d.get('anomalyId')!r} status={d.get('status')!r} "
            f"conf={d.get('confidence')!r} createdAt={d.get('createdAt')!r} "
            f"explainerBackend={d.get('explainerBackend')!r}"
        )
        # 1c. evidenced_by / involves traverse
        sec("1c. evidenced_by(→Obs) / involves(→AC) traverse")
        tv = traverse("Anomaly", pk, "observations")
        print(
            f"  Anomaly.observations → {tv}  (근거 형성? {isinstance(tv, list) and EVID_OBS in tv})"
        )
        rev = traverse("Observation", EVID_OBS, "anomalies")
        print(
            f"  역 Observation.anomalies → {rev}  (포함? {isinstance(rev, list) and pk in rev})"
        )
        # correlatedWith placeholder는 엣지 형성 안 돼야 정상(non-existent ref)
        cw = traverse("Anomaly", pk, "correlatedWithAnomalies")
        print(f"  Anomaly.correlatedWithAnomalies (placeholder) → {cw}  (빈=정상)")

    # 1e. involves(aircraft) 포함 + optional 생략 케이스
    sec("1e. involves(aircraft) 포함 EXECUTE + optional(news/orbit) 생략")
    an2 = f"p7e-an2-{T}"
    params2 = base_params(an2, ABSENT)
    params2["aircraft"] = INVOLVES_AC  # optional involves
    # newsEvents / orbitPasses 생략 (optional 확인)
    okv, r, pk2, err2 = ex("create-anomaly", params2)
    print(f"  EXECUTE(+aircraft, -news, -orbit) → valid={okv} err={err2}")
    if pk2 is None and an2 in [_get(o, "anomalyId") for o in OO.list(ONT, "Anomaly")]:
        pk2 = an2
    if pk2:
        created.append(pk2)
        inv = traverse("Anomaly", pk2, "aircraft")
        print(
            f"  involves Anomaly.aircraft → {inv}  (형성? {isinstance(inv, list) and INVOLVES_AC in inv})"
        )

    # 1d-ii. correlatedWith 실 ref → 엣지 형성?
    sec("1d-ii. correlatedWith=실 Anomaly ref → correlatedWith 엣지 traverse")
    if created:
        target = created[0]
        an3 = f"p7e-an3-{T}"
        params3 = base_params(an3, target)  # correlatedWith = 실존 anomaly
        okv, r, pk3, err3 = ex("create-anomaly", params3)
        print(f"  EXECUTE(correlatedWith={target!r}) → valid={okv} err={err3}")
        if pk3 is None and an3 in [
            _get(o, "anomalyId") for o in OO.list(ONT, "Anomaly")
        ]:
            pk3 = an3
        if pk3:
            created.append(pk3)
            cw = traverse("Anomaly", pk3, "correlatedWithAnomalies")
            print(
                f"  Anomaly.correlatedWithAnomalies → {cw}  (형성? {isinstance(cw, list) and target in cw})"
            )

    # confirm 전이 (데모 백본)
    sec("1f. confirm-anomaly 전이 (candidate→confirmed)")
    if created:
        okv, r, _, err = ex("confirm-anomaly", {"anomaly": created[0]})
        print(f"  confirm-anomaly({created[0]!r}) → valid={okv} err={err}")
        d = OO.get(ONT, "Anomaly", created[0])
        print(f"  status read-back → {d.get('status')!r}")

    # 정리
    sec("CLEANUP")
    for a in created:
        okv, r, _, err = ex("delete-anomaly", {"Anomaly": a})
        print(f"  delete-anomaly({a!r}) → {'삭제' if okv else 'FAIL:' + str(err)[:80]}")
    stray = [
        _get(o, "anomalyId")
        for o in OO.list(ONT, "Anomaly")
        if str(_get(o, "anomalyId") or "").startswith("p7e-")
    ]
    for s in stray:
        ex("delete-anomaly", {"Anomaly": s})
        print(f"  [stray] delete-anomaly({s!r})")

    sec("after / delta")
    c1 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly", "Region")}
    print("before:", c0)
    print("after :", c1)
    print("delta :", {t: c1[t] - c0[t] for t in c0})
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
