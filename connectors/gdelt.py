"""GDELT 커넥터 — 글로벌 뉴스 인덱스 → NewsEvent(저신뢰) → 온톨로지.

파이프: GDELT doc API(괄호 OR 쿼리) → NewsEvent(confidence ≤ 0.4)
        + mentions→ Region 키워드 링킹(별칭 사전) / mentions→ Aircraft(DB 콜사인 exact match).

P0A gotcha 반영:
  - **OR 쿼리는 전체를 괄호로 감쌈** — 안 감싸면 HTTP 200 + 텍스트 오류(JSON 아님).
  - **요청 간격 5초 강제**(코드 레벨 — 우회 아님, 준수 자동화). 버스트 시 IP 429.
  - `articles` null 처리(`data.get("articles") or []`), 텍스트 오류 응답 방어.

뉴스는 확증이 아니다(DR-0005): confidence ≤ NEWS_MAX_CONFIDENCE(0.4), 하드 소스로 교차검증.
폴링 주기(architecture.md): 뉴스 5분. 단 검증 실행은 1회 ingest(쿼리 2회, 5초 간격 준수).
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ontology.model import NEWS_MAX_CONFIDENCE, KADIZ_REGION, NewsEvent
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT = 30

GDELT_POLL_INTERVAL = 5 * 60  # architecture.md: 뉴스 5분
GDELT_MIN_REQUEST_INTERVAL = 5.0  # P0A: 요청 간 최소 5초(코드 강제)
DEFAULT_TIMESPAN = "7d"
DEFAULT_MAXRECORDS = 15

# 지역명 별칭 사전 (DR-0005: NER 대신 키워드 링킹). region_id → 별칭 리스트.
REGION_ALIASES: dict[str, list[str]] = {
    "KADIZ": [
        "KADIZ",
        "Korea Air Defense Identification Zone",
        "한국방공식별구역",
        "Korean air defense",
        "South Korea air defense",
        "Korea airspace",
        # GDELT 쿼리가 한반도 공역으로 스코프됨 → 아래 일반 ADIZ 표현도 KADIZ로 링킹(실측 회수).
        "air defense zone",
        "defense identification zone",
        "한반도",
        "Korean Peninsula",
        "서해",
        "West Sea",
        "Yellow Sea",
        "동중국해",
        "East China Sea",
        "대한해협",
        "Korea Strait",
    ],
}
# GDELT 쿼리에 쓸 강한 키워드(영문 편향 → 영문 OR 조합, P0A).
# 좁은 인용구(예: "Korea airspace")는 artlist에서 자주 null → 실측(2026-07-04)으로
# KADIZ/한반도 공역 기사를 회수하는 조합으로 조정. mentions 링킹은 별도 별칭 사전이 담당.
_QUERY_TERMS = [
    "KADIZ",
    "Korea Air Defense Identification Zone",
    "South Korea military aircraft",
    "Korean Peninsula airspace",
]

# ── 5초 규율 (모듈 레벨 상태로 호출 간격 강제) ────────────────────────────────
_last_request_ts = 0.0


def _rate_limit_guard() -> None:
    """직전 요청 이후 GDELT_MIN_REQUEST_INTERVAL(5초)이 안 지났으면 그만큼 sleep.

    레이트리밋 우회가 아니라 준수 자동화 — 버스트로 IP 429를 부르지 않도록 코드가 강제한다.
    """
    global _last_request_ts
    now = time.monotonic()
    elapsed = now - _last_request_ts
    if _last_request_ts > 0 and elapsed < GDELT_MIN_REQUEST_INTERVAL:
        wait = GDELT_MIN_REQUEST_INTERVAL - elapsed
        print(f"[gdelt] 5초 규율: 직전 요청 {elapsed:.1f}s 전 → {wait:.1f}s 대기")
        time.sleep(wait)
    else:
        print(f"[gdelt] 5초 규율: 대기 불필요(직전 {elapsed:.1f}s 전)")
    _last_request_ts = time.monotonic()


def build_query(terms: Optional[list[str]] = None) -> str:
    """괄호로 감싼 OR 쿼리 문자열(P0A gotcha 5)."""
    terms = terms or _QUERY_TERMS
    return "(" + " OR ".join(f'"{t}"' for t in terms) + ")"


def match_region_aliases(title: str) -> list[str]:
    """제목에서 KADIZ 별칭 매칭(대소문자 무시) → 매칭된 별칭 리스트."""
    if not title:
        return []
    low = title.lower()
    return [a for a in REGION_ALIASES["KADIZ"] if a.lower() in low]


def _parse_seendate(seendate: str, fallback_ts: int) -> int:
    """'YYYYMMDDTHHmmssZ' → Unix ts. 실패 시 fallback_ts."""
    try:
        dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return fallback_ts


def _news_id(url: str) -> str:
    return "news-" + hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def gdelt_response_to_news(
    data: Optional[dict], source_url: str, fetched_at: Optional[int] = None
) -> list[NewsEvent]:
    """GDELT 응답(dict) → NewsEvent 리스트 (순수 매핑 — 테스트 대상).

    - `articles` null/부재 → [] (P0A gotcha 7).
    - confidence: 기본 0.3, 지역 별칭 매칭 시 0.35(모두 ≤ NEWS_MAX_CONFIDENCE=0.4).
    - entities = 제목에서 매칭된 지역 별칭.
    """
    fetched_at = fetched_at if fetched_at is not None else int(time.time())
    articles = (data or {}).get("articles") or []
    out: list[NewsEvent] = []
    for a in articles:
        url = a.get("url") or ""
        if not url:
            continue
        title = a.get("title") or ""
        aliases = match_region_aliases(title)
        confidence = 0.35 if aliases else 0.30
        out.append(
            NewsEvent(
                id=_news_id(url),
                source="gdelt",
                source_url=url,
                ts=_parse_seendate(a.get("seendate", ""), fetched_at),
                title=title,
                summary="",
                confidence=min(confidence, NEWS_MAX_CONFIDENCE),
                entities=aliases,
                attrs={
                    "domain": a.get("domain"),
                    "language": a.get("language"),
                    "source_country": a.get("sourcecountry"),
                },
            )
        )
    return out


def fetch_articles(
    client: httpx.Client,
    query: str,
    timespan: str = DEFAULT_TIMESPAN,
    maxrecords: int = DEFAULT_MAXRECORDS,
) -> Optional[dict]:
    """GDELT 1회 호출(5초 규율 준수). JSON dict 반환 또는 None(오류/레이트리밋).

    연결 오류(RemoteProtocolError·타임아웃 등)는 삼켜서 None 반환 — 한 쿼리 실패가
    ingest 전체를 죽이지 않게 한다(GDELT 서버는 간헐적으로 응답 없이 끊긴다).
    """
    _rate_limit_guard()
    try:
        resp = client.get(
            GDELT_URL,
            params={
                "query": query,
                "mode": "artlist",
                "maxrecords": str(maxrecords),
                "format": "json",
                "timespan": timespan,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        print(f"[gdelt] 연결 오류(쿼리 건너뜀): {e!r}")
        return None
    print(f"[gdelt] HTTP {resp.status_code} query={query!r} timespan={timespan}")
    if resp.status_code == 429:
        print("[gdelt] 429 레이트리밋 — 이번 사이클 건너뜀(우회 안 함)")
        return None
    if resp.status_code != 200:
        print(f"[gdelt] 비정상 응답: {resp.text[:120]!r}")
        return None
    text = resp.text.strip()
    # GDELT 오류는 HTTP 200 + 텍스트(JSON 아님) → 파싱 전 방어(P0A gotcha 5).
    if not text or text[0] not in "{[":
        print(f"[gdelt] JSON 아님(텍스트 오류): {text[:120]!r}")
        return None
    try:
        return resp.json()
    except Exception as e:
        print(f"[gdelt] JSON 파싱 실패: {e!r}")
        return None


def ingest(store: LocalOntologyStore) -> tuple[int, int]:
    """1 ingest 사이클: 괄호 OR 쿼리 1회 → NewsEvent + mentions 링크.

    mentions→Region: 제목의 지역 별칭. mentions→Aircraft: DB 콜사인 exact match(교차검증).
    쿼리는 1회만(GDELT는 1req/5s를 엄격 적용 — 버스트 시 429). 5초 규율은 fetch_articles가
    호출마다 강제하며, 폴링 사이클(5분) 간에도 유지된다.
    반환: (NewsEvent write 수, mentions 링크 수).
    """
    # DB 콜사인 → icao24 (엔티티 링킹 대상, exact match). 대문자 정규화.
    callsign_to_icao = {
        (ac.callsign or "").upper(): ac.icao24
        for ac in store.query_aircraft()
        if ac.callsign
    }

    articles: dict[str, NewsEvent] = {}
    with httpx.Client() as client:
        data = fetch_articles(client, build_query())
        for nv in gdelt_response_to_news(data, GDELT_URL):
            articles.setdefault(nv.id, nv)  # url 해시로 dedup

    n_news = n_mentions = 0
    for nv in articles.values():
        mentions: list[tuple] = []
        if nv.entities:  # 지역 별칭 매칭 → mentions→Region
            mentions.append(("Region", KADIZ_REGION.id))
        # 제목에 DB 콜사인이 그대로 등장하면 → mentions→Aircraft(하드 소스 교차)
        title_upper = nv.title.upper()
        matched_ac = [
            icao
            for cs, icao in callsign_to_icao.items()
            if cs and f" {cs} " in f" {title_upper} "
        ]
        for icao in matched_ac:
            mentions.append(("Aircraft", icao))
        # 지역+항공기 모두 매칭 시 소폭 상향(여전히 ≤ 0.4).
        if nv.entities and matched_ac:
            nv.confidence = min(0.4, nv.confidence + 0.05)
        store.write_newsevent(nv, mentions=mentions)
        n_news += 1
        n_mentions += len(mentions)
    return n_news, n_mentions


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    store.write_region(KADIZ_REGION)
    n_news, n_mentions = ingest(store)
    print(
        f"[gdelt] NewsEvent write={n_news} mentions={n_mentions} 누적={store.counts()}"
    )


if __name__ == "__main__":
    main()
