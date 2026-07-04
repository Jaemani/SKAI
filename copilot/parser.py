"""copilot/parser.py — 결정적 질의 파서 (지역 + 시간창).

DR-0006 결정 2: 질의 파싱은 결정적 파서가 기본. LLM 파싱은 비목표(데모 질의가
한정적이라 과설계, 결정적 파서가 재현성 우위). 파싱 결과(region, window)는 응답에
그대로 노출한다(투명성 — 사용자가 "무엇으로 해석됐나"를 검증 가능).

파싱 대상:
  - 지역: 기본 KADIZ. 별칭(카디즈/KADIZ/한반도/서해/황해/한국 공역...) 매칭.
  - 시간창: "지금"=최근 30분, "최근 N분/시간", 기본 30분.
현재는 관심지역이 KADIZ 1곳이라 지역 파싱은 항상 KADIZ로 귀결되지만, 별칭 매칭 결과를
matched_region_alias로 노출해 "어떤 표현이 어떤 Region에 걸렸나"를 투명하게 보인다.
Region이 늘면 REGION_ALIASES에 항목만 추가하면 된다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

# 지역 별칭 → Region.id. 한국어 질의 중심(gdelt REGION_ALIASES와 목적이 다르다:
# 그쪽은 영문 뉴스 제목 매칭, 이쪽은 한국어 사용자 질의 매칭). Region이 늘면 여기 추가.
REGION_ALIASES: dict[str, list[str]] = {
    "KADIZ": [
        "KADIZ",
        "카디즈",
        "방공식별구역",
        "한반도",
        "한국",
        "한국 공역",
        "우리 공역",
        "서해",
        "황해",
        "동해",
        "대한해협",
        "제주",
    ],
}
DEFAULT_REGION = "KADIZ"

# 시간창 상수(초) — "지금"/기본값. architecture.md 폴링 주기와 무관한 질의 해석용.
DEFAULT_WINDOW_SECONDS = 30 * 60  # 기본·"지금" = 최근 30분
_UNIT_SECONDS = {"분": 60, "시간": 3600, "일": 86400}


@dataclass
class ParsedQuery:
    """파싱 결과 — 지역·시간창을 결정적으로 확정. 응답에 그대로 노출(투명성).

    window_start/end = [now - window_seconds, now]. now는 주입 가능(테스트 결정성).
    matched_region_alias = 어떤 별칭 표현이 걸렸나(없으면 기본 지역 사용 표시).
    matched_window_phrase = 어떤 시간 표현이 걸렸나(없으면 기본 30분).
    """

    raw: str
    region_id: str
    window_seconds: int
    window_start: int
    window_end: int
    window_label: str
    now: int
    matched_region_alias: Optional[str] = None
    matched_window_phrase: Optional[str] = None
    fields_defaulted: list[str] = field(default_factory=list)  # 기본값으로 채운 항목


def _parse_region(text: str) -> tuple[str, Optional[str]]:
    """질의에서 지역을 판별. 반환 (region_id, matched_alias|None).

    별칭이 하나도 안 걸리면 기본 지역(KADIZ) + None. 여러 개면 첫 매칭(현재 Region 1곳).
    """
    low = text.lower()
    for region_id, aliases in REGION_ALIASES.items():
        for alias in aliases:
            if alias.lower() in low:
                return region_id, alias
    return DEFAULT_REGION, None


def _parse_window(text: str) -> tuple[int, str, Optional[str]]:
    """질의에서 시간창(초)을 판별. 반환 (window_seconds, label, matched_phrase|None).

    우선순위: "최근 N분/시간/일" 명시 > "지금"/"현재" > 기본 30분.
    """
    # "최근 N분/시간/일" (숫자 + 단위). 공백 유무 허용.
    m = re.search(r"최근\s*(\d+)\s*(분|시간|일)", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        secs = n * _UNIT_SECONDS[unit]
        return secs, f"최근 {n}{unit}", m.group(0)
    # "지금"/"현재"/"방금" → 최근 30분(DR-0006: "지금"=최근 30분)
    m = re.search(r"지금|현재|방금", text)
    if m:
        return DEFAULT_WINDOW_SECONDS, "최근 30분(지금)", m.group(0)
    # "최근"만 있고 숫자 없음 → 기본 30분(라벨에 표시)
    if "최근" in text:
        return DEFAULT_WINDOW_SECONDS, "최근 30분", "최근"
    return DEFAULT_WINDOW_SECONDS, "최근 30분(기본)", None


def parse_query(query: str, now: Optional[int] = None) -> ParsedQuery:
    """자연어 질의 → ParsedQuery (지역·시간창 결정적 확정).

    now 미지정 시 wall-clock. 시간창은 [now - window_seconds, now](뒤로 보는 창).
    파싱은 순수·결정적 — 같은 질의·now는 항상 같은 결과(재현성, DR-0006).
    """
    now = int(now if now is not None else time.time())
    region_id, matched_alias = _parse_region(query)
    window_seconds, label, matched_phrase = _parse_window(query)

    defaulted: list[str] = []
    if matched_alias is None:
        defaulted.append("region")  # 별칭 안 걸림 → 기본 지역 사용
    if matched_phrase is None:
        defaulted.append("window")  # 시간 표현 안 걸림 → 기본 30분

    return ParsedQuery(
        raw=query,
        region_id=region_id,
        window_seconds=window_seconds,
        window_start=now - window_seconds,
        window_end=now,
        window_label=label,
        now=now,
        matched_region_alias=matched_alias,
        matched_window_phrase=matched_phrase,
        fields_defaulted=defaulted,
    )
