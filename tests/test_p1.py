"""P1 검증 테스트 — provenance 거부 · Track gap 판정 · Event→객체 매핑.

실행: .venv/bin/python -m pytest tests/test_p1.py -v
"""

from __future__ import annotations

import pytest

from ontology import mapping
from ontology.custody import build_track
from ontology.model import Observation
from ontology.store import ProvenanceError, validate_provenance
from ontology.store_local import LocalOntologyStore


# ──────────────────────────────────────────────
# 1. provenance 강제 (환각방지 백본의 선행 구현)
# ──────────────────────────────────────────────
def _obs(**kw) -> Observation:
    base = dict(
        id="abc123-1783091489",
        aircraft_ref="abc123",
        ts=1783091489,
        lat=36.5,
        lon=127.0,
        source="opensky",
        source_url="https://opensky-network.org/api/states/all?lamin=32",
    )
    base.update(kw)
    return Observation(**base)


def test_provenance_ok():
    validate_provenance(_obs())  # 예외 없어야 함


def test_provenance_missing_source():
    with pytest.raises(ProvenanceError):
        validate_provenance(_obs(source=""))


def test_provenance_missing_source_url():
    with pytest.raises(ProvenanceError):
        validate_provenance(_obs(source_url=""))


def test_provenance_missing_ts():
    with pytest.raises(ProvenanceError):
        validate_provenance(_obs(ts=0))


def test_store_rejects_write_without_provenance(tmp_path):
    store = LocalOntologyStore(str(tmp_path / "t.db"))
    with pytest.raises(ProvenanceError):
        store.write_observation(_obs(source=""))
    assert store.counts()["observation"] == 0  # 거부된 관측은 저장 안 됨


def test_store_accepts_valid_observation(tmp_path):
    store = LocalOntologyStore(str(tmp_path / "t.db"))
    store.write_observation(_obs())
    assert store.counts()["observation"] == 1


# ──────────────────────────────────────────────
# 2. Track custody — gap 판정 (>90초)
# ──────────────────────────────────────────────
def _obs_seq(icao24: str, times: list[int]) -> list[Observation]:
    return [
        _obs(
            id=f"{icao24}-{t}",
            aircraft_ref=icao24,
            ts=t,
            lat=36 + i * 0.01,
            lon=127 + i * 0.01,
        )
        for i, t in enumerate(times)
    ]


def test_track_no_gap():
    # 30초 간격 연속 → gap 없음
    obs = _obs_seq("aaa", [1000, 1030, 1060, 1090])
    track = build_track("aaa", obs)
    assert track.has_gap is False
    assert track.start_ts == 1000 and track.end_ts == 1090
    assert len(track.path) == 4


def test_track_with_gap():
    # 120초 간격(>90) 포함 → gap 있음
    obs = _obs_seq("bbb", [1000, 1030, 1150])  # 1030→1150 = 120초
    track = build_track("bbb", obs)
    assert track.has_gap is True


def test_track_boundary_exactly_90():
    # 정확히 90초는 gap 아님(> 임계값만 gap)
    obs = _obs_seq("ccc", [1000, 1090])
    assert build_track("ccc", obs).has_gap is False


def test_track_unsorted_input():
    # 입력 순서 무관 — 정렬 후 판정
    obs = _obs_seq("ddd", [1150, 1000, 1030])
    track = build_track("ddd", obs)
    assert track.start_ts == 1000 and track.end_ts == 1150
    assert track.has_gap is True


def test_track_gap_threshold_poll_interval_aware(monkeypatch):
    # 폴 간격 인지: 같은 120초 간격이 폴 간격에 따라 gap 여부가 달라진다.
    obs = _obs_seq("eee", [1000, 1120])  # 120초 간격
    assert (
        build_track("eee", obs).has_gap is True
    )  # env 미설정 → 임계 90 → 120 > 90 → gap
    monkeypatch.setenv("SKAI_POLL_INTERVAL", "60")
    assert build_track("eee", obs).has_gap is False  # 임계 180(=3×60) → 120 < 180
    obs2 = _obs_seq("eee", [1000, 1200])  # 200초 > 180 → gap
    assert build_track("eee", obs2).has_gap is True


# ──────────────────────────────────────────────
# 3. Event → 온톨로지 객체 매핑 (P0A gotcha 포함)
# ──────────────────────────────────────────────
# P0A-sources.md §1 실측 인덱스 기반 상태벡터 (callsign 공백 패딩 · squawk str)
SAMPLE_STATE = [
    "84d283",  # 0 icao24
    "JJP11   ",  # 1 callsign (공백 패딩 — strip 필요)
    "Japan",  # 2 origin_country
    1783091480,  # 3 time_position
    1783091489,  # 4 last_contact → ts
    131.3442,  # 5 longitude
    32.1258,  # 6 latitude
    10363.0,  # 7 baro_altitude
    False,  # 8 on_ground
    220.5,  # 9 velocity
    145.0,  # 10 true_track (heading)
    0.0,  # 11 vertical_rate
    None,  # 12 sensors
    10400.0,  # 13 geo_altitude
    "3647",  # 14 squawk (str!)
    False,  # 15 spi
    0,  # 16 position_source
]
SRC_URL = (
    "https://opensky-network.org/api/states/all?lamin=32&lomin=122&lamax=39&lomax=132"
)


def test_state_to_event_basic():
    ev = mapping.opensky_state_to_event(SAMPLE_STATE, SRC_URL, fetched_at=1783091500)
    assert ev is not None
    assert ev.kind == "aircraft"
    assert ev.ts == 1783091489  # last_contact
    assert ev.lat == 32.1258 and ev.lon == 131.3442
    assert ev.source == "opensky" and ev.source_url == SRC_URL
    assert ev.attrs["callsign"] == "JJP11"  # gotcha 1: strip 됨
    assert ev.attrs["squawk"] == "3647"  # gotcha 2: str 유지
    assert isinstance(ev.attrs["squawk"], str)


def test_state_to_event_null_position_skipped():
    # lat/lon None → Observation 불가 → None 반환
    s = list(SAMPLE_STATE)
    s[5] = None  # longitude
    s[6] = None  # latitude
    assert mapping.opensky_state_to_event(s, SRC_URL, 1783091500) is None


def test_event_to_aircraft_and_observation():
    ev = mapping.opensky_state_to_event(SAMPLE_STATE, SRC_URL, 1783091500)
    ac = mapping.event_to_aircraft(ev)
    obs = mapping.event_to_observation(ev)

    assert ac.icao24 == "84d283" and ac.callsign == "JJP11"
    assert obs.id == "84d283-1783091489"  # (icao24, ts) 자연키
    assert obs.aircraft_ref == "84d283"
    assert obs.squawk == "3647"
    assert obs.heading == 145.0 and obs.velocity == 220.5
    # 매핑된 Observation은 provenance 완비 → 거부되지 않아야 함
    validate_provenance(obs)


def test_full_pipeline_write_and_track(tmp_path):
    # state → event → 객체 → store write → custody 재구성 왕복
    from ontology.custody import rebuild_tracks

    store = LocalOntologyStore(str(tmp_path / "pipe.db"))
    for ts in (1783091489, 1783091519, 1783091549):  # 30초 간격 3건
        s = list(SAMPLE_STATE)
        s[4] = ts
        ev = mapping.opensky_state_to_event(s, SRC_URL, ts + 1)
        store.write_aircraft(mapping.event_to_aircraft(ev))
        store.write_observation(mapping.event_to_observation(ev))
        store.link("Aircraft", "84d283", "observed_as", "Observation", f"84d283-{ts}")
    n = rebuild_tracks(store)

    c = store.counts()
    assert c["aircraft"] == 1
    assert c["observation"] == 3
    assert c["track"] == 1 and n == 1
    tracks = store.query_tracks()
    assert tracks[0].has_gap is False and len(tracks[0].path) == 3
