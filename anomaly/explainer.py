"""anomaly/explainer.py — ExplainerBackend 인터페이스 + 3 백엔드 (DR-0004).

룰이 사실(squawk 값·기체·좌표)을 하드하게 확정하고, explainer는 **서술·설명**만
담당한다(aip-integration.md §3: "사실추출·citation은 룰+온톨로지로, 서술만 LLM").
그래서 confidence는 룰(스쿽 코드)이 정하고, LLM은 설명문 품질만 높인다.

백엔드는 store 어댑터와 동일한 교체 패턴(DR-0004):
  TemplateExplainer   (기본)  결정적 설명문+신뢰도. LLM 없이 항상 동작 → 데모 재현성.
  ClaudeCliExplainer  (옵션)  `claude -p` 서술 강화. 실패/타임아웃 시 template 자동 폴백.
  AipLogicExplainer   (개통)  Foundry AIP Logic 함수 explain-anomaly 실호출(설명+신뢰도를
                             AIP가 생성). 크리덴셜/네트워크/함수 실패 시 template 자동 폴백.

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


# ── 공용 Foundry AIP 헬퍼(explain-anomaly·region-situation-summary 공용) ──────────
def make_foundry_osdk_client(timeout: int = 30):
    """생성 OSDK FoundryClient lazy 생성(env FOUNDRY_TOKEN·FOUNDRY_HOSTNAME).

    AIP Logic 함수(explain-anomaly·region-situation-summary)를 호출하는 여러 경로가 같은
    클라이언트 생성 규율을 쓰도록 한 곳으로 모은다(SSOT). SDK는 메인 .venv(3.14)엔 없어
    반드시 lazy import(store_foundry와 동일). 크리덴셜 없으면 RuntimeError(호출자가 폴백).
    """
    from foundry_sdk import Config, UserTokenAuth
    from skai_osdk_sdk import FoundryClient

    token = os.environ.get("FOUNDRY_TOKEN")
    hostname = os.environ.get("FOUNDRY_HOSTNAME")
    if not token or not hostname:
        raise RuntimeError(
            "FOUNDRY_TOKEN·FOUNDRY_HOSTNAME 미설정 — AIP Logic 호출 불가(폴백)."
        )
    return FoundryClient(
        auth=UserTokenAuth(token=token),
        hostname=hostname,
        config=Config(timeout=timeout),
    )


def allow_beta_features():
    """AIP Logic 응답(beta StructType)용 AllowBetaFeatures 컨텍스트.

    OSDK 미설치 환경(메인 .venv·단위테스트)에선 foundry_sdk_runtime이 없어 nullcontext로
    강등(주입 fake client는 beta 게이팅 없음). 실 환경(.venv312)에선 진짜 컨텍스트를 쓴다.
    """
    try:
        from foundry_sdk_runtime import AllowBetaFeatures

        return AllowBetaFeatures()
    except ImportError:
        import contextlib

        return contextlib.nullcontext()


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
        ANOMALY_TYPE_RAPID_MANEUVER,
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

    if draft.type == ANOMALY_TYPE_RAPID_MANEUVER:
        kind = s.get("kind")
        fpm = s.get("peak_vertical_fpm", 0)
        acc = s.get("peak_accel_mps2", 0)
        if kind == "speed":
            what = f"속도 급변(최대 가속 {acc} m/s²)"
        elif kind == "both":
            what = f"고도·속도 동시 급변(수직률 최대 {fpm} ft/min, 가속 {acc} m/s²)"
        else:
            what = f"고도 급변(수직률 최대 {fpm} ft/min)"
        return (
            f"{prefix}항공기 {callsign}(icao24 {icao24})가 연속 관측 {s.get('n_obs')}건에서 "
            f"{what}을 보였습니다(급기동, 신뢰도 {c:.2f}). 민항 정상 기동 범위를 초과하는 "
            f"보수적 임계 기반 정황 — 회피·전투기동·비상강하 가능성, 교차검증 요망."
        )

    # 미지원 유형 — 방어적 폴백(사실만).
    return f"{prefix}이상징후({draft.type}) 신뢰도 {c:.2f}. 근거 객체를 확인하십시오."


class AipLogicExplainer:
    """Foundry AIP Logic 함수 `explain-anomaly`(OSDK 0.9.0 타입드 쿼리 `explainAnomaly`)를
    실호출해 설명·신뢰도를 생성한다(DR-0004의 최종 이관 대상 — 개통 완료).

    ## 호출 형태 (OSDK 0.9.0 실측)
    `client.ontology.queries.explain_anomaly(evidence=<Observation|str>, anomaly_type=str,
    region_name=str?, callsign=str?) -> ExplainAnomalyResponse(explanation, confidence,
    recommendation)`. 응답은 beta StructType라 반드시 `AllowBetaFeatures()` 컨텍스트 안에서
    호출한다(밖이면 BetaWarning 예외).

    ## evidence = 온톨로지 객체 참조 (해자)
    candidate.observation.id로 Foundry Observation **객체**를 fetch해 evidence로 넘긴다 →
    AIP가 그 객체의 실제 속성(squawk·on_ground·lat/lon·alt·ts)을 온톨로지 위에서 읽어 추론한다
    (단순 LLM 호출과의 차이). 객체가 Foundry에 없거나 조회 실패 시 관측 요약 **String**으로
    폴백하되(함수 시그니처가 str도 허용) 그 한계를 backend 라벨에 명시한다.

    ## confidence
    ExplainerResult.confidence를 **AIP 응답값**으로 채운다 — 즉 이 백엔드에선 신뢰도도 AIP가
    산출한다(template/claude가 룰 확정 신뢰도를 유지하는 것과의 차이. DR-0004의 "confidence=룰"
    원칙에 대한 이 백엔드 한정 의도적 편차 — "AIP가 설명을 생성"을 사실로 만들기 위함).
    함수 출력의 recommendation(권고)은 설명문 말미에 덧붙여 보존한다.

    ## 폴백 (DR-0004 패턴 — 데모 안전)
    크리덴셜 미설정·네트워크·타임아웃·함수 실패·빈 응답 등 어떤 예외든 TemplateExplainer로
    폴백한다(backend="template(aip_logic 폴백)"). SDK는 lazy import(메인 .venv엔 OSDK 없음 —
    store_foundry와 동일 규율)이며 SKAI_EXPLAINER=aip 명시 opt-in일 때만 이 경로를 탄다.
    """

    backend_name = "aip_logic"

    def __init__(
        self,
        fallback: Optional[ExplainerBackend] = None,
        timeout: int = 30,
        client=None,
    ):
        self.fallback = fallback or TemplateExplainer()
        self.timeout = timeout
        self._client = client  # 주입 가능(테스트) — 없으면 첫 호출 시 lazy 생성.

    def explain(self, candidate: AnomalyCandidate) -> ExplainerResult:
        try:
            return self._call_aip(candidate)
        except (
            Exception
        ) as e:  # 크리덴셜·네트워크·타임아웃·함수 실패 → template 폴백(데모 안전)
            print(f"[explainer] aip-logic 실패 → template 폴백: {e!r}")
            fb = self.fallback.explain(candidate)
            return ExplainerResult(
                explanation=fb.explanation,
                confidence=fb.confidence,
                backend="template(aip_logic 폴백)",
            )

    def _get_client(self):
        """OSDK FoundryClient lazy 생성(공용 헬퍼 위임 — region summary와 동일 규율)."""
        if self._client is None:
            self._client = make_foundry_osdk_client(self.timeout)
        return self._client

    def _resolve_evidence(self, client, candidate: AnomalyCandidate, facts: dict):
        """evidence로 넘길 값 결정. 온톨로지 Observation 객체 우선, 실패 시 요약 String 폴백.

        반환: (evidence_value, mode) — mode ∈ {"object", "string"}.
        """
        obs_id = candidate.observation.id
        try:
            obj = client.ontology.objects.Observation.get(obs_id)
            if obj is not None:
                return obj, "object"  # 온톨로지 위 추론(객체 참조)
        except Exception as e:
            print(
                f"[explainer] Observation({obs_id}) 조회 실패 → String 근거 폴백: {e!r}"
            )
        # 객체 미존재/조회 실패 → 요약 String(함수가 str도 허용). 온톨로지 참조 아님(한계).
        summary = (
            f"obsId={obs_id} callsign={facts['callsign']} icao24={facts['icao24']} "
            f"squawk={facts['squawk']}({facts['meaning']}) ts={facts['when']}Z "
            f"pos=({facts['where']}) source={facts['source']}"
        )
        return summary, "string"

    @staticmethod
    def _allow_beta():
        """explain-anomaly 응답(beta StructType)용 컨텍스트(공용 헬퍼 위임)."""
        return allow_beta_features()

    def _call_aip(self, candidate: AnomalyCandidate) -> ExplainerResult:
        client = self._get_client()
        f = _facts(candidate)
        evidence, mode = self._resolve_evidence(client, candidate, f)

        kwargs: dict = {"evidence": evidence, "anomaly_type": candidate.type}
        callsign = (
            candidate.signal.get("callsign") or candidate.observation.aircraft_ref
        )
        if callsign:
            kwargs["callsign"] = callsign
        region = candidate.signal.get("region")
        if region:
            kwargs["region_name"] = region

        # 응답이 beta StructType → AllowBetaFeatures 컨텍스트 필수(밖이면 BetaWarning 예외).
        with self._allow_beta():
            resp = client.ontology.queries.explain_anomaly(**kwargs)

        explanation = (getattr(resp, "explanation", "") or "").strip()
        if not explanation:
            raise RuntimeError("AIP Logic explain-anomaly 빈 explanation")
        rec = (getattr(resp, "recommendation", "") or "").strip()
        if rec:
            explanation = f"{explanation}\n권고: {rec}"
        # confidence는 AIP 산출값(이 백엔드 한정) — 방어적으로 [0,1] 클램프.
        conf = float(getattr(resp, "confidence", f["confidence"]))
        conf = min(max(conf, 0.0), 1.0)
        # String 근거로 폴백했으면 온톨로지 참조가 아님을 backend 라벨에 명시(정직).
        backend = (
            self.backend_name if mode == "object" else "aip_logic(string-evidence)"
        )
        return ExplainerResult(
            explanation=explanation, confidence=conf, backend=backend
        )


def get_explainer(name: Optional[str] = None) -> ExplainerBackend:
    """백엔드 팩토리. name/SKAI_EXPLAINER 로 선택(기본 template — 데모 재현성)."""
    name = (name or os.environ.get("SKAI_EXPLAINER", "template")).lower()
    if name == "claude":
        return ClaudeCliExplainer()
    if name == "aip":
        return AipLogicExplainer()
    return TemplateExplainer()
