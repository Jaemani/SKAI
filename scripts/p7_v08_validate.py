#!/usr/bin/env python
"""P7 §17 — OSDK 0.8.0 최종 재검증 라이브 왕복 (2026-07-04).

§17 신규 배선(attrsJson·groundTrackJson·station·sentencesJson 채움 + correlatedWith placeholder
제거)을 **실 store 코드 경로**(HybridStore, SKAI_STORE=foundry)로 왕복 검증한다:
  1. 전 타입 write(신규 속성 채움 포함) → Foundry read-back으로 속성 보존 확인
  2. within(Observation→Region)·over(OrbitPass→Region)·observed_as·composed_of traverse
  3. Anomaly 클린 생성(correlatedWith placeholder 없이) + §12 흡수 경고 미발동 확인 + evidenced_by traverse
  4. correlatedWith 원시 프로브: 실 ref 주면 correlatedWithAnomalies 엣지 형성(Optional=제공가능),
     store는 생략(Optional=생략가능) — 양방향 확인
  5. confirm-anomaly 전이 → confirmed
  6. 전부 delete 정리 → 순증 0(KADIZ Region 데모 자산은 유지)

로컬 소재(문장·링크)는 임시 db에 써서 실 skai.db 미오염. 합성 데이터(OpenSky 호출 없음). 시크릿 미출력.
"""

import io
import os
import sys
import time
from contextlib import redirect_stderr

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import json  # noqa: E402

from ontology.model import (  # noqa: E402
    Aircraft,
    Anomaly,
    AssessmentSentence,
    NewsEvent,
    Observation,
    Operator,
    OrbitPass,
    Satellite,
    SituationAssessment,
    Track,
    WeatherState,
)
from ontology.store_foundry import _unix_to_iso, make_store  # noqa: E402

T = int(time.time())
NOW = T
TMP_DB = f"/private/tmp/claude-501/p7v08-{T}.db"

os.environ["SKAI_STORE"] = "foundry"
store = make_store(TMP_DB)  # HybridStore
fs = store.foundry
pf = fs._pf
ONT = fs.ont
A = pf.ontologies.Action
OO = pf.ontologies.OntologyObject
LO = pf.ontologies.LinkedObject

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
cleanup: list[tuple] = []
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
    try:
        return OO.get(ONT, t, pk)
    except Exception:
        return None


def traverse(otype, pk, link):
    try:
        return [_pk(o) for o in LO.list_linked_objects(ONT, otype, pk, link)]
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:50]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def ok(cond):
    return "OK" if cond else "FAIL"


def main():
    sec("0. before counts + KADIZ 확보(id 파라미터, ⑤ 리네임)")
    c0 = {t: count(t) for t in TYPES}
    print(c0)
    rg = "KADIZ"
    region_created = False
    if sget("Region", rg) is None:
        A.apply(
            ONT,
            "create-region",
            parameters={
                "name": "한국 방공식별구역 (KADIZ)",
                "classification": "ADIZ",
                "geoJson": "{}",
                "id": rg,  # ⑤: create-region PK가 'id'(소문자)로 리네임됨
            },
            options={"mode": "VALIDATE_AND_EXECUTE"},
        )
        region_created = True
    print(f"  KADIZ Region: {'신규 생성(유지)' if region_created else '기존 존재'}")

    ac_pk = f"p7v08ac-{T}"
    op_pk = f"p7v08op-{T}"
    sat_pk = f"p7v08sat-{T}"
    orp_pk = f"pass-{sat_pk}-{NOW}"
    trk_pk = f"track-{ac_pk}"
    ws_pk = f"wx-RKSI-{NOW}"
    news_pk = f"news-p7v08-{T}"
    obs_pk = f"{ac_pk}-{NOW}"
    assess_pk = f"assess-KADIZ-{T}"
    anom_pk = f"anomaly-emergency_squawk-{ac_pk}-{NOW // 600}"
    anom2_pk = f"{anom_pk}-corr"

    # ── 1. Aircraft / Operator / Satellite ──
    sec("1. Aircraft·Operator·Satellite write")
    store.write_operator(
        Operator(id=op_pk, name="P7V08-AF", kind="airforce", country="KR")
    )
    cleanup.append(("delete-operator", {"Operator": op_pk}))
    store.write_aircraft(
        Aircraft(
            icao24=ac_pk,
            callsign="P7V08",
            registration="P7V0801",
            is_military=True,
            type="RC-135",
            operator_ref=op_pk,
        )
    )
    cleanup.append(("delete-aircraft", {"Aircraft": ac_pk}))
    store.write_satellite(
        Satellite(
            norad_id=sat_pk,
            name="P7V08-SAT",
            operator_ref=op_pk,
            object_type="PAYLOAD",
            tle_epoch="2026-07-04T00:00:00+00:00",
        )
    )
    cleanup.append(("delete-satellite", {"Satellite": sat_pk}))
    print(f"  aircraft/operator/satellite write 완료 (PK={ac_pk}/{op_pk}/{sat_pk})")

    # ── 2. ★ OrbitPass + groundTrackJson (②) ──
    sec("2. write_orbitpass + groundTrackJson(②) + over traverse")
    gt = [[36.0, 124.0], [36.1, 124.1], [36.2, 124.2]]
    store.write_orbitpass(
        OrbitPass(
            id=orp_pk,
            satellite_ref=sat_pk,
            region_ref=rg,
            start_ts=NOW,
            end_ts=NOW + 600,
            max_elevation=45.0,
            ground_track=gt,
        )
    )
    cleanup.append(("delete-orbit-pass", {"OrbitPass": orp_pk}))
    d = OO.get(ONT, "OrbitPass", orp_pk)
    gt_readback = json.loads(_get(d, "groundTrackJson") or "[]")
    over_tr = traverse("OrbitPass", orp_pk, "region")
    of_tr = traverse("OrbitPass", orp_pk, "satellite")
    gt_ok = gt_readback == gt
    over_ok = isinstance(over_tr, list) and rg in over_tr
    results["orbitpass_groundTrackJson(②)"] = ok(gt_ok)
    results["over(OrbitPass→Region)"] = ok(over_ok)
    print(f"  groundTrackJson read-back == 원본? {gt_ok}  (점 {len(gt_readback)}개)")
    print(f"  over: OrbitPass.region → {over_tr} (=={rg}? {over_ok})  of: {of_tr}")

    # ── 3. Track ──
    sec("3. write_track(→Aircraft FK)")
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
    results["track"] = ok(isinstance(trk_ac, list) and ac_pk in trk_ac)
    print(f"  Track.aircraft → {trk_ac}")

    # ── 4. ★ WeatherState + station (②) ──
    sec("4. write_weatherstate + station(②) 직접 write")
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
    d = OO.get(ONT, "WeatherState", ws_pk)
    station_rb = _get(d, "station")
    ws_rg = traverse("WeatherState", ws_pk, "region")
    station_ok = station_rb == "RKSI"  # PK 복원이 아닌 실제 속성값
    results["weather_station(②)"] = ok(station_ok)
    print(
        f"  station read-back(직접 속성)={station_rb!r} (=='RKSI'? {station_ok})  region→{ws_rg}"
    )
    # read 경로도 station 우선 확인
    wl = [w for w in store.query_weather_latest() if w.id == ws_pk]
    print(f"  store.read station={wl[0].station if wl else None!r}")

    # ── 5. NewsEvent ──
    sec("5. write_newsevent(mentions Optional ① 인접)")
    store.write_newsevent(
        NewsEvent(
            id=news_pk,
            source="gdelt",
            source_url="https://a.example/p7v08",
            ts=NOW,
            title="P7V08",
            summary="s",
            confidence=0.9,
            entities=["KADIZ"],
        ),
        mentions=[("Region", rg)],
    )
    cleanup.append(("delete-news-event", {"NewsEvent": news_pk}))
    d = OO.get(ONT, "NewsEvent", news_pk)
    results["newsevent"] = ok(
        d is not None and abs((_get(d, "confidence") or 0) - 0.4) < 1e-6
    )
    print(f"  newsId={_get(d, 'newsId')!r} confidence(clamp)={_get(d, 'confidence')!r}")

    # ── 6. ★ Observation + attrsJson(②) + within(③ 인접) + composed_of ──
    sec("6. write_observation + attrsJson(②) + within traverse + composed_of")
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
            attrs={"origin_country": "Republic of Korea", "spi": False},
        )
    )
    cleanup.append(("delete-observation", {"Observation": obs_pk}))
    d = OO.get(ONT, "Observation", obs_pk)
    attrs_rb = json.loads(_get(d, "attrsJson") or "{}")
    within_tr = traverse("Observation", obs_pk, "region")
    obs_ac = traverse("Observation", obs_pk, "aircraft")
    attrs_ok = attrs_rb.get("origin_country") == "Republic of Korea"
    within_ok = (
        isinstance(within_tr, list) and rg in within_tr
    )  # KADIZ 내부(36.5,124.5)
    results["obs_attrsJson(②)"] = ok(attrs_ok)
    results["within(Observation→Region)"] = ok(within_ok)
    print(f"  attrsJson read-back={attrs_rb} (origin_country OK? {attrs_ok})")
    print(
        f"  within: Observation.region → {within_tr} (=={rg}? {within_ok})  observed_as→{obs_ac}"
    )
    # composed_of (edit-observation.trackId, create-observation.trackId(③)는 미사용 유지)
    store.link("Track", trk_pk, "composed_of", "Observation", obs_pk)
    obs_trk = traverse("Observation", obs_pk, "track")
    results["composed_of(edit경로 유지)"] = ok(
        isinstance(obs_trk, list) and trk_pk in obs_trk
    )
    print(
        f"  composed_of: Observation.track → {obs_trk} (edit-observation.trackId 경로)"
    )

    # ── 7. ★ SituationAssessment + sentencesJson(② 최우선) ──
    sec("7. write_assessment + sentencesJson(② 최우선, Foundry 문장 cites 보존)")
    store.write_assessment(
        SituationAssessment(
            id=assess_pk,
            region_ref=rg,
            window_start=NOW - 1800,
            window_end=NOW,
            query="KADIZ 상황?",
            summary="P7V08 요약",
            sentences=[
                AssessmentSentence(
                    text="관측 근거 문장",
                    cites=[obs_pk],
                    confidence=0.9,
                    kind="summary",
                ),
                AssessmentSentence(
                    text="위성 통과 문장",
                    cites=[orp_pk],
                    confidence=0.7,
                    kind="satellite",
                ),
            ],
            confidence=0.8,
            produced_by="template",
            created_at=NOW,
        )
    )
    cleanup.append(("delete-situation-assessment", {"SituationAssessment": assess_pk}))
    d = OO.get(ONT, "SituationAssessment", assess_pk)
    sj = json.loads(_get(d, "sentencesJson") or "[]")
    local_a = store.local.get_assessment(assess_pk)
    foundry_cites = [s["cites"] for s in sj]
    sj_ok = foundry_cites == [[obs_pk], [orp_pk]]
    local_ok = local_a is not None and local_a.sentences[0].cites == [obs_pk]
    results["assessment_sentencesJson(②)"] = ok(sj_ok)
    print(f"  Foundry sentencesJson cites={foundry_cites} (보존 OK? {sj_ok})")
    print(
        f"  로컬 권위본 문장 cites={local_a.sentences[0].cites if local_a else None} (read 권위 유지? {local_ok})"
    )

    # ── 8. ★★ Anomaly 클린 생성(correlatedWith placeholder 없이, ①) + 흡수 미발동 ──
    sec("8. write_anomaly 클린(① correlatedWith 생략) + §12 흡수 경고 미발동 확인")
    buf = io.StringIO()
    with redirect_stderr(buf):
        store.write_anomaly(
            Anomaly(
                id=anom_pk,
                type="emergency_squawk",
                ts=NOW,
                confidence=0.9,
                status="candidate",
                lat=36.5,
                lon=124.5,
                explanation="squawk 7700",
                created_at=NOW,
                explainer_backend="template",
            ),
            evidence=[obs_pk],
            involves=[ac_pk],
        )
    cleanup.append(("delete-anomaly", {"Anomaly": anom_pk}))
    warns = buf.getvalue()
    absorb_fired = "무해 ApplyActionFailed 흡수" in warns
    foundry_fail = "Foundry write_anomaly 실패" in warns
    d = OO.get(ONT, "Anomaly", anom_pk)
    evby = traverse("Anomaly", anom_pk, "observations")
    involves_tr = traverse("Anomaly", anom_pk, "aircraft")
    corr_tr = traverse("Anomaly", anom_pk, "correlatedWithAnomalies")
    clean_ok = (not absorb_fired) and (not foundry_fail) and d is not None
    evby_ok = isinstance(evby, list) and obs_pk in evby
    corr_empty = (
        isinstance(corr_tr, list) and len(corr_tr) == 0
    )  # placeholder 생략 → 엣지 없음
    results["anomaly_clean(①)"] = ok(clean_ok)
    results["anomaly_evidenced_by"] = ok(evby_ok)
    results["correlatedWith_omitted_no_edge(①)"] = ok(corr_empty)
    print(
        f"  §12 흡수 경고 발동? {absorb_fired} / Foundry 실패? {foundry_fail} → 클린? {clean_ok}"
    )
    print(f"  evidenced_by: Anomaly.observations → {evby} (=={obs_pk}? {evby_ok})")
    print(f"  involves: Anomaly.aircraft → {involves_tr}")
    print(f"  correlatedWith(생략 → 엣지 없음): {corr_tr} (빈 엣지? {corr_empty})")
    if warns.strip():
        print(f"  [stderr 경고 원문]\n    " + warns.strip().replace("\n", "\n    "))

    # ── 9. correlatedWith 원시 프로브: 실 ref 주면 엣지 형성(Optional=제공가능) ──
    sec("9. correlatedWith 원시 프로브(실 ref → 엣지, Optional 양방향 확인)")
    A.apply(
        ONT,
        "create-anomaly",
        parameters={
            "type": "correlated_pair",
            "ts": _unix_to_iso(NOW),
            "lat": 36.5,
            "lon": 124.5,
            "observations": obs_pk,
            "anomalyId": anom2_pk,
            "correlatedWith": anom_pk,  # 실 ref
        },
        options={"mode": "VALIDATE_AND_EXECUTE"},
    )
    cleanup.append(("delete-anomaly", {"Anomaly": anom2_pk}))
    corr2 = traverse("Anomaly", anom2_pk, "correlatedWithAnomalies")
    corr_real_ok = isinstance(corr2, list) and anom_pk in corr2
    results["correlatedWith_real_ref_edge(①)"] = ok(corr_real_ok)
    print(
        f"  anom2.correlatedWithAnomalies → {corr2} (=={anom_pk}? {corr_real_ok})  [Optional=제공가능 확인]"
    )

    # ── 10. confirm-anomaly 전이 ──
    sec("10. set_anomaly_status(confirmed) dual 전이")
    store.set_anomaly_status(anom_pk, "confirmed")
    d = OO.get(ONT, "Anomaly", anom_pk)
    local_status = (
        store.local.get_anomaly(anom_pk).status
        if hasattr(store.local, "get_anomaly")
        else "?"
    )
    confirm_ok = _get(d, "status") == "confirmed"
    results["confirm_anomaly"] = ok(confirm_ok)
    print(f"  Foundry status → {_get(d, 'status')!r} / 로컬 → {local_status!r}")

    # ── 정리 ──
    sec("CLEANUP (delete 역순, KADIZ 유지)")
    for action, params in reversed(cleanup):
        try:
            A.apply(
                ONT, action, parameters=params, options={"mode": "VALIDATE_AND_EXECUTE"}
            )
        except Exception as e:
            print(
                f"  {action}({list(params.values())[0]!r}) FAIL:{type(e).__name__}:{str(e)[:60]}"
            )

    sec("after counts / delta")
    c1 = {t: count(t) for t in TYPES}
    delta = {t: c1[t] - c0[t] for t in TYPES}
    print("before:", c0)
    print("after :", c1)
    print("delta :", delta)
    exp_rg = 1 if region_created else 0
    net_zero = all(
        (delta[t] == 0) or (t == "Region" and delta[t] == exp_rg) for t in TYPES
    )
    print(f"\n순증 0(KADIZ 유지 허용)? {net_zero}")

    sec("판정")
    for k, v in results.items():
        print(f"  {k}: {v}")
    all_ok = all(v == "OK" for v in results.values()) and net_zero
    print(
        f"\n[V08-{'OK' if all_ok else '부분/FAIL'}]  순증: {'0(KADIZ 유지)' if net_zero else '확인필요'}"
    )
    print(f"임시 로컬 db: {TMP_DB} (skai.db 미오염)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
