"""라이브 교차소스 dropout 판정 검증 — connectors/crosscheck_live.py (DR-0007 결정 1 배선).

커버:
  1. 게이트 팩토리 make_crosscheck — SKAI_CROSSCHECK=live 만 라이브, 기본/기타는 Null.
  2. confirm_absence 의미론 — 관측중(False)·부재확인(True)·stale/오류(None) (네트워크 없이 MockTransport).
  3. 리밋 규율 — TTL 캐시(같은 hex 재질의 억제), 실 호출 수 관측.
  4. 라이브 소스가 dropout 룰(detect_adsb_dropout)에 SyntheticMirror와 동일하게 꽂힘.

라이브 실 API 왕복(실 hex/가짜 hex)은 네트워크 의존이라 테스트 아님 —
docs/worklog/crosscheck-live.md의 수동 검증 기록 참조.

실행: .venv/bin/python -m pytest tests/test_crosscheck_live.py -v
"""

from __future__ import annotations

import json

import httpx

from anomaly.crosscheck import NullCrossCheckSource
from anomaly.rules import (
    ANOMALY_TYPE_ADSB_DROPOUT,
    DROPOUT_CONFIRMED_CONFIDENCE,
    DROPOUT_UNCONFIRMED_CONFIDENCE,
    detect_adsb_dropout,
)
from connectors.crosscheck_live import LiveCrossCheckSource, make_crosscheck
from ontology.model import KADIZ_REGION, OPAREA_WEST_REGION, Observation, Track

SENSITIVE = [KADIZ_REGION, OPAREA_WEST_REGION]


def _client(handler) -> httpx.Client:
    """MockTransport로 네트워크 없는 httpx.Client — handler(request)->Response."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_resp(status: int, body) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


def _live(handler) -> LiveCrossCheckSource:
    # min_interval=0 → 테스트에서 sleep 없음. 캐시 TTL은 기본 유지.
    return LiveCrossCheckSource(client=_client(handler), min_interval=0.0)


# ══════════════════════════════════════════════════════════════════════════════
# 1. 게이트 팩토리
# ══════════════════════════════════════════════════════════════════════════════
def test_make_crosscheck_default_is_null():
    # 미설정 → Null(미확인·저신뢰). 크레딧·안정성 기본.
    assert isinstance(make_crosscheck(env={}), NullCrossCheckSource)


def test_make_crosscheck_live_gate():
    src = make_crosscheck(env={"SKAI_CROSSCHECK": "live"})
    assert isinstance(src, LiveCrossCheckSource)
    assert src.source == "adsbfi"  # 기본 소스(ToS verbatim 확인)


def test_make_crosscheck_live_case_insensitive_and_source_select():
    src = make_crosscheck(
        env={"SKAI_CROSSCHECK": "LIVE", "SKAI_CROSSCHECK_SOURCE": "airplaneslive"}
    )
    assert isinstance(src, LiveCrossCheckSource) and src.source == "airplaneslive"


def test_make_crosscheck_other_values_null():
    for v in ("", "off", "synthetic", "null", "1"):
        assert isinstance(
            make_crosscheck(env={"SKAI_CROSSCHECK": v}), NullCrossCheckSource
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. confirm_absence 의미론 (crosscheck.py 계약)
# ══════════════════════════════════════════════════════════════════════════════
def test_confirm_absence_present_fresh_is_false():
    # 2차 소스가 hex를 지금 신선하게 관측 → 우리 결측은 아티팩트 → False(dropout 아님).
    def handler(req):
        return _json_resp(200, {"ac": [{"hex": "abc123", "seen": 2.1}], "total": 1})

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is False


def test_confirm_absence_empty_is_true():
    # 2차 소스도 hex 미관측(빈 ac) → 부재 교차 확인 → True(상향 근거).
    def handler(req):
        return _json_resp(200, {"ac": [], "total": 0})

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is True


def test_confirm_absence_present_stale_is_none():
    # hex는 응답에 있으나 seen이 신선도 상한 초과(오래됨) → 애매 → None(과잉단정 회피).
    def handler(req):
        return _json_resp(200, {"ac": [{"hex": "abc123", "seen": 999.0}], "total": 1})

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is None


def test_confirm_absence_http_error_is_none():
    # 429/5xx → 미확인(리밋·오류에 단정 안 함).
    def handler(req):
        return _json_resp(429, {"error": "rate limited"})

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is None


def test_confirm_absence_network_exception_is_none():
    def handler(req):
        raise httpx.ConnectError("boom")

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is None


def test_confirm_absence_blank_hex_is_none_no_call():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json_resp(200, {"ac": []})

    src = _live(handler)
    assert src.confirm_absence("", (1000, 2000)) is None
    assert calls["n"] == 0  # 빈 hex는 호출조차 안 함


def test_case_insensitive_hex_match():
    # OpenSky hex는 소문자, 2차 소스가 대문자로 줘도 매칭.
    def handler(req):
        return _json_resp(200, {"ac": [{"hex": "ABC123", "seen": 1.0}]})

    assert _live(handler).confirm_absence("abc123", (1000, 2000)) is False


# ══════════════════════════════════════════════════════════════════════════════
# 3. 리밋 규율 — TTL 캐시
# ══════════════════════════════════════════════════════════════════════════════
def test_cache_suppresses_repeat_query():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return _json_resp(200, {"ac": []})

    src = _live(handler)
    a = src.confirm_absence("abc123", (1000, 2000))
    b = src.confirm_absence("abc123", (1000, 2000))  # TTL 내 재질의 → 캐시
    assert a is True and b is True
    assert calls["n"] == 1  # 실 HTTP 1회만
    assert src.calls == 1 and src.cache_hits == 1


def test_query_hits_expected_endpoint():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return _json_resp(200, {"ac": []})

    _live(handler).confirm_absence("abc123", (1000, 2000))
    assert seen["url"] == "https://opendata.adsb.fi/api/v2/hex/abc123"


# ══════════════════════════════════════════════════════════════════════════════
# 4. dropout 룰 통합 — 라이브 소스가 SyntheticMirror와 동일 계약으로 꽂힘
# ══════════════════════════════════════════════════════════════════════════════
def _dropout_setup():
    """민감구역(OpArea) 내 gap 트랙 + 마지막 관측 (test_p5 패턴 재사용)."""
    last = Observation(
        id="d1-1000",
        aircraft_ref="d1",
        ts=1000,
        lat=36.2,
        lon=124.5,
        squawk="2000",
        source="opensky",
        source_url="https://opensky/d1/1000",
    )
    tr = Track(
        id="track-d1",
        aircraft_ref="d1",
        start_ts=400,
        end_ts=1000,
        path=[[36.2, 124.5], [36.21, 124.51]],
        has_gap=True,
    )
    return tr, last


def test_live_source_absent_confirms_dropout():
    # 라이브 2차 소스가 빈 응답(부재확인) → dropout 상향(0.72), SyntheticMirror(absent)와 동일.
    def handler(req):
        return _json_resp(200, {"ac": []})

    tr, last = _dropout_setup()
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=_live(handler)
    )
    assert len(drafts) == 1
    assert drafts[0].type == ANOMALY_TYPE_ADSB_DROPOUT
    assert drafts[0].confidence == DROPOUT_CONFIRMED_CONFIDENCE
    assert drafts[0].signal["cross_confirmed"] is True


def test_live_source_present_suppresses_dropout():
    # 라이브 2차 소스가 여전히 관측(신선) → 센서 아티팩트 → dropout 생성 안 함.
    def handler(req):
        return _json_resp(200, {"ac": [{"hex": "d1", "seen": 3.0}]})

    tr, last = _dropout_setup()
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=_live(handler)
    )
    assert drafts == []


def test_live_source_error_stays_low_confidence():
    # 2차 소스 오류(None) → 저신뢰 후보(0.42) 유지(단정 금지) — Null과 동일 결과.
    def handler(req):
        return _json_resp(500, {"error": "down"})

    tr, last = _dropout_setup()
    drafts = detect_adsb_dropout(
        [tr], {"d1": last}, SENSITIVE, now=2000, crosscheck=_live(handler)
    )
    assert len(drafts) == 1
    assert drafts[0].confidence == DROPOUT_UNCONFIRMED_CONFIDENCE
    assert drafts[0].signal["cross_confirmed"] is None
