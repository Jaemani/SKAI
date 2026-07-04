# DR-0009 — Foundry 이관: 하이브리드 스토어

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P0-B 왕복 검증(P0B-foundry.md §8) · 사용자 Foundry 스키마 확장(Aircraft 보강·Observation·observed_as·OSDK 재발행, 2026-07-04)

## 맥락
Foundry 측 온톨로지는 현재 Aircraft·Observation(+observed_as)만 구축됨. 로컬 온톨로지는 11객체 전부 사용 중. 전량 이관은 나머지 스키마(Track·Anomaly·Assessment·OrbitPass·WeatherState·NewsEvent·Region + 액션들)의 UI 구축이 선행돼야 해서 지금 불가.

## 결정
1. **HybridStore**: 코어 엔티티(Aircraft·Observation·observed_as 링크)는 **Foundry에 write/read**, 나머지 객체는 로컬 SQLite 유지. `SKAI_STORE=foundry` 환경변수로 활성화(기본은 기존 local — 데모 재현성 보존).
2. 쓰기는 액션 경유(하이브리드 read=OSDK/write=액션, §8-3 패턴). 배치 액션 API가 있으면 사용(폴링 사이클당 호출 수 절감).
3. provenance 강제(source·source_url·ts 검증)는 스토어 앞단에서 동일 적용 — 백엔드가 바뀌어도 불변식 유지.
4. Foundry 스키마가 v0.1 스펙과 다른 지점(속성 누락·타입 차이·액션 파라미터 부재)은 이관 에이전트가 **실측 introspection으로 확정**하고 갭 목록을 기록 — 추측 매핑 금지.
5. 이후 사용자가 나머지 스키마를 구축하는 대로 객체 단위로 Foundry 측 구현을 늘림(인터페이스 불변).

## 기각 대안
- 전량 이관 대기 — 데모까지 Foundry에 실데이터가 한 건도 못 들어감. "Palantir 배포 패러다임 적합성"(심사 30%) 어필 기회 상실. 기각.
- 로컬 스키마를 Foundry 현황에 맞춰 축소 — 본말전도. 기각.

## 영향
- 데모에서 "실 ADS-B 데이터가 Palantir 온톨로지 객체로 들어가고 OSDK로 읽힌다"를 실연 가능.
- 되돌리기: SKAI_STORE 미설정이면 기존 동작 그대로(순수 로컬).
