#!/usr/bin/env python
"""P7 Tier1 재검증 — write 왕복 + evidence 강제 여부 실측 (저수준 foundry_sdk).

⚠️ 핵심 발견(2026-07-04): create-aircraft/observation/anomaly의 required 파라미터
`newParameter`는 **PK 바인딩 파라미터**다(UI가 자동으로 'newParameter'라 오명명). 값을 주면
그 값이 그대로 PK가 된다. 빈 문자열("")을 주면 빈 PK → ApplyActionFailed(=구 Gap 0의 정체).
→ store_foundry가 _JUNK_STR=""를 보내던 것이 Gap 0의 원인. 실 PK를 주면 액션은 정상 실행된다.

항목당 execute 1건(왕복). 테스트 객체는 끝에서 delete로 정리(실데이터 카운트 오염 방지).
시크릿 값 미출력.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]

VALIDATE = ApplyActionRequestOptions(mode="VALIDATE_ONLY")
EXECUTE = ApplyActionRequestOptions(mode="VALIDATE_AND_EXECUTE", return_edits="ALL")

c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject

# 테스트 PK(정리 대상). 실 hex와 안 겹치게 접두어.
AC_PK = "p7t1-aircraft"
OBS_PK = "p7t1-obs-{}".format(int(time.time()))
AN_PK = "p7t1-anomaly-{}".format(int(time.time()))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def apply_exec(action, params):
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:200]}"
    pk = None
    edits = getattr(resp, "edits", None)
    modified = getattr(edits, "edits", None) if edits is not None else None
    if modified:
        for ed in modified:
            got = getattr(ed, "primary_key", None)
            if got:
                pk = got
                break
    return True, pk


def apply_validate(action, params):
    try:
        resp = A.apply(ONT, action, parameters=params, options=VALIDATE)
    except Exception as e:
        return f"EXC {type(e).__name__}", str(e).replace(chr(10), " ")[:140]
    val = getattr(resp, "validation", None)
    return str(getattr(val, "result", val)), ""


def count(otype):
    return len(list(OO.list(ONT, otype)))


def read_obj(otype, pk):
    try:
        return OO.get(ONT, otype, pk)
    except Exception as e:
        return {"_err": type(e).__name__ + ": " + str(e)[:80]}


def section(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


created = []  # (otype, pk, delete-action)


def main():
    section("0. 쓰기 전 카운트")
    c0 = {t: count(t) for t in ("Aircraft", "Observation", "Region", "Anomaly")}
    print("before:", c0)

    # ── A. create-aircraft: newParameter=PK(icao24) ──
    section("A. create-aircraft — newParameter로 icao24 PK 지정(엔티티 해소)")
    ok, pk = apply_exec(
        "create-aircraft",
        {
            "callsign": "P7T1",
            "registration": "P7T101",
            "isMilitary": True,
            "type": "C-130",
            "operatorRef": "TESTAF",
            "newParameter": AC_PK,  # ← PK 바인딩
        },
    )
    print(f"execute ok={ok}  PK={pk!r}  (요청값 {AC_PK!r})")
    if ok and pk:
        created.append(("Aircraft", pk, "delete-aircraft"))
        d = read_obj("Aircraft", pk)
        print(
            f"read-back icao24={d.get('icao24')!r} callsign={d.get('callsign')!r} "
            f"isMilitary={d.get('isMilitary')!r} type={d.get('type')!r} operatorRef={d.get('operatorRef')!r}"
        )
        print(
            f"  → PK==요청 icao24? {pk == AC_PK}  (True면 엔티티 해소 가능=Gap1 해소)"
        )

    # ── A-2. dedup: 같은 PK 재생성 시도 ──
    section("A-2. dedup — 같은 icao24 PK 재생성 시도")
    ok2, pk2 = apply_exec(
        "create-aircraft",
        {"callsign": "DUP", "registration": "DUP", "newParameter": AC_PK},
    )
    print(f"재생성 ok={ok2}  결과={pk2!r}")
    print("  → 실패(ObjectAlreadyExists류)면 PK dedup 강제됨 / 성공이면 upsert")

    # ── B. create-observation: newParameter=obsId PK + aircraftIcao24=FK ──
    section("B. create-observation — obsId PK + aircraftIcao24 FK(observed_as) + Gap4")
    ok, opk = apply_exec(
        "create-observation",
        {
            "sourceUrl": "https://opensky-network.org/api/states/all?p7=tier1",
            "source": "p7tier1",
            "ts": now_iso(),
            "lat": 36.5,
            "lon": 124.5,
            "onGround": False,
            "aircraftIcao24": AC_PK,  # FK → 위 Aircraft PK
            "newParameter": OBS_PK,  # obsId PK
            # alt/velocity/heading/squawk 고의 생략(Gap4: 옛날 required)
        },
    )
    print(f"execute ok={ok}  PK={opk!r}  (텔레메트리 4종 생략)")
    if ok and opk:
        created.append(("Observation", opk, "delete-observation"))
        d = read_obj("Observation", opk)
        print(
            f"read-back obsId={d.get('obsId')!r} aircraftIcao24={d.get('aircraftIcao24')!r} "
            f"source={d.get('source')!r} ts={d.get('ts')!r}"
        )
        print(
            f"  생략 텔레메트리: alt={d.get('alt')!r} velocity={d.get('velocity')!r} "
            f"heading={d.get('heading')!r} squawk={d.get('squawk')!r} (None이면 Gap4 해소)"
        )
        print(
            f"  → obsId==요청? {d.get('obsId') == OBS_PK} (자연키 dedup 가능=Gap2 PK 해소)"
        )
        print(
            f"  → aircraftIcao24==AircraftPK? {d.get('aircraftIcao24') == AC_PK} (observed_as FK 링크형성=Gap2 FK 해소)"
        )

    # ── C. create-anomaly: evidence 없이 생성(강제 여부) ──
    section(
        "C. create-anomaly — evidence 없이 생성(온톨로지 provenance 강제 여부=데모 핵심)"
    )
    ok, apk = apply_exec(
        "create-anomaly",
        {
            "type": "emergency_squawk",
            "ts": now_iso(),
            "lat": 36.5,
            "lon": 124.5,
            "newParameter": AN_PK,  # anomalyId PK
        },
    )
    print(f"evidence 없이 execute ok={ok}  PK={apk!r}")
    if ok and apk:
        created.append(("Anomaly", apk, "delete-anomaly"))
        d = read_obj("Anomaly", apk)
        print(
            f"read-back anomalyId={d.get('anomalyId')!r} type={d.get('type')!r} "
            f"status={d.get('status')!r} confidence={d.get('confidence')!r} explanation={d.get('explanation')!r}"
        )
        print(
            "  → evidence 없이 생성 성공 = 온톨로지 레벨 evidence 강제 '없음' (ontology.md §3 미구현)"
        )
        print(
            "  → status/confidence/explanation은 create-anomaly 파라미터에 없어 항상 None(설정 불가)"
        )

    # ── D. VALIDATE_ONLY 프로브: 없는 파라미터는 BadRequest(=부재 증명) ──
    section("D. 파라미터 부재 증명 (VALIDATE_ONLY — 쓰기 없음)")
    for action, extra in [
        ("create-anomaly", {"evidence": "x"}),
        ("create-anomaly", {"observations": "x"}),
        ("create-aircraft", {"icao24": "x"}),
        ("create-aircraft", {"newParameter1": False}),
        ("create-anomaly", {"confidence": 0.9}),
        ("create-anomaly", {"status": "confirmed"}),
    ]:
        base = (
            {"type": "x", "ts": now_iso(), "lat": 1.0, "lon": 1.0, "newParameter": "x"}
            if action == "create-anomaly"
            else {"callsign": "x", "registration": "x", "newParameter": "x"}
        )
        r, msg = apply_validate(action, {**base, **extra})
        pname = list(extra.keys())[0]
        print(
            f"  {action} + '{pname}' → {r}  {'(부재=BadRequest)' if r.startswith('EXC') else ''}"
        )

    # ── E. confirm/dismiss-anomaly 액션 존재 여부 ──
    section("E. confirm/dismiss-anomaly 액션 존재 여부")
    for act in ("confirm-anomaly", "dismiss-anomaly", "set-region-alert-level"):
        try:
            A.apply(ONT, act, parameters={}, options=VALIDATE)
            print(f"  {act}: 존재(파라미터 검증 반환)")
        except Exception as e:
            en = getattr(e, "error_name", None) or type(e).__name__
            # ActionTypeNotFound류면 미존재
            print(f"  {act}: {en} (ActionType 부재면 미구현)")

    # ── F. 정리 ──
    section("F. 정리 (delete-* 로 테스트 객체 삭제)")
    for otype, pk, act in created:
        ok, err = apply_exec(act, {otype: pk})
        print(f"  {act}({otype}={pk!r}) → {'삭제' if ok else 'FAIL ' + str(err)}")

    section("G. 정리 후 카운트")
    c1 = {t: count(t) for t in ("Aircraft", "Observation", "Region", "Anomaly")}
    print("before:", c0)
    print("after :", c1)
    print("delta :", {t: c1[t] - c0[t] for t in c0})
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
