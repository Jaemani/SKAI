"""이상탐지(Anomaly Engine) 패키지 — architecture.md §3.

룰(rules)로 후보 탐지 → explainer로 설명·신뢰도 → actions.CreateAnomaly로
Anomaly 생성(evidence 링크 필수). P2 범위 = 비상 스쿽 1종.

AIP-spine: 룰은 사실을 하드하게 확정, explainer(LLM)는 서술만 강화
(aip-integration.md §3). 최종 목표는 AIP Logic 이관(explainer.AipLogicExplainer).
"""
