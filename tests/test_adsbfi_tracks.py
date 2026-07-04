"""adsb.fi 항적 커넥터 검증 — connectors/adsbfi_tracks.py (OpenSky 대체 track 소스).

커버:
  1. 순수 매핑 entry_to_observation — 단위변환(ft→m·kt→m/s·ftmin→m/s), "ground" 처리,
     callsign strip, squawk str, seen 기반 ts, 위치/hex 결측 스킵, registration/type 보강.
  2. response_to_pairs — aircraft null/부재 방어, 비-dict entry 스킵.
  3. fetch_point 오류 격리 — 200 정상 / 429·비200·네트워크예외·비JSON·비dict → None(질의만 skip).
  4. ingest_cycle(store) — 2점 질의 write(Aircraft/Observation/observed_as)+Track 재구성+카운트,
     질의별 실패 격리(한 점 429여도 다른 점은 기여), source_url = 실제 질의 URL.
  5. 폴러 등록 — resolve_sources가 adsbfi를 base 소스로 유지, due_sources는 adsbfi를 aux로
     스케줄하지 않음(base 사이클 소스).

라이브 실 API 왕복은 네트워크 의존이라 테스트 아님 — docs/worklog/adsbfi-tracks.md의 실측 참조.

실행: .venv/bin/python -m pytest tests/test_adsbfi_tracks.py -v
"""

from __future__ import annotations

import json

import httpx

from connectors import adsbfi_tracks as A
from connectors import opensky
from ontology.store_local import LocalOntologyStore

# 변환 기대치(모듈 상수 미러 — 값이 바뀌면 테스트가 알려줌).
FT_TO_M = 0.3048
KT_TO_MPS = 0.514444
FTMIN_TO_MPS = 0.00508

SRC_URL = "https://opendata.adsb.fi/api/v2/lat/35.5/lon/124.5/dist/250"


def _client(handler) -> httpx.Client:
    """MockTransport로 네트워크 없는 httpx.Client — handler(request)->Response."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_resp(status: int, body) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


def _radius_body(entries) -> dict:
    """반경 응답 스켈레톤(실측 top-level 키: aircraft/now/ptime/resultCount)."""
    return {
        "aircraft": entries,
        "now": 1783204452.001,
        "ptime": 0.07,
        "resultCount": len(entries),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. 순수 매핑 entry_to_observation
# ══════════════════════════════════════════════════════════════════════════════
def test_map_units_and_fields():
    # KAL486 실측 entry: 7525ft→2293.6m, 266.8kt→137.3m/s, baro_rate 변환.
    entry = {
        "hex": "71c559",
        "flight": "KAL486  ",  # 공백 패딩
        "lat": 37.35,
        "lon": 126.15,
        "alt_baro": 7525,
        "gs": 266.8,
        "track": 345.23,
        "squawk": "3217",
        "seen": 0.2,
        "t": "A21N",
        "r": "HL8559",
        "type": "adsb_icao",
        "baro_rate": -2048,
        "dst": 118.42,
        "category": "A3",
    }
    ac, obs = A.entry_to_observation(entry, SRC_URL, fetched_at=1783204452)
    # Aircraft
    assert ac.icao24 == "71c559"
    assert ac.callsign == "KAL486"  # strip
    assert ac.registration == "HL8559" and ac.type == "A21N"
    assert ac.is_military is False
    # Observation — 단위변환
    assert abs(obs.alt - 7525 * FT_TO_M) < 1e-6  # 2293.62 m
    assert abs(obs.velocity - 266.8 * KT_TO_MPS) < 1e-6  # 137.25 m/s
    assert obs.heading == 345.23  # 도(°) 그대로
    assert obs.squawk == "3217" and isinstance(obs.squawk, str)
    assert obs.on_ground is False
    assert obs.source == "adsbfi" and obs.source_url == SRC_URL
    assert obs.id == "71c559-1783204452"  # ts = fetched_at - round(0.2)
    assert abs(obs.attrs["vertical_rate"] - (-2048 * FTMIN_TO_MPS)) < 1e-6
    assert obs.attrs["dst_nm"] == 118.4 and obs.attrs["seen_s"] == 0.2


def test_map_ground_alt():
    # alt_baro="ground" → alt=None + on_ground=True.
    entry = {
        "hex": "71d426",
        "lat": 37.5,
        "lon": 126.4,
        "alt_baro": "ground",
        "seen": 1,
    }
    ac, obs = A.entry_to_observation(entry, SRC_URL, fetched_at=2000)
    assert obs.alt is None and obs.on_ground is True
    assert obs.id == "71d426-1999"  # ts = 2000 - round(1)


def test_map_none_fields_are_tolerated():
    # gs·track·squawk·flight·baro_rate 결측 → None(스킵 아님). 위치만 있으면 관측.
    entry = {"hex": "abcded", "lat": 35.0, "lon": 127.0}
    ac, obs = A.entry_to_observation(entry, SRC_URL, fetched_at=1000)
    assert ac.callsign is None and obs.velocity is None and obs.heading is None
    assert obs.squawk is None and obs.on_ground is False
    assert obs.attrs["vertical_rate"] is None
    assert obs.ts == 1000  # seen 없음 → fetched_at


def test_map_missing_position_is_none():
    # 위치 없는 entry는 증거로 못 씀 → None(스킵).
    assert A.entry_to_observation({"hex": "abc123"}, SRC_URL, 1000) is None
    assert A.entry_to_observation({"hex": "a", "lat": 1.0}, SRC_URL, 1000) is None


def test_map_missing_hex_is_none():
    assert A.entry_to_observation({"lat": 35.0, "lon": 127.0}, SRC_URL, 1000) is None
    assert (
        A.entry_to_observation({"hex": "", "lat": 35.0, "lon": 127.0}, SRC_URL, 1)
        is None
    )


def test_map_hex_lowercased_and_stripped():
    ac, _ = A.entry_to_observation(
        {"hex": " AE0679 ", "lat": 35.0, "lon": 127.0}, SRC_URL, 1000
    )
    assert ac.icao24 == "ae0679"


def test_map_squawk_zero_preserved_as_str():
    # squawk 숫자 0 같은 값도 문자열 보존(str 비교 규약).
    _, obs = A.entry_to_observation(
        {"hex": "x", "lat": 35.0, "lon": 127.0, "squawk": 0}, SRC_URL, 1000
    )
    assert obs.squawk == "0"


# ══════════════════════════════════════════════════════════════════════════════
# 2. response_to_pairs
# ══════════════════════════════════════════════════════════════════════════════
def test_response_pairs_basic():
    body = _radius_body(
        [
            {"hex": "a1", "lat": 35.0, "lon": 127.0},
            {"hex": "a2", "lat": 36.0, "lon": 128.0},
        ]
    )
    pairs = A.response_to_pairs(body, SRC_URL, 1000)
    assert len(pairs) == 2


def test_response_pairs_null_aircraft_is_empty():
    # aircraft null/부재/None 데이터 → [] (방어).
    assert A.response_to_pairs({"aircraft": None}, SRC_URL, 1000) == []
    assert A.response_to_pairs({}, SRC_URL, 1000) == []
    assert A.response_to_pairs(None, SRC_URL, 1000) == []


def test_response_pairs_skips_non_dict_and_no_pos():
    body = _radius_body(
        [
            "junk",  # 비-dict
            {"hex": "a1"},  # 위치 없음
            {"hex": "a2", "lat": 35.0, "lon": 127.0},  # 유효
        ]
    )
    pairs = A.response_to_pairs(body, SRC_URL, 1000)
    assert len(pairs) == 1 and pairs[0][0].icao24 == "a2"


# ══════════════════════════════════════════════════════════════════════════════
# 3. fetch_point 오류 격리
# ══════════════════════════════════════════════════════════════════════════════
def test_fetch_ok():
    def handler(req):
        return _json_resp(200, _radius_body([{"hex": "a1", "lat": 35.0, "lon": 127.0}]))

    data, url = A.fetch_point(_client(handler), 35.5, 124.5, 250)
    assert data is not None and data["resultCount"] == 1
    assert url == "https://opendata.adsb.fi/api/v2/lat/35.5/lon/124.5/dist/250"


def test_fetch_429_is_none():
    def handler(req):
        return _json_resp(429, {"error": "rate limited"})

    data, url = A.fetch_point(_client(handler), 35.5, 124.5, 250)
    assert data is None and "dist/250" in url


def test_fetch_non200_is_none():
    def handler(req):
        return _json_resp(400, {"error": "bad"})

    assert A.fetch_point(_client(handler), 35.5, 124.5, 250)[0] is None


def test_fetch_network_exception_is_none():
    def handler(req):
        raise httpx.ConnectError("boom")

    assert A.fetch_point(_client(handler), 35.5, 124.5, 250)[0] is None


def test_fetch_bad_json_is_none():
    def handler(req):
        return httpx.Response(200, content=b"not json <html>")

    assert A.fetch_point(_client(handler), 35.5, 124.5, 250)[0] is None


def test_fetch_non_dict_json_is_none():
    def handler(req):
        return httpx.Response(200, content=b"[1,2,3]")  # 리스트(dict 아님)

    assert A.fetch_point(_client(handler), 35.5, 124.5, 250)[0] is None


def test_fetch_sends_user_agent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return _json_resp(200, _radius_body([]))

    A.fetch_point(_client(handler), 35.5, 124.5, 250)
    assert seen["ua"] and "SKAI" in seen["ua"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. ingest_cycle(store)
# ══════════════════════════════════════════════════════════════════════════════
def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "adsbfi.db"))


def test_ingest_cycle_writes_and_returns(tmp_path):
    # 2점 질의 각각 다른 hex → write + observed_as 링크 + Track 재구성 + 카운트.
    def handler(req):
        if "124.5" in str(req.url):  # 서
            return _json_resp(
                200, _radius_body([{"hex": "w1", "lat": 35.5, "lon": 124.0, "gs": 200}])
            )
        return _json_resp(  # 동
            200, _radius_body([{"hex": "e1", "lat": 35.5, "lon": 130.0, "gs": 210}])
        )

    store = _store(tmp_path)
    n_obs, n_ac, n_anom = A.ingest_cycle(store, _client(handler), min_interval=0.0)
    assert n_obs == 2 and n_ac == 2
    # 실제 저장 확인
    icaos = {a.icao24 for a in store.query_aircraft()}
    assert icaos == {"w1", "e1"}
    assert len(store.query_all_observations()) == 2
    assert len(store.query_tracks()) == 2  # 기체별 Track
    # observed_as 링크 존재
    obs = store.query_observations_for("w1")[0]
    assert obs.source == "adsbfi"
    assert "dist/250" in obs.source_url


def test_ingest_cycle_isolates_point_failure(tmp_path):
    # 한 점(서)이 429여도 다른 점(동)은 그대로 기여(질의별 격리).
    def handler(req):
        if "124.5" in str(req.url):
            return _json_resp(429, {"error": "limited"})
        return _json_resp(200, _radius_body([{"hex": "e1", "lat": 35.5, "lon": 130.0}]))

    store = _store(tmp_path)
    n_obs, n_ac, _ = A.ingest_cycle(store, _client(handler), min_interval=0.0)
    assert n_obs == 1 and n_ac == 1
    assert {a.icao24 for a in store.query_aircraft()} == {"e1"}


def test_ingest_cycle_both_fail_is_zero(tmp_path):
    def handler(req):
        return _json_resp(429, {"error": "limited"})

    store = _store(tmp_path)
    assert A.ingest_cycle(store, _client(handler), min_interval=0.0) == (0, 0, 0)


def test_ingest_cycle_queries_both_points(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return _json_resp(200, _radius_body([]))

    A.ingest_cycle(_store(tmp_path), _client(handler), min_interval=0.0)
    assert len(seen) == 2
    assert any("124.5" in u for u in seen) and any("129.5" in u for u in seen)


def test_ingest_cycle_overlap_dedup(tmp_path):
    # 두 점이 같은 hex를 같은 seen으로 반환 → 같은 ts → Observation id 자연 dedup(1건).
    def handler(req):
        return _json_resp(
            200,
            _radius_body([{"hex": "dup", "lat": 35.5, "lon": 127.0, "seen": 0}]),
        )

    store = _store(tmp_path)
    n_obs, n_ac, _ = A.ingest_cycle(store, _client(handler), min_interval=0.0)
    # n_obs는 write 시도 2회(양쪽 점) 카운트하지만, 저장 Observation은 1건(dedup).
    assert n_ac == 1
    assert len(store.query_all_observations()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. 폴러 등록 (opensky.resolve_sources / due_sources)
# ══════════════════════════════════════════════════════════════════════════════
def test_resolve_sources_keeps_adsbfi():
    # adsbfi는 base track 소스 → 명시되면 유지, opensky를 강제 추가하지 않음(단독 운용 가능).
    assert opensky.resolve_sources(["adsbfi"]) == ["adsbfi"]
    got = opensky.resolve_sources(["adsbfi", "opensky", "gdelt", "bogus"])
    assert "adsbfi" in got and "opensky" in got and "bogus" not in got


def test_resolve_sources_forces_opensky_when_no_track_source():
    # 항적 base 소스가 하나도 없으면(뉴스만) opensky를 선두 보장 — 기존 동작 불변.
    assert opensky.resolve_sources(["gdelt"])[0] == "opensky"
    assert opensky.resolve_sources(None) == ["opensky"]


def test_due_sources_skips_adsbfi():
    # adsbfi는 base 사이클 소스라 aux due 스케줄에 들어가지 않음(SOURCE_INTERVALS에 없음).
    due = opensky.due_sources(
        {}, now=10_000, intervals=opensky.SOURCE_INTERVALS, sources=["adsbfi", "gdelt"]
    )
    assert "adsbfi" not in due and "gdelt" in due
