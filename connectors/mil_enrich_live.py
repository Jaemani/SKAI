"""connectors/mil_enrich_live.py — 라이브 군용 식별 보강 (DR-0013 결정 5).

`anomaly.mil_enrich.MilEnrichmentSource`의 라이브 구현. 무료·공개 2차 ADS-B 네트워크(adsb.fi)의
**군용 DB 플래그**로 라이브 항적의 군용 여부를 저신뢰 보강한다. `crosscheck_live`(dropout 부재
교차판정)의 자매 모듈 — 같은 소스·같은 리밋 규율, 다른 질문("이 hex가 군용으로 DB 플래그돼 있나?").

## 소스 & 실측 근거 (2026-07-05 실검, 워크로그 docs/worklog/mil-enrich.md 인용)
- **adsb.fi `/v2/mil`** — 무인증, **1 req/s**. adsb.fi opendata 공식 문서: "Returns aircraft
  marked as military". 실호출 결과 HTTP 200, `ac` 배열 94기, **각 entry에 `dbFlags` 필드 존재**,
  전부 `dbFlags == 1`. `/v2/hex/<hex>` 응답에도 동일 `dbFlags`가 실림(실호출 ae0679 → dbFlags 1).
- **dbFlags 의미**(readsb `README-json.md` 문서): 비트필드 — `military = dbFlags & 1`
  (bit0=military, bit1=interesting, bit2=PIA, bit3=LADD). 우리는 **bit0만** 군용 신호로 채택.
  추측이 아니라 문서·실측 근거다(엔드포인트 이름이 mil이어도 문서화된 비트로 이중 확인).
- **정직한 한계**: 커뮤니티 DB라 오탐·미탐이 있다. 실측 스냅샷에 민간 등록기(N342TA, Piper PA-28)가
  dbFlags=1로 섞여 있었다 → 오탐 사례. 그래서 confidence를 저신뢰 상한(≤0.65)에 둔다.

## 설계 — 옵션 A(스냅샷 폴링) 채택
`/v2/mil`은 전 세계 군용 플래그 기체를 **1회 호출로 전량** 반환(≈94기, 38KB). hex별 질의(옵션 B,
crosscheck 패턴)는 후보 수만큼 호출이 늘지만, 이 스냅샷은 refresh_ttl(기본 60s)마다 **1회** 폴링해
군용 hex 집합을 캐시하고 bbox 내 항적을 O(1) 집합조회로 대조한다 → 호출량 = 최대 1 req / 60s
(추적 hex 수와 무관, 옵션 B 대비 압도적으로 적음). 실패는 격리 — 스냅샷이 없으면 lookup은 None
(신호 없음)이라 콜사인·대역 휴리스틱 경로가 그대로 판정한다.

## 리밋 규율
- `/v2/mil` 폴링을 refresh_ttl(기본 60s) 간격으로 강제. **성공·실패 무관**하게 마지막 시도 후
  60s 이내면 재호출하지 않는다(429·오류 폭주 방지). 최대 1 req / 60s.
- httpx.Client 주입 가능(테스트는 MockTransport로 네트워크 없이 검증). 폴러는 **1 인스턴스를
  사이클 간 재사용**해야 캐시·리밋 상태가 유지된다.

## ToS (adsb.fi opendata 문서)
개인·비상업/교육용만. 데이터 라이선스·판매·임대 금지. **adsb.fi 인용 + 홈페이지 링크 필수**.
해커톤 데모 = 비상업·교육 → 허용. UI 크레딧 표기 필요(프론트 몫 — 워크로그에 명시).

## 게이트
`make_mil_enrichment()`는 **SKAI_MIL_ENRICH=live** 일 때만 라이브, 기본 Null(신호 없음).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import httpx

from anomaly.mil_enrich import MilEnrichmentSource, NullMilEnrichment

# 2차 소스 base URL (SSOT). /v2/mil·/v2/hex 경로는 readsb 계열 API 공통.
SOURCE_BASES = {
    "adsbfi": "https://opendata.adsb.fi/api",
    # airplanes.live도 동일 readsb 계열(/v2/mil·dbFlags) 제공(옵션). 기본 adsbfi(ToS verbatim 확인).
    "airplaneslive": "https://api.airplanes.live",
}
DEFAULT_SOURCE = "adsbfi"

DEFAULT_TIMEOUT = 6.0  # 초 — 느린 2차 소스가 폴러 사이클을 오래 막지 않게
REFRESH_TTL = 60.0  # /v2/mil 스냅샷 캐시 수명 = 폴링 최소 간격(초). 최대 1 req/60s.
MIL_DB_BIT = (
    0b1  # dbFlags bit0 = military (readsb README-json.md: `military = dbFlags & 1`)
)

# 판정 근거 문구(provenance) — explainer가 그대로 서술하므로 출처가 문장까지 전파된다.
MIL_DB_REASON = "adsb.fi 커뮤니티 ADS-B DB 군용 플래그(dbFlags & 1)"

# API 시민성: 앱 식별 UA(과도요청 시 운영자가 식별·연락 가능).
_USER_AGENT = (
    "SKAI-AirISR/1.0 (hackathon; military DB-flag enrich; contact via project)"
)


class LiveMilEnrichment:
    """adsb.fi `/v2/mil` 스냅샷을 주기 폴링해 군용 hex 집합을 캐시하고 hex 조회를 제공.

    옵션 A(스냅샷) — 전 세계 군용 플래그 기체를 1회 호출로 받아 집합조회. 캐시·리밋 상태를
    인스턴스가 보유하므로 폴러는 **1개 인스턴스를 사이클 간 재사용**해야 호출이 절감된다.
    """

    def __init__(
        self,
        source: str = DEFAULT_SOURCE,
        client: Optional[httpx.Client] = None,
        timeout: float = DEFAULT_TIMEOUT,
        refresh_ttl: float = REFRESH_TTL,
    ):
        self.source = source if source in SOURCE_BASES else DEFAULT_SOURCE
        self.base = SOURCE_BASES[self.source]
        self._own_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )
        self.timeout = timeout
        self.refresh_ttl = refresh_ttl
        self._mil_hexes: set[str] = set()  # 군용 플래그 hex(소문자) 집합
        self._last_attempt = 0.0  # monotonic — 성공·실패 무관 폴링 최소 간격 강제
        self._fetched_at = 0.0  # 마지막 성공 스냅샷 시각(관측용)
        self._lock = threading.Lock()
        self.calls = 0  # 실 HTTP 호출 수(검증·관측용)
        self.refreshes = 0  # 스냅샷 갱신 성공 횟수

    # ── MilEnrichmentSource 프로토콜 ──────────────────────────────────────────
    def lookup(self, icao24: str) -> Optional[tuple[bool, str]]:
        """hex가 군용 스냅샷에 있으면 (True, 근거문구), 없으면/미확인이면 None.

        부재는 군용 아님 단정이 아니다 — 트랜스폰더 OFF이거나 DB 미수록일 수 있으므로
        (False, ...)를 반환하지 않는다. 콜사인·대역 휴리스틱이 이어서 판정한다.
        """
        hexid = (icao24 or "").strip().lower()
        if not hexid:
            return None
        self._refresh_if_stale()
        with self._lock:
            hit = hexid in self._mil_hexes
        if hit:
            return (True, MIL_DB_REASON)
        return None

    # ── 내부 ──────────────────────────────────────────────────────────────
    def _refresh_if_stale(self) -> None:
        """스냅샷이 오래됐으면(또는 최초) 1회 폴링. 리밋: 시도 간 최소 refresh_ttl 간격."""
        with self._lock:
            now = time.monotonic()
            # 성공 여부와 무관하게 refresh_ttl 이내면 재호출 안 함(리밋·에러 폭주 방지).
            if self._last_attempt and (now - self._last_attempt) < self.refresh_ttl:
                return
            self._last_attempt = now
            snapshot = self._fetch_mil()
            if snapshot is not None:
                self._mil_hexes = snapshot
                self._fetched_at = now
                self.refreshes += 1
            # 실패 시 직전 스냅샷 유지(있으면). 최초 실패면 빈 집합 → lookup None(휴리스틱 폴백).

    def _fetch_mil(self) -> Optional[set[str]]:
        """`/v2/mil` 1회 조회 → 군용(dbFlags&1) hex 집합. 예외·비200·이상응답은 None(미확인)."""
        url = f"{self.base}/v2/mil"
        try:
            self.calls += 1
            resp = self._client.get(url, timeout=self.timeout)
        except Exception:
            return None  # 네트워크·타임아웃 → 미확인(직전 스냅샷 유지)
        if resp.status_code != 200:
            return None  # 429/4xx/5xx → 미확인(리밋·오류에 단정 안 함)
        try:
            data = resp.json()
        except Exception:
            return None
        ac = data.get("ac") if isinstance(data, dict) else None
        if not isinstance(ac, list):
            return None
        hexes: set[str] = set()
        for entry in ac:
            if not isinstance(entry, dict):
                continue
            hexid = (entry.get("hex") or "").strip().lower()
            if not hexid:
                continue
            try:
                flags = int(entry.get("dbFlags"))
            except (TypeError, ValueError):
                continue  # dbFlags 없음·비정수 → 이 entry는 군용 플래그로 못 씀
            # 엔드포인트가 /v2/mil이어도 문서화된 bit0으로 이중 확인(dbFlags & 1).
            if flags & MIL_DB_BIT:
                hexes.add(hexid)
        return hexes

    def close(self) -> None:
        if self._own_client:
            self._client.close()


def make_mil_enrichment(env: Optional[dict] = None) -> MilEnrichmentSource:
    """게이트 팩토리 — SKAI_MIL_ENRICH=live 일 때만 라이브 보강, 기본 Null(신호 없음).

    기본이 Null인 이유: 라이브 네트워크 의존 없이도 콜사인·대역 휴리스틱 판정은 낸다(안정성).
    라이브 켤 때 SKAI_MIL_ENRICH_SOURCE(adsbfi|airplaneslive)로 소스 선택(기본 adsbfi).
    """
    env = env if env is not None else os.environ
    mode = (env.get("SKAI_MIL_ENRICH") or "").strip().lower()
    if mode != "live":
        return NullMilEnrichment()
    source = (env.get("SKAI_MIL_ENRICH_SOURCE") or DEFAULT_SOURCE).strip().lower()
    return LiveMilEnrichment(source=source)
