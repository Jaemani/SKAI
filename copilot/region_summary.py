"""copilot/region_summary.py — AIP Logic 함수 region-situation-summary 배선.

situation_summary 의도의 요약(헤드라인) 문장 **서술**을 Foundry AIP Logic이 생성하게 한다
(SKAI_EXPLAINER=aip 또는 SKAI_COPILOT_LLM=aip 게이트 + Foundry 스토어일 때만). 사실 확정·문장별
cites 조립은 여전히 룰(assessment.py)이 담당하고, 이 모듈은 요약 서술(summary·overallAssessment)만
AIP가 생성하게 한다. citation 불변식 우회 없음: 헤드라인 문장의 cites(집계된 Anomaly id 등)는
호출자가 그대로 유지하고 텍스트만 교체한다.

## 호출 형태 (OSDK 0.10.0 실측)
`client.ontology.queries.region_situation_summary(region_name=str, anomalies2=AnomalyObjectSet,
window_label=str?, weather_summary=str?) -> RegionSituationSummaryResponse(summary,
overall_assessment, confidence)`. 파라미터명은 **anomalies2**(Foundry 함수 입력명 그대로)이고,
응답이 beta StructType라 `allow_beta_features()` 컨텍스트 안에서 호출해야 한다(밖이면 BetaWarning
예외).

## anomalies2 = Foundry Anomaly Object set (해자)
로컬 anomaly id로 `client.ontology.objects.Anomaly.where(anomaly_id.in_(ids))` 객체집합을 만들어
넘긴다 → AIP가 각 Anomaly의 실제 속성(type·status·confidence·explanation·lat/lon)을 온톨로지
위에서 읽어 종합한다(단순 LLM 호출과의 차이). explain-anomaly의 evidence가 str도 허용한 것과
달리 이 함수의 anomalies2는 **Object set만** 받으므로(str 근거 폴백 불가), Foundry 소재가 아니면
호출 자체가 무의미 → 호출자가 template 헤드라인을 유지한다(로컬 스토어 게이트는 assessment.py).

## 폴백 (DR-0004 패턴)
크리덴셜 미설정·네트워크·타임아웃·빈 응답 등 어떤 예외든 None을 반환 → 호출자가 template 유지.
0건이면 호출 없이 None(0건 규칙 이중 안전 + 호출 절약). SDK는 lazy import(메인 .venv엔 OSDK 없음).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RegionSummaryResult:
    """AIP region-situation-summary 산출 — 요약문 + 한줄판정 + 종합신뢰도 + 백엔드(추적)."""

    summary: str
    overall_assessment: str
    confidence: float
    backend: str = "aip_logic"


class AipRegionSummarizer:
    """Foundry AIP Logic 함수 `region-situation-summary`(OSDK 0.10.0 타입드 쿼리
    `regionSituationSummary`)를 실호출해 지역 상황요약 서술을 생성한다.

    client 주입 가능(테스트) — 없으면 첫 호출 시 공용 헬퍼로 lazy 생성한다.
    """

    def __init__(self, timeout: int = 30, client=None):
        self.timeout = timeout
        self._client = client  # 주입 가능(테스트) — 없으면 lazy 생성.

    def summarize(
        self,
        region_name: str,
        anomaly_ids: list[str],
        window_label: Optional[str] = None,
        weather_summary: Optional[str] = None,
    ) -> Optional[RegionSummaryResult]:
        """지역+이상징후 집합 → AIP 상황요약. 실패·0건 → None(호출자가 template 유지)."""
        if not anomaly_ids:
            return None  # 0건 → 호출 스킵(0건 규칙 이중 안전)
        try:
            return self._call(region_name, anomaly_ids, window_label, weather_summary)
        except (
            Exception
        ) as e:  # 크리덴셜·네트워크·타임아웃·빈응답 → template 폴백(데모 안전)
            print(f"[region_summary] aip-logic 실패 → template 폴백: {e!r}")
            return None

    def _get_client(self):
        """OSDK FoundryClient lazy 생성(explainer와 공용 헬퍼 — 동일 규율)."""
        if self._client is None:
            from anomaly.explainer import make_foundry_osdk_client

            self._client = make_foundry_osdk_client(self.timeout)
        return self._client

    def _anomaly_object_set(self, client, anomaly_ids: list[str]):
        """anomaly id 목록 → Foundry AnomalyObjectSet(anomaly_id in_ 필터).

        AIP 함수 입력(anomalies2)이 Object set이므로 로컬 id로 Foundry 객체집합을 참조한다.
        (테스트는 이 메서드를 오버라이드해 OSDK 없이 매핑을 검증한다.)
        """
        from skai_osdk_sdk.ontology.search import AnomalyObjectType

        return client.ontology.objects.Anomaly.where(
            AnomalyObjectType.anomaly_id.in_(list(anomaly_ids))
        )

    def _call(
        self,
        region_name: str,
        anomaly_ids: list[str],
        window_label: Optional[str],
        weather_summary: Optional[str],
    ) -> RegionSummaryResult:
        from anomaly.explainer import allow_beta_features

        client = self._get_client()
        obj_set = self._anomaly_object_set(client, anomaly_ids)

        # ⚠️ 함정(실측): OSDK 시그니처는 window_label·weather_summary를 optional(Empty 기본)로
        # 노출하지만, 배포된 AIP Logic 함수가 프롬프트에서 두 파라미터를 참조하므로 값이 **부재**
        # (Empty)면 런타임 QueryRuntimeError(ReferenceHasNoValue)로 실패한다. 빈 문자열("")은
        # "값 있음"으로 통과한다 → None이면 생략하지 말고 ""로 넘긴다.
        kwargs: dict = {
            "region_name": region_name,
            "anomalies2": obj_set,
            "window_label": window_label or "",
            "weather_summary": weather_summary or "",
        }

        # 응답이 beta StructType → AllowBetaFeatures 컨텍스트 필수(밖이면 BetaWarning 예외).
        with allow_beta_features():
            resp = client.ontology.queries.region_situation_summary(**kwargs)

        summary = (getattr(resp, "summary", "") or "").strip()
        if not summary:
            raise RuntimeError("AIP Logic region-situation-summary 빈 summary")
        overall = (getattr(resp, "overall_assessment", "") or "").strip()
        # confidence는 AIP 산출값 — 방어적으로 [0,1] 클램프.
        conf = float(getattr(resp, "confidence", 0.0) or 0.0)
        conf = min(max(conf, 0.0), 1.0)
        return RegionSummaryResult(
            summary=summary, overall_assessment=overall, confidence=conf
        )
