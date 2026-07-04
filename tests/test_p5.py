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
    ANOMALY_TYPE_SATELLITE_PROXIMITY,
    DROPOUT_CONFIRMED_CONFIDENCE,
    DROPOUT_UNCONFIRMED_CONFIDENCE,
    detect_adsb_dropout,
    detect_loitering,
    detect_military_approach,
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


def test_dropout_negative_no_gap():
    tr, last = _dropout_setup()
    tr.has_gap = False
    assert (
        detect_adsb_dropout([tr], {"d1": last}, SENSITIVE, 2000, NullCrossCheckSource())
        == []
    )


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
