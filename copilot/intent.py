"""copilot/intent.py — 질의 의도 분류 (규칙 1차 + 선택적 LLM 폴백).

DR-0011: 코파일럿이 지역·시간창만 파싱하고 나머지 질문엔 고정 요약을 반환하던 것을,
질의 의도별로 다른 tool read·문장 조립으로 라우팅하도록 확장한다. 분류는 parser.py와
같은 규율을 따른다:
  - 규칙 기반 1차 분류 = 빠르고 **결정적**(같은 질의는 항상 같은 의도 → replay 재현성).
  - (SKAI_COPILOT_LLM=claude 일 때만) 규칙이 기본값으로 흘러내린 **모호 질의**만 claude로
    재분류. 실패·타임아웃·허용 밖 의도 → 규칙 결과 유지(폴백, DR-0004 패턴). 기본=규칙만.
  - 분류 결과(intent, slots, matched)를 응답에 그대로 노출한다(투명성 — 파서처럼).

⚠️ citation 불변식과 직교한다: 의도는 "어떤 read·조립을 쓸지"만 정한다. 어떤 의도든 문장은
사실에서 조립되고 cites를 갖는다(assessment.py). 의도 분류는 환각 창구가 아니다.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

# ── 의도 상수 ────────────────────────────────────────────────────────────────
INTENT_SITUATION_SUMMARY = "situation_summary"  # 지역 상황 요약(현행 기본)
INTENT_COUNT = "count"  # 몇 대/몇 건(집계)
INTENT_FILTER = "filter"  # operator/국적/군용/기종 조건 항적
INTENT_ENTITY_EXPLAIN = "entity_explain"  # 이 이상징후/기체/위성/뉴스 뭐야(id·지시어)
INTENT_WHY = "why"  # 왜 이상한가(근거·룰 설명)
INTENT_CORRELATION = "correlation"  # 은닉 정황(교차소스 상관)
INTENT_WEATHER = "weather"  # 기상만
INTENT_NEWS = "news"  # 뉴스/OSINT만

ALL_INTENTS = (
    INTENT_SITUATION_SUMMARY,
    INTENT_COUNT,
    INTENT_FILTER,
    INTENT_ENTITY_EXPLAIN,
    INTENT_WHY,
    INTENT_CORRELATION,
    INTENT_WEATHER,
    INTENT_NEWS,
)

# ── 규칙 키워드(한국어 질의 중심) ────────────────────────────────────────────
# 요약 강제 마커 — 있으면 무조건 situation_summary(포커스 의도 억제). 데모 백본 질의
# "…요약해줘"·"…상황"이 여기 걸려 결정성이 보존된다.
_SUMMARY_MARKERS = ("요약", "상황", "브리핑", "종합", "전반", "정리해", "총평", "개요")
_COUNT_MARKERS = (
    "몇 대",
    "몇대",
    "몇 건",
    "몇건",
    "몇 개",
    "몇개",
    "몇 기",
    "몇기",
    "개수",
    "얼마나",
    "카운트",
    "대수",
    "건수",
    "수는",
    "총 몇",
)
_WHY_MARKERS = (
    "왜",
    "이유",
    "어째서",
    "무슨 근거",
    "근거가",
    "근거는",
    "위험한가",
    "이상한가",
)
_MILITARY_MARKERS = (
    "군용",
    "군용기",
    "밀리터리",
    "military",
    "전투기",
    "군 소속",
    "군기",
)
_CIVIL_MARKERS = ("민간기", "민간 항공", "민항", "여객기", "civil")
_HIDDEN_MARKERS = (
    "은닉",
    "숨은",
    "숨긴",
    "숨겨",
    "은폐",
    "감춘",
    "감추",
    "은밀",
    "숨기",
)
_WEATHER_MARKERS = (
    "기상",
    "날씨",
    "metar",
    "시정",
    "실링",
    "가시거리",
    "운고",
    "기상 상태",
)
_NEWS_MARKERS = ("뉴스", "osint", "기사", "보도", "언론", "오신트")
# 엔티티 지시(뭐야/설명) — kind 키워드와 함께여야 발동(과발동 방지).
_ENTITY_MARKERS = (
    "뭐야",
    "뭔데",
    "뭔지",
    "무엇",
    "정체",
    "설명해",
    "설명 좀",
    "이게 뭐",
    "무슨 이상",
)
# 지시어 구문(선택된 객체 지칭). "이상징후" 부분문자열 오탐을 막으려 구문 전체로 매칭.
_DEMONSTRATIVE_PHRASES = (
    "이 이상징후",
    "그 이상징후",
    "저 이상징후",
    "해당 이상징후",
    "이 징후",
    "이 기체",
    "그 기체",
    "저 기체",
    "해당 기체",
    "이 항공기",
    "이 항적",
    "이 위성",
    "그 위성",
    "해당 위성",
    "이 통과",
    "이 뉴스",
    "그 뉴스",
    "이 사건",
    "이 기상",
)

# 국적(OpenSky origin_country 영문명) 별칭. "국적" 키워드와 함께일 때만 필터로 인정한다
# (지역 별칭 "한국"과의 충돌 방지 — 국적 필터는 반드시 "국적" 명시가 있어야 발동).
_ORIGIN_COUNTRY_ALIASES = {
    "미국": "United States",
    "중국": "China",
    "일본": "Japan",
    "러시아": "Russian Federation",
    "한국": "South Korea",
    "대한민국": "South Korea",
    "북한": "North Korea",
    "대만": "Taiwan",
    "영국": "United Kingdom",
}

# 근거 객체 id 패턴(질의문에 직접 박힌 id → entity_explain/why 대상). id에 하이픈이
# 들어가므로(anomaly-{type}-{ac}-{win} 등) 하이픈을 포함해 전체 토큰을 잡는다.
_ID_RE = re.compile(
    r"(anomaly-[A-Za-z0-9_.\-]+|pass-[A-Za-z0-9_.\-]+|wx-[A-Za-z0-9_.\-]+|news-[A-Za-z0-9_.\-]+)"
)


@dataclass
class Intent:
    """질의 의도 분류 결과 — 응답에 그대로 노출(투명성).

    intent = ALL_INTENTS 중 하나. slots = 의도별 파라미터(필터 조건·집계 대상·엔티티 id 등).
    confidence = 규칙 매칭 강도(휴리스틱, LLM 폴백 판단용). matched = 걸린 키워드(감사).
    backend = "rule" | "rule(default)" | "claude" (분류 경로).
    """

    intent: str
    slots: dict = field(default_factory=dict)
    confidence: float = 0.0
    matched: list[str] = field(default_factory=list)
    backend: str = "rule"


def _find(low: str, markers) -> list[str]:
    """low(소문자 질의)에 등장한 마커들(감사용 — 어떤 표현이 걸렸나)."""
    return [m for m in markers if m in low]


def _entity_kind(low: str) -> Optional[str]:
    """질의에서 대상 객체 종류를 판별(엔티티·집계 대상 슬롯용)."""
    if any(k in low for k in ("이상징후", "징후", "anomaly")):
        return "anomaly"
    if any(k in low for k in ("위성", "궤도", "통과", "satellite")):
        return "satellite"
    if any(k in low for k in ("기상", "날씨", "weather")):
        return "weather"
    if any(k in low for k in ("뉴스", "기사", "news", "osint")):
        return "news"
    if any(k in low for k in ("기체", "항공기", "항적", "비행기", "aircraft")):
        return "flight"
    return None


def _count_target(low: str) -> str:
    """집계 대상 판별(count 슬롯). 군용은 필터 집계이므로 target='military'."""
    if _find(low, _MILITARY_MARKERS):
        return "military"
    kind = _entity_kind(low)
    if kind == "anomaly":
        return "anomalies"
    if kind == "satellite":
        return "passes"
    if kind == "news":
        return "news"
    return "flights"


def _origin_country(low: str) -> Optional[str]:
    """ "국적" 키워드가 있을 때만 국적 별칭을 OpenSky origin_country 영문명으로 해소."""
    if "국적" not in low:
        return None
    for alias, canonical in _ORIGIN_COUNTRY_ALIASES.items():
        if alias in low:
            return canonical
    return None


def _extract_after(low: str, keyword: str) -> Optional[str]:
    """keyword 뒤 토큰 1개를 뽑는다(operator/기종 등 얇은 데이터용 최소 추출)."""
    idx = low.find(keyword)
    if idx < 0:
        return None
    rest = low[idx + len(keyword) :].strip()
    if not rest:
        return None
    token = re.split(r"[\s,?.!]", rest, maxsplit=1)[0].strip()
    return token or None


def _filter_slots(low: str, query: str) -> dict:
    """필터 조건 슬롯을 뽑는다(없으면 빈 dict → filter 의도 아님)."""
    slots: dict = {}
    if _find(low, _MILITARY_MARKERS):
        slots["military"] = True
    elif _find(low, _CIVIL_MARKERS):
        slots["military"] = False
    origin = _origin_country(low)
    if origin:
        slots["origin_country"] = origin
    for kw in ("소속", "운용사", "operator", "운영사"):
        if kw in low:
            val = _extract_after(query.lower(), kw)
            if val and val not in ("이", "그", "은", "는"):
                slots["operator"] = val
            break
    if "기종" in low:
        val = _extract_after(query.lower(), "기종")
        if val:
            slots["aircraft_type"] = val
    return slots


def classify(
    query: str,
    now: Optional[int] = None,
    llm: Optional[str] = None,
    focus_id: Optional[str] = None,
) -> Intent:
    """자연어 질의 → Intent(규칙 1차, 모호 시 선택적 claude 폴백).

    llm: "claude"면 규칙이 default로 흘러내린 모호 질의만 claude로 재분류(그 외엔 규칙만).
    None/미지정이면 순수 규칙(결정적) — 테스트·replay 경로. 규칙 우선순위는 "더 구체적인
    의도가 먼저"다: why → count → filter → entity → correlation → summary마커 → weather →
    news → (모호)summary. 구체 마커가 없는 데모 백본 질의는 상황요약으로 귀결된다.

    focus_id: 프론트가 선택한 객체 id(질의문에 박힌 id보다 우선). 선택 객체가 있으면 "이거
    뭐야"류 모호 질의도 그 객체의 entity_explain으로 확정된다(선택=대상 확정).
    """
    low = query.lower()
    ids = _ID_RE.findall(query)
    focus = focus_id or (ids[0] if ids else None)
    kind = _entity_kind(low)

    # 1) why — "왜/근거" 추론 요구(가장 구체적 질문).
    why_hit = _find(low, _WHY_MARKERS)
    if why_hit:
        return Intent(
            INTENT_WHY,
            {"entity_id": focus, "entity_kind": kind},
            0.9,
            matched=why_hit + ([focus] if focus else []),
        )

    # 2) count — "몇 대/몇 건" 집계.
    count_hit = _find(low, _COUNT_MARKERS)
    if count_hit:
        return Intent(
            INTENT_COUNT, {"target": _count_target(low)}, 0.9, matched=count_hit
        )

    # 3) filter — operator/국적/군용/기종 조건.
    fslots = _filter_slots(low, query)
    if fslots:
        return Intent(
            INTENT_FILTER, fslots, 0.85, matched=[str(v) for v in fslots.values()]
        )

    # 4) entity_explain — id·선택 객체(focus_id)·지시어·(뭐야+kind).
    entity_hit = _find(low, _ENTITY_MARKERS)
    demo_hit = _find(low, _DEMONSTRATIVE_PHRASES)
    if focus or demo_hit or (entity_hit and kind):
        return Intent(
            INTENT_ENTITY_EXPLAIN,
            {"entity_id": focus, "entity_kind": kind},
            0.85,
            matched=(([focus] if focus else []) + demo_hit + entity_hit),
        )

    # 5) correlation — 은닉 정황(명시 마커만 — "겹치는"류는 요약이 커버).
    hidden_hit = _find(low, _HIDDEN_MARKERS)
    if hidden_hit:
        return Intent(INTENT_CORRELATION, {}, 0.8, matched=hidden_hit)

    # 6) 요약 강제 마커 — 데모 백본·명시 요약 요청.
    summary_hit = _find(low, _SUMMARY_MARKERS)
    if summary_hit:
        return Intent(INTENT_SITUATION_SUMMARY, {}, 0.9, matched=summary_hit)

    # 7) weather-only (뉴스 언급 없을 때).
    wx_hit = _find(low, _WEATHER_MARKERS)
    news_hit = _find(low, _NEWS_MARKERS)
    if wx_hit and not news_hit:
        return Intent(INTENT_WEATHER, {}, 0.75, matched=wx_hit)

    # 8) news-only (기상 언급 없을 때).
    if news_hit and not wx_hit:
        return Intent(INTENT_NEWS, {}, 0.75, matched=news_hit)

    # 9) 모호 → 상황요약 기본. LLM 켜졌으면 재분류 시도(실패 시 이 기본 유지).
    default = Intent(
        INTENT_SITUATION_SUMMARY, {}, 0.4, matched=[], backend="rule(default)"
    )
    if _llm_enabled(llm):
        refined = _classify_llm(query)
        if refined is not None:
            return refined
    return default


# ── LLM 폴백(모호 질의만) ─────────────────────────────────────────────────────
def _llm_enabled(llm: Optional[str]) -> bool:
    """분류 LLM 폴백 활성 여부. 인자 우선, 없으면 SKAI_COPILOT_LLM 환경변수."""
    name = (llm if llm is not None else os.environ.get("SKAI_COPILOT_LLM", "")).lower()
    return name == "claude"


def _classify_llm(
    query: str, claude_bin: str = "claude", timeout: int = 30
) -> Optional[Intent]:
    """claude -p로 모호 질의를 ALL_INTENTS 중 하나로 재분류. 실패·허용 밖 → None(규칙 유지).

    LLM은 **의도 단어 하나만** 고른다(slots는 규칙/조립이 채운다 — 환각 최소화). 응답이
    허용 의도가 아니거나 타임아웃·비정상 종료면 None을 돌려 규칙 기본이 유지된다(DR-0004).
    """
    prompt = (
        "너는 공중 ISR 코파일럿의 의도 분류기다. 사용자 질의를 아래 의도 중 정확히 "
        "하나로 분류하고, 그 **영문 의도 단어 하나만** 출력하라(설명·문장부호 없이).\n"
        f"허용 의도: {', '.join(ALL_INTENTS)}\n"
        "- situation_summary: 지역 전반 상황 요약\n"
        "- count: 몇 대/몇 건 집계\n"
        "- filter: 군용/국적/기종 등 조건에 맞는 항적\n"
        "- entity_explain: 특정 이상징후/기체/위성/뉴스가 무엇인지\n"
        "- why: 왜 이상한지 근거·이유\n"
        "- correlation: 숨은 정황·교차소스 상관\n"
        "- weather: 기상만\n"
        "- news: 뉴스/OSINT만\n\n"
        f"질의: {query}\n의도:"
    )
    try:
        proc = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude rc={proc.returncode}")
        out = (proc.stdout or "").strip().lower()
        # 첫 토큰만(모델이 여분 텍스트를 붙여도 방어).
        token = re.split(r"[^a-z_]", out, maxsplit=1)[0] if out else ""
        if token in ALL_INTENTS:
            return Intent(token, {}, 0.6, matched=[token], backend="claude")
        raise RuntimeError(f"허용 밖 의도 {out!r}")
    except Exception as e:  # 실패·타임아웃·허용 밖 → 규칙 기본 유지(폴백)
        print(f"[intent] claude 분류 폴백 실패 → 규칙 기본 유지: {e!r}")
        return None
