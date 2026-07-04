# DR-0006 — P4 코파일럿: citation 강제 구조 + 질의 오케스트레이션

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P3 검증 통과 (`docs/worklog/P3-fusion.md`) · aip-integration.md §3 원칙 · DR-0004 패턴 계승

## 맥락
P4 = 자연어 질의 → 병렬 read → 이상탐지 병합 → SituationAssessment(문장별 cites). 핵심 위험: LLM이 문장을 생성하면 citation이 사후 장식이 되어 환각 방지가 무너짐. AIP Logic(원설계의 추론 엔진)은 여전히 BLOCKED.

## 결정
1. **citation은 생성이 아니라 조립으로 강제한다 (역방향 금지)**:
   - 파이프라인이 store 질의 결과(객체 id 달린 사실)를 먼저 확정 → **문장은 사실 단위로 조립**되며 각 문장이 근거 객체 id를 갖고 태어남.
   - LLM(옵션)은 조립된 사실문장의 **서술만 다듬고**, cites 매핑은 룰 측에 남는다(aip-integration.md §3: "사실추출·citation 매핑은 룰+온톨로지로 하드하게").
   - cites 없는 문장은 Assessment에 못 들어감 — P2 EvidenceError 패턴을 `write_assessment`에 재사용(store 레벨 거부).
2. **질의 파싱은 결정적 파서 기본**: 지역(KADIZ 고정 + 별칭)·시간창("지금/최근 N분/시간") 룰 파싱. LLM 파싱은 비목표(데모 질의 3개가 확정적이라 과설계).
3. **SituationAssessment를 온톨로지 객체로 저장**: aggregates→Anomaly, cites→Observation/NewsEvent/OrbitPass 링크 포함(ontology.md §2). 채팅 답변 = Assessment 객체의 뷰. Q&A가 아니라 "산출 인텔 객체 생성"임을 구조로 보임.
4. **서브그래프 뷰 포함**(PROMPTS P4 성공기준): 선택 Assessment/Anomaly 중심의 객체-링크 그래프를 경량 자체 렌더(SVG/canvas)로 — 외부 그래프 라이브러리 도입 안 함.
5. **P3 이월 이슈 #1 동시 수정**: celestrak 재계산 시 해당 위성의 future-pass 선삭제(stale 누적 차단).

## 기각 대안
- **LLM 자유 생성 + 사후 citation 매칭** — 문장↔근거 정합을 사후 검증해야 하고 실패 시 환각 잔존. "근거 객체 없는 주장 출력 금지"(CLAUDE.md 원칙 4)를 구조로 못 지킴. 기각.
- **LLM 질의 파서** — 데모 질의가 한정적, 결정적 파서가 재현성 우위. 기각(P6 후 여유 시 재검토).
- **그래프 라이브러리(d3/vis) 도입** — 서브그래프는 수십 노드 규모, 자체 SVG로 충분. 의존 추가 기각.

## 영향
- GenerateSituationAssessment 액션이 코드로 구현됨(온톨로지 v0.1 액션 4종 중 3종째). SetRegionAlertLevel만 잔여(P5/P6).
- Foundry 개통 시: 조립 파이프라인은 AIP Logic 함수로, store 질의는 OSDK read로 치환 — 구조 불변.
