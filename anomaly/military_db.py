"""anomaly/military_db.py — 군용기 저신뢰 판정 사전 (공개 지식 기반).

DR-0007 결정 2: 군용 판정은 **저신뢰 휴리스틱**이다. OpenSky 익명 피드엔 is_military
표식이 없으므로(P1 발견 #4), 공개적으로 알려진 군 콜사인 프리픽스·군용 예약 icao24
대역으로 "군용 추정"만 한다. 단정하지 않는다(CLAUDE.md: 대역은 국가 할당이지 군용
표식이 아님 → 오탐 양산). 그래서 confidence 상한을 낮게(≤0.65) 둔다.

데모 기본은 합성 군용기 주입(is_military=True 명시). 이 사전은 라이브 관측에 대한
보조 저신뢰 신호다.

## 출처 (공개 지식)
- **군 콜사인 프리픽스**: 항공 애호가·ADS-B 커뮤니티에 널리 문서화된 미군/다국적 군
  수송·특수 콜사인 접두어(예: RCH=Reach/미 공수, CNV=미 해군, PAT=미 육군 우선수송).
  회사(항공사) 콜사인과 겹치지 않는 것만 보수적으로 수록. 완전하지 않음(휴리스틱).
- **군용 예약 icao24 대역**: ICAO 24비트 주소는 국가 블록으로 할당되며, 미국은
  `0xAE0000`–`0xAFFFFF`를 정부/군용으로 예약한 것으로 널리 알려져 있다(ADS-B 커뮤니티
  문서화). 국가 블록이므로 군용 "표식"이 아니라 정황 → 저신뢰.
"""

from __future__ import annotations

from typing import Optional

from anomaly.mil_enrich import MilEnrichmentSource

# 군 콜사인 프리픽스 → 설명(의미). 대문자, 콜사인 앞부분과 매칭.
# 상용 항공사 콜사인과 충돌하지 않도록 보수적으로 수록(휴리스틱, 완전하지 않음).
MILITARY_CALLSIGN_PREFIXES: dict[str, str] = {
    "RCH": "Reach (미 공중기동사령부 수송)",
    "CNV": "미 해군",
    "PAT": "미 육군 우선항공수송",
    "SPAR": "미 공군 특수공수(VIP)",
    "EVAC": "항공 후송",
    "GRZLY": "군 훈련/작전",
    "SENTRY": "E-3 조기경보(AWACS)",
    "DRAGON": "정찰/특수",
    "HERKY": "C-130 수송",
    "ROKAF": "대한민국 공군",
    "KAF": "대한민국 공군(약칭)",
}

# 군용 예약 icao24 대역 [(lo, hi), ...] (16진 정수, 포함구간). 공개 문서화 기반.
# 국가 블록 = 군용 표식 아님 → 저신뢰(단정 금지).
MILITARY_ICAO24_RANGES: list[tuple[int, int, str]] = [
    (0xAE0000, 0xAFFFFF, "미국 정부/군용 예약 대역"),
]

# 군용 판정 신뢰도 상한(저신뢰 휴리스틱). 콜사인·대역 둘 다 걸려도 0.65를 넘지 않음.
_CONF_CALLSIGN = 0.55
_CONF_ICAO24 = 0.5
_CONF_BOTH = 0.65


def _callsign_prefix_match(callsign: Optional[str]) -> Optional[str]:
    """콜사인이 군 프리픽스로 시작하면 그 프리픽스 반환(없으면 None)."""
    if not callsign:
        return None
    cs = callsign.strip().upper()
    # 긴 프리픽스 우선(예: ROKAF vs KAF 오인 방지)
    for prefix in sorted(MILITARY_CALLSIGN_PREFIXES, key=len, reverse=True):
        if cs.startswith(prefix):
            return prefix
    return None


def _icao24_range_match(icao24: Optional[str]) -> Optional[str]:
    """icao24(16진 문자열)가 군용 예약 대역이면 설명 반환(없으면 None)."""
    if not icao24:
        return None
    try:
        v = int(icao24, 16)
    except (ValueError, TypeError):
        return None  # 합성 icao24(예: "synth01") 등 16진 아님 → 대역 판정 불가
    for lo, hi, desc in MILITARY_ICAO24_RANGES:
        if lo <= v <= hi:
            return desc
    return None


def classify_military(
    icao24: Optional[str], callsign: Optional[str]
) -> tuple[bool, float, str]:
    """군용 여부를 **저신뢰**로 추정. 반환 (is_military, confidence, reason).

    콜사인 프리픽스·군용 예약 대역 중 하나라도 걸리면 저신뢰 True. 둘 다면 소폭 상향(≤0.65).
    아무것도 안 걸리면 (False, 0.0, "").  단정이 아니라 "추정"임을 reason이 명시한다.
    """
    cs_match = _callsign_prefix_match(callsign)
    icao_match = _icao24_range_match(icao24)

    if cs_match and icao_match:
        return (
            True,
            _CONF_BOTH,
            f"군 콜사인 프리픽스 '{cs_match}'({MILITARY_CALLSIGN_PREFIXES[cs_match]}) "
            f"+ {icao_match}",
        )
    if cs_match:
        return (
            True,
            _CONF_CALLSIGN,
            f"군 콜사인 프리픽스 '{cs_match}'({MILITARY_CALLSIGN_PREFIXES[cs_match]})",
        )
    if icao_match:
        return (True, _CONF_ICAO24, icao_match)
    return (False, 0.0, "")


def resolve_is_military(
    existing_is_military: bool,
    icao24: Optional[str],
    callsign: Optional[str],
    mil_enrich: Optional[MilEnrichmentSource] = None,
) -> bool:
    """Aircraft.is_military에 **영속**시킬 종합판정(단조 — 한 번 True면 계속 True).

    이상탐지(detect_military_approach)는 매 스캔마다 즉석 판정하지만, 지도가 항공기를
    군용으로 구분하려면 Aircraft 레코드 자체에 판정이 남아야 한다(그래야 OpArea 밖 항적도
    /api/observations에서 구분됨). write_aircraft가 INSERT OR REPLACE라 이 함수가 계산한
    값을 매 write에 실어야 판정이 소실되지 않는다(호출측 ingest_cycle 책임).

    우선순위: 기존 True(합성 주입·이전 판정) 불변 > mil_enrich 공개 DB 플래그
    > 콜사인·대역 휴리스틱. detect_military_approach와 같은 신호원이지만 여긴 근거문구 없이
    boolean만 낸다(근거는 이상탐지 쪽 signal.mil_reason이 이미 다룬다).
    """
    if existing_is_military:
        return True
    if mil_enrich is not None:
        if mil_enrich.lookup(icao24 or "") is not None:
            return True
    is_mil, _, _ = classify_military(icao24, callsign)
    return is_mil
