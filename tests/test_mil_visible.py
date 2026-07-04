"""군용기 지도 가시화 배선 검증 — is_military 영속 + API 노출.

배경: mil_enrich(adsb.fi dbFlags)·콜사인 휴리스틱(military_db)은 기존에
detect_military_approach(이상탐지)에만 주입돼 있었고, Aircraft.is_military는 라이브
경로에서 항상 False였다(ontology/mapping.py). 그래서 /api/observations에 군용 구분
필드가 없어 지도에서 군용기가 시각적으로 안 보였다.

커버:
  1. anomaly.military_db.resolve_is_military — 영속용 종합판정(단조: 기존 True 불변).
  2. connectors/opensky.py ingest_cycle — mil_enrich DB 플래그·콜사인 휴리스틱 히트 시
     Aircraft.is_military=True로 write(REPLACE라도 소실 안 됨) + 기존 True 보존(단조)
     + 민간기는 False 유지(회귀 없음).
  3. connectors/adsbfi_tracks.py ingest_cycle — 위와 동일 규율(adsbfi 브랜치).
  4. server/app.py — /api/observations·/api/tracks가 aircraft_map에서 is_military 노출.

실행: .venv/bin/python -m pytest tests/test_mil_visible.py -v
"""

from __future__ import annotations

import json

import httpx

from anomaly.mil_enrich import NullMilEnrichment
from anomaly.military_db import resolve_is_military
from connectors import adsbfi_tracks, opensky
from connectors.mil_enrich_live import LiveMilEnrichment
from ontology.model import Aircraft, Observation
from ontology.store_local import LocalOntologyStore
from server import app as server_app


def _client(handler) -> httpx.Client:
    """MockTransport로 네트워크 없는 httpx.Client — handler(request)->Response."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_resp(status: int, body) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


def _mil_source(military_hexes: set[str]) -> LiveMilEnrichment:
    """/v2/mil 스냅샷이 지정 hex만 dbFlags=1로 반환하는 LiveMilEnrichment(테스트용)."""

    def handler(req):
        return _json_resp(
            200,
            {
                "ac": [{"hex": h, "dbFlags": 1} for h in military_hexes],
                "msg": "No error",
                "now": 1783201522000,
                "total": len(military_hexes),
            },
        )

    return LiveMilEnrichment(client=_client(handler))


# ══════════════════════════════════════════════════════════════════════════════
# 1. resolve_is_military — 영속용 종합판정
# ══════════════════════════════════════════════════════════════════════════════
def test_resolve_existing_true_is_monotonic():
    # 기존 True(합성 주입·이전 판정)는 이번 사이클 신호와 무관하게 불변.
    assert resolve_is_military(True, "71c101", "KAL123", None) is True


def test_resolve_db_flag_hit():
    src = _mil_source({"780abc"})
    assert resolve_is_military(False, "780abc", "CCA1234", src) is True


def test_resolve_heuristic_hit_no_db_source():
    assert resolve_is_military(False, "abc123", "RCH451", None) is True


def test_resolve_none_hit_is_false():
    assert resolve_is_military(False, "71c101", "KAL123", NullMilEnrichment()) is False
    assert resolve_is_military(False, "71c101", "KAL123", None) is False


# ══════════════════════════════════════════════════════════════════════════════
# 2. connectors/opensky.py ingest_cycle — 판정 영속
# ══════════════════════════════════════════════════════════════════════════════
def _store(tmp_path, name="opensky_mil.db") -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / name))


def _opensky_state(icao24, callsign, lat=36.0, lon=127.0):
    return [
        icao24,
        callsign,
        "Korea",
        1783091480,
        1783091489,
        lon,
        lat,
        5000.0,
        False,
        200.0,
        90.0,
        0.0,
        None,
        5100.0,
        "2000",
        False,
        0,
    ]


def _opensky_client(states):
    def handler(req):
        return _json_resp(200, {"states": states})

    return _client(handler)


KADIZ_BBOX = {"lamin": 32, "lomin": 122, "lamax": 39, "lomax": 132}


def test_opensky_ingest_persists_heuristic_hit(tmp_path):
    # 콜사인 프리픽스(RCH)만으로도 히트 → Aircraft.is_military=True로 write.
    store = _store(tmp_path)
    states = [_opensky_state("abc123", "RCH451  ")]
    opensky.ingest_cycle(store, _opensky_client(states), KADIZ_BBOX)
    ac = store.aircraft_map()["abc123"]
    assert ac.is_military is True


def test_opensky_ingest_persists_db_flag_hit(tmp_path):
    # 콜사인·대역 어느 휴리스틱에도 안 걸리는 민간로 보이는 기체라도 DB 플래그면 True.
    store = _store(tmp_path)
    states = [_opensky_state("780abc", "CCA1234 ")]
    mil_enrich = _mil_source({"780abc"})
    opensky.ingest_cycle(
        store, _opensky_client(states), KADIZ_BBOX, mil_enrich=mil_enrich
    )
    ac = store.aircraft_map()["780abc"]
    assert ac.is_military is True


def test_opensky_ingest_preserves_existing_true(tmp_path):
    # 이전 사이클(또는 합성)에서 이미 True로 판정된 기체는, 이번 사이클에 신호가 없어도
    # write_aircraft(REPLACE)로 False로 되돌아가지 않는다(단조).
    store = _store(tmp_path)
    store.write_aircraft(Aircraft(icao24="m1", callsign="CIVLOOK", is_military=True))
    states = [_opensky_state("m1", "CIVLOOK ")]  # 이번 사이클엔 히트 신호 없음
    opensky.ingest_cycle(store, _opensky_client(states), KADIZ_BBOX)
    ac = store.aircraft_map()["m1"]
    assert ac.is_military is True


def test_opensky_ingest_civilian_stays_false(tmp_path):
    # 회귀 방어 — 아무 신호도 없는 민간기는 여전히 False.
    store = _store(tmp_path)
    states = [_opensky_state("71c101", "KAL123  ")]
    opensky.ingest_cycle(store, _opensky_client(states), KADIZ_BBOX)
    ac = store.aircraft_map()["71c101"]
    assert ac.is_military is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. connectors/adsbfi_tracks.py ingest_cycle — 동일 규율(adsbfi 브랜치)
# ══════════════════════════════════════════════════════════════════════════════
def _adsbfi_client(entries_by_query: dict[str, list]):
    """query_points의 lon(124.5=서/129.5=동)으로 응답을 분기."""

    def handler(req):
        url = str(req.url)
        for marker, entries in entries_by_query.items():
            if marker in url:
                return _json_resp(
                    200,
                    {
                        "aircraft": entries,
                        "now": 1783204452.001,
                        "ptime": 0.07,
                        "resultCount": len(entries),
                    },
                )
        return _json_resp(200, {"aircraft": [], "now": 0, "ptime": 0, "resultCount": 0})

    return _client(handler)


def test_adsbfi_ingest_persists_heuristic_hit(tmp_path):
    store = LocalOntologyStore(str(tmp_path / "adsbfi_mil.db"))
    client = _adsbfi_client(
        {"124.5": [{"hex": "def456", "flight": "RCH99", "lat": 35.5, "lon": 124.0}]}
    )
    adsbfi_tracks.ingest_cycle(store, client, min_interval=0.0)
    ac = store.aircraft_map()["def456"]
    assert ac.is_military is True


def test_adsbfi_ingest_preserves_existing_true(tmp_path):
    store = LocalOntologyStore(str(tmp_path / "adsbfi_mil2.db"))
    store.write_aircraft(Aircraft(icao24="m2", callsign="CIVLOOK", is_military=True))
    client = _adsbfi_client(
        {"124.5": [{"hex": "m2", "flight": "CIVLOOK", "lat": 35.5, "lon": 124.0}]}
    )
    adsbfi_tracks.ingest_cycle(store, client, min_interval=0.0)
    ac = store.aircraft_map()["m2"]
    assert ac.is_military is True


# ══════════════════════════════════════════════════════════════════════════════
# 4. server/app.py — API 노출
# ══════════════════════════════════════════════════════════════════════════════
def _app_store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "mil_api.db"))


def test_api_observations_exposes_is_military(tmp_path, monkeypatch):
    store = _app_store(tmp_path)
    monkeypatch.setattr(server_app, "DB_PATH", str(tmp_path / "mil_api.db"))
    store.write_aircraft(Aircraft(icao24="mil1", callsign="RCH1", is_military=True))
    store.write_aircraft(Aircraft(icao24="civ1", callsign="KAL1", is_military=False))
    for icao24, ts in (("mil1", 1000), ("civ1", 1000)):
        store.write_observation(
            Observation(
                id=f"{icao24}-{ts}",
                aircraft_ref=icao24,
                ts=ts,
                lat=36.0,
                lon=127.0,
                source="opensky",
                source_url="https://opensky-network.org/api/states/all",
            )
        )
    by_icao = {d["icao24"]: d for d in server_app.api_observations()}
    assert by_icao["mil1"]["is_military"] is True
    assert by_icao["civ1"]["is_military"] is False


def test_api_tracks_exposes_is_military(tmp_path, monkeypatch):
    from ontology.custody import rebuild_tracks

    store = _app_store(tmp_path)
    monkeypatch.setattr(server_app, "DB_PATH", str(tmp_path / "mil_api.db"))
    store.write_aircraft(Aircraft(icao24="mil2", callsign="RCH2", is_military=True))
    for ts in (1000, 1030, 1060):
        store.write_observation(
            Observation(
                id=f"mil2-{ts}",
                aircraft_ref="mil2",
                ts=ts,
                lat=36.0,
                lon=127.0,
                source="opensky",
                source_url="https://opensky-network.org/api/states/all",
            )
        )
    rebuild_tracks(store)
    tracks = server_app.api_tracks()
    assert tracks and tracks[0]["is_military"] is True
