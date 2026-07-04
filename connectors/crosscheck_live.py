"""connectors/crosscheck_live.py — 라이브 2차 소스 교차 dropout 판정 (DR-0007 결정 1).

CLAUDE.md 기술기준: **dropout 단정은 복수 소스로.** 단일 소스 결측은 송신기/수신 커버리지
문제일 수 있으므로 단정하지 않는다. 이 모듈은 `anomaly.crosscheck.CrossCheckSource`의
라이브 구현 — 무료·공개 2차 ADS-B 네트워크(adsb.fi)에 "그 기체가 지금 관측되는가?"를 물어
1차 소스(OpenSky)의 결측을 교차 판정한다.

## 소스 & ToS (2026-07-04 실검)
- **adsb.fi**(기본): `https://opendata.adsb.fi/api/v2/hex/<hex>` — 무인증, **1 req/s**,
  **개인·비상업/교육용 허용**(라이선스·판매·임대 금지, adsb.fi 인용 필수). 해커톤 데모는
  비상업·교육 사용 → ToS 명확 허용. 인용 요건은 UI/워크로그에 명시.
- **airplanes.live**(옵션, SKAI_CROSSCHECK_SOURCE=airplaneslive): 동일 `/v2/hex/` 인터페이스,
  1 req/s, 비상업/교육용. ToS 페이지가 봇 차단(403)이라 verbatim 미확인 → 기본 아님(옵션).
- **ADS-B Exchange**: 2025-03 무료 폐지·RapidAPI 유료 전환 → **후보 탈락**(가드레일: 무료 공개만).

## confirm_absence(icao24, window) 반환 의미 (crosscheck.py 계약 준수)
- **False** = 2차 소스가 그 hex를 **지금 신선하게 관측 중** → 우리 결측은 센서 아티팩트 → dropout 아님.
- **True**  = 2차 소스도 그 hex를 **관측 못 함**(응답 `ac` 비어있음) → 부재 교차 확인 → 신뢰도 상향.
- **None**  = 미확인(HTTP 오류·429·타임아웃·응답 이상, 또는 관측되나 stale) → 저신뢰 후보 유지(단정 금지).

## 정직한 한계 (워크로그에 명시)
- 무료 API는 **현재 스냅샷**만 준다(임의 과거 window 조회 불가). 따라서 판정은 호출 시점(now)의
  "2차 소스가 이 hex를 지금 보는가"이다. dropout 후보는 `last_seen`이 gap window 안(=최근)일 때만
  발화하므로 now 스냅샷 교차확인은 타당한 근사지만, "window 전체 부재"를 단정하진 않는다.

## 리밋 규율
- 실 HTTP 호출 사이 **최소 간격**(min_interval, 기본 1.05s = 1req/s 여유) 강제.
- **TTL 캐시**(기본 30s)로 같은 hex 재질의 억제(dropout 후보는 사이클마다 재등장 → 캐시로 호출 절감).
- dropout **후보당 1회**만 질의(룰이 후보에 대해서만 confirm_absence 호출).
- 기본 게이트 off: `make_crosscheck()`는 **SKAI_CROSSCHECK=live** 일 때만 이 소스를 쓴다(기본 Null).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import httpx

from anomaly.crosscheck import CrossCheckSource, NullCrossCheckSource

# 2차 소스 base URL (SSOT). hex 조회 경로는 두 소스 모두 /v2/hex/<hex>.
SOURCE_BASES = {
    "adsbfi": "https://opendata.adsb.fi/api",
    "airplaneslive": "https://api.airplanes.live",
}
DEFAULT_SOURCE = "adsbfi"

DEFAULT_TIMEOUT = 6.0  # 초 — 느린 2차 소스가 폴러 사이클을 오래 막지 않게
MIN_INTERVAL = 1.05  # 실 호출 최소 간격(초) — 1 req/s 리밋 존중(+5% 여유)
CACHE_TTL = 30.0  # hex 판정 캐시 수명(초) — 같은 후보 재질의 억제
FRESH_SECONDS = (
    60.0  # 2차 소스 관측 신선도 상한 — seen이 이보다 크면 present로 보지 않음
)

# API 시민성: 앱 식별 UA(과도요청 시 운영자가 식별·연락 가능).
_USER_AGENT = "SKAI-AirISR/1.0 (hackathon; dropout cross-check; contact via project)"


class LiveCrossCheckSource:
    """무료 2차 ADS-B 네트워크(adsb.fi 기본)로 dropout 부재를 교차 판정.

    httpx.Client는 주입 가능(테스트는 MockTransport로 네트워크 없이 검증). 캐시·레이트리밋
    상태를 인스턴스가 보유하므로 폴러는 **1개 인스턴스를 사이클 간 재사용**해야 호출이 절감된다.
    """

    def __init__(
        self,
        source: str = DEFAULT_SOURCE,
        client: Optional[httpx.Client] = None,
        timeout: float = DEFAULT_TIMEOUT,
        min_interval: float = MIN_INTERVAL,
        cache_ttl: float = CACHE_TTL,
        fresh_seconds: float = FRESH_SECONDS,
    ):
        self.source = source if source in SOURCE_BASES else DEFAULT_SOURCE
        self.base = SOURCE_BASES[self.source]
        self._own_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )
        self.timeout = timeout
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl
        self.fresh_seconds = fresh_seconds
        # hex(소문자) → (판정 시각, 결과) 캐시. 레이트리밋·통계.
        self._cache: dict[str, tuple[float, Optional[bool]]] = {}
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.calls = 0  # 실 HTTP 호출 수(검증·관측용)
        self.cache_hits = 0

    # ── CrossCheckSource 프로토콜 ──────────────────────────────────────────
    def confirm_absence(self, icao24: str, window: tuple[int, int]) -> Optional[bool]:
        """2차 소스에 hex 현재 관측 여부 질의 → True(부재확인)/False(관측중)/None(미확인).

        window는 계약·로깅용(마지막 관측~now). 무료 API는 과거 조회 불가라 실판정은
        호출 시점 스냅샷이다(모듈 docstring '정직한 한계' 참조).
        """
        hexid = (icao24 or "").strip().lower()
        if not hexid:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(hexid)
            if cached is not None and (now - cached[0]) < self.cache_ttl:
                self.cache_hits += 1
                return cached[1]
            # 레이트리밋 — 직전 실 호출과 최소 간격 확보.
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            result = self._query(hexid)
            self._last_call = time.monotonic()
            self._cache[hexid] = (self._last_call, result)
            return result

    # ── 내부 ──────────────────────────────────────────────────────────────
    def _query(self, hexid: str) -> Optional[bool]:
        """1회 실 조회. 예외·비200·이상응답은 모두 None(미확인)으로 격리(단정 금지)."""
        url = f"{self.base}/v2/hex/{hexid}"
        try:
            self.calls += 1
            resp = self._client.get(url, timeout=self.timeout)
        except Exception:
            return None  # 네트워크·타임아웃 → 미확인
        if resp.status_code != 200:
            return None  # 429/4xx/5xx → 미확인(리밋·오류에 단정 안 함)
        try:
            data = resp.json()
        except Exception:
            return None
        ac = data.get("ac") if isinstance(data, dict) else None
        if not ac:  # None 또는 빈 배열 = 2차 소스도 이 hex 미관측 → 부재 교차 확인
            return True
        # 응답에 hex가 있으면 신선도 확인 — 최근(seen ≤ 상한) 관측이 하나라도 있으면 '관측 중'.
        for entry in ac:
            if not isinstance(entry, dict):
                continue
            if (entry.get("hex") or "").strip().lower() != hexid:
                continue
            seen = entry.get("seen")
            try:
                if seen is not None and float(seen) <= self.fresh_seconds:
                    return False  # 2차 소스가 지금 관측 중 → dropout 아님
            except (TypeError, ValueError):
                continue
        # hex는 응답에 있으나 stale(오래된 seen)뿐 → 애매 → 미확인(과잉단정 회피).
        return None

    def close(self) -> None:
        if self._own_client:
            self._client.close()


def make_crosscheck(env: Optional[dict] = None) -> CrossCheckSource:
    """게이트 팩토리 — SKAI_CROSSCHECK=live 일 때만 라이브 2차 소스, 기본 Null(미확인).

    기본이 Null인 이유: 크레딧·안정성(라이브 2차 호출 없이도 저신뢰 dropout 후보는 낸다).
    라이브 켤 때 SKAI_CROSSCHECK_SOURCE(adsbfi|airplaneslive)로 소스 선택(기본 adsbfi).
    """
    env = env if env is not None else os.environ
    mode = (env.get("SKAI_CROSSCHECK") or "").strip().lower()
    if mode != "live":
        return NullCrossCheckSource()
    source = (env.get("SKAI_CROSSCHECK_SOURCE") or DEFAULT_SOURCE).strip().lower()
    return LiveCrossCheckSource(source=source)
