"""P3 검증 테스트 — 융합 확장(위성·기상·뉴스) 온톨로지 통합.

커버:
  1. OrbitPass 통과창 — GMST 보정 subpoint · 통과 그룹핑 · 실제 sgp4 통합.
  2. METAR → WeatherState 매핑(실링 ft · 시정 sm · 가변풍 VRB).
  3. GDELT 응답 파싱(articles null · OR 괄호 문법 · 지역 별칭 링킹).
  4. 뉴스 confidence 상한(≤ 0.4).
  5. store 왕복 + provenance 강제(뉴스·기상도 증거 객체).

실행: .venv/bin/python -m pytest tests/test_p3.py -v
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from connectors import celestrak as C
from connectors import gdelt as G
from connectors import metar as M
from ontology.model import (
    KADIZ_REGION,
    NEWS_MAX_CONFIDENCE,
    NewsEvent,
    OrbitPass,
    Satellite,
    WeatherState,
)
from ontology.store import ProvenanceError
from ontology.store_local import LocalOntologyStore

# 알려진 ISS TLE (P0A epoch — 결정적 검증용)
ISS_L1 = "1 25544U 98067A   26182.50817465  .00006185  00000+0  11827-3 0  9996"
ISS_L2 = "2 25544  51.6311 229.1989 0004224 255.0896 104.9625 15.49503254573972"


# ──────────────────────────────────────────────
# 1. OrbitPass — GMST 보정 (P0A gotcha 3)
# ──────────────────────────────────────────────
def test_gmst_correction_shifts_longitude():
    """eci_to_subpoint은 경도에서 GMST를 뺀다(자전 보정). +x축 벡터로 검증."""
    # r이 ECI +x축, GMST=90° → 경도 = atan2(0,7000) - 90 = -90°. 위도 = 0.
    lat, lon, alt = C.eci_to_subpoint((7000.0, 0.0, 0.0), gmst_degrees=90.0)
    assert abs(lat - 0.0) < 1e-6
    assert abs(lon - (-90.0)) < 1e-6  # 보정 없으면 0°가 나옴 → 보정 적용 증명
    assert abs(alt - (7000.0 - C.EARTH_RADIUS_KM)) < 1e-6


def test_gmst_correction_matches_p0a_iss_subpoint():
    """ISS를 P0A 시각에 전파 → subpoint가 P0A 기준값(lat -14.4, lon 57.3, alt 427)과 일치."""
    from sgp4.api import Satrec, jday

    sat = Satrec.twoline2rv(ISS_L1, ISS_L2)
    dt = datetime(2026, 7, 3, 15, 11, 43, tzinfo=timezone.utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    err, r, _v = sat.sgp4(jd, fr)
    assert err == 0
    lat, lon, alt = C.eci_to_subpoint(r, C.gmst_deg(jd + fr))
    assert -16 < lat < -13  # P0A: -14.4°
    assert 56 < lon < 59  # P0A: 57.3° (GMST 보정 없으면 -153° 근처)
    assert 400 < alt < 450  # P0A: 427 km


def test_subpoint_ranges_sane():
    """다양한 시각에서 subpoint 위경도가 물리적 범위 안."""
    from sgp4.api import Satrec, jday

    sat = Satrec.twoline2rv(ISS_L1, ISS_L2)
    for hour in range(0, 24, 3):
        dt = datetime(2026, 7, 1, hour, 0, 0, tzinfo=timezone.utc)
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        err, r, _v = sat.sgp4(jd, fr)
        assert err == 0
        lat, lon, _alt = C.eci_to_subpoint(r, C.gmst_deg(jd + fr))
        assert -90 <= lat <= 90
        assert -180 <= lon <= 180
        # ISS 경사각 51.6° → 위도는 그 안에 머문다(궤도 물리 sanity)
        assert -52 < lat < 52


# ──────────────────────────────────────────────
# 1b. 통과 그룹핑 (순수 함수)
# ──────────────────────────────────────────────
def test_group_passes_single_contiguous_run():
    # 5샘플 중 가운데 3개가 bbox 안 → 통과 1건
    samples = [
        (100, 30.0, 120.0, None, False),
        (130, 33.0, 125.0, 40.0, True),
        (160, 35.0, 127.0, 70.0, True),
        (190, 37.0, 129.0, 55.0, True),
        (220, 41.0, 133.0, None, False),
    ]
    passes = C.group_passes(samples)
    assert len(passes) == 1
    p = passes[0]
    assert p["start_ts"] == 130 and p["end_ts"] == 190
    assert p["max_elev"] == 70.0  # 세 앙각 중 최대
    assert p["track"] == [[33.0, 125.0], [35.0, 127.0], [37.0, 129.0]]


def test_group_passes_two_separate_runs():
    samples = [
        (100, 35.0, 127.0, 60.0, True),  # 통과 1
        (130, 45.0, 140.0, None, False),
        (160, 34.0, 126.0, 50.0, True),  # 통과 2
        (190, 33.0, 125.0, 45.0, True),
    ]
    passes = C.group_passes(samples)
    assert len(passes) == 2
    assert passes[0]["start_ts"] == 100 and passes[0]["end_ts"] == 100
    assert passes[1]["start_ts"] == 160 and passes[1]["end_ts"] == 190


def test_group_passes_none_when_never_in_bbox():
    samples = [(t, 45.0, 140.0, None, False) for t in (100, 130, 160)]
    assert C.group_passes(samples) == []


# ──────────────────────────────────────────────
# 1c. 실제 sgp4 통합 — ISS가 지나는 위도대 bbox
# ──────────────────────────────────────────────
def test_compute_passes_real_iss_integration():
    """ISS TLE로 24h 통과 계산. lat 30~40 전 경도 bbox는 반드시 통과 → ≥1 통과·점 범위."""
    from sgp4.api import Satrec

    sat = Satrec.twoline2rv(ISS_L1, ISS_L2)
    bbox = {"lamin": 30.0, "lomin": -180.0, "lamax": 40.0, "lomax": 180.0}
    start = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    passes = C.compute_passes_for_sat(
        sat, bbox, 35.0, 0.0, start, horizon_hours=24, step_seconds=30
    )
    assert passes is not None and len(passes) >= 1
    for p in passes:
        assert p["start_ts"] <= p["end_ts"]
        assert isinstance(p["max_elev"], float)
        for lat, lon in p["track"]:  # 통과 점열은 모두 bbox 안
            assert 30.0 <= lat <= 40.0
            assert -180.0 <= lon <= 180.0


def test_norad_and_epoch_and_objtype_parse():
    assert C.norad_id_of(ISS_L1) == "25544"
    epoch = C.epoch_iso_of(ISS_L1)
    assert epoch is not None and epoch.startswith("2026-")  # YYDDD → 2026
    assert C.object_type_of("COSMOS 2251 DEB") == "DEBRIS"
    assert C.object_type_of("CZ-2D R/B") == "ROCKET BODY"
    assert C.object_type_of("ISS (ZARYA)") == "PAYLOAD"


# ──────────────────────────────────────────────
# 2. METAR → WeatherState
# ──────────────────────────────────────────────
def _metar_rec(**kw) -> dict:
    base = dict(
        icaoId="RKSI",
        obsTime=1783090800,
        temp=23,
        dewp=22,
        wdir=200,
        wspd=8,
        visib=4.35,
        altim=1010,
        rawOb="METAR RKSI 031500Z 20008KT 7000 BKN010 BKN020 23/22 Q1010 NOSIG",
        lat=37.469,
        lon=126.451,
        clouds=[{"cover": "BKN", "base": 1000}, {"cover": "BKN", "base": 2000}],
        fltCat="MVFR",
    )
    base.update(kw)
    return base


def test_metar_maps_units_and_ceiling():
    ws = M.record_to_weatherstate(_metar_rec(), "KADIZ", "http://x")
    assert ws.station == "RKSI"
    assert ws.visibility_sm == 4.35  # statute miles 보존
    assert ws.ceiling_ft == 1000  # 최저 BKN base (피트)
    assert ws.wind_dir == 200 and ws.wind_speed_kt == 8.0
    assert ws.flight_category == "MVFR"
    assert ws.region_ref == "KADIZ"
    assert "RKSI" in ws.conditions  # rawOb 보존


def test_metar_ceiling_none_when_no_bkn_ovc():
    # FEW/SCT만 있으면 실링 없음(무제한)
    ws = M.record_to_weatherstate(
        _metar_rec(clouds=[{"cover": "FEW", "base": 3000}]), "KADIZ", "http://x"
    )
    assert ws.ceiling_ft is None


def test_metar_ceiling_picks_lowest_broken_layer():
    ws = M.record_to_weatherstate(
        _metar_rec(
            clouds=[
                {"cover": "SCT", "base": 500},  # SCT은 실링 아님
                {"cover": "OVC", "base": 1500},
                {"cover": "BKN", "base": 800},
            ]
        ),
        "KADIZ",
        "http://x",
    )
    assert ws.ceiling_ft == 800  # 최저 BKN/OVC


def test_metar_variable_wind_is_none():
    ws = M.record_to_weatherstate(_metar_rec(wdir="VRB"), "KADIZ", "http://x")
    assert ws.wind_dir is None
    assert ws.attrs["wind_dir_raw"] == "VRB"  # 원값 보존


def test_metar_visibility_plus_string():
    ws = M.record_to_weatherstate(_metar_rec(visib="10+"), "KADIZ", "http://x")
    assert ws.visibility_sm == 10.0


# ──────────────────────────────────────────────
# 3. GDELT 파싱
# ──────────────────────────────────────────────
def test_gdelt_query_is_parenthesized_or():
    q = G.build_query()
    assert q.startswith("(") and q.endswith(")")
    assert " OR " in q
    assert '"KADIZ"' in q


def test_gdelt_null_articles_returns_empty():
    assert G.gdelt_response_to_news({"articles": None}, "http://x") == []
    assert G.gdelt_response_to_news({}, "http://x") == []
    assert G.gdelt_response_to_news(None, "http://x") == []


def test_gdelt_region_alias_linking():
    data = {
        "articles": [
            {
                "url": "https://ex.com/1",
                "title": "Chinese jets enter KADIZ over the West Sea",
                "seendate": "20260703T150000Z",
                "domain": "ex.com",
                "language": "English",
                "sourcecountry": "South Korea",
            }
        ]
    }
    news = G.gdelt_response_to_news(data, "http://x")
    assert len(news) == 1
    n = news[0]
    assert "KADIZ" in n.entities and "West Sea" in n.entities
    assert n.ts == int(datetime(2026, 7, 3, 15, 0, 0, tzinfo=timezone.utc).timestamp())
    assert n.attrs["domain"] == "ex.com"


def test_gdelt_no_region_match_lower_confidence():
    data = {
        "articles": [{"url": "https://ex.com/2", "title": "Unrelated weather story"}]
    }
    n = G.gdelt_response_to_news(data, "http://x")[0]
    assert n.entities == []
    assert n.confidence == 0.30  # 지역 매칭 없음 → 기본 저신뢰


def test_match_region_aliases_case_insensitive():
    assert "KADIZ" in G.match_region_aliases("tensions in the kadiz today")
    assert G.match_region_aliases("no relevant place") == []


# ──────────────────────────────────────────────
# 4. 뉴스 confidence 상한 (≤ 0.4)
# ──────────────────────────────────────────────
def test_news_confidence_never_exceeds_cap():
    # 지역 매칭 기사도 상한 이하
    data = {
        "articles": [
            {"url": f"https://ex.com/{i}", "title": "KADIZ Korea airspace incident"}
            for i in range(5)
        ]
    }
    for n in G.gdelt_response_to_news(data, "http://x"):
        assert n.confidence <= NEWS_MAX_CONFIDENCE


def test_store_clamps_news_confidence(tmp_path):
    # 0.9로 넣어도 store가 0.4로 clamp
    store = LocalOntologyStore(str(tmp_path / "p3.db"))
    n = NewsEvent(
        id="news-x",
        source="gdelt",
        source_url="https://ex.com/x",
        ts=1783090000,
        title="t",
        confidence=0.9,
    )
    store.write_newsevent(n)
    assert store.query_news()[0].confidence == NEWS_MAX_CONFIDENCE


# ──────────────────────────────────────────────
# 5. store 왕복 + provenance 강제
# ──────────────────────────────────────────────
def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "p3.db"))


def test_orbitpass_roundtrip_and_links(tmp_path):
    store = _store(tmp_path)
    store.write_satellite(
        Satellite(norad_id="25544", name="ISS (ZARYA)", source_url="http://tle")
    )
    op = OrbitPass(
        id="pass-25544-100",
        satellite_ref="25544",
        region_ref="KADIZ",
        start_ts=100,
        end_ts=220,
        max_elevation=72.3,
        ground_track=[[36.0, 127.0], [36.5, 128.0]],
        source_url="http://tle",
    )
    store.write_orbitpass(op)
    got = store.query_orbitpasses()
    assert len(got) == 1 and got[0].max_elevation == 72.3
    assert len(got[0].ground_track) == 2
    # of→Satellite / over→Region 링크가 저장됐는지
    c = store.counts()
    assert c["satellite"] == 1 and c["orbitpass"] == 1 and c["link"] >= 2


def test_weatherstate_provenance_enforced(tmp_path):
    store = _store(tmp_path)
    bad = WeatherState(
        id="wx-RKSI-1",
        region_ref="KADIZ",
        ts=1783090800,
        station="RKSI",
        source="metar",
        source_url="",  # source_url 누락
    )
    with pytest.raises(ProvenanceError):
        store.write_weatherstate(bad)
    assert store.counts()["weatherstate"] == 0  # 거부 → 저장 0건


def test_newsevent_provenance_enforced(tmp_path):
    store = _store(tmp_path)
    bad = NewsEvent(
        id="news-y", source="gdelt", source_url="", ts=1783090000, title="t"
    )
    with pytest.raises(ProvenanceError):
        store.write_newsevent(bad)
    assert store.counts()["newsevent"] == 0


def test_news_mentions_roundtrip(tmp_path):
    store = _store(tmp_path)
    n = NewsEvent(
        id="news-z",
        source="gdelt",
        source_url="https://ex.com/z",
        ts=1783090000,
        title="KADIZ incident",
        confidence=0.35,
        entities=["KADIZ"],
    )
    store.write_newsevent(n, mentions=[("Region", "KADIZ"), ("Aircraft", "abc123")])
    mentions = store.query_mentions("news-z")
    kinds = {m["type"] for m in mentions}
    assert kinds == {"Region", "Aircraft"}


def test_counts_includes_p3_tables(tmp_path):
    store = _store(tmp_path)
    c = store.counts()
    for key in ("satellite", "orbitpass", "weatherstate", "newsevent", "operator"):
        assert key in c


def test_four_source_coexistence(tmp_path):
    """4종 소스 객체가 한 store에 공존(시공간 정렬의 저장 레벨 증명)."""
    from ontology.model import Aircraft, Observation

    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    # opensky
    store.write_aircraft(Aircraft(icao24="abc123", callsign="KAL123"))
    store.write_observation(
        Observation(
            id="abc123-1783090000",
            aircraft_ref="abc123",
            ts=1783090000,
            lat=36.5,
            lon=127.0,
            source="opensky",
            source_url="http://os",
        )
    )
    # celestrak
    store.write_satellite(
        Satellite(norad_id="25544", name="ISS", source_url="http://tle")
    )
    store.write_orbitpass(
        OrbitPass(
            id="pass-25544-1",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=1783090000,
            end_ts=1783090120,
            max_elevation=70.0,
            ground_track=[[36.0, 127.0]],
            source_url="http://tle",
        )
    )
    # metar
    store.write_weatherstate(
        WeatherState(
            id="wx-RKSI-1",
            region_ref="KADIZ",
            ts=1783090000,
            station="RKSI",
            flight_category="MVFR",
            source="metar",
            source_url="http://wx",
        )
    )
    # gdelt
    store.write_newsevent(
        NewsEvent(
            id="news-1",
            source="gdelt",
            source_url="https://ex.com/1",
            ts=1783090000,
            title="KADIZ",
            confidence=0.35,
        )
    )
    c = store.counts()
    assert c["observation"] == 1 and c["orbitpass"] == 1
    assert c["weatherstate"] == 1 and c["newsevent"] == 1
