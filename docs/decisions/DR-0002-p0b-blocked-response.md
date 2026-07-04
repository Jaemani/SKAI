# DR-0002 — P0-B BLOCKED 대응 전략

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P0-B 정찰 결과 (`docs/worklog/P0B-foundry.md`)

## 맥락
P0-B(opus)가 Foundry/OSDK 파이프를 정찰한 결과 **BLOCKED**: ① 로컬 크리덴셜 0건, ② 온톨로지 스키마 생성이 UI 전용(생성 API 미출시, foundry-platform-python #318), ③ OSDK 발행도 Developer Console UI 전용, ④ 생성 OSDK는 Python <3.13 요구인데 이 머신은 3.14.5. PROMPTS.md 원칙: "AIP 막히면 원인 기록 + 멘토 질문 목록화. 폴백은 보험이지 전환 아님."

## 결정
1. **AIP-spine 유지, 폴백 비전환.** SQLite 폴백으로 갈아타지 않는다. Foundry UI 단계는 사용자(또는 사용자 승인 하 브라우저 세션)가 수행하고, 그 사이 엔지니어링은 멈추지 않는다.
2. **정정 5건을 `aip-integration.md`에 즉시 반영** (§0-보강 + §3 스니펫). 검증된 사실이 SSOT에 없으면 다음 에이전트가 같은 함정을 다시 밟는다.
3. **Python 3.12 venv 선제 준비를 자동 실행** (사용자 액션 5개 중 #5를 에이전트가 흡수 → 사용자 몫은 UI 4개로 축소).
4. **저수준 `foundry_sdk`(1.97.0, 3.14 지원)를 대체 경로 후보로 병기** — 생성 OSDK가 3.12 요구로 계속 마찰이면 platform API로 객체 왕복 가능한지 Morph 멘토에 확인(질문 #4).
5. **P0-A 결과 수신 후**: 소스가 살아있으면 P1 커넥터를 저장 백엔드 착탈형(온톨로지 매핑 레이어 분리)으로 설계해 Foundry 크리덴셜 도착 즉시 꽂을 수 있게 한다. 이는 폴백 전환이 아니라 대기시간 흡수.

## 기각 대안
- **SQLite 폴백 즉시 전환** — PROMPTS.md 명시 위반("전환 아님"). 온톨로지 깊이(Action·staged review·provenance 강제)는 Foundry에서만 심사 어필 가능. 기각.
- **사용자 UI 완료까지 전면 대기** — 24H 해커톤에서 30~40분 낭비. 병렬 가능한 것(3.12 env, P1 설계)이 명확. 기각.
- **브라우저 자동화로 UI 단계 대행** — 사용자 Palantir 계정 조작은 외부 시스템 변경, 사전 승인 필요. 무단 실행 기각. 사용자에게 옵션으로만 제시.

## 영향
- 사용자 액션 4개(로그인/enrollment 확인 → 토큰 발급·.env 저장 → Ontology Manager 스키마 생성 → OSDK 발행)가 P0-B 완주의 크리티컬 패스.
- 되돌리기: aip-integration.md 정정은 git diff로 역추적 가능. 3.12 venv는 디렉터리 삭제로 원복.
