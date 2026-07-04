"""라이브 군용 식별 보강 검증 — connectors/mil_enrich_live.py (DR-0013 결정 5 배선).

커버:
  1. 게이트 팩토리 make_mil_enrichment — SKAI_MIL_ENRICH=live 만 라이브, 기본/기타는 Null.
  2. lookup 의미론 — 군용 hex(True+근거)·비군용/미확인(None)·오류(None) (네트워크 없이 MockTransport).
  3. dbFlags 비트 필터 — bit0(=1)만 군용. dbFlags 0·2(interesting)·누락·비정수는 제외.
  4. 리밋 규율 — /v2/mil 스냅샷 60s TTL(재조회 억제), 실 호출 수 관측, 엔드포인트 URL.
  5. detect_military_approach 통합 — DB 플래그가 콜사인 휴리스틱보다 상위(0.65·db_flag·adsb.fi 근거),
     게이트 off(Null) 시 종전과 완전 동일(휴리스틱만).

라이브 실 API 왕복은 네트워크 의존이라 테스트 아님 — docs/worklog/mil-enrich.md의 실측 기록 참조.

실행: .venv/bin/python -m pytest tests/test_mil_enrich.py -v
"""

from __future__ import annotations

import json

import httpx

from anomaly.mil_enrich import NullMilEnrichment
from anomaly.rules import (
    ANOMALY_TYPE_MILITARY_APPROACH,
    MILITARY_DB_FLAG_CONFIDENCE,
    detect_military_approach,
)
from connectors.mil_enrich_live import (
    MIL_DB_REASON,
    LiveMilEnrichment,
    make_mil_enrichment,
)
from ontology.model import OPAREA_WEST_REGION, Aircraft, Observation

OPAREAS = [OPAREA_WEST_REGION]


def _client(handler) -> httpx.Client:
    """MockTransport로 네트워크 없는 httpx.Client — handler(request)->Response."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_resp(status: int, body) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


def _live(handler, **kw) -> LiveMilEnrichment:
    return LiveMilEnrichment(client=_client(handler), **kw)


def _mil_body(entries) -> dict:
    """/v2/mil 응답 스켈레톤(실측 top-level 키 반영)."""
    return {
        "ac": entries,
        "msg": "No error",
        "now": 1783201522000,
        "total": len(entries),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. 게이트 팩토리
# ══════════════════════════════════════════════════════════════════════════════
def test_make_default_is_null():
    # 미설정 → Null(신호 없음). 휴리스틱만으로 판정(안정성 기본).
    assert isinstance(make_mil_enrichment(env={}), NullMilEnrichment)


def test_make_live_gate():
    src = make_mil_enrichment(env={"SKAI_MIL_ENRICH": "live"})
    assert isinstance(src, LiveMilEnrichment)
    assert src.source == "adsbfi"  # 기본 소스(ToS verbatim 확인)


def test_make_live_case_insensitive_and_source_select():
    src = make_mil_enrichment(
        env={"SKAI_MIL_ENRICH": "LIVE", "SKAI_MIL_ENRICH_SOURCE": "airplaneslive"}
    )
    assert isinstance(src, LiveMilEnrichment) and src.source == "airplaneslive"


def test_make_other_values_null():
    for v in ("", "off", "synthetic", "null", "1"):
        assert isinstance(
            make_mil_enrichment(env={"SKAI_MIL_ENRICH": v}), NullMilEnrichment
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. lookup 의미론 (mil_enrich.py 계약)
# ══════════════════════════════════════════════════════════════════════════════
def test_lookup_military_hex_is_true_with_reason():
    # 군용 플래그(dbFlags&1) hex → (True, 근거문구). 근거에 출처(adsb.fi/dbFlags) 명시.
    def handler(req):
        return _json_resp(200, _mil_body([{"hex": "ae0679", "dbFlags": 1}]))

    got = _live(handler).lookup("ae0679")
    assert got == (True, MIL_DB_REASON)
    assert "adsb.fi" in got[1] and "dbFlags" in got[1]


def test_lookup_unknown_hex_is_none():
    # 스냅샷에 없는 hex → None(군용 아님 단정 아님 — 휴리스틱이 이어서 판정).
    def handler(req):
        return _json_resp(200, _mil_body([{"hex": "ae0679", "dbFlags": 1}]))

    assert _live(handler).lookup("abc123") is None


def test_lookup_case_insensitive_hex_match():
    # OpenSky hex는 소문자, 2차 소스가 대문자로 줘도 매칭.
    def handler(req):
        return _json_resp(200, _mil_body([{"hex": "AE0679", "dbFlags": 1}]))

    assert _live(handler).lookup("ae0679") == (True, MIL_DB_REASON)


def test_lookup_blank_hex_is_none_no_call():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json_resp(200, _mil_body([]))

    src = _live(handler)
    assert src.lookup("") is None
    assert calls["n"] == 0  # 빈 hex는 스냅샷 조회조차 트리거 안 함


def test_lookup_http_error_is_none():
    # 429/5xx → 미확인(스냅샷 없음) → None. 리밋·오류에 단정 안 함.
    def handler(req):
        return _json_resp(429, {"error": "rate limited"})

    assert _live(handler).lookup("ae0679") is None


def test_lookup_network_exception_is_none():
    def handler(req):
        raise httpx.ConnectError("boom")

    assert _live(handler).lookup("ae0679") is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. dbFlags 비트 필터 — bit0(military)만 채택
# ══════════════════════════════════════════════════════════════════════════════
def test_dbflags_bit0_only():
    # dbFlags: 1(mil)·3(mil+interesting) = 군용. 0·2(interesting만)·누락·비정수 = 제외.
    def handler(req):
        return _json_resp(
            200,
            _mil_body(
                [
                    {"hex": "mil1", "dbFlags": 1},
                    {"hex": "mil3", "dbFlags": 3},  # bit0 set
                    {"hex": "nomil0", "dbFlags": 0},
                    {"hex": "nomil2", "dbFlags": 2},  # interesting만(bit1)
                    {"hex": "nokey"},  # dbFlags 누락
                    {"hex": "badval", "dbFlags": "x"},  # 비정수
                ]
            ),
        )

    src = _live(handler)
    assert src.lookup("mil1") == (True, MIL_DB_REASON)
    assert src.lookup("mil3") == (True, MIL_DB_REASON)
    for h in ("nomil0", "nomil2", "nokey", "badval"):
        assert src.lookup(h) is None


# ══════════════════════════════════════════════════════════════════════════════
# 4. 리밋 규율 — 스냅샷 60s TTL, 엔드포인트
# ══════════════════════════════════════════════════════════════════════════════
def test_snapshot_cached_within_ttl():
    # 여러 hex 조회가 스냅샷 1회만 fetch(60s TTL 내 재조회 억제).
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json_resp(200, _mil_body([{"hex": "ae0679", "dbFlags": 1}]))

    src = _live(handler)  # refresh_ttl 기본 60s
    src.lookup("ae0679")
    src.lookup("abc123")
    src.lookup("ae0679")
    assert calls["n"] == 1  # 실 HTTP 1회만
    assert src.calls == 1 and src.refreshes == 1


def test_snapshot_refetch_after_ttl():
    # refresh_ttl=0 → 매 lookup마다 스냅샷 재조회(폴러 주기 갱신 경로).
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json_resp(200, _mil_body([{"hex": "ae0679", "dbFlags": 1}]))

    src = _live(handler, refresh_ttl=0.0)
    src.lookup("ae0679")
    src.lookup("ae0679")
    assert calls["n"] == 2


def test_error_keeps_previous_snapshot():
    # 첫 조회 성공 후 오류 → refresh_ttl=0라도 직전 스냅샷 유지(빈 집합으로 덮지 않음).
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return _json_resp(200, _mil_body([{"hex": "ae0679", "dbFlags": 1}]))
        return _json_resp(500, {"error": "down"})

    src = _live(handler, refresh_ttl=0.0)
    assert src.lookup("ae0679") == (True, MIL_DB_REASON)  # 성공 스냅샷
    assert src.lookup("ae0679") == (True, MIL_DB_REASON)  # 오류지만 직전 유지


def test_query_hits_mil_endpoint():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return _json_resp(200, _mil_body([]))

    _live(handler).lookup("ae0679")
    assert seen["url"] == "https://opendata.adsb.fi/api/v2/mil"


# ══════════════════════════════════════════════════════════════════════════════
# 5. detect_military_approach 통합
# ══════════════════════════════════════════════════════════════════════════════
def _obs(icao24, ts=1000, lat=36.3, lon=124.5) -> Observation:
    return Observation(
        id=f"{icao24}-{ts}",
        aircraft_ref=icao24,
        ts=ts,
        lat=lat,
        lon=lon,
        squawk="2000",
        source="opensky",
        source_url=f"https://opensky/{icao24}/{ts}",
    )


def _mil_source(military_hexes: set[str]) -> LiveMilEnrichment:
    def handler(req):
        return _json_resp(
            200, _mil_body([{"hex": h, "dbFlags": 1} for h in military_hexes])
        )

    return _live(handler)


def test_db_flag_promotes_civilian_looking_callsign():
    # 콜사인·대역 어느 휴리스틱에도 안 걸리는 hex라도 DB 플래그면 군용 접근으로 승격(0.65·db_flag).
    o = _obs("780abc")  # 중국 대역(휴리스틱 미포함) — 콜사인 없음
    ac_map = {"780abc": Aircraft(icao24="780abc", callsign="CCA1234")}
    drafts = detect_military_approach(
        [o], ac_map, OPAREAS, now=1000, mil_enrich=_mil_source({"780abc"})
    )
    assert len(drafts) == 1
    d = drafts[0]
    assert d.type == ANOMALY_TYPE_MILITARY_APPROACH
    assert d.confidence == MILITARY_DB_FLAG_CONFIDENCE == 0.65
    assert d.signal["mil_source"] == "db_flag"
    assert "adsb.fi" in d.signal["mil_reason"]  # provenance 문장 전파


def test_db_flag_takes_precedence_over_callsign_heuristic():
    # 콜사인 휴리스틱(RCH=0.55)과 DB 플래그(0.65)가 동시 → DB 플래그 우선(더 강한 근거).
    o = _obs("ae0679")
    ac_map = {"ae0679": Aircraft(icao24="ae0679", callsign="RCH348")}
    drafts = detect_military_approach(
        [o], ac_map, OPAREAS, now=1000, mil_enrich=_mil_source({"ae0679"})
    )
    assert len(drafts) == 1
    assert drafts[0].confidence == 0.65  # 0.55(콜사인)가 아니라 0.65(DB)
    assert drafts[0].signal["mil_source"] == "db_flag"


def test_gate_off_unchanged_heuristic_only():
    # 게이트 off(Null) → DB 신호 없음. 콜사인 휴리스틱만으로 판정(mil_source=heuristic).
    o = _obs("m2")
    ac_map = {"m2": Aircraft(icao24="m2", callsign="RCH88")}
    drafts = detect_military_approach(
        [o], ac_map, OPAREAS, now=1000, mil_enrich=NullMilEnrichment()
    )
    assert len(drafts) == 1
    assert drafts[0].confidence <= 0.65
    assert drafts[0].signal["mil_source"] == "heuristic"


def test_gate_off_default_arg_no_regression():
    # mil_enrich 미지정(기존 호출부) → 종전과 동일. 민간 hex는 후보 아님.
    o = _obs("71c101")
    ac_map = {"71c101": Aircraft(icao24="71c101", callsign="KAL123")}
    assert detect_military_approach([o], ac_map, OPAREAS, now=1000) == []


def test_db_flag_still_requires_oparea():
    # DB 플래그라도 OpArea 밖이면 접근 이상징후 아님(공간 게이트 불변).
    o = _obs("ae0679", lat=38.5, lon=130.0)  # OpArea 밖
    ac_map = {"ae0679": Aircraft(icao24="ae0679", callsign="RCH348")}
    drafts = detect_military_approach(
        [o], ac_map, OPAREAS, now=1000, mil_enrich=_mil_source({"ae0679"})
    )
    assert drafts == []
