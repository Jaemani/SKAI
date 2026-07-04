"""A4+A5 검증 — RSS 커넥터 파싱 + 공용 엔티티 링킹(entity_linking).

커버:
  A4 RSS 파싱:
    1. RSS 2.0 item → NewsEvent(title·source="rss:<피드>"·실 URL·pubDate 파싱·HTML 제거 요약).
    2. link 없는 item skip · null items · 파싱 오류 → [].
    3. DOCTYPE/ENTITY 포함 응답 거부(엔티티 확장 공격 방어).
    4. RFC822 pubDate 파싱 + fallback.
  A5 엔티티 링킹(사전+패턴+실존대조 — 진짜 NER 아님):
    5. 지역 별칭 · 오퍼레이터 사전(단어경계 오탐 방지 포함).
    6. 항공기 콜사인/icao24 hex — **실존 대조**(무존재 링크 금지).
    7. link_newsevent 통합 — mentions·attrs['linking']·confidence 상향(≤0.4)·entities.
    8. 오퍼레이터 시드 적재 + 실존 Aircraft 인덱스.
    9. 합성 교차소스: 실존 Aircraft ↔ 뉴스 콜사인 → mentions_aircraft 왕복(라이브 대체 검증).
  통합:
    10. rss.ingest mock(fetch_feed patch) → NewsEvent 적재·피드별 count·mentions·시드.
    11. 폴러 등록(SOURCE_INTERVALS·resolve_sources·_ingest_source dispatch).

실행: .venv/bin/python -m pytest tests/test_rss.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from connectors import opensky, rss
from ontology import entity_linking as el
from ontology.model import (
    KADIZ_REGION,
    NEWS_MAX_CONFIDENCE,
    Aircraft,
    NewsEvent,
    Observation,
)
from ontology.store_local import LocalOntologyStore


@pytest.fixture()
def store(tmp_path):
    s = LocalOntologyStore(str(tmp_path / "rss.db"))
    s.write_region(KADIZ_REGION)
    return s


# ── 샘플 RSS ────────────────────────────────────────────────────────────────

_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>USAF F-16 intercepts jets near KADIZ</title>
    <link>https://example.test/a1</link>
    <pubDate>Fri, 03 Jul 2026 16:35:53 +0000</pubDate>
    <description>&lt;p&gt;An &lt;b&gt;incident&lt;/b&gt; near the West Sea.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Unrelated weather story</title>
    <link>https://example.test/a2</link>
    <pubDate>Sat, 04 Jul 2026 01:00:00 +0900</pubDate>
    <description>Just weather.</description>
  </item>
  <item>
    <title>No link item is skipped</title>
    <description>should be dropped</description>
  </item>
</channel></rss>"""

# DOCTYPE/ENTITY 포함(billion-laughs류) — 파싱 거부돼야 함.
_RSS_XML_WITH_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE rss [ <!ENTITY x "boom"> ]>
<rss version="2.0"><channel>
  <item><title>&x;</title><link>https://example.test/e</link></item>
</channel></rss>"""


# ── A4: RSS 파싱 ─────────────────────────────────────────────────────────────


def test_parse_feed_basic():
    news = rss.parse_feed(_RSS_XML, "testfeed", "https://example.test/feed")
    assert len(news) == 2  # link 없는 3번째 item은 skip
    n = news[0]
    assert n.source == "rss:testfeed"
    assert n.source_url == "https://example.test/a1"
    assert n.title == "USAF F-16 intercepts jets near KADIZ"
    # HTML 태그 제거된 요약
    assert n.summary == "An incident near the West Sea."
    assert "<" not in n.summary
    # pubDate 파싱
    assert n.ts == int(
        datetime(2026, 7, 3, 16, 35, 53, tzinfo=timezone.utc).timestamp()
    )
    assert n.confidence <= NEWS_MAX_CONFIDENCE
    assert n.attrs["feed"] == "testfeed"


def test_parse_feed_rejects_doctype_entity():
    assert rss.parse_feed(_RSS_XML_WITH_ENTITY, "f", "u") == []


def test_parse_feed_malformed_returns_empty():
    assert rss.parse_feed(b"<rss><broken", "f", "u") == []


def test_parse_pubdate_and_fallback():
    ts = rss._parse_pubdate("Fri, 03 Jul 2026 16:35:53 +0000", 111)
    assert ts == int(datetime(2026, 7, 3, 16, 35, 53, tzinfo=timezone.utc).timestamp())
    assert rss._parse_pubdate("", 111) == 111
    assert rss._parse_pubdate("garbage", 222) == 222


def test_strip_html():
    assert rss._strip_html("<p>a  <b>b</b>\nc</p>") == "a b c"
    assert rss._strip_html("") == ""


def test_rss_news_id_stable_and_url_based():
    assert rss._rss_news_id("https://x/1") == rss._rss_news_id("https://x/1")
    assert rss._rss_news_id("https://x/1") != rss._rss_news_id("https://x/2")
    # gdelt와 동일 규약 → 소스 간 같은 URL은 자연 dedup
    from connectors.gdelt import _news_id

    assert rss._rss_news_id("https://x/1") == _news_id("https://x/1")


# ── A5: 지역·오퍼레이터 사전 ─────────────────────────────────────────────────


def test_match_regions():
    got = el.match_regions("Chinese jets enter KADIZ over the West Sea")
    ids = {rid for _, rid in got}
    aliases = {a for a, _ in got}
    assert ids == {"KADIZ"}
    assert "KADIZ" in aliases and "West Sea" in aliases
    assert el.match_regions("nothing here") == []


def test_match_region_aliases_backcompat():
    # 구 gdelt 시그니처 보존(KADIZ 별칭 문자열 리스트).
    assert "KADIZ" in el.match_region_aliases("tensions in the kadiz today")
    assert el.match_region_aliases("no relevant place") == []


def test_match_operators_dictionary():
    ms = el.match_operators("A USAF jet and 대한항공 flight")
    ids = {m.dst_id for m in ms}
    assert "op-usaf" in ids and "op-kal" in ids
    assert all(m.method == "operator_name" and m.label == "medium" for m in ms)


def test_match_operators_word_boundary_no_false_positive():
    # "Korean airline"은 오퍼레이터 "Korean Air"로 오탐되면 안 됨(단어경계).
    ms = el.match_operators("Korean airline credentials leaked")
    assert [m.dst_id for m in ms] == []


# ── A5: 항공기 실존 대조 ─────────────────────────────────────────────────────


def _index(pairs, icaos=None):
    cs = {c.upper(): i for c, i in pairs}
    return el.AircraftIndex(
        callsign_to_icao=cs, icao24_set=set(icaos or [i for _, i in pairs])
    )


def test_match_aircraft_callsign_existence_gate():
    idx = _index([("KAL092", "a1b2c3")])
    # 실존 콜사인 → 링크
    ms = el.match_aircraft("KAL092 spotted over KADIZ", idx)
    assert [(m.dst_id, m.method) for m in ms] == [("a1b2c3", "callsign_exact")]
    # 무존재 콜사인(패턴은 맞지만 DB에 없음) → 링크 금지
    assert el.match_aircraft("ZZ999 unknown flight", idx) == []


def test_match_aircraft_icao24_hex_existence_gate():
    idx = _index([], icaos=["a1b2c3"])
    ms = el.match_aircraft("track hex a1b2c3 observed", idx)
    assert [(m.dst_id, m.method) for m in ms] == [("a1b2c3", "icao24_hex")]
    # 실존 set에 없는 6-hex 토큰은 링크 안 됨(오탐 방지)
    assert el.match_aircraft("random beefed token", idx) == []


# ── A5: link_newsevent 통합 ──────────────────────────────────────────────────


def test_link_newsevent_enriches_all_kinds():
    idx = _index([("KAL092", "a1b2c3")])
    nv = NewsEvent(
        id="news-1",
        source="rss:test",
        source_url="https://x/1",
        ts=0,
        title="KAL092 of USAF near KADIZ",
        confidence=0.30,
    )
    mentions = el.link_newsevent(nv, aircraft_index=idx, base_confidence=0.30)
    kinds = {t for t, _ in mentions}
    assert kinds == {"Region", "Operator", "Aircraft"}
    assert ("Aircraft", "a1b2c3") in mentions
    # 방식별 provenance 기록
    methods = {m["method"] for m in nv.attrs["linking"]}
    assert {"region_keyword", "operator_name", "callsign_exact"} <= methods
    # 지역+오퍼레이터+항공기 exact → 0.30+0.05+0.05+0.10=0.50 → 0.4 clamp
    assert nv.confidence == NEWS_MAX_CONFIDENCE
    assert "KADIZ" in nv.entities


def test_link_newsevent_no_phantom_aircraft():
    idx = _index([("KAL092", "a1b2c3")])
    nv = NewsEvent(
        id="news-2",
        source="rss:test",
        source_url="https://x/2",
        ts=0,
        title="Flight ABC123 vanished near KADIZ",
        confidence=0.30,
    )
    mentions = el.link_newsevent(nv, aircraft_index=idx, base_confidence=0.30)
    # ABC123은 DB에 없음 → Aircraft 링크 금지, 지역만
    assert ("Aircraft", "a1b2c3") not in mentions
    assert not any(t == "Aircraft" for t, _ in mentions)
    assert ("Region", "KADIZ") in mentions
    assert nv.confidence == 0.35  # 지역만 → base+0.05


def test_link_newsevent_region_only_confidence():
    nv = NewsEvent(
        id="n",
        source="rss:t",
        source_url="https://x/n",
        ts=0,
        title="Korean Peninsula tension",
        confidence=0.25,
    )
    el.link_newsevent(nv, aircraft_index=None, base_confidence=0.25)
    assert nv.confidence == 0.30  # SM base 0.25 + 지역 0.05


# ── A5: 시드·인덱스·왕복 ─────────────────────────────────────────────────────


def test_ensure_operator_seeds(store):
    n = el.ensure_operator_seeds(store)
    assert n == len(el.OPERATOR_SEEDS)
    ops = {o.id for o in store.query_operators()}
    assert {"op-usaf", "op-kal", "op-plaaf", "op-rokaf"} <= ops


def test_build_aircraft_index(store):
    store.write_aircraft(Aircraft(icao24="a1b2c3", callsign="KAL092"))
    store.write_aircraft(Aircraft(icao24="dead01", callsign=None))
    idx = el.build_aircraft_index(store)
    assert idx.callsign_to_icao == {"KAL092": "a1b2c3"}
    assert idx.icao24_set == {"a1b2c3", "dead01"}


def test_synthetic_cross_source_aircraft_mention(store):
    """라이브 항적 대체 검증: 실존 Aircraft ↔ 뉴스 콜사인 → mentions_aircraft 왕복.

    라이브 뉴스 제목에 ADS-B 콜사인이 실릴 확률은 낮아(정직 한계), 실존 대조 로직 자체는
    합성으로 증명한다 — DB에 실재하는 KAL092가 뉴스에 등장 → NewsEvent —mentions→ Aircraft.
    """
    # 실존 Aircraft + provenance 있는 Observation(교차검증 대상이 실재함을 보장)
    store.write_aircraft(Aircraft(icao24="a1b2c3", callsign="KAL092"))
    store.write_observation(
        Observation(
            id="a1b2c3-100",
            aircraft_ref="a1b2c3",
            ts=100,
            lat=36.0,
            lon=124.0,
            source="opensky",
            source_url="https://opensky/x",
        )
    )
    idx = el.build_aircraft_index(store)
    nv = NewsEvent(
        id="news-x",
        source="rss:test",
        source_url="https://x/live",
        ts=100,
        title="KAL092 diverted over KADIZ amid tension",
        confidence=0.30,
    )
    mentions = el.link_newsevent(nv, aircraft_index=idx, base_confidence=0.30)
    store.write_newsevent(nv, mentions=mentions)
    got = store.query_mentions("news-x")
    types = {m["type"] for m in got}
    ac = [m for m in got if m["type"] == "Aircraft"]
    assert "Aircraft" in types and ac[0]["id"] == "a1b2c3"


# ── 통합: ingest mock + 폴러 등록 ────────────────────────────────────────────


def test_rss_ingest_mock(store, monkeypatch):
    """ingest: fetch_feed를 캔ned RSS로 대체 → NewsEvent 적재·피드별 count·mentions·시드."""
    monkeypatch.setattr(rss, "fetch_feed", lambda client, name, url: _RSS_XML)
    counts = rss.ingest(store)
    # 피드 4종 × 유효 item 2건 = 8, 단 URL이 피드 간 동일(a1,a2)이라 dedup되어 store엔 2건
    assert counts["total"] == 8
    assert counts["aviationist"] == 2 and counts["yonhap"] == 2
    assert counts["mentions"] >= 1  # 최소 KADIZ 지역 링크
    assert len(store.query_news()) == 2  # URL 해시 dedup
    # 오퍼레이터 시드 적재됨(USAF 링크가 실체를 가리킴)
    assert len(store.query_operators()) == len(el.OPERATOR_SEEDS)


def test_rss_registered_in_poller(store, monkeypatch):
    assert "rss" in opensky.SOURCE_INTERVALS
    assert opensky.SOURCE_INTERVALS["rss"] == rss.RSS_POLL_INTERVAL
    # resolve_sources가 rss를 유효 소스로 인식(opensky 선두 보장)
    assert opensky.resolve_sources(["opensky", "rss"]) == ["opensky", "rss"]
    # _ingest_source dispatch → rss.ingest 호출
    monkeypatch.setattr(rss, "ingest", lambda s: {"total": 3, "mentions": 5})
    summary = opensky._ingest_source("rss", store)
    assert "rss_news=3" in summary and "mentions=5" in summary
