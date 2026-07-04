#!/usr/bin/env python
"""P7 §11 store_foundry 전량 확장 라이브 검증 (2026-07-04).

HybridStore(SKAI_STORE=foundry)로 신규 7타입 + 링크 write → Foundry에서 read-back·traverse →
전부 delete 정리. before==after(순증 0, KADIZ Region 데모 자산은 유지 판단). 시크릿 미출력.

로컬 소재(mentions·assessment 문장)는 **임시 db**에 써서 실 skai.db를 오염시키지 않는다.
Foundry 왕복만이 검증 대상. 합성/고정 데이터(OpenSky 호출 없음).
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from ontology.model import (  # noqa: E402
    AssessmentSentence,
    Aircraft,
    NewsEvent,
    Observation,
    Operator,
    OrbitPass,
    Satellite,
    SituationAssessment,
    Track,
    WeatherState,
)
from ontology.store_foundry import make_store  # noqa: E402

T = int(time.time())
NOW = T
TMP_DB = f"/private/tmp/claude-501/p7x-{T}.db"

os.environ["SKAI_STORE"] = "foundry"
store = make_store(TMP_DB)  # HybridStore
fs = store.foundry
pf = fs._pf
ONT = fs.ont
A = pf.ontologies.Action
OO = pf.ontologies.OntologyObject
LO = pf.ontologies.LinkedObject

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
TYPES = (
    "Aircraft",
    "Observation",
    "Region",
    "Operator",
    "Track",
    "Satellite",
    "OrbitPass",
    "WeatherState",
    "NewsEvent",
    "SituationAssessment",
)
cleanup: list[tuple] = []  # (delete_action, {param: pk})
results: dict[str, str] = {}


def _get(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _pk(o):
    pk = _get(o, "__primaryKey")
    if pk is None:
        for k in PK_FIELDS:
            v = _get(o, k)
            if v is not None:
                return v
    return pk


def count(t):
    return len(list(OO.list(ONT, t)))


def sget(t, pk):
    """OO.get이 미존재 시 NotFoundError를 던지므로 None 폴백 래퍼."""
    try:
        return OO.get(ONT, t, pk)
    except Exception:
        return None


def traverse(otype, pk, link):
    try:
        return [_pk(o) for o in LO.list_linked_objects(ONT, otype, pk, link)]
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:60]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def ok(cond):
    return "OK" if cond else "FAIL"


def main():
    sec("0. before counts")
    c0 = {t: count(t) for t in TYPES}
    print(c0)

    # ── KADIZ Region(FK 타깃, 데모 자산) 확보 — write_region은 로컬이라 저수준으로 시딩 ──
    region_created = False
    rg = "KADIZ"
    if sget("Region", rg) is None:
        A.apply(
            ONT,
            "create-region",
            parameters={
                "name": "한국 방공식별구역 (KADIZ)",
                "classification": "ADIZ",
                "geoJson": "{}",
                "newParameter": rg,
            },
            options={"mode": "VALIDATE_AND_EXECUTE"},
        )
        region_created = True
    print(f"  KADIZ Region: {'신규 생성(유지)' if region_created else '기존 존재'}")

    ac_pk = f"p7xac-{T}"
    sat_pk = f"p7xsat-{T}"
    op_pk = f"p7xop-{T}"
    orp_pk = f"pass-{sat_pk}-{NOW}"
    trk_pk = f"track-{ac_pk}"
    ws_pk = f"wx-RKSI-{NOW}"
    news_pk = f"news-p7x-{T}"
    obs_pk = f"{ac_pk}-{NOW}"
    assess_pk = f"assess-KADIZ-{T}"

    # ── Aircraft(FK 타깃) ──
    sec("SETUP Aircraft (Foundry, track/obs FK 타깃)")
    store.write_aircraft(
        Aircraft(
            icao24=ac_pk,
            callsign="P7X",
            registration="P7X01",
            is_military=True,
            type="RC-135",
            operator_ref=op_pk,
        )
    )
    cleanup.append(("delete-aircraft", {"Aircraft": ac_pk}))
    d = OO.get(ONT, "Aircraft", ac_pk)
    print(f"  read-back icao24={_get(d, 'icao24')!r} callsign={_get(d, 'callsign')!r}")

    # ── 1. Operator ──
    sec("1. write_operator")
    store.write_operator(
        Operator(id=op_pk, name="P7X-AF", kind="airforce", country="KR")
    )
    cleanup.append(("delete-operator", {"Operator": op_pk}))
    d = OO.get(ONT, "Operator", op_pk)
    good = (
        d is not None
        and _get(d, "operatorId") == op_pk
        and _get(d, "kind") == "airforce"
    )
    results["write_operator"] = ok(good)
    print(
        f"  read-back operatorId={_get(d, 'operatorId')!r} kind={_get(d, 'kind')!r} → {results['write_operator']}"
    )

    # ── 2. Satellite (dedup 확인) ──
    sec("2. write_satellite (+ dedup)")
    sat = Satellite(
        norad_id=sat_pk,
        name="P7X-SAT",
        operator_ref=op_pk,
        object_type="PAYLOAD",
        tle_epoch="2026-07-04T00:00:00+00:00",
    )
    store.write_satellite(sat)
    cleanup.append(("delete-satellite", {"Satellite": sat_pk}))
    d = OO.get(ONT, "Satellite", sat_pk)
    pk_match = d is not None and _get(d, "noradId") == sat_pk
    # dedup: 같은 세션 재호출 → _apply 미발생(카운트 불변)
    before = count("Satellite")
    store.write_satellite(sat)
    dedup_ok = count("Satellite") == before
    results["write_satellite"] = ok(pk_match and dedup_ok)
    print(
        f"  noradId={_get(d, 'noradId')!r} (PK==요청? {pk_match}) dedup 불변? {dedup_ok} → {results['write_satellite']}"
    )

    # ── 3. OrbitPass (of→Satellite FK, over→Region) ──
    sec("3. write_orbitpass (of/over FK)")
    store.write_orbitpass(
        OrbitPass(
            id=orp_pk,
            satellite_ref=sat_pk,
            region_ref=rg,
            start_ts=NOW,
            end_ts=NOW + 600,
            max_elevation=45.0,
        )
    )
    cleanup.append(("delete-orbit-pass", {"OrbitPass": orp_pk}))
    of_tr = traverse("OrbitPass", orp_pk, "satellite")
    over_tr = traverse("OrbitPass", orp_pk, "region")
    of_ok = isinstance(of_tr, list) and sat_pk in of_tr
    results["write_orbitpass"] = ok(of_ok)
    print(f"  of:   OrbitPass.satellite → {of_tr} (=={sat_pk}? {of_ok})")
    print(f"  over: OrbitPass.region → {over_tr}  (P7 §10-3: 미형성 예상)")

    # ── 4. Track (→Aircraft FK) ──
    sec("4. write_track (→Aircraft FK)")
    store.write_track(
        Track(
            id=trk_pk,
            aircraft_ref=ac_pk,
            start_ts=NOW,
            end_ts=NOW + 60,
            path=[[36.5, 124.5], [36.6, 124.6]],
            has_gap=False,
        )
    )
    cleanup.append(("delete-track", {"Track": trk_pk}))
    trk_ac = traverse("Track", trk_pk, "aircraft")
    trk_ok = isinstance(trk_ac, list) and ac_pk in trk_ac
    results["write_track"] = ok(trk_ok)
    print(
        f"  Track.aircraft → {trk_ac} (=={ac_pk}? {trk_ok}) → {results['write_track']}"
    )

    # ── 5. WeatherState (→Region FK, provenance) ──
    sec("5. write_weatherstate (→Region FK)")
    store.write_weatherstate(
        WeatherState(
            id=ws_pk,
            region_ref=rg,
            ts=NOW,
            station="RKSI",
            wind_dir=200,
            wind_speed_kt=8,
            visibility_sm=6.0,
            ceiling_ft=3000,
            flight_category="MVFR",
            conditions="METAR RKSI 200208KT",
            source="aviationweather",
            source_url="https://aviationweather.gov/api/data/metar",
        )
    )
    cleanup.append(("delete-weather-state", {"WeatherState": ws_pk}))
    ws_rg = traverse("WeatherState", ws_pk, "region")
    d = OO.get(ONT, "WeatherState", ws_pk)
    ws_ok = isinstance(ws_rg, list) and rg in ws_rg and _get(d, "conditions") == "MVFR"
    results["write_weatherstate"] = ok(ws_ok)
    print(
        f"  WeatherState.region → {ws_rg} (=={rg}?)  conditions(←flight_cat)={_get(d, 'conditions')!r} wind={_get(d, 'wind')!r} → {results['write_weatherstate']}"
    )

    # ── 6. NewsEvent (객체 Foundry, mentions 로컬) ──
    sec("6. write_newsevent (객체 Foundry / mentions 로컬)")
    store.write_newsevent(
        NewsEvent(
            id=news_pk,
            source="gdelt",
            source_url="https://a.example/p7x",
            ts=NOW,
            title="P7X 테스트",
            summary="s",
            confidence=0.9,
            entities=["KADIZ"],
        ),
        mentions=[("Region", rg)],
    )
    cleanup.append(("delete-news-event", {"NewsEvent": news_pk}))
    d = OO.get(ONT, "NewsEvent", news_pk)
    local_mentions = store.local.query_mentions(news_pk)
    news_ok = (
        d is not None
        and _get(d, "newsId") == news_pk
        and abs((_get(d, "confidence") or 0) - 0.4) < 1e-6  # clamp
        and {"type": "Region", "id": rg} in local_mentions
    )
    results["write_newsevent"] = ok(news_ok)
    print(
        f"  newsId={_get(d, 'newsId')!r} confidence(clamp0.4)={_get(d, 'confidence')!r} url={_get(d, 'url')!r}"
    )
    print(f"  로컬 권위 mentions={local_mentions} → {results['write_newsevent']}")

    # ── 7. Observation + composed_of(trackId 귀속) ──
    sec("7. Observation + composed_of (edit-observation.trackId)")
    store.write_observation(
        Observation(
            id=obs_pk,
            aircraft_ref=ac_pk,
            ts=NOW,
            lat=36.5,
            lon=124.5,
            alt=9500.0,
            velocity=210.0,
            heading=270.0,
            squawk="7700",
            on_ground=False,
            source="opensky",
            source_url="https://opensky-network.org/api/states/all",
        )
    )
    cleanup.append(("delete-observation", {"Observation": obs_pk}))
    obs_ac = traverse("Observation", obs_pk, "aircraft")  # observed_as
    print(f"  observed_as: Observation.aircraft → {obs_ac} (=={ac_pk}?)")
    # composed_of: link → edit-observation.trackId
    store.link("Track", trk_pk, "composed_of", "Observation", obs_pk)
    obs_trk = traverse("Observation", obs_pk, "track")
    trk_obs = traverse("Track", trk_pk, "observations")
    comp_ok = (
        isinstance(obs_trk, list)
        and trk_pk in obs_trk
        and isinstance(trk_obs, list)
        and obs_pk in trk_obs
    )
    results["composed_of"] = ok(comp_ok)
    print(
        f"  composed_of: Observation.track → {obs_trk} / Track.observations → {trk_obs} → {results['composed_of']}"
    )

    # ── 8. Assessment (dual-write: 로컬 문장 + Foundry 스칼라) ──
    sec("8. write_assessment (dual: 로컬 권위본 + Foundry 스칼라)")
    store.write_assessment(
        SituationAssessment(
            id=assess_pk,
            region_ref=rg,
            window_start=NOW - 1800,
            window_end=NOW,
            query="KADIZ 상황?",
            summary="P7X 요약",
            sentences=[
                AssessmentSentence(text="근거 문장", cites=[obs_pk], confidence=0.9)
            ],
            confidence=0.8,
            produced_by="template",
            created_at=NOW,
        )
    )
    cleanup.append(("delete-situation-assessment", {"SituationAssessment": assess_pk}))
    d = OO.get(ONT, "SituationAssessment", assess_pk)
    local_a = store.local.get_assessment(assess_pk)
    assess_ok = (
        d is not None
        and _get(d, "assessmentId") == assess_pk
        and local_a is not None
        and local_a.sentences[0].cites == [obs_pk]
    )
    results["write_assessment"] = ok(assess_ok)
    print(
        f"  Foundry 스칼라 assessmentId={_get(d, 'assessmentId')!r} / 로컬 문장 cites={local_a.sentences[0].cites if local_a else None} → {results['write_assessment']}"
    )

    # ── 9. set-region-alert-level (임시 Region으로 — KADIZ 미변경) ──
    sec("9. set_region_alert_level (임시 Region, Modify)")
    tmp_rg = f"p7x-rg-{T}"
    A.apply(
        ONT,
        "create-region",
        parameters={
            "name": "P7X-TMP",
            "classification": "OpArea",
            "geoJson": "{}",
            "newParameter": tmp_rg,
        },
        options={"mode": "VALIDATE_AND_EXECUTE"},
    )
    cleanup.append(("delete-region", {"Region": tmp_rg}))
    before_rg = count("Region")
    store.set_region_alert_level(tmp_rg, "RED")
    d = OO.get(ONT, "Region", tmp_rg)
    after_rg = count("Region")
    alert_ok = _get(d, "alertLevel") == "RED" and before_rg == after_rg
    results["set_region_alert_level"] = ok(alert_ok)
    print(
        f"  alertLevel → {_get(d, 'alertLevel')!r}  Region count {before_rg}→{after_rg}(팬텀 없음?) → {results['set_region_alert_level']}"
    )

    # ── 10. delete-orbit-pass 재계산 정리 배선 ──
    sec("10. delete_future_orbitpasses_for (Foundry 정리)")
    n_del = store.delete_future_orbitpasses_for(
        sat_pk, NOW - 1
    )  # orp_pk(start=NOW) 삭제 대상
    # 삭제되면 cleanup 목록의 orbit-pass는 이미 없음 → 정리 시 skip 처리
    still = sget("OrbitPass", orp_pk)
    del_ok = n_del >= 1 and still is None
    results["delete_orbit_pass"] = ok(del_ok)
    if still is None:
        cleanup[:] = [c for c in cleanup if c[1].get("OrbitPass") != orp_pk]
    print(
        f"  삭제 수={n_del} OrbitPass({orp_pk}) 잔존? {still is not None} → {results['delete_orbit_pass']}"
    )

    # ── 정리 ──
    sec("CLEANUP (delete 역순, KADIZ 유지)")
    for action, params in reversed(cleanup):
        try:
            A.apply(
                ONT, action, parameters=params, options={"mode": "VALIDATE_AND_EXECUTE"}
            )
            print(f"  {action}({list(params.values())[0]!r}) 삭제")
        except Exception as e:
            print(
                f"  {action}({list(params.values())[0]!r}) FAIL:{type(e).__name__}:{str(e)[:70]}"
            )

    sec("after counts / delta")
    c1 = {t: count(t) for t in TYPES}
    delta = {t: c1[t] - c0[t] for t in TYPES}
    print("before:", c0)
    print("after :", c1)
    print("delta :", delta)
    # KADIZ 신규 생성분(+1 Region)은 의도적 유지
    expected_region_delta = 1 if region_created else 0
    net_zero = all(
        (delta[t] == 0) or (t == "Region" and delta[t] == expected_region_delta)
        for t in TYPES
    )
    print(f"\n순증 0(KADIZ 유지 허용)? {net_zero}")

    sec("판정")
    print("write 7종 + 링크 + 정리·전이:")
    for k, v in results.items():
        print(f"  {k}: {v}")
    all_ok = all(v == "OK" for v in results.values()) and net_zero
    print(
        f"\n[EXTEND-{'OK' if all_ok else '부분/FAIL'}]  Foundry 정리: {'완료(순증0)' if net_zero else '확인필요'}"
    )
    print(f"임시 로컬 db: {TMP_DB} (skai.db 미오염)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
