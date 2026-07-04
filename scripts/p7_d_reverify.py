#!/usr/bin/env python
"""P7 D-1~D-6 수정 재검증 (2026-07-04). 저수준 foundry_sdk 라이브.

VALIDATE 우선, EXECUTE는 항목당 최소. 끝에서 전부 delete. before==after 확인.
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
cleanup = []
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
PK_FIELDS = (
    "obsId",
    "icao24",
    "anomalyId",
    "newsId",
    "id",
    "operatorId",
    "passId",
    "trackId",
    "noradId",
    "assessmentId",
    "weatherId",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def _get(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def pkset(t):
    out = set()
    for o in OO.list(ONT, t):
        pk = _get(o, "__primaryKey")
        if pk is None:
            for k in PK_FIELDS:
                v = _get(o, k)
                if v is not None:
                    pk = v
                    break
        out.add(pk)
    return out


def count(t):
    return len(list(OO.list(ONT, t)))


def val(action, params):
    """VALIDATE_ONLY → validation result 문자열 반환."""
    try:
        resp = A.apply(ONT, action, parameters=params, options=VALIDATE)
        v = getattr(resp, "validation", None)
        return str(getattr(v, "result", "?"))
    except Exception as e:
        return f"EXC:{type(e).__name__}:{str(e).replace(chr(10), ' ')[:110]}"


def ex(action, params, otype=None, del_action=None, del_param=None):
    """EXECUTE. otype 주면 list-diff로 UUID PK 포착. (executed, result, pk, err)"""
    before = pkset(otype) if otype else None
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return (
            False,
            "EXC",
            None,
            f"{type(e).__name__}: {str(e).replace(chr(10), ' ')[:160]}",
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
    if pk is None and otype:
        new = pkset(otype) - before
        if len(new) == 1:
            pk = next(iter(new))
    if pk and del_action:
        cleanup.append((del_action, {del_param: pk}))
    return (result == "VALID"), result, pk, None


def traverse(otype, pk, link):
    try:
        res = LO.list_linked_objects(ONT, otype, pk, link)
        objs = list(res)
        pks = []
        for o in objs:
            p = _get(o, "__primaryKey")
            if p is None:
                for k in PK_FIELDS:
                    v = _get(o, k)
                    if v is not None:
                        p = v
                        break
            pks.append(p)
        return pks
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:80]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main():
    sec("0. before counts")
    c0 = {t: count(t) for t in TYPES}
    print(c0)

    # ---- 셋업 ----
    sec("SETUP: aircraft / region / satellite / observation (실 PK)")
    ac_pk = f"p7d-ac-{T}"
    okv, r, ac, _ = ex(
        "create-aircraft",
        {
            "callsign": "P7D",
            "registration": "P7D01",
            "isMilitary": True,
            "type": "RC-135",
            "operatorRef": "P7DAF",
            "newParameter": ac_pk,
        },
        "Aircraft",
        "delete-aircraft",
        "Aircraft",
    )
    print(f"  create-aircraft exec={okv} pk={ac!r}")
    rg_pk = f"p7d-rg-{T}"
    okv, r, rg, _ = ex(
        "create-region",
        {
            "name": "P7D-KADIZ",
            "classification": "ADIZ",
            "geoJson": "{}",
            "newParameter": rg_pk,
        },
        "Region",
        "delete-region",
        "Region",
    )
    print(f"  create-region exec={okv} pk={rg!r}")
    sat_pk = f"p7d-sat-{T}"
    okv, r, sat, _ = ex(
        "create-satellite",
        {
            "name": "P7D-SAT",
            "objectType": "PAYLOAD",
            "operatorRef": "x",
            "tleEpoch": now(),
            "newParameter": sat_pk,
        },
        "Satellite",
        "delete-satellite",
        "Satellite",
    )
    print(f"  create-satellite exec={okv} pk={sat!r} (noradId==요청? {sat == sat_pk})")
    obs_pk = f"p7d-obs-{T}"
    okv, r, obs, err = ex(
        "create-observation",
        {
            "aircraftIcao24": ac,
            "lat": 36.5,
            "lon": 124.5,
            "onGround": False,
            "source": "opensky",
            "sourceUrl": "https://x",
            "ts": now(),
            "newParameter": obs_pk,
        },
        "Observation",
        "delete-observation",
        "Observation",
    )
    print(f"  create-observation exec={okv} pk={obs!r} err={err}")
    # observed_as traverse
    print(
        f"  observed_as: Observation.aircraft → {traverse('Observation', obs, 'aircraft')} (=={ac!r}?)"
    )

    # ============ D-1 create-anomaly ============
    sec("D-1. create-anomaly evidence 강제 (★데모 최우선)")
    # 1a. evidence(Observation 링크 파라미터) 없이 → 거부되나?
    r_noev = val(
        "create-anomaly",
        {
            "type": "emergency_squawk",
            "ts": now(),
            "lat": 36.5,
            "lon": 124.5,
            "newParameter": f"p7d-an-{T}",
        },
    )
    print(f"  [거부검증] observations·newParameter1(Observation 필수) 생략 → {r_noev}")
    print("    (INVALID이면 = 근거 없는 Anomaly 거부 = provenance 강제 ✓)")

    # 1b. objectSet(newParameter1) 인코딩 탐색 — VALIDATE로 통과형 찾기
    base_ok = {
        "type": "emergency_squawk",
        "ts": now(),
        "lat": 36.5,
        "lon": 124.5,
        "confidence": 0.9,
        "status": "candidate",
        "explanation": "P7D obs-grounded",
        "evidence": f"obs:{obs}",
        "observations": obs,
        "newParameter": f"p7d-an-probe-{T}",
    }
    objset_forms = {
        "pk_string": obs,
        "pk_list": [obs],
        "filter_def": {
            "type": "filter",
            "objectSet": {"type": "base", "objectType": "Observation"},
            "where": {"type": "eq", "field": "obsId", "value": obs},
        },
        "base_def": {"type": "base", "objectType": "Observation"},
    }
    chosen = None
    for name, form in objset_forms.items():
        r = val("create-anomaly", {**base_ok, "newParameter1": form})
        print(f"  [np1 인코딩] {name} → {r}")
        if r == "VALID" and chosen is None:
            chosen = (name, form)
    # observations 단독(np1 생략) 수용?
    r_obsonly = val("create-anomaly", {k: v for k, v in base_ok.items()})
    print(
        f"  [np1 생략, observations만] → {r_obsonly} (newParameter1 required면 INVALID)"
    )

    # 1c. EXECUTE happy-path (통과형 있으면)
    an = None
    if chosen:
        print(f"  → objectSet 통과형 채택: {chosen[0]}")
        okv, r, an, err = ex(
            "create-anomaly",
            {
                "type": "emergency_squawk",
                "ts": now(),
                "lat": 36.5,
                "lon": 124.5,
                "confidence": 0.9,
                "status": "candidate",
                "explanation": "P7D obs-grounded",
                "evidence": f"obs:{obs}",
                "observations": obs,
                "newParameter1": chosen[1],
                "newParameter": f"p7d-an-{T}",
            },
            "Anomaly",
            "delete-anomaly",
            "Anomaly",
        )
        print(
            f"  create-anomaly (evidence 포함) exec={okv} result={r} pk={an!r} err={err}"
        )
        print(f"    → ApplyActionFailed 재발? {'예' if err else '아니오(깔끔)'}")
        if an:
            d = OO.get(ONT, "Anomaly", an)
            print(
                f"    read-back: anomalyId={d.get('anomalyId')!r} status={d.get('status')!r} "
                f"conf={d.get('confidence')!r} evidence(scalar)={d.get('evidence')!r}"
            )
            print(
                f"    evidenced_by traverse: Anomaly.observations → {traverse('Anomaly', an, 'observations')}"
            )
            print(f"      (== [{obs!r}] 이면 Observation-근거 그래프 엣지 형성 ✓)")
    else:
        print(
            "  → objectSet 통과형 없음 → newParameter1(required objectSet)이 clean create 차단 가능"
        )
        # observations만으로라도 EXECUTE 시도(np1에 pk string)
        okv, r, an, err = ex(
            "create-anomaly",
            {
                "type": "emergency_squawk",
                "ts": now(),
                "lat": 36.5,
                "lon": 124.5,
                "confidence": 0.9,
                "status": "candidate",
                "explanation": "P7D obs-grounded",
                "evidence": f"obs:{obs}",
                "observations": obs,
                "newParameter1": obs,
                "newParameter": f"p7d-an-{T}",
            },
            "Anomaly",
            "delete-anomaly",
            "Anomaly",
        )
        print(
            f"  fallback EXECUTE (np1=pk string) exec={okv} result={r} pk={an!r} err={err}"
        )
        if an:
            print(
                f"    evidenced_by traverse: Anomaly.observations → {traverse('Anomaly', an, 'observations')}"
            )

    # ============ D-2 set-region-alert-level ============
    sec("D-2. set-region-alert-level (Region 대상 + Modify)")
    before_rg_count = count("Region")
    b = OO.get(ONT, "Region", rg)
    okv, r, _, err = ex("set-region-alert-level", {"region": rg, "alertLevel": "RED"})
    a = OO.get(ONT, "Region", rg)
    after_rg_count = count("Region")
    print(f"  exec={okv} result={r} err={err}")
    print(
        f"  Region.alertLevel: before={b.get('alertLevel')!r} after={a.get('alertLevel')!r}"
    )
    print(
        f"  Region count: {before_rg_count} → {after_rg_count} (팬텀 Region {'없음 ✓' if before_rg_count == after_rg_count else '생성됨 ✗'})"
    )

    # ============ D-3 신규 7타입 PK + dedup ============
    sec("D-3. 신규 7타입 PK 파라미터 + dedup")
    print(f"  satellite noradId 지정 PK: {sat!r} (=={sat_pk}? {sat == sat_pk})")
    # dedup: 같은 PK 재생성
    okv, r, _, err = ex(
        "create-satellite",
        {
            "name": "dup",
            "objectType": "x",
            "operatorRef": "x",
            "tleEpoch": now(),
            "newParameter": sat_pk,
        },
    )
    print(f"  같은 noradId 재생성 → exec={okv} err={err}")
    print(f"    (ObjectAlreadyExists 계열이면 dedup 강제 ✓)")
    # 나머지 create에 newParameter 수용여부 VALIDATE
    for action, base in [
        ("create-operator", {"name": "x", "kind": "x", "country": "x"}),
        (
            "create-orbit-pass",
            {
                "satelliteNoradId": sat,
                "regionId": rg,
                "startTs": now(),
                "endTs": now(),
                "maxElevation": 1.0,
            },
        ),
        (
            "create-track",
            {
                "aircraftIcao24": ac,
                "startTs": now(),
                "endTs": now(),
                "hasGap": False,
                "pathJson": "[]",
            },
        ),
        (
            "create-weather-state",
            {
                "regionId": rg,
                "ts": now(),
                "wind": "x",
                "visibilitySm": 1.0,
                "ceilingFt": 1.0,
                "conditions": "x",
                "rawText": "x",
                "source": "x",
                "sourceUrl": "x",
            },
        ),
        (
            "create-news-event",
            {
                "source": "g",
                "url": "https://x",
                "ts": now(),
                "title": "t",
                "summary": "s",
                "entitiesJson": "[]",
                "confidence": 0.3,
                "lat": 1.0,
                "lon": 1.0,
            },
        ),
        (
            "create-situation-assessment",
            {
                "regionId": rg,
                "windowStart": now(),
                "windowEnd": now(),
                "summary": "s",
                "confidence": 0.5,
                "producedBy": "p",
                "createdAt": now(),
            },
        ),
    ]:
        r = val(action, {**base, "newParameter": "x-pk-probe"})
        print(f"  {action} + newParameter → {r}")

    # ============ D-4 링크 형성/채움 ============
    sec("D-4. 링크 traverse (of/over/within/Track→AC/Weather→Region/Assessment→Region)")
    okv, r, orp, err = ex(
        "create-orbit-pass",
        {
            "satelliteNoradId": sat,
            "regionId": rg,
            "startTs": now(),
            "endTs": now(),
            "maxElevation": 45.0,
            "newParameter": f"p7d-orp-{T}",
        },
        "OrbitPass",
        "delete-orbit-pass",
        "OrbitPass",
    )
    print(f"  create-orbit-pass exec={okv} pk={orp!r} err={err}")
    if orp:
        print(
            f"  of:  OrbitPass.satellite → {traverse('OrbitPass', orp, 'satellite')} (=={sat!r}?)"
        )
        print(
            f"  over: OrbitPass.region → {traverse('OrbitPass', orp, 'region')} (링크 없으면 ERR)"
        )
    okv, r, trk, err = ex(
        "create-track",
        {
            "aircraftIcao24": ac,
            "startTs": now(),
            "endTs": now(),
            "hasGap": False,
            "pathJson": "[]",
            "newParameter": f"p7d-trk-{T}",
        },
        "Track",
        "delete-track",
        "Track",
    )
    print(f"  create-track exec={okv} pk={trk!r} err={err}")
    if trk:
        print(
            f"  Track→AC: Track.aircraft → {traverse('Track', trk, 'aircraft')} (=={ac!r}?)"
        )
    okv, r, ws, err = ex(
        "create-weather-state",
        {
            "regionId": rg,
            "ts": now(),
            "wind": "200/8",
            "visibilitySm": 6.0,
            "ceilingFt": 3000.0,
            "conditions": "MVFR",
            "rawText": "METAR",
            "source": "x",
            "sourceUrl": "https://x",
            "newParameter": f"p7d-ws-{T}",
        },
        "WeatherState",
        "delete-weather-state",
        "WeatherState",
    )
    print(f"  create-weather-state exec={okv} pk={ws!r} err={err}")
    if ws:
        print(
            f"  Weather→Region: WeatherState.region → {traverse('WeatherState', ws, 'region')} (=={rg!r}?)"
        )
    # within: Observation.region — regionId 채울 파라미터 있나? (create/edit-observation)
    print(
        f"  within: Observation.region traverse → {traverse('Observation', obs, 'region')} (regionId 미채움이면 빈값)"
    )

    # ============ D-5 composed_of ============
    sec("D-5. composed_of (edit-observation.trackId)")
    obs2_pk = f"p7d-obs2-{T}"
    okv, r, obs2, err = ex(
        "create-observation",
        {
            "aircraftIcao24": ac,
            "lat": 1.0,
            "lon": 1.0,
            "onGround": False,
            "source": "x",
            "sourceUrl": "https://x",
            "ts": now(),
            "newParameter": obs2_pk,
        },
        "Observation",
        "delete-observation",
        "Observation",
    )
    print(f"  create-observation(obs2) exec={okv} pk={obs2!r}")
    if obs2 and trk:
        okv, r, _, err = ex(
            "edit-observation",
            {
                "Observation": obs2,
                "alt": 0.0,
                "heading": 0.0,
                "lat": 1.0,
                "lon": 1.0,
                "onGround": False,
                "source": "x",
                "sourceUrl": "https://x",
                "squawk": "0000",
                "ts": now(),
                "velocity": 0.0,
                "newParameter": obs2,
                "trackId": trk,
            },
        )
        print(f"  edit-observation(trackId={trk!r}) exec={okv} result={r} err={err}")
        d = OO.get(ONT, "Observation", obs2)
        print(f"    Observation.trackId read-back = {d.get('trackId')!r}")
        print(
            f"    composed_of: Track.observations → {traverse('Track', trk, 'observations')} (obs2 포함?)"
        )
        print(
            f"    Observation.track → {traverse('Observation', obs2, 'track')} (=={trk!r}?)"
        )

    # ============ D-6 self-link required 해제 ============
    sec("D-6. self-link required 해제 (VALIDATE, self ref 생략)")
    r_ne = val(
        "create-news-event",
        {
            "aircraft": ac,
            "operators": "x",
            "regions": rg,
            "source": "g",
            "url": "https://x",
            "ts": now(),
            "title": "t",
            "summary": "s",
            "entitiesJson": "[]",
            "confidence": 0.3,
            "lat": 1.0,
            "lon": 1.0,
            "newParameter": "x",
        },
    )
    print(f"  create-news-event (newsEvents self 생략) → {r_ne}")
    r_sa = val(
        "create-situation-assessment",
        {
            "regionId": rg,
            "windowStart": now(),
            "windowEnd": now(),
            "summary": "s",
            "confidence": 0.5,
            "producedBy": "p",
            "createdAt": now(),
            "anomalies": an or "x",
            "newsEvents": "x",
            "observations": obs,
            "orbitPasses": orp or "x",
            "newParameter": "x",
        },
    )
    print(f"  create-situation-assessment (situationAssessments self 생략) → {r_sa}")
    print("  (VALID/무INVALID이면 self-link required 해제 ✓; INVALID면 잔존)")

    # ============ 정리 ============
    sec("CLEANUP (delete 역순)")
    for action, params in reversed(cleanup):
        okv, r, _, err = ex(action, params)
        print(
            f"  {action}({list(params.values())[0]!r}) → {'삭제' if okv else 'FAIL:' + str(err)[:60]}"
        )

    sec("after counts / delta")
    c1 = {t: count(t) for t in TYPES}
    print("before:", c0)
    print("after :", c1)
    print("delta :", {t: c1[t] - c0[t] for t in TYPES})
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
