# DR-0004 — P2 진입 + AnomalyExplainer 백엔드 분리

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P1 검증 통과 (`docs/worklog/P1-vertical.md`) · DR-0002(폴백 비전환) 패턴 계승

## 맥락
PROMPTS.md P2는 "룰 → **AIP Logic 설명**·신뢰도 → CreateAnomaly(evidence) → 화면"인데, AIP Logic은 Foundry UI 개통 전까지 접근 불가(P0-B BLOCKED). 한편 aip-integration.md §3 원칙: "사실추출·citation 매핑은 룰+온톨로지로 하드하게, **서술·설명만 LLM**."

## 결정
1. **P2 즉시 진입.** 비상 스쿽 1종의 끝단(탐지→설명→Action→화면)을 지금 구축.
2. **ExplainerBackend 인터페이스 분리** (store 어댑터와 동일 패턴):
   - `TemplateExplainer` (기본): 룰 컨텍스트에서 결정적 설명문+신뢰도 생성. LLM 없이 항상 동작 — 데모 재현성 보장.
   - `ClaudeCliExplainer` (옵션): `claude -p`로 서술 강화(로컬 Max 구독 활용, API 키 불요). 실패 시 template 자동 폴백.
   - `AipLogicExplainer` (스텁): Foundry 개통 시 이관 대상 — 최종 목표임을 코드에 명시.
3. **evidence 강제는 Action 레벨에서**: CreateAnomaly가 evidence 링크 없으면 거부(ontology.md §3 규칙). P1의 validate_provenance 재사용. 합성 주입도 `source="synthetic"`으로 provenance 유지(P1 발견 #3).
4. confirm/dismiss 상태 전이(candidate→confirmed/dismissed)를 API+UI로 — human-on-the-loop를 P2에서 시연 가능하게.

## 기각 대안
- **AIP Logic 개통까지 P2 보류** — 크리티컬 패스 낭비, 기각.
- **Claude API(키) 직접 통합** — 사용자는 Max 구독(API 키 별도), 데모 중 외부 의존 추가. claude-cli가 동등 효과. 기각.
- **LLM 설명을 필수 경로로** — 데모 재현성 훼손(네트워크·쿼터 의존). 설명의 사실 부분은 룰이 이미 보장, LLM은 서술 강화만. 기각.

## 영향
- P4(코파일럿)의 GenerateSituationAssessment도 같은 백엔드 패턴을 재사용할 것.
- 되돌리기: anomaly/ 모듈 삭제. 온톨로지 스키마 변경 없음(v0.1 그대로).
