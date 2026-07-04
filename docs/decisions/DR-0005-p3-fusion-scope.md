# DR-0005 — P3 진입 + 융합 확장 스코프

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P2 검증 통과 (`docs/worklog/P2-anomaly.md`) · PROMPTS.md P3 · data-sources.md 실응답 확인 소절

## 맥락
P3는 Celestrak(→OrbitPass)·METAR(→WeatherState)·GDELT(→NewsEvent)를 온톨로지에 통합. 열려 있는 선택지: 위성 카탈로그 범위(active 그룹은 수천 기 — 전부 전파 계산은 낭비), 뉴스 엔티티 링킹 방식(NER vs 키워드), 신뢰도 가중.

## 결정
1. **위성 카탈로그는 경량 그룹 한정 + 설정 교체 가능**: 기본 `stations`+`visual` 수준(수십~수백 기)으로 OrbitPass 계산을 데모 시간 내로. GROUP은 상수/환경변수로 교체 가능하게. TLE는 12h 캐시(architecture.md 폴링 주기 준수, Celestrak 캐시 존중).
2. **GDELT 5초 규율을 커넥터에 하드코딩**: P0-A에서 IP 429 실측 — 호출 간 최소 5초를 코드 레벨 강제(우회 아님, 준수 자동화).
3. **뉴스 confidence ≤ 0.4 고정 + 키워드 엔티티 링킹**: NewsEvent는 저신뢰 증거(교차검증용). mentions 링크는 지역명 별칭 사전(KADIZ·한반도·서해 등) 키워드 매칭 — NER 파이프라인은 해커톤 범위 밖(과설계). Operator/Aircraft 링킹은 콜사인·기관명 exact match만.
4. **이상탐지 룰 추가는 P3 범위 밖**: P3는 "객체 통합 + 시공간 정렬"까지. 위성/뉴스가 낀 이상탐지(correlated_with)는 P5 — 스코프 규율(ontology.md §스코프 규율) 준수.

## 기각 대안
- active 전체(~1만 기) 전파 계산 — 데모 가치 없이 CPU·시간 소모. 기각.
- LLM 기반 뉴스 NER — 비용·재현성 대비 이득 없음(지역 1곳 고정 데모). 기각. P4 코파일럿이 질의 시점에 LLM 서술로 보완.

## 영향
- 온톨로지 v0.1의 OrbitPass·WeatherState·NewsEvent·Operator + 링크(of/over/mentions)가 코드로 구현됨 — 스키마 신규 정의 없음.
- P5의 correlated_with 내러티브가 이 객체들 위에 얹힘.
