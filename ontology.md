# ontology.md — 도메인 온톨로지 (Air ISR Fusion Copilot)

**이 프로젝트의 지적 척추.** Palantir 패러다임 = 데이터를 객체(object) + 링크(link) + 액션(action)으로 모델링하고, AIP 에이전트가 그 위에서 추론·행동. 여기서 GPT-wrapper와의 해자가 난다.

> ⚠️ 유저 우려 = "온톨로지가 억지로/얕게 쓰이는 것". 그래서 이 문서는 **스멜테스트를 먼저** 두고, 모든 객체·링크가 그걸 통과함을 보인다.

---

## 0. 온톨로지 스멜테스트 (억지/얕음 방지)
아래 중 **최소 2개**를 만족 못 하는 모델은 온톨로지가 아니라 그냥 테이블이다. 버려라.

1. **다중홉 질의**: 답을 내려면 여러 객체타입을 관계 따라 traverse 해야 한다(1테이블 조인으로 안 됨).
2. **엔티티 해소(entity resolution)**: 같은 실세계 대상이 여러 관측·소스에 흩어져 하나의 객체로 병합돼야 한다.
3. **액션이 상태를 바꾼다**: 사람이 승인하는 Action이 객체를 생성/전이시킨다(단순 조회 아님).
4. **provenance 그래프**: 결론 객체가 근거 객체들을 링크로 인용한다.

**공중 도메인이 통과하는 이유**(아래 §3에서 구체화):
- 이상징후 = Aircraft + OrbitPass + NewsEvent + Region이 **같은 시공간 창**을 공유하는 서브그래프(1행이 아님). → (1)
- 하나의 Aircraft가 수백 Observation으로 흩어져 있고, ADS-B gap·소스 교차로 track custody를 유지해야 함. → (2)
- ConfirmAnomaly / GenerateAssessment / SetAlertLevel = 상태 전이 액션. → (3)
- SituationAssessment → cites → Observation/NewsEvent. → (4)
네 개 다 통과. 억지 아님.

---

## 1. 객체 타입 (Object Types)

| 객체 | 핵심 속성 | 역할 |
|---|---|---|
| **Aircraft** | icao24(PK), callsign, registration, operator_ref, type, is_military | 실세계 항공기 (엔티티 해소 대상) |
| **Observation** | ts, lat, lon, alt, velocity, heading, squawk, on_ground, source, source_url | ADS-B 상태벡터 = **증거 객체** |
| **Track** | aircraft_ref, start_ts, end_ts, path[], has_gap | 한 항공기의 시계열 경로(custody) |
| **Satellite** | norad_id(PK), name, operator_ref, object_type, tle_epoch | 위성 |
| **OrbitPass** | satellite_ref, region_ref, start_ts, end_ts, max_elevation | 관심지역 상공 통과창 |
| **Region** | id(PK), name, geo(polygon), classification(ADIZ/OpArea/civil) | 관심지역/지오펜스 |
| **WeatherState** | region_ref, ts, wind, visibility, ceiling, conditions | 지역 기상 |
| **NewsEvent** | source, url, ts, geo?, title, summary, entities[] | OSINT/뉴스 = **증거 객체**(저신뢰) |
| **Operator** | id(PK), name, kind(airline/airforce/satop), country | 귀속용 주체 |
| **Anomaly** | type, ts, geo, confidence, status(candidate/confirmed/dismissed/**resolved**) | **파생** 이상징후. resolved = 주장(예: 신호 끊김)이 **반증 증거**(복귀 관측, evidenced_by로 연결)로 시스템 해소 — 사람 결정(confirm/dismiss)과 구분 |
| **SituationAssessment** | region_ref, window, summary, produced_by, created_at | **산출 인텔** 객체(요약) |

## 2. 링크 타입 (Link Types) — 여기가 깊이의 원천
| 링크 | 카디널리티 | 왜 필요(flat table로 안 되는 이유) |
|---|---|---|
| Aircraft —observed_as→ Observation | 1:N | custody/entity resolution: 한 기체가 수백 관측 |
| Track —composed_of→ Observation | 1:N | gap 탐지·경로 재구성 |
| Aircraft —operated_by→ Operator | N:1 | 귀속(누구 소속기인가) |
| Observation —within→ Region | N:1 | 공간 포함(지오펜스 진입 판정) |
| OrbitPass —of→ Satellite / —over→ Region | N:1 / N:1 | 위성-지역 시공간 상관 |
| NewsEvent —mentions→ Region/Operator/Aircraft | N:M | **엔티티 링킹**(뉴스↔실체) |
| **Anomaly —evidenced_by→ Observation/NewsEvent/OrbitPass** | N:M | **provenance/citation 백본** |
| **Anomaly —involves→ Aircraft/Satellite** | N:M | 이상징후 주체 연결 |
| **Anomaly —correlated_with→ Anomaly** | N:M | **교차소스 내러티브**(dropout+뉴스+위성통과) |
| SituationAssessment —aggregates→ Anomaly | 1:N | 지역 요약이 이상징후 묶음 |
| SituationAssessment —cites→ Observation/NewsEvent | 1:N | 문장별 근거 |

### 깊이 증명 — "flat table이면 못 하는 질의" 예시
> **질의**: "지난 30분 KADIZ에서 ADS-B가 끊긴 기체가, 위성이 머리 위 지나갈 때, 뉴스가 언급한 지역과 겹치나?"
> **그래프 traverse**: `Region(KADIZ)` ← within ← `Observation(gap 있는 Track)` → aircraft → `Aircraft`; 같은 `Region`·window로 `OrbitPass —over→`; 같은 window로 `NewsEvent —mentions→ Region`. 셋을 `Anomaly —correlated_with→ Anomaly`로 묶어 하나의 "은닉 정황" 내러티브.
> flat table로는 이 4객체 교차를 한 행으로 못 표현 → **온톨로지가 정당하다.**

## 3. 액션 타입 (Action Types) — human-on-the-loop
| 액션 | 행위자 | 효과 |
|---|---|---|
| `CreateAnomaly` | 룰/AIP agent | Anomaly 생성 (evidence 링크 **필수**, 없으면 거부) |
| `ConfirmAnomaly` / `DismissAnomaly` | 분석가 | status 전이 (사람 승인) |
| `GenerateSituationAssessment` | AIP agent | 지역+window로 Assessment 생성, cites 링크 채움 |
| `SetRegionAlertLevel` | 분석가/agent(제안) | Region 경보등급 전이 |

**규칙**: 근거(evidence/cites) 링크 없는 Anomaly·Assessment는 액션이 거부한다. = provenance 강제를 온톨로지 레벨에서 못박음.

## 4. 이상징후 유형 → 온톨로지 매핑
| 유형 | 탐지 = 그래프 패턴 |
|---|---|
| 비상 스쿽 | Observation.squawk ∈ {7500,7600,7700} → CreateAnomaly involves Aircraft |
| ADS-B dropout | 민감구역 내 **현재 신호 침묵**(now−마지막 관측>임계, 폴간격 인지) + 교차소스 미확인 → 저신뢰 Anomaly. Track.has_gap은 경로 재구성용 표식으로 유지 |
| 군용기 접근 | Aircraft.is_military + Observation within Region(OpArea) |
| 로이터링 | Track 경로가 반경 내 반복/원형 |
| 위성 근접/통과 | OrbitPass over Region during window |
| 항적↔뉴스 상관 | Anomaly(track) correlated_with NewsEvent(mentions Region) |

## 5. AIP/OSDK 구현 노트
- 이 객체·링크·액션을 **Foundry Ontology**에 그대로 정의(Object Types, Link Types, Action Types).
- OSDK로 타입드 클래스 생성 → 커넥터가 Observation/OrbitPass 등을 write, AIP Logic이 Anomaly/Assessment를 액션으로 생성.
- 상세: `aip-integration.md`.

### 스코프 규율
객체를 늘리고 싶을 때마다 §0 스멜테스트를 다시 통과시켜라. 통과 못 하면 그건 속성이지 객체가 아니다.
