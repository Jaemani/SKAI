# DR-0003 — P1 진입 + 저장 어댑터 분리 + 얇은 프론트

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P0 종합 (`docs/worklog/P0.md`) · DR-0002 §5

## 맥락
P0-A 통과(4소스 생존 + gotcha 8건 확보), P0-B는 사용자 UI 액션 대기. PROMPTS.md P1은 "저장은 Foundry 온톨로지(폴백 시 동일 스키마 SQLite)"인데 Foundry 왕복이 아직 안 뚫림. 프론트는 architecture.md에 React/Vite 명시.

## 결정
1. **P1 즉시 진입.** OpenSky→온톨로지→지도 수직관통을 지금 구축.
2. **저장 어댑터 분리**: `OntologyStore` 인터페이스(공통) + `store_local.py`(SQLite, ontology.md 스키마 미러) + `store_foundry.py`(스텁 — 크리덴셜 도착 시 구현). 커넥터·트랙·API는 인터페이스에만 의존 → Foundry가 뚫리면 구현 교체만. **provenance 강제(source·source_url·ts 없는 write 거부)는 store 레벨에 지금부터 못박는다** (온톨로지 Action 레벨 강제의 선행 구현).
3. **P1 프론트는 빌드 없는 얇은 구성**: FastAPI(JSON API + 정적 서빙) + vanilla Leaflet 단일 페이지, 30초 갱신. React/Vite 전환은 P4(채팅 UI 필요 시점)에 재판단.
4. **P0-A gotcha 8건을 커넥터 구현 스펙에 주입** (callsign strip·squawk str 비교·크레딧 헤더 모니터링 등).
5. 실행은 opus 에이전트 1개(단일 수직관통의 응집성 우선, 분할 병렬화 안 함).

## 기각 대안
- **Foundry 뚫릴 때까지 P1 보류** — 24H에서 크리티컬 패스 낭비. 기각.
- **React/Vite로 처음부터** — P1 성공기준은 "지도에 실항적"이지 SPA가 아님. 빌드 툴체인은 지금 마찰만 추가. 기각(P4 재판단).
- **SQLite를 그냥 메인으로** — AIP-spine 위반. store 인터페이스 뒤에 숨겨 "동일 스키마 보험" 지위를 코드 구조로 명시. 기각.

## 영향
- 디렉터리 신설: `connectors/` `ontology/` `server/` `web/`. 이후 P2(이상탐지)는 store 인터페이스 위에 얹힘.
- 되돌리기: 신규 디렉터리 삭제. 루트 문서 무변경.
