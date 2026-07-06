"""P5 검증 테스트 — 이상탐지 확장(4종) + dropout 교차 로직 + correlated_with + 시공간 버킷.

커버:
  1. 룰 4종 양성·음성 (dropout / 로이터링 / 군용기 접근 / 위성 근접).
  2. dropout 교차 로직 — 미확인(None)=저신뢰 0.4대 · 확인(True)=상향 0.7대 · 관측(False)=생성 안 함.
  3. military_db 저신뢰 판정(콜사인 프리픽스·군용 대역·해당없음).
  4. correlated_with 링크 영속 + query.
  5. 시공간 버킷 경계 — 위성 ±60분 경계 · 이상징후↔이상징후 시간·공간 경계.
  6. scan_and_create_all 통합(전 유형 + 상관 영속).

실행: .venv/bin/python -m pytest tests/test_p5.py -v
"""

from __future__ import annotations

import math

from anomaly.correlation import (
    CORRELATION_WINDOW_SECONDS,
    SPATIAL_CORRELATION_KM,
    correlate,
)
from anomaly.crosscheck import NullCrossCheckSource, SyntheticMirrorSource
from anomaly.explainer import explain_draft
from anomaly.military_db import classify_military
from anomaly.rules import (
    ANOMALY_TYPE_ADSB_DROPOUT,
    ANOMALY_TYPE_LOITERING,
    ANOMALY_TYPE_MILITARY_APPROACH,
    ANOMALY_TYPE_RAPID_MANEUVER,
    ANOMALY_TYPE_SATELLITE_PROXIMITY,
    DROPOUT_ACTIVE_WINDOW_SECONDS,
    DROPOUT_CONFIRMED_CONFIDENCE,
    DROPOUT_LANDING_CONFIDENCE,
    DROPOUT_UNCONFIRMED_CONFIDENCE,
    MANEUVER_CONFIDENCE_BASE,
    detect_adsb_dropout,
    detect_loitering,
    detect_military_approach,
    detect_rapid_maneuver,
    detect_satellite_proximity,
)
from ontology.model import (
    KADIZ_REGION,
    OPAREA_WEST_REGION,
    Aircraft,
    Anomaly,
    Observation,
    OrbitPass,
    Region,
)
from ontology.store_local import LocalOntologyStore

SENSITIVE = [KADIZ_REGION, OPAREA_WEST_REGION]


def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "p5.db"))


def _obs(icao24="d1", ts=1000, lat=36.2, lon=124.5, squawk="2000", source="synthetic"):
    return Observation(
        id=f"{icao24}-{ts}",
        aircraft_ref=icao24,
        ts=ts,
        lat=lat,
        lon=lon,
        squawk=squawk,
        source=source,
        source_url=f"synthetic://{icao24}/{ts}",
    )


def _track(icao24, path, start, end, has_gap):
    from ontology.model import Track

    return Track(
        id=f"track-{icao24}",
        aircraft_ref=icao24,
        start_ts=start,
        end_ts=end,
        path=path,
        has_gap=has_gap,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. military_db — 저신뢰 판정
# ══════════════════════════════════════════════════════════════════════════════
def test_military_db_callsign_prefix():
    is_mil, conf, reason = classify_military("abc123", "RCH451")
    assert is_mil and 0 < conf <= 0.65 and "RCH" in reason


def test_military_db_icao24_range():
    is_mil, conf, reason = classify_military("AE1234", "UNKNOWN")
    assert is_mil and conf <= 0.65 and "대역" in reason


def test_military_db_none():
    assert classify_military("71c101", "KAL123") == (False, 0.0, "")


# ══════════════════════════════════════════════════════════════════════════════
# 2. ADS-B dropout — 교차 로직(단정 금지)
# ══════════════════════════════════════════════════════════════════════════════
def _dropout_setup(icao24="d1", lat=36.2, lon=124.5):
    """민감구역(OpArea) 내 gap 있는 트랙 + 마지막 관측."""
    last = _obs(icao24, ts=1000, lat=lat, lon=lon)
    tr = _track(icao24, [[lat, lon], [lat + 0.01, lon + 0.01]], 400, 1000, has_gap=True)
    return tr, last


def test_dropout_unconfirmed_low_confidence():
    tr, last = _dropout_setup()
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=NullCrossCheckSource()
    )
    assert len(drafts) == 1
    d = drafts[0]
    assert d.type == ANOMALY_TYPE_ADSB_DROPOUT
    assert d.confidence == DROPOUT_UNCONFIRMED_CONFIDENCE  # 0.4대
    # 단정 금지 문구가 설명에 명시된다(CLAUDE.md 기술기준).
    assert "단정하지 않습니다" in explain_draft(d)


def test_dropout_confirmed_higher_confidence():
    tr, last = _dropout_setup()
    mirror = SyntheticMirrorSource(absent={"d1"})
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=mirror
    )
    assert len(drafts) == 1
    assert drafts[0].confidence == DROPOUT_CONFIRMED_CONFIDENCE  # 0.7대
    assert DROPOUT_CONFIRMED_CONFIDENCE > DROPOUT_UNCONFIRMED_CONFIDENCE


def test_dropout_present_mirror_no_anomaly():
    # 2차 소스가 여전히 관측(False) → 센서 아티팩트, dropout 단정 안 함(생성 0).
    tr, last = _dropout_setup()
    mirror = SyntheticMirrorSource(present={"d1"})
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=mirror
    )
    assert drafts == []


def test_dropout_negative_fresh_transmitting():
    # 핵심 오탐 교정: 과거 gap 이력(has_gap=True)이 있어도 **지금 관측이 신선하면** dropout 아님.
    # 정상 송신 중(매 사이클 새 관측) 기체가 발화하던 폭주를 차단한다.
    last = _obs("d1", ts=1980, lat=36.2, lon=124.5)  # now−last = 20s < 침묵 임계(90)
    tr = _track("d1", [[36.2, 124.5]], 400, 1980, has_gap=True)
    assert (
        detect_adsb_dropout([tr], {"d1": last}, SENSITIVE, 2000, NullCrossCheckSource())
        == []
    )


def test_dropout_negative_stale_silence():
    # 활성 창(30분)보다 오래 전 침묵 → 지난 일, 발화 안 함(콜드스타트 stale DB 폭주 방어).
    stale_last = _obs("d1", ts=1000, lat=36.2, lon=124.5)
    now = 1000 + DROPOUT_ACTIVE_WINDOW_SECONDS + 60
    tr = _track("d1", [[36.2, 124.5]], 400, 1000, has_gap=True)
    assert (
        detect_adsb_dropout(
            [tr], {"d1": stale_last}, SENSITIVE, now, NullCrossCheckSource()
        )
        == []
    )


def test_dropout_return_then_resilence_new_event():
    # 침묵 → 복귀(새 관측) → 재침묵은 **새 이벤트**(dedup 앵커 = 침묵 시작 ts).
    tr = _track("d1", [[36.2, 124.5]], 400, 1000, has_gap=True)
    # 침묵 1: 마지막 관측 ts=1000, 두 번 스캔해도 같은 이벤트(1개 id).
    d1a = detect_adsb_dropout(
        [tr], {"d1": _obs("d1", ts=1000)}, SENSITIVE, 1300, NullCrossCheckSource()
    )
    d1b = detect_adsb_dropout(
        [tr], {"d1": _obs("d1", ts=1000)}, SENSITIVE, 1400, NullCrossCheckSource()
    )
    assert len(d1a) == 1 and len(d1b) == 1
    assert d1a[0].anomaly_id == d1b[0].anomaly_id  # 같은 침묵 = 1회
    # 복귀 후 재침묵: 마지막 관측 ts=2000 → 다른 id(정당한 새 이벤트).
    d2 = detect_adsb_dropout(
        [tr], {"d1": _obs("d1", ts=2000)}, SENSITIVE, 2300, NullCrossCheckSource()
    )
    assert len(d2) == 1
    assert d2[0].anomaly_id != d1a[0].anomaly_id


def test_dropout_poll_interval_scales_threshold(monkeypatch):
    # 폴 간격 인지: SKAI_POLL_INTERVAL=60이면 침묵 임계=180s. base(90)는 넘지만 180 미만인
    # 150s 침묵은 발화하지 않아야 한다(느린 폴에서의 정상 지연을 dropout으로 오판 방지).
    last = _obs("d1", ts=1000, lat=36.2, lon=124.5)
    tr = _track("d1", [[36.2, 124.5]], 400, 1000, has_gap=True)
    monkeypatch.setenv("SKAI_POLL_INTERVAL", "60")
    assert (
        detect_adsb_dropout([tr], {"d1": last}, SENSITIVE, 1150, NullCrossCheckSource())
        == []  # 침묵 150s < 180s(=3×60)
    )
    # 같은 폴 간격에서 침묵 200s(>180)는 정상 발화.
    assert (
        len(
            detect_adsb_dropout(
                [tr], {"d1": last}, SENSITIVE, 1200, NullCrossCheckSource()
            )
        )
        == 1
    )


def test_dropout_landing_suppressed_on_ground():
    # 지상 접촉(on_ground) 마지막 관측 → 착륙·택싱, dropout 아님(전면 억제).
    last = _obs("d1", ts=1000, lat=36.2, lon=124.5)
    last.on_ground = True
    tr = _track("d1", [[36.2, 124.5]], 400, 1000, has_gap=True)
    assert (
        detect_adsb_dropout([tr], {"d1": last}, SENSITIVE, 2000, NullCrossCheckSource())
        == []
    )


def test_dropout_low_altitude_downgrades_confidence():
    # 저고도(<1000m) 침묵 → 착륙 추정 → 신뢰도 상한을 저신뢰로 하향(단정 금지 강화).
    last = _obs("d1", ts=1000, lat=36.2, lon=124.5)
    last.alt = 500.0
    tr = _track("d1", [[36.2, 124.5]], 400, 1000, has_gap=True)
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, 2000, SyntheticMirrorSource(absent={"d1"})
    )
    assert len(drafts) == 1
    # 교차 확인(0.72)이어도 저고도면 착륙 상한(0.3) 이하로.
    assert drafts[0].confidence <= DROPOUT_LANDING_CONFIDENCE
    assert drafts[0].signal["likely_landing"] is True


def test_dropout_negative_outside_sensitive_region():
    # 마지막 위치가 민감구역 밖 → dropout 후보 아님.
    last = _obs("d1", ts=1000, lat=20.0, lon=100.0)
    tr = _track("d1", [[20.0, 100.0]], 400, 1000, has_gap=True)
    assert (
        detect_adsb_dropout([tr], {"d1": last}, SENSITIVE, 2000, NullCrossCheckSource())
        == []
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. 로이터링
# ══════════════════════════════════════════════════════════════════════════════
def _circle_path(clat, clon, r_deg, n):
    return [
        [
            clat + r_deg * math.sin(2 * math.pi * i / (n - 1)),
            clon + r_deg * math.cos(2 * math.pi * i / (n - 1)),
        ]
        for i in range(n)
    ]


def test_loitering_positive_circle():
    path = _circle_path(36.0, 127.0, 0.15, 13)
    tr = _track("l1", path, start=0, end=720, has_gap=False)  # 12분
    last = _obs("l1", ts=720, lat=36.0, lon=127.15)
    drafts = detect_loitering([tr], {"l1": last}, now=800)
    assert len(drafts) == 1 and drafts[0].type == ANOMALY_TYPE_LOITERING


def test_loitering_negative_straight():
    path = [[36.0 + i * 0.3, 127.0 + i * 0.3] for i in range(8)]  # 직선(고 변위비)
    tr = _track("l2", path, start=0, end=720, has_gap=False)
    last = _obs("l2", ts=720, lat=38.1, lon=129.1)
    assert detect_loitering([tr], {"l2": last}, now=800) == []


def test_loitering_negative_short_duration():
    path = _circle_path(36.0, 127.0, 0.15, 13)
    tr = _track("l3", path, start=0, end=300, has_gap=False)  # 5분 < 임계 10분
    last = _obs("l3", ts=300, lat=36.0, lon=127.15)
    assert detect_loitering([tr], {"l3": last}, now=400) == []


# ══════════════════════════════════════════════════════════════════════════════
# 4. 군용기 접근
# ══════════════════════════════════════════════════════════════════════════════
def test_military_positive_flag_in_oparea():
    o = _obs("m1", ts=1000, lat=36.3, lon=124.5)  # OpArea 내
    ac_map = {"m1": Aircraft(icao24="m1", callsign="FALCON", is_military=True)}
    drafts = detect_military_approach([o], ac_map, [OPAREA_WEST_REGION], now=1000)
    assert len(drafts) == 1 and drafts[0].type == ANOMALY_TYPE_MILITARY_APPROACH


def test_military_positive_callsign_heuristic():
    o = _obs("m2", ts=1000, lat=36.3, lon=124.5)
    ac_map = {"m2": Aircraft(icao24="m2", callsign="RCH88")}
    drafts = detect_military_approach([o], ac_map, [OPAREA_WEST_REGION], now=1000)
    assert len(drafts) == 1 and drafts[0].confidence <= 0.65  # 저신뢰 휴리스틱


def test_military_negative_outside_oparea():
    o = _obs("m3", ts=1000, lat=38.5, lon=130.0)  # KADIZ 내지만 OpArea 밖
    ac_map = {"m3": Aircraft(icao24="m3", callsign="RCH88", is_military=True)}
    assert detect_military_approach([o], ac_map, [OPAREA_WEST_REGION], now=1000) == []


def test_military_negative_not_military():
    o = _obs("m4", ts=1000, lat=36.3, lon=124.5)  # OpArea 내지만 민간
    ac_map = {"m4": Aircraft(icao24="71c101", callsign="KAL123")}
    assert detect_military_approach([o], ac_map, [OPAREA_WEST_REGION], now=1000) == []


def test_scan_all_military_incursion_end_to_end(tmp_path):
    # 합성 군용 다중 접근 시나리오: 3대 → 군용 접근 3건만(다른 유형 오탐 없음),
    # 전부 OpArea 내·저신뢰(≤0.65)·합성 표식. 탐지 3신호(명시 플래그·콜사인·icao24 대역)를
    # 각 1대가 대표하는지 확인.
    from anomaly.actions import scan_and_create_all
    from scripts.scenarios import apply_scenario, scenario_by_id

    store = _store(tmp_path)
    now = 1783000000
    sc = scenario_by_id("military_incursion")
    mirror = apply_scenario(store, sc, now)
    created = scan_and_create_all(store, now=now, crosscheck=mirror)
    assert set(created.keys()) == {ANOMALY_TYPE_MILITARY_APPROACH}  # 군용 접근만
    mils = [
        a for a in store.query_anomalies() if a.type == ANOMALY_TYPE_MILITARY_APPROACH
    ]
    assert len(mils) == 3
    for a in mils:
        assert a.confidence <= 0.65  # 저신뢰(단정 금지)
        assert a.attrs.get("region") == OPAREA_WEST_REGION.name  # OpArea 내
        assert a.attrs.get("is_synthetic") is True  # 합성 표식(실항적 오도 금지)
        assert len(store.query_evidence_ids(a.id)) >= 1  # 근거 관측 영속
    # 탐지 3신호가 각각 하나씩 표현됐다(mil_reason 문자열).
    reasons = " ".join(a.attrs.get("mil_reason", "") for a in mils)
    assert "플래그" in reasons  # A) 명시 is_military 플래그
    assert "프리픽스" in reasons  # B) 군 콜사인 프리픽스(ROKAF)
    assert "대역" in reasons  # C) 군용 예약 icao24 대역


# ══════════════════════════════════════════════════════════════════════════════
# 5. 위성 근접
# ══════════════════════════════════════════════════════════════════════════════
def _pass(norad="90001", region="KADIZ", start=900, end=1100, elev=84.0):
    return OrbitPass(
        id=f"pass-{norad}-{start}",
        satellite_ref=norad,
        region_ref=region,
        start_ts=start,
        end_ts=end,
        max_elevation=elev,
        ground_track=[[35.9, 126.9], [36.0, 127.0], [36.1, 127.1]],
        source="synthetic",
    )


def test_satellite_positive():
    p = _pass(elev=84.0, start=900, end=1100)
    drafts = detect_satellite_proximity(
        [p], {"KADIZ": KADIZ_REGION}, now=1000, satellite_map={}
    )
    assert len(drafts) == 1 and drafts[0].type == ANOMALY_TYPE_SATELLITE_PROXIMITY


def test_satellite_negative_low_elevation():
    p = _pass(elev=50.0)  # 임계(70) 미만 = 스치는 통과
    assert (
        detect_satellite_proximity(
            [p], {"KADIZ": KADIZ_REGION}, now=1000, satellite_map={}
        )
        == []
    )


def test_satellite_negative_outside_window():
    p = _pass(start=100000, end=100200)  # now±창 밖
    assert (
        detect_satellite_proximity(
            [p], {"KADIZ": KADIZ_REGION}, now=1000, satellite_map={}
        )
        == []
    )


def test_satellite_negative_nonsensitive_region():
    civ = Region(id="CIV", name="민간", classification="civil", geo=KADIZ_REGION.geo)
    p = _pass(region="CIV", elev=84.0)
    assert (
        detect_satellite_proximity([p], {"CIV": civ}, now=1000, satellite_map={}) == []
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. correlated_with 링크 영속
# ══════════════════════════════════════════════════════════════════════════════
def _seed_anomaly(store, aid, ts, lat, lon, atype="adsb_dropout"):
    a = Anomaly(id=aid, type=atype, ts=ts, confidence=0.5, lat=lat, lon=lon)
    store.write_anomaly(a, evidence=[("Observation", f"obs-{aid}")])
    return a


def test_correlated_with_persisted(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_anomaly(store, "anomaly-adsb_dropout-x-1", T, 36.0, 127.0)
    # 겹치는 위성 통과 + KADIZ 언급 뉴스
    store.write_orbitpass(_pass(start=T + 100, end=T + 200))
    from ontology.model import NewsEvent

    store.write_newsevent(
        NewsEvent(
            id="news-1",
            source="synthetic",
            source_url="synthetic://n",
            ts=T - 500,
            title="KADIZ",
            confidence=0.3,
            entities=["KADIZ"],
        ),
        mentions=[("Region", "KADIZ")],
    )
    correlate(store, [a], now=T)
    corr = store.query_correlations(a.id)
    types = {c["dst_type"] for c in corr}
    assert "OrbitPass" in types and "NewsEvent" in types
    assert len(store.query_all_correlations()) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# 7. 시공간 버킷 경계
# ══════════════════════════════════════════════════════════════════════════════
def test_bucket_boundary_orbitpass(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 100000
    a = _seed_anomaly(store, "anomaly-adsb_dropout-b-1", T, 36.0, 127.0)
    W = CORRELATION_WINDOW_SECONDS
    # 창 안(겹침): 통과가 T+W-50 에 시작 → 겹침
    store.write_orbitpass(_pass(norad="IN", start=T + W - 50, end=T + W - 10))
    # 창 밖: 통과가 T+W+50 에 시작 → 안 겹침
    store.write_orbitpass(_pass(norad="OUT", start=T + W + 50, end=T + W + 90))
    correlate(store, [a], now=T)
    corr_passes = {
        c["dst_id"]
        for c in store.query_correlations(a.id)
        if c["dst_type"] == "OrbitPass"
    }
    assert any("IN" in pid for pid in corr_passes)
    assert not any("OUT" in pid for pid in corr_passes)


def test_bucket_boundary_anomaly_temporal_and_spatial(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 100000
    W = CORRELATION_WINDOW_SECONDS
    a = _seed_anomaly(store, "anomaly-adsb_dropout-a-1", T, 36.0, 127.0)
    # 시간 안 + 공간 근접 → 상관
    b_ok = _seed_anomaly(
        store, "anomaly-loitering-b-1", T + W - 60, 36.1, 127.1, "loitering"
    )
    # 시간 밖 → 비상관
    _seed_anomaly(store, "anomaly-loitering-c-1", T + W + 60, 36.1, 127.1, "loitering")
    # 공간 밖(멀리) → 비상관 (haversine > 임계)
    far_lon = 127.0 + (SPATIAL_CORRELATION_KM / 90.0) + 2  # 위경도상 충분히 멀리
    _seed_anomaly(store, "anomaly-loitering-d-1", T + 100, 33.0, far_lon, "loitering")
    correlate(store, [a], now=T)
    corr_anoms = {
        c["dst_id"]
        for c in store.query_correlations(a.id)
        if c["dst_type"] == "Anomaly"
    }
    assert b_ok.id in corr_anoms
    assert "anomaly-loitering-c-1" not in corr_anoms  # 시간 경계 밖
    assert "anomaly-loitering-d-1" not in corr_anoms  # 공간 경계 밖


# ══════════════════════════════════════════════════════════════════════════════
# 8. scan_and_create_all 통합 (전 유형 + 상관 영속)
# ══════════════════════════════════════════════════════════════════════════════
def test_scan_all_narrative_end_to_end(tmp_path):
    from anomaly.actions import scan_and_create_all
    from scripts.scenarios import apply_scenario, scenario_by_id

    store = _store(tmp_path)
    now = 1783000000
    sc = scenario_by_id("narrative_hidden")
    mirror = apply_scenario(store, sc, now)
    created = scan_and_create_all(store, now=now, crosscheck=mirror)
    # dropout + 위성 근접 둘 다 생성
    assert ANOMALY_TYPE_ADSB_DROPOUT in created
    assert ANOMALY_TYPE_SATELLITE_PROXIMITY in created
    # dropout 이상징후가 OrbitPass·NewsEvent와 correlated_with (은닉 정황)
    dropout = [
        a for a in store.query_anomalies() if a.type == ANOMALY_TYPE_ADSB_DROPOUT
    ][0]
    types = {c["dst_type"] for c in store.query_correlations(dropout.id)}
    assert "OrbitPass" in types and "NewsEvent" in types


def test_scan_all_normal_traffic_no_anomaly(tmp_path):
    from anomaly.actions import scan_and_create_all
    from scripts.scenarios import apply_scenario, scenario_by_id

    store = _store(tmp_path)
    now = 1783000000
    sc = scenario_by_id("normal_transit_a")
    mirror = apply_scenario(store, sc, now)
    created = scan_and_create_all(store, now=now, crosscheck=mirror)
    assert created == {}  # 정상 트래픽 → 이상징후 0


# ══════════════════════════════════════════════════════════════════════════════
# 9. 급기동(rapid maneuver) — 고도·속도 급변, 근거 = Observation 시퀀스
# ══════════════════════════════════════════════════════════════════════════════
def _maneuver_seq(
    icao24="r1",
    *,
    n=8,
    step=30,
    alt0=3000.0,
    dalt=1400.0,  # 스텝당 고도 변화(m). 1400/30 ≈ 46.7 m/s ≈ 9186 ft/min (임계 초과)
    vel0=220.0,
    dvel=0.0,
    lat=36.0,
    lon=127.0,
    dlat=0.01,
    dlon=0.01,
    start_ts=1000,
):
    """급기동 판정용 관측 시퀀스(균등 간격 직선 + 스텝당 alt/velocity 변화)."""
    obs = []
    for i in range(n):
        obs.append(
            Observation(
                id=f"{icao24}-{start_ts + i * step}",
                aircraft_ref=icao24,
                ts=start_ts + i * step,
                lat=lat + i * dlat,
                lon=lon + i * dlon,
                alt=alt0 + i * dalt,
                velocity=vel0 + i * dvel,
                squawk="2000",
                source="synthetic",
                source_url=f"synthetic://{icao24}/{i}",
            )
        )
    return obs


def _mtrack(obs):
    return _track(
        obs[0].aircraft_ref,
        [[o.lat, o.lon] for o in obs],
        obs[0].ts,
        obs[-1].ts,
        has_gap=False,
    )


def test_rapid_maneuver_positive_climb():
    obs = _maneuver_seq("r1", dalt=1400.0)  # 46.7 m/s > 30.5 임계
    drafts = detect_rapid_maneuver([_mtrack(obs)], {"r1": obs}, now=obs[-1].ts + 10)
    assert len(drafts) == 1
    d = drafts[0]
    assert d.type == ANOMALY_TYPE_RAPID_MANEUVER
    assert len(d.evidence) >= 2  # 근거 = Observation 시퀀스(≥2)
    assert all(t == "Observation" for t, _ in d.evidence)
    assert d.signal["kind"] == "vertical"
    assert MANEUVER_CONFIDENCE_BASE <= d.confidence <= 0.65  # 정황(단정 아님)


def test_rapid_maneuver_positive_speed():
    # 고도는 정상, 속도만 급변(가속 5 m/s² > 3 임계) → kind=speed.
    obs = _maneuver_seq("rs", dalt=0.0, vel0=180.0, dvel=150.0)  # 150/30=5 m/s²
    drafts = detect_rapid_maneuver([_mtrack(obs)], {"rs": obs}, now=obs[-1].ts + 10)
    assert len(drafts) == 1 and drafts[0].signal["kind"] == "speed"


def test_rapid_maneuver_negative_normal_climb():
    # 민항 정상 상승률(13.3 m/s ≈ 2625 ft/min) < 임계 → 후보 아님.
    obs = _maneuver_seq("r2", dalt=400.0)
    assert detect_rapid_maneuver([_mtrack(obs)], {"r2": obs}, now=obs[-1].ts + 10) == []


def test_rapid_maneuver_negative_too_few_obs():
    # 최소 관측 수(4) 미만 → 판정 유보(노이즈 방어).
    obs = _maneuver_seq("r3", n=3, dalt=1400.0)
    assert detect_rapid_maneuver([_mtrack(obs)], {"r3": obs}, now=obs[-1].ts + 10) == []


def test_rapid_maneuver_negative_single_interval():
    # 급변이 단일 구간뿐(연속 2 미만) → 후보 아님(단일점 글리치 방어).
    # 정상 순항 4관측 사이에 1구간만 큰 고도차 → 런 길이 1 < MANEUVER_MIN_RUN.
    obs = _maneuver_seq("r4", n=5, dalt=0.0)  # 전부 동일 고도
    obs[3].alt = (
        obs[2].alt + 2000.0
    )  # 구간 2→3 한 곳만 급변(다음 구간 3→4는 -2000=복귀)
    obs[4].alt = obs[2].alt  # 3→4 복귀(부호 반대) → 같은방향 연속 아님
    assert detect_rapid_maneuver([_mtrack(obs)], {"r4": obs}, now=obs[-1].ts + 10) == []


def test_rapid_maneuver_excludes_baro_spike():
    # 비물리적 고도 스파이크(기압고도 글리치, >150 m/s) → 해당 구간 무효 → 후보 아님.
    obs = _maneuver_seq("rb", n=6, dalt=0.0)  # 정상 순항
    obs[3].alt = obs[2].alt + 8000.0  # 한 관측만 +8000m 스파이크(글리치)
    # 구간 2→3(+8000/30≈267 m/s)·3→4(-8000/30) 모두 비물리적 → 무효 → 런 없음.
    assert detect_rapid_maneuver([_mtrack(obs)], {"rb": obs}, now=obs[-1].ts + 10) == []


def test_rapid_maneuver_excludes_gps_jump():
    # GPS 튐(비물리적 위치 점프) — 고도값은 급상승처럼 보이나 구간의 지상속도가 비물리적이라
    # 구간이 무효화된다 → 후보 아님(위치 글리치를 급기동으로 오탐하지 않음).
    obs = _maneuver_seq("rg", n=5, dalt=1400.0, dlat=15.0, dlon=0.0, lat=10.0)
    # 스텝당 15° 위도 점프(≈1665km/30s ≫ 600 m/s) → 전 구간 무효.
    assert detect_rapid_maneuver([_mtrack(obs)], {"rg": obs}, now=obs[-1].ts + 10) == []


def test_rapid_maneuver_dedup_and_evidence(tmp_path):
    # dedup 자연키: 같은 기체·유형·시간창 → Anomaly 1건. evidence ≥ 2 영속.
    from anomaly.actions import create_from_draft

    store = _store(tmp_path)
    obs = _maneuver_seq("r5", dalt=1400.0)
    store.write_aircraft(Aircraft(icao24="r5", callsign="ZOOM"))
    for o in obs:
        store.write_observation(o)  # provenance 통과(synthetic + url)
    drafts = detect_rapid_maneuver([_mtrack(obs)], {"r5": obs}, now=obs[-1].ts + 10)
    d = drafts[0]
    a1 = create_from_draft(store, d)
    a2 = create_from_draft(store, d)  # 재호출 = dedup
    assert a1.id == a2.id
    maneuvers = [
        a for a in store.query_anomalies() if a.type == ANOMALY_TYPE_RAPID_MANEUVER
    ]
    assert len(maneuvers) == 1
    assert len(store.query_evidence_ids(a1.id)) >= 2  # Observation 시퀀스 근거


def test_scan_all_rapid_climb_end_to_end(tmp_path):
    from anomaly.actions import scan_and_create_all
    from scripts.scenarios import apply_scenario, scenario_by_id

    store = _store(tmp_path)
    now = 1783000000
    sc = scenario_by_id("rapid_climb")
    mirror = apply_scenario(store, sc, now)
    created = scan_and_create_all(store, now=now, crosscheck=mirror)
    # 급기동만 트리거(다른 유형 오탐 없음).
    assert set(created.keys()) == {ANOMALY_TYPE_RAPID_MANEUVER}
    a = [x for x in store.query_anomalies() if x.type == ANOMALY_TYPE_RAPID_MANEUVER][0]
    assert len(store.query_evidence_ids(a.id)) >= 2  # 주입→탐지→근거≥2


def test_scan_all_normal_climb_no_anomaly(tmp_path):
    from anomaly.actions import scan_and_create_all
    from scripts.scenarios import apply_scenario, scenario_by_id

    store = _store(tmp_path)
    now = 1783000000
    sc = scenario_by_id("normal_climb")
    mirror = apply_scenario(store, sc, now)
    created = scan_and_create_all(store, now=now, crosscheck=mirror)
    assert created == {}  # 정상 상승 → 이상징후 0
