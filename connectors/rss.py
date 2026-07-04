"""RSS 보조 뉴스 커넥터 — 키 불요 공개 구독 피드 → NewsEvent(저신뢰) → 온톨로지.

파이프: 공개 RSS 2.0 피드(항공·한반도 안보) → 표준 파싱(stdlib xml.etree)
        → NewsEvent(source="rss:<피드명>", 실 기사 URL, confidence ≤ 0.4)
        + 공용 entity_linking으로 mentions→ Region/Operator/Aircraft.

# 선정 피드 (2026-07-04 라이브·robots 검증, docs/worklog/news-enrich.md)
  - aviationist    : 군용 항공 전문(콜사인·기종·공군 언급 잦음).
  - twz            : The War Zone(군사 항공/방위).
  - defensenews-air: Defense News 항공 카테고리.
  - yonhap         : 연합뉴스 영문(한반도·지역 링킹 신호).
전부 robots.txt 허용 + application/rss+xml 응답 확인. 공개 구독 피드의 정상 사용.

# 정직한 한계
- stdlib xml.etree는 신뢰 못 할 XML 입력에 안전하지 않다(공식 문서). 방어:
  ① 응답 크기 상한(MAX_RESPONSE_BYTES) ② DOCTYPE/ENTITY 포함 응답 거부(엔티티 확장 공격 차단).
  피드는 평판 매체의 공개 피드로 한정. 새 패키지(feedparser/lxml/defusedxml) 미추가.
- NewsAPI는 API 키 필요 → 기본 no-op. NEWSAPI_KEY 환경변수 있을 때만 활성(구현은 스텁, 사유 기록).

# 폴링 규율
- poller SOURCE_INTERVALS['rss'] = RSS_POLL_INTERVAL(15분). 피드 간 예의상 2초 간격(코드 강제).
- User-Agent 명시. SKAI_POLL_SOURCES에 'rss' 추가 시 활성(기본 소스에는 미포함 — 아래 주석).
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from ontology import entity_linking
from ontology.model import NEWS_MAX_CONFIDENCE, NewsEvent
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

# ── 상수 ─────────────────────────────────────────────────────────────────────
RSS_POLL_INTERVAL = 15 * 60  # 보조 뉴스 15분(과호출 금지)
RSS_MIN_REQUEST_INTERVAL = 2.0  # 피드 간 최소 간격(초, 코드 강제)
TIMEOUT = 15
MAX_RESPONSE_BYTES = 5_000_000  # 응답 크기 상한(방어)
DEFAULT_ITEMS_PER_FEED = 15  # 피드당 상위 N개만 매핑
RSS_BASE_CONFIDENCE = (
    0.30  # 저신뢰 기준(지역/오퍼레이터/항공기 매칭 시 소폭 상향, ≤0.4)
)
USER_AGENT = "SKAI-ISR-Copilot/0.1 (research; public RSS feeds)"

# 선정 피드: 피드명 → URL. 피드명은 source="rss:<피드명>" 접미어가 된다.
FEEDS: dict[str, str] = {
    "aviationist": "https://theaviationist.com/feed/",
    "twz": "https://www.twz.com/feed",
    "defensenews-air": "https://www.defensenews.com/arc/outboundfeeds/rss/category/air/?outputType=xml",
    "yonhap": "https://en.yna.co.kr/RSS/news.xml",
}

_ATOM = "{http://www.w3.org/2005/Atom}"
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# ── 속도 제한 상태(모듈 레벨) ────────────────────────────────────────────────
_last_request_ts = 0.0


def _rate_limit_guard() -> None:
    """직전 요청 이후 RSS_MIN_REQUEST_INTERVAL(2초)이 안 지났으면 그만큼 sleep(준수 자동화)."""
    global _last_request_ts
    now = time.monotonic()
    elapsed = now - _last_request_ts
    if _last_request_ts > 0 and elapsed < RSS_MIN_REQUEST_INTERVAL:
        time.sleep(RSS_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_ts = time.monotonic()


# ── 순수 헬퍼(테스트 대상) ───────────────────────────────────────────────────


def _rss_news_id(url: str) -> str:
    """NewsEvent PK — URL 해시(gdelt와 동일 규약 → 소스 간 동일 URL은 자연 dedup)."""
    return "news-" + hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def _parse_pubdate(pubdate: Optional[str], fallback_ts: int) -> int:
    """RFC 822 pubDate('Fri, 03 Jul 2026 16:35:53 +0000') → Unix ts. 실패 시 fallback."""
    if not pubdate:
        return fallback_ts
    try:
        dt = parsedate_to_datetime(pubdate)
        return int(dt.timestamp())
    except (ValueError, TypeError, IndexError):
        return fallback_ts


def _strip_html(text: str) -> str:
    """description의 HTML 태그 제거 + 공백 정규화(요약 텍스트화)."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", _HTML_TAG_RE.sub(" ", text)).strip()


def _item_field(item: ET.Element, name: str) -> str:
    """RSS(item/<name>) 또는 Atom(entry/<name>) 필드 텍스트. link는 href 폴백."""
    el = item.find(name)
    if el is None:
        el = item.find(_ATOM + name)
    if el is None:
        return ""
    if el.text and el.text.strip():
        return el.text.strip()
    # Atom link는 텍스트 없이 href 속성에 URL을 담는다.
    return (el.get("href") or "").strip()


def _rejects_entities(content: bytes) -> bool:
    """DOCTYPE/ENTITY 선언 포함 여부(엔티티 확장 공격 방어 — 있으면 파싱 거부)."""
    head = content[:2048].lower()
    return b"<!doctype" in head or b"<!entity" in head


def parse_feed(
    content: bytes,
    feed_name: str,
    feed_url: str,
    fetched_at: Optional[int] = None,
    max_items: int = DEFAULT_ITEMS_PER_FEED,
) -> list[NewsEvent]:
    """RSS/Atom 바이트 → NewsEvent 리스트 (순수 매핑 — 테스트 대상).

    링킹(mentions·confidence 상향)은 ingest에서 entity_linking이 담당. 여기선 base 신뢰도만.
    DOCTYPE/ENTITY 포함·파싱 실패·link 부재는 방어적으로 skip.
    """
    fetched_at = fetched_at if fetched_at is not None else int(time.time())
    if _rejects_entities(content):
        print(f"[rss][{feed_name}] DOCTYPE/ENTITY 포함 → 파싱 거부(방어)")
        return []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f"[rss][{feed_name}] XML 파싱 실패: {e!r}")
        return []

    items = root.findall(".//item")
    if not items:
        items = root.findall(f".//{_ATOM}entry")

    out: list[NewsEvent] = []
    for item in items[:max_items]:
        link = _item_field(item, "link")
        if not link:
            continue  # citation PK(URL) 없으면 skip
        title = _item_field(item, "title")
        summary = _strip_html(
            _item_field(item, "description") or _item_field(item, "summary")
        )[:280]
        ts = _parse_pubdate(
            _item_field(item, "pubDate") or _item_field(item, "updated"), fetched_at
        )
        out.append(
            NewsEvent(
                id=_rss_news_id(link),
                source=f"rss:{feed_name}",
                source_url=link,
                ts=ts,
                title=title,
                summary=summary,
                confidence=RSS_BASE_CONFIDENCE,
                entities=[],
                attrs={"feed": feed_name, "feed_url": feed_url},
            )
        )
    return out


# ── 라이브 fetch ─────────────────────────────────────────────────────────────


def fetch_feed(client: httpx.Client, feed_name: str, url: str) -> Optional[bytes]:
    """RSS 피드 1회 fetch(2초 규율 준수). 바이트 반환 또는 None(오류·과대 응답).

    연결 오류·비정상 상태·크기 초과는 삼켜서 None — 한 피드 실패가 ingest 전체를 죽이지 않게.
    """
    _rate_limit_guard()
    try:
        resp = client.get(url, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        print(f"[rss][{feed_name}] 연결 오류(건너뜀): {e!r}")
        return None
    print(f"[rss][{feed_name}] HTTP {resp.status_code} {url}")
    if resp.status_code != 200:
        print(f"[rss][{feed_name}] 비정상 응답: {resp.text[:120]!r}")
        return None
    content = resp.content
    if len(content) > MAX_RESPONSE_BYTES:
        print(
            f"[rss][{feed_name}] 응답 과대({len(content)}B > {MAX_RESPONSE_BYTES}B) → 거부"
        )
        return None
    return content


def newsapi_enabled() -> bool:
    """NewsAPI 활성 여부 — NEWSAPI_KEY 있을 때만. 기본 no-op(키 필요 소스라 미구현).

    RSS로 '키 불요 보조 뉴스' 목표를 이미 충족하므로 NewsAPI fetch는 구현하지 않는다
    (스텁 가드만 유지 — 키가 생기면 여기서 분기). 사유: docs/worklog/news-enrich.md.
    """
    return bool(os.environ.get("NEWSAPI_KEY"))


# ── ingest ────────────────────────────────────────────────────────────────────


def ingest(store: LocalOntologyStore) -> dict[str, int]:
    """1 ingest 사이클: 선정 피드 각 1회 fetch → NewsEvent + 공용 링킹 mentions.

    시작 시 오퍼레이터 시드 적재 + 실존 Aircraft 인덱스 구성(무존재 링크 방지).
    반환: {피드명: write수, ..., 'mentions': 총 mentions, 'total': 총 write}.
    """
    entity_linking.ensure_operator_seeds(store)
    aircraft_index = entity_linking.build_aircraft_index(store)

    counts: dict[str, int] = {}
    total_mentions = 0
    with httpx.Client(
        follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        for feed_name, url in FEEDS.items():
            content = fetch_feed(client, feed_name, url)
            if content is None:
                counts[feed_name] = 0
                continue
            written = 0
            for nv in parse_feed(content, feed_name, url):
                mentions = entity_linking.link_newsevent(
                    nv,
                    aircraft_index=aircraft_index,
                    base_confidence=RSS_BASE_CONFIDENCE,
                )
                store.write_newsevent(nv, mentions=mentions)
                written += 1
                total_mentions += len(mentions)
            counts[feed_name] = written

    if newsapi_enabled():
        print(
            "[rss] NEWSAPI_KEY 감지 — NewsAPI 연동은 미구현(스텁). 사유: news-enrich.md"
        )

    counts["mentions"] = total_mentions
    counts["total"] = sum(
        v for k, v in counts.items() if k not in ("mentions", "total")
    )
    return counts


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    from ontology.model import KADIZ_REGION

    store.write_region(KADIZ_REGION)
    counts = ingest(store)
    per_feed = {k: v for k, v in counts.items() if k not in ("mentions", "total")}
    print(
        f"[rss] NewsEvent write={counts.get('total')} mentions={counts.get('mentions')} "
        f"피드별={per_feed} 누적={store.counts()}"
    )


if __name__ == "__main__":
    main()
