"""anomaly/explainer.py — ExplainerBackend 인터페이스 + 3 백엔드 (DR-0004).

룰이 사실(squawk 값·기체·좌표)을 하드하게 확정하고, explainer는 **서술·설명**만
담당한다(aip-integration.md §3: "사실추출·citation은 룰+온톨로지로, 서술만 LLM").
그래서 confidence는 룰(스쿽 코드)이 정하고, LLM은 설명문 품질만 높인다.

백엔드는 store 어댑터와 동일한 교체 패턴(DR-0004):
  TemplateExplainer   (기본)  결정적 설명문+신뢰도. LLM 없이 항상 동작 → 데모 재현성.
  ClaudeCliExplainer  (옵션)  `claude -p` 서술 강화. 실패/타임아웃 시 template 자동 폴백.
  AipLogicExplainer   (스텁)  Foundry AIP Logic 개통 시 최종 이관 대상.

get_explainer()가 SKAI_EXPLAINER 환경변수로 백엔드 선택(기본 template).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from anomaly.rules import EMERGENCY_SQUAWKS, AnomalyCandidate

# 비상 스쿽별 기본 신뢰도 — 조종사가 명시 설정하는 하드 신호라 0.9대(심각도 순).
_SQUAWK_CONFIDENCE = {"7500": 0.95, "7700": 0.93, "7600": 0.90}
_DEFAULT_CONFIDENCE = 0.90


@dataclass
class ExplainerResult:
    """explainer 산출 — 설명문 + 신뢰도 + 사용 백엔드(추적용)."""

    explanation: str
    confidence: float
    backend: str


@runtime_checkable
class ExplainerBackend(Protocol):
    """이상징후 후보 → 설명·신뢰도. store 어댑터처럼 교체 가능."""

    def explain(self, candidate: AnomalyCandidate) -> ExplainerResult: ...


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _facts(candidate: AnomalyCandidate) -> dict:
    """후보에서 설명에 필요한 사실을 추출(룰이 확정한 하드 사실만)."""
    o = candidate.observation
    squawk = str(candidate.signal.get("squawk") or o.squawk or "")
    return {
        "squawk": squawk,
        "meaning": candidate.signal.get("meaning")
        or EMERGENCY_SQUAWKS.get(squawk, "비상"),
        "callsign": candidate.signal.get("callsign") or o.aircraft_ref,
        "icao24": o.aircraft_ref,
        "when": _fmt_ts(o.ts),
        "where": (
            f"{o.lat:.3f}, {o.lon:.3f}"
            if o.lat is not None and o.lon is not None
            else "미상"
        ),
        "source": o.source,
        "is_synthetic": o.source == "synthetic",
        "confidence": _SQUAWK_CONFIDENCE.get(squawk, _DEFAULT_CONFIDENCE),
    }


class TemplateExplainer:
    """기본 백엔드 — 결정적 설명문 + 신뢰도. LLM 없이 항상 동작(데모 재현성)."""

    backend_name = "template"

    def explain(self, candidate: AnomalyCandidate) -> ExplainerResult:
        f = _facts(candidate)
        # 합성 시나리오면 설명문에 명시(요구사항 #3).
        prefix = "[합성 시나리오] " if f["is_synthetic"] else ""
        text = (
            f"{prefix}항공기 {f['callsign']}(icao24 {f['icao24']})가 비상 스쿽 "
            f"{f['squawk']}({f['meaning']})를 송신했습니다. "
            f"관측 시각 {f['when']} UTC, 위치 {f['where']}. "
            f"비상 스쿽은 조종사가 명시적으로 설정하는 하드 신호로 신뢰도가 높습니다. "
            f"근거 관측 1건(source={f['source']})을 확인하십시오."
        )
        return ExplainerResult(
            explanation=text, confidence=f["confidence"], backend=self.backend_name
        )


def _build_prompt(candidate: AnomalyCandidate) -> str:
    """claude -p 에 넘길 프롬프트. 사실은 고정, 서술만 요청(환각 방지)."""
    f = _facts(candidate)
    synth = (
        "\n※ 이것은 합성(주입) 시나리오다. 설명 첫머리에 '[합성 시나리오]'를 붙여라."
        if f["is_synthetic"]
        else ""
    )
    return (
        "너는 공중 ISR 상황분석 보조다. 아래 '확정 사실'만 사용해 한국어 2~3문장으로 "
        "분석가용 이상징후 설명을 써라. 사실을 지어내지 말고, 주어진 값만 서술하라. "
        "결론(권고 조치)까지 간결히.\n\n"
        f"[확정 사실]\n"
        f"- 유형: 비상 스쿽\n"
        f"- 콜사인: {f['callsign']} (icao24 {f['icao24']})\n"
        f"- 스쿽 코드: {f['squawk']} = {f['meaning']}\n"
        f"- 관측 시각(UTC): {f['when']}\n"
        f"- 위치(lat, lon): {f['where']}\n"
        f"- 출처: {f['source']}\n"
        f"- 신뢰도: {f['confidence']:.2f}"
        f"{synth}\n\n설명문만 출력하라(머리말·마크다운 없이)."
    )


class ClaudeCliExplainer:
    """옵션 백엔드 — `claude -p` 로 서술 강화. 실패/타임아웃 시 template 폴백.

    confidence는 룰(스쿽 코드)이 정한 값을 그대로 쓴다(LLM은 서술만 강화 — DR-0004).
    로컬 Max 구독 활용(API 키 불요). SKAI_EXPLAINER=claude 일 때만 사용.
    """

    backend_name = "claude_cli"

    def __init__(
        self,
        fallback: Optional[ExplainerBackend] = None,
        claude_bin: str = "claude",
        timeout: int = 30,
    ):
        self.fallback = fallback or TemplateExplainer()
        self.claude_bin = claude_bin
        self.timeout = timeout

    def explain(self, candidate: AnomalyCandidate) -> ExplainerResult:
        try:
            return self._call_claude(candidate)
        except Exception as e:  # 실패·타임아웃·파싱실패 → template 폴백(데모 안전)
            print(f"[explainer] claude-cli 실패 → template 폴백: {e!r}")
            fb = self.fallback.explain(candidate)
            return ExplainerResult(
                explanation=fb.explanation,
                confidence=fb.confidence,
                backend="template(claude_cli 폴백)",
            )

    def _call_claude(self, candidate: AnomalyCandidate) -> ExplainerResult:
        f = _facts(candidate)
        proc = subprocess.run(
            [self.claude_bin, "-p", _build_prompt(candidate)],
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude 비정상 종료 rc={proc.returncode}: {(proc.stderr or '')[:200]}"
            )
        text = (proc.stdout or "").strip()
        if not text:
            raise RuntimeError("claude 빈 출력")
        # confidence는 룰이 확정(LLM은 서술만) — 사실 무결성 유지.
        return ExplainerResult(
            explanation=text, confidence=f["confidence"], backend=self.backend_name
        )


def explain_draft(draft) -> str:
    """P5 AnomalyDraft → 결정적 설명문(유형별 템플릿).

    비상 스쿽(AnomalyCandidate)의 explainer와 같은 규율: 사실·신뢰도는 룰이 확정하고
    여기선 서술만 만든다(신뢰도는 draft.confidence 그대로). dropout 미확인은 **단정하지
    않음**을 명시하고(CLAUDE.md 기술기준), 군용 판정은 **저신뢰 휴리스틱**임을 명시한다.
    합성 시나리오면 '[합성 시나리오]' 접두를 붙인다(요구사항 #3).
    """
    from anomaly.rules import (
        ANOMALY_TYPE_ADSB_DROPOUT,
        ANOMALY_TYPE_LOITERING,
        ANOMALY_TYPE_MILITARY_APPROACH,
        ANOMALY_TYPE_SATELLITE_PROXIMITY,
    )

    s = draft.signal
    c = draft.confidence
    prefix = "[합성 시나리오] " if s.get("is_synthetic") else ""
    icao24 = draft.dedup_key
    callsign = s.get("callsign") or icao24

    if draft.type == ANOMALY_TYPE_ADSB_DROPOUT:
        head = (
            f"{prefix}항공기 {callsign}(icao24 {icao24})의 ADS-B 신호가 민감구역 "
            f"{s.get('region')} 내에서 끊겼습니다(마지막 관측 {_fmt_ts(draft.ts)} UTC). "
        )
        if s.get("cross_confirmed") is True:
            return head + (
                f"2차 소스(미러)도 같은 구간에서 이 기체를 관측하지 못해 부재가 교차 "
                f"확인됐습니다 — 의도적 트랜스폰더 차단 가능성(신뢰도 {c:.2f}). 검토 요망."
            )
        return head + (
            f"교차 소스로 부재를 확인하지 못했습니다 — 단일 소스 결측은 송신기 문제일 수 "
            f"있어 단정하지 않습니다(신뢰도 {c:.2f}). 2차 소스 확인 필요."
        )

    if draft.type == ANOMALY_TYPE_LOITERING:
        return (
            f"{prefix}항공기 {callsign}(icao24 {icao24})가 약 {s.get('duration_min')}분간 "
            f"변위/경로 비율 {s.get('ratio')}의 반복·선회 패턴(로이터링)을 보였습니다"
            f"(경로 {s.get('path_km')}km, 신뢰도 {c:.2f}). 정찰·대기 정황 — 검토 요망."
        )

    if draft.type == ANOMALY_TYPE_MILITARY_APPROACH:
        return (
            f"{prefix}군용 추정 항공기 {callsign}(icao24 {icao24})가 작전구역 "
            f"{s.get('region')}에 진입했습니다. 근거: {s.get('mil_reason')}"
            f"(저신뢰 휴리스틱, 신뢰도 {c:.2f}). 군용 판정은 콜사인·대역 기반으로 오탐 "
            f"가능 — 교차검증 요망."
        )

    if draft.type == ANOMALY_TYPE_SATELLITE_PROXIMITY:
        win = f"{_fmt_ts(s.get('start_ts', draft.ts))}~{_fmt_ts(s.get('end_ts', draft.ts))}"
        return (
            f"{prefix}위성 {s.get('sat_name')}(NORAD {s.get('norad_id')})이 {win} UTC "
            f"{s.get('region')} 상공을 최대앙각 {s.get('max_elevation', 0):.0f}°(천정 근접)로 "
            f"통과합니다(신뢰도 {c:.2f}, 정황). ISR 수집 창 가능성 — 항적과의 시공간 상관 "
            f"확인 권장."
        )

    # 미지원 유형 — 방어적 폴백(사실만).
    return f"{prefix}이상징후({draft.type}) 신뢰도 {c:.2f}. 근거 객체를 확인하십시오."


class AipLogicExplainer:
    """스텁 — Foundry AIP Logic 개통 시 최종 이관 대상(DR-0004).

    이관 순서: aip-integration.md의 AnomalyExplainer AIP Logic 함수가
    candidate→설명·신뢰도를 생성하고, CreateAnomaly Action이 그 결과로 Anomaly 생성.
    현재는 로컬에 AIP Logic 접근 경로가 없어(P0-B BLOCKED) NotImplementedError.
    """

    backend_name = "aip_logic"

    def explain(self, candidate: AnomalyCandidate) -> ExplainerResult:
        raise NotImplementedError(
            "AipLogicExplainer는 Foundry AIP Logic 개통 시 이관 대상(DR-0004, BLOCKED). "
            "현재는 TemplateExplainer(기본) 또는 ClaudeCliExplainer(SKAI_EXPLAINER=claude) 사용."
        )


def get_explainer(name: Optional[str] = None) -> ExplainerBackend:
    """백엔드 팩토리. name/SKAI_EXPLAINER 로 선택(기본 template — 데모 재현성)."""
    name = (name or os.environ.get("SKAI_EXPLAINER", "template")).lower()
    if name == "claude":
        return ClaudeCliExplainer()
    if name == "aip":
        return AipLogicExplainer()
    return TemplateExplainer()
