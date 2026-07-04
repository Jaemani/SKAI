#!/usr/bin/env python
"""P7 전량 스키마 왕복 검증 — evidence 강제·confirm·mentions·alert-level·PK 부재 실측.

VALIDATE_ONLY 우선(부재/거부 증명은 쓰기 없음). EXECUTE는 happy-path 최소 1건씩.
생성물 전부 끝에서 delete 정리(카운트 before==after). 시크릿 미출력.
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
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]

VALIDATE = ApplyActionRequestOptions(mode="VALIDATE_ONLY")
EXECUTE = ApplyActionRequestOptions(mode="VALIDATE_AND_EXECUTE", return_edits="ALL")

c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject

T = int(time.time())
TYPES = (
    "Aircraft",
    "Observation",
    "Region",
    "Anomaly",
    "Operator",
    "Track",
    "Satellite",
    "OrbitPass",
    "WeatherState",
    "NewsEvent",
    "SituationAssessment",
)
created = []  # (delete-action, {ParamName: pk})


def now():
    return datetime.now(timezone.utc).isoformat()


def ex(action, params):
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:180]}"
    pk = None
    edits = getattr(resp, "edits", None)
    mod = getattr(edits, "edits", None) if edits is not None else None
    if mod:
        for ed in mod:
            got = getattr(ed, "primary_key", None)
            if got:
                pk = got
                break
    return True, pk


def val(action, params):
    try:
        A.apply(ONT, action, parameters=params, options=VALIDATE)
        return "VALID_OR_INVALID_NOEXC", ""
    except Exception as e:
        return f"EXC:{type(e).__name__}", str(e).replace(chr(10), " ")[:150]


def count(t):
    try:
        return len(list(OO.list(ONT, t)))
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def read(t, pk):
    try:
        return OO.get(ONT, t, pk)
    except Exception as e:
        return {"_err": type(e).__name__}


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main():
    sec("0. before counts")
    c0 = {t: count(t) for t in TYPES}
    print(c0)

    # ============ EXECUTE happy-path 셋업 객체 ============
    sec("1. 셋업 객체 생성 (EXECUTE, 각 1건)")
    ac_pk = f"p7f-ac-{T}"
    ok, pk = ex(
        "create-aircraft",
        {
            "callsign": "P7F",
            "registration": "P7F01",
            "isMilitary": True,
            "type": "RC-135",
            "operatorRef": "P7FAF",
            "newParameter": ac_pk,
        },
    )
    print(f"create-aircraft ok={ok} pk={pk!r}")
    if ok and pk:
        created.append(("delete-aircraft", {"Aircraft": pk}))
        ac_pk = pk

    rg_pk = f"p7f-rg-{T}"
    ok, pk = ex(
        "create-region",
        {
            "name": "P7F-KADIZ",
            "classification": "ADIZ",
            "geoJson": '{"type":"Polygon","coordinates":[]}',
            "newParameter": rg_pk,
        },
    )
    print(f"create-region ok={ok} pk={pk!r}")
    if ok and pk:
        created.append(("delete-region", {"Region": pk}))
        rg_pk = pk

    ok, op_pk = ex(
        "create-operator", {"name": "P7F-AF", "kind": "airforce", "country": "XX"}
    )
    print(f"create-operator ok={ok} pk={op_pk!r} (PK 파라미터 없음→UUID?)")
    if ok and op_pk:
        created.append(("delete-operator", {"Operator": op_pk}))

    ok, orp_pk = ex(
        "create-orbit-pass",
        {
            "satelliteNoradId": "99999",
            "regionId": rg_pk,
            "startTs": now(),
            "endTs": now(),
            "maxElevation": 45.0,
        },
    )
    print(f"create-orbit-pass ok={ok} pk={orp_pk!r} (PK 파라미터 없음→UUID?)")
    if ok and orp_pk:
        created.append(("delete-orbit-pass", {"OrbitPass": orp_pk}))

    # ============ create-news-event: 순환 self-link 검사 ============
    sec("2. create-news-event — 순환 self-link(newsEvents 필수) 검사")
    # 2a. VALIDATE: self newsEvents 없이 → 거부되나?
    r, m = val(
        "create-news-event",
        {
            "aircraft": ac_pk,
            "operators": op_pk,
            "regions": rg_pk,
            "confidence": 0.3,
            "entitiesJson": "[]",
            "lat": 36.5,
            "lon": 124.5,
            "source": "gdelt",
            "summary": "s",
            "title": "t",
            "ts": now(),
            "url": "https://example.org/p7f",
        },
    )
    print(f"  self-link 생략 VALIDATE → {r} {m[:90]}")
    # 2b. EXECUTE: 모든 필수(self 제외) 시도 — 실행되나?
    ne_pk = None
    ok, ne_pk = ex(
        "create-news-event",
        {
            "aircraft": ac_pk,
            "operators": op_pk,
            "regions": rg_pk,
            "confidence": 0.3,
            "entitiesJson": "[]",
            "lat": 36.5,
            "lon": 124.5,
            "source": "gdelt",
            "summary": "s",
            "title": "t",
            "ts": now(),
            "url": "https://example.org/p7f",
        },
    )
    print(f"  self-link 생략 EXECUTE → ok={ok} pk={ne_pk!r}")
    if ok and ne_pk:
        created.append(("delete-news-event", {"NewsEvent": ne_pk}))
        d = read("NewsEvent", ne_pk)
        print(f"    read-back newsId={d.get('newsId')!r} source={d.get('source')!r}")

    # ============ create-anomaly: evidence 강제 실측 ============
    sec("3. create-anomaly — evidence 강제/Observation-evidence 부재 실측")
    # 3a. 아무 링크 없이 → 거부되나? (provenance 강제 여부)
    r, m = val(
        "create-anomaly",
        {
            "type": "emergency_squawk",
            "ts": now(),
            "lat": 36.5,
            "lon": 124.5,
            "newParameter": f"p7f-an-{T}",
            "newParameter1": "x",
        },
    )
    print(f"  링크 전무 VALIDATE(aircraft/newsEvents/orbitPasses 생략) → {r} {m[:90]}")
    # 3b. Observation-evidence 파라미터 수용? (observations/evidence)
    for probe in ("observations", "evidence"):
        r, m = val(
            "create-anomaly",
            {
                "type": "x",
                "ts": now(),
                "lat": 1.0,
                "lon": 1.0,
                "newParameter": "x",
                "newParameter1": "x",
                "aircraft": ac_pk,
                "newsEvents": ne_pk or "x",
                "orbitPasses": orp_pk or "x",
                probe: "x",
            },
        )
        print(f"  '{probe}' 파라미터 수용? → {r} {m[:80]}")
    # 3c. EXECUTE happy-path: 모든 필수 링크 제공 → 생성 성공?
    an_pk = f"p7f-an-{T}"
    if ne_pk and orp_pk:
        ok, pk = ex(
            "create-anomaly",
            {
                "type": "emergency_squawk",
                "ts": now(),
                "lat": 36.5,
                "lon": 124.5,
                "confidence": 0.9,
                "status": "candidate",
                "explanation": "P7F evidence-forced anomaly",
                "aircraft": ac_pk,
                "newsEvents": ne_pk,
                "orbitPasses": orp_pk,
                "newParameter": an_pk,
                "newParameter1": "SENTINEL_NP1",
            },
        )
        print(f"  happy-path EXECUTE ok={ok} pk={pk!r}")
        if ok and pk:
            created.append(("delete-anomaly", {"Anomaly": pk}))
            an_pk = pk
            d = read("Anomaly", pk)
            print(
                f"    read-back anomalyId={d.get('anomalyId')!r} status={d.get('status')!r} "
                f"confidence={d.get('confidence')!r} explanation={d.get('explanation')!r}"
            )
            print(
                f"    newParameter1 행방: anomalyId==PK요청? {d.get('anomalyId') == an_pk} "
                f"(anomalyId!=SENTINEL_NP1 이면 np1은 orphan/미저장)"
            )
        else:
            an_pk = None
    else:
        print("  happy-path SKIP (news_event/orbit_pass 미생성 → 필수 링크 ref 없음)")
        an_pk = None

    # ============ confirm-anomaly: status 전이 ============
    sec("4. confirm-anomaly — status candidate→confirmed 전이")
    if an_pk:
        ok, _ = ex("confirm-anomaly", {"anomaly": an_pk})
        d = read("Anomaly", an_pk)
        print(f"  confirm ok={ok} → status={d.get('status')!r} (confirmed면 전이 확인)")
    else:
        print("  SKIP (anomaly 미생성)")

    # ============ set-region-alert-level: Region param 부재 실측 ============
    sec("5. set-region-alert-level — 대상 Region 파라미터 존재 여부")
    for extra in ({}, {"Region": rg_pk}, {"region": rg_pk}, {"id": rg_pk}):
        r, m = val("set-region-alert-level", {"alertLevel": "RED", **extra})
        key = list(extra.keys())[0] if extra else "(no-region-param)"
        print(f"  with {key} → {r} {m[:80]}")
    # EXECUTE 시도 (Region 지정 가능하면)
    ok, _ = ex("set-region-alert-level", {"alertLevel": "RED", "Region": rg_pk})
    print(f"  EXECUTE(Region=rg) ok={ok}")
    if ok:
        d = read("Region", rg_pk)
        print(f"    read-back alertLevel={d.get('alertLevel')!r}")

    # ============ PK 부재 실측: 7 신규 타입 create에 newParameter 수용? ============
    sec("6. 신규 7타입 create-*에 PK 파라미터(newParameter) 수용 여부")
    pk_probes = [
        ("create-operator", {"name": "x", "kind": "x", "country": "x"}),
        (
            "create-satellite",
            {"name": "x", "objectType": "x", "operatorRef": "x", "tleEpoch": now()},
        ),
        (
            "create-orbit-pass",
            {
                "satelliteNoradId": "1",
                "regionId": rg_pk,
                "startTs": now(),
                "endTs": now(),
                "maxElevation": 1.0,
            },
        ),
        (
            "create-track",
            {
                "aircraftIcao24": ac_pk,
                "startTs": now(),
                "endTs": now(),
                "hasGap": False,
                "pathJson": "[]",
            },
        ),
    ]
    for action, base in pk_probes:
        r, m = val(action, {**base, "newParameter": "x"})
        print(f"  {action} + newParameter → {r} {m[:80]}")

    # ============ Observation trackId(composed_of) 채움 수단 ============
    sec("7. Observation.trackId(composed_of FK) 채우는 파라미터 존재?")
    r, m = val(
        "create-observation",
        {
            "aircraftIcao24": ac_pk,
            "lat": 1.0,
            "lon": 1.0,
            "onGround": False,
            "source": "x",
            "sourceUrl": "x",
            "ts": now(),
            "newParameter": "x",
            "trackId": "x",
        },
    )
    print(f"  create-observation + trackId → {r} {m[:80]}")

    # ============ 정리 ============
    sec("8. 정리 (delete-* 역순)")
    for action, params in reversed(created):
        ok, err = ex(action, params)
        print(
            f"  {action}({list(params.values())[0]!r}) → {'삭제' if ok else 'FAIL ' + str(err)}"
        )

    sec("9. after counts")
    c1 = {t: count(t) for t in TYPES}
    print("before:", c0)
    print("after :", c1)
    print(
        "delta :",
        {
            t: (c1[t] - c0[t])
            if isinstance(c1[t], int) and isinstance(c0[t], int)
            else "?"
            for t in TYPES
        },
    )
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
