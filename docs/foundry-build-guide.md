# Foundry Ontology Manager 구축 가이드 (전량 스키마)

> 2026-07-04 기준. 실측(P7 §7) 반영. 순서대로 진행 후 **마지막에 OSDK 재발행 1회**.
> 네이밍 컨벤션: 속성은 기존과 동일하게 camelCase (isMilitary·aircraftIcao24·geoJson 패턴).

## ⚠️ 공통 함정 (매 항목 적용)

1. **create-* 액션마다 PK 파라미터가 속성에 바인딩**돼야 함. "Add parameter"만 하면 `newParameter`로 생기고 기능은 해도 이름이 혼란 — **파라미터 이름을 PK 이름으로 바꾸고** Create object 규칙에서 PK 속성에 연결.
2. 링크는 두 방식: **FK 기반**(1:N — 자식 객체에 FK 속성을 두고 링크로 지정, observed_as 방식) / **MANY-MANY**(N:M — 링크 타입만 생성, 액션의 링크 파라미터로 채움).
3. N:M 링크를 채우려면 **액션에 링크 파라미터**가 있어야 함(없으면 그 링크는 영원히 빈다 — create-anomaly evidence가 이 문제였음).
4. 배열/폴리곤 속성은 **JSON 문자열**(String)로: `pathJson`·`entitiesJson`·`geoJson`.
5. required는 정말 필수인 것만(결측 가능 텔레메트리는 optional).
6. 저장 후 **배포(Deploy/Save all)까지**.

---

# A부 — 기존 수정 (최우선, ~15분)

## A-1. `create-anomaly`에 evidence 강제 ⭐데모 핵심
- 링크 파라미터 추가: **evidence** → `evidenced_by`(Anomaly↔Observation MANY-MANY, 기존 링크) 바인딩, **Required**
- 효과: "근거 없는 이상징후는 생성 거부"가 Palantir 온톨로지 레벨에서 강제됨 (ontology.md §3)

## A-2. `create-anomaly` 파라미터 3개 추가
- `confidence`(Double) · `status`(String, 기본 "candidate") · `explanation`(String) — 각각 동명 속성 바인딩. 현재는 전부 None으로만 생성됨.

## A-3. involves·correlated_with 채울 수단
- `create-anomaly`에 링크 파라미터 2개 추가(Optional): **involves** → `involves`(Anomaly↔Aircraft) / **correlatedWith** → `correlated_with`(Anomaly↔Anomaly)

## A-4. `confirm-anomaly` / `dismiss-anomaly` 액션 신설
- 각: 대상 `Anomaly`(object 파라미터) + Modify object 규칙으로 `status`를 `"confirmed"` / `"dismissed"` 고정값 설정
- (대안 최소형: edit-anomaly에 status 파라미터 추가 — 단 데모 버튼 서사는 별도 액션이 좋음)

## A-5. Region 보수
- `create-region`에 PK(`id`) 바인딩 파라미터 추가 (현재 UUID 자동 → KADIZ를 고정 id로 못 만듦)
- 속성 추가: `alertLevel`(String)
- 액션 신설: **`set-region-alert-level`** — 대상 Region + `alertLevel`(String) 파라미터 → v0.1 액션 4종 완성

## A-6. (권장) `newParameter` 리네임
- create-aircraft→`icao24`, create-observation→`obsId`, create-anomaly→`anomalyId` (기능 정상, 혼동 방지용)

---

# B부 — 신규 Object Type 7종 (~40분)

> 각 항목: 생성 마법사에서 create/edit/delete 액션 자동 생성 체크 + **create 액션 PK 바인딩 확인**(공통 함정 1).

## B-1. Operator (귀속 주체)
| 속성 | 타입 | 비고 |
|---|---|---|
| `operatorId` | String | **PK** |
| `name` | String | |
| `kind` | String | airline/airforce/satop |
| `country` | String | |

**링크(operated_by)**: 기존 Aircraft의 `operatorRef` 속성을 FK로 → Aircraft(many)→Operator(one) 링크 지정 (Aircraft 편집)

## B-2. Track (항적 custody)
| 속성 | 타입 | 비고 |
|---|---|---|
| `trackId` | String | **PK** |
| `aircraftIcao24` | String | FK→Aircraft (링크 지정) |
| `startTs` / `endTs` | Timestamp | |
| `hasGap` | Boolean | |
| `pathJson` | String | 경로 점열 JSON |

**링크(composed_of)**: 기존 Observation에 `trackId`(String) 속성 추가 → Track(one)→Observation(many) FK 링크
**edit-track**: `endTs`·`hasGap`·`pathJson` 갱신 가능해야 함(custody 연장)

## B-3. Satellite
| 속성 | 타입 | 비고 |
|---|---|---|
| `noradId` | String | **PK** |
| `name` | String | |
| `operatorRef` | String | (선택: Operator FK) |
| `objectType` | String | |
| `tleEpoch` | Timestamp | |

## B-4. OrbitPass (통과창)
| 속성 | 타입 | 비고 |
|---|---|---|
| `passId` | String | **PK** |
| `satelliteNoradId` | String | FK→Satellite = 링크 `of` |
| `regionId` | String | FK→Region = 링크 `over` |
| `startTs` / `endTs` | Timestamp | |
| `maxElevation` | Double | |

**delete-orbitpass 필수** (재계산 시 미래 pass 정리 — P4에서 로컬에 구현된 로직과 동일)

## B-5. WeatherState
| 속성 | 타입 | 비고 |
|---|---|---|
| `weatherId` | String | **PK** |
| `regionId` | String | FK→Region |
| `ts` | Timestamp | |
| `wind` | String | "200°/8kt", VRB 대응 위해 String |
| `visibilitySm` | Double | statute miles |
| `ceilingFt` | Double | 피트 |
| `conditions` | String | MVFR 등 |
| `rawText` | String | METAR 원문 |
| `source` / `sourceUrl` | String | provenance |

## B-6. NewsEvent (OSINT 증거 — GDELT + **StealthMole** 공용)
| 속성 | 타입 | 비고 |
|---|---|---|
| `newsId` | String | **PK** |
| `source` | String | "gdelt" / "stealthmole" / … |
| `url` | String | |
| `ts` | Timestamp | |
| `title` | String | |
| `summary` | String | |
| `entitiesJson` | String | 엔티티 배열 JSON |
| `confidence` | Double | ≤0.4 (저신뢰 OSINT) |
| `lat` / `lon` | Double | optional |

**링크(mentions — 대상 타입별 분리)**: `mentions_region`(NewsEvent↔Region N:M) · `mentions_aircraft`(↔Aircraft N:M) · `mentions_operator`(↔Operator N:M)
**create-news 액션에 세 링크 파라미터(Optional) 포함** (공통 함정 3)

## B-7. SituationAssessment (산출 인텔)
| 속성 | 타입 | 비고 |
|---|---|---|
| `assessmentId` | String | **PK** |
| `regionId` | String | FK→Region |
| `windowStart` / `windowEnd` | Timestamp | |
| `summary` | String | |
| `confidence` | Double | |
| `producedBy` | String | |
| `createdAt` | Timestamp | |

**링크**: `aggregates`(Assessment↔Anomaly N:M) · `cites_observation`(↔Observation) · `cites_news`(↔NewsEvent) · `cites_orbitpass`(↔OrbitPass)
**create-assessment 액션**: PK 바인딩 + 위 4개 링크 파라미터(aggregates·cites_* Optional — 문장 단위 강제는 코드가 담당, 단 cites 하나는 채우는 습관)

## B-8. Anomaly evidence 확장 (A-1의 후속)
- evidenced_by를 Observation 외 **NewsEvent·OrbitPass**로도: `evidenced_by_news`(Anomaly↔NewsEvent N:M) · `evidenced_by_orbitpass`(Anomaly↔OrbitPass N:M) 링크 신설 + create-anomaly에 Optional 링크 파라미터 (은닉 정황 내러티브의 Foundry 재현에 필요)

---

# C부 — 마무리 (~5분)

1. 모든 변경 저장·배포 확인
2. **Developer Console → OSDK 재발행**: Object 11종 + **Action 전부** 체크 → 새 버전
3. "전량 구축 + 재발행 완료" 신호 → 코드측이 이어받음: store_foundry 확장(Anomaly·Track·OrbitPass·WeatherState·NewsEvent·Assessment write) → 전량 이관 검증 → 데모의 Foundry 스텝 교체

> 진행 중 화면이 가이드와 다르면(파라미터 바인딩 UI를 못 찾는 등) 그 지점을 알려주세요.
