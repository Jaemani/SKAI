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

---

# D부 — 액션 규칙(Rules) 트러블슈팅 (2026-07-04 전량 재검증 결과)

> Object 11종은 전부 완성됐다. 남은 문제는 **액션의 "규칙(Rules)" 배선**이다.
> Foundry 액션 = **파라미터** + **규칙(Rules)** 2층. 파라미터만 만들고 규칙에 안 엮으면 값이 아무 데도 안 들어간다.
> 규칙 종류: **Create object / Modify object / Add link / Remove link / Delete object**.
> (아래 UI 경로 세부는 버전마다 다를 수 있음 — 개념 기준으로 찾고, 막히면 화면 상태 공유.)

## D-1. ⭐⭐ create-anomaly 재배선 (데모 최우선 — 지금 3중 결함)

**증상**: ① Observation을 근거로 못 붙임(evidence 파라미터 없음) ② aircraft·newsEvents·orbitPasses가 required라 뉴스·위성 없는 이상징후도 억지 ref 필요 ③ 유효 ref를 다 줘도 ApplyActionFailed(단 Anomaly 스칼라는 생성됨 = 반쪽 객체).

**고칠 것**:
1. **evidence 파라미터 신설**: `evidence` (type = **Observation, 다중/리스트**) 추가 → **Add link 규칙**으로 `evidenced_by`(Anomaly↔Observation) 링크에 바인딩. **Required.** ← 이게 "근거 없는 이상징후 거부"의 진짜 구현.
2. **aircraft·newsEvents·orbitPasses를 Optional로 강등** (또는 involves/evidenced_by_news/evidenced_by_orbitpass를 채우는 별도 링크 규칙으로 분리하고 required 해제). 관측 기반 이상징후가 뉴스·위성 없이 생성돼야 함.
3. **newParameter1(orphan) 제거** — 어디에도 안 들어가는 required junk. ApplyActionFailed의 유력 원인 후보.
4. **ApplyActionFailed 디버깅**: 위 정리 후에도 실행 실패가 남으면, create-anomaly의 규칙 목록에서 "Create Anomaly" 외에 **부가 Add link 규칙이 존재하지 않는 링크/파라미터를 참조**하는지 확인(에러 상세가 안 나와서 규칙 하나씩 소거로 범인 찾기). 막히면 규칙 스크린샷 공유.

## D-2. ⭐ set-region-alert-level 재작성 (지금 Create로 오작동)

**증상**: 대상 Region 파라미터가 없고 `alertLevel` 하나뿐 → 실행하면 기존 Region을 수정하는 게 아니라 **alertLevel만 채운 빈 Region을 새로 생성**(팬텀 Region 양산).

**고칠 것**: 액션을 지우고 다시 만들거나 규칙 교체 —
- 파라미터: **대상 Region** (object) + `alertLevel` (String)
- 규칙: **Modify object**(Create 아님!) → 그 Region의 `alertLevel` 속성을 파라미터 값으로 설정.
- ✅ 참고: **confirm-anomaly가 이 패턴으로 정상 작동**한다(대상 object 파라미터 + Modify로 status 전이). 그걸 복제하면 됨.

## D-3. 신규 7타입 create에 PK 파라미터 (엔티티 해소)

operator·satellite·orbit-pass·track·weather-state·news-event·situation-assessment의 create 액션이 전부 **PK 파라미터 없이 UUID 자동**이다. create-aircraft가 하듯:
- 각 create 액션에 PK 파라미터 추가(`operatorId`·`noradId`·`passId`·`trackId`·`weatherId`·`newsId`·`assessmentId`) → **Create object 규칙에서 primary key에 바인딩**.
- 특히 **Satellite(noradId)·Operator·OrbitPass**는 실세계 안정키 dedup이 필수(같은 위성=같은 noradId). UUID면 매 인제스트마다 중복 생성됨.

## D-4. 링크 FK/규칙 지정 완료 (속성은 있는데 링크가 없음)

아래는 **속성 값은 저장되는데 링크(그래프 엣지)가 안 맺혀** traverse가 안 된다. Object Type의 Links 탭에서 FK 링크로 지정:
- **OrbitPass.satelliteNoradId → `of`(Satellite)** + **OrbitPass.regionId → `over`(Region)** ← 위성 통과 서사
- **Observation → `within`(Region)** ← 지오펜스 진입 판정(ontology.md §2 백본). Observation에 `regionId`(String) 속성 추가 후 FK 링크.
- **Track.aircraftIcao24 → Aircraft** · **WeatherState.regionId → Region** · **SituationAssessment.regionId → Region**

## D-5. composed_of 채움 수단

Track↔Observation 링크(FK=`trackId`)는 만들어졌으나 **create/edit-observation에 `trackId` 파라미터가 없어** 채울 수가 없다. edit-observation에 `trackId` 파라미터 추가(custody 확정 시 관측을 트랙에 귀속).

## D-6. self-link required junk 제거

create-news-event의 `newsEvents`(자기참조), create-situation-assessment의 `situationAssessments`(자기참조)가 **required junk**(첫 객체도 존재하지 않는 self ref를 넣어야 통과). Optional로 강등 또는 제거.

## D-7. (권장·낮은 우선순위)

- `newParameter` → `icao24`/`obsId`/`anomalyId`/`id` 리네임(기능 정상, 자기문서화)
- edit-aircraft.isMilitary 타입 String→Boolean(객체 속성·create와 불일치)
- `editrack`↔`edit-track` 중복 명명 정리
- **OSDK 0.4.0 누락분**: `delete-orbit-pass`·`editrack`이 재발행에 빠짐 → 다음 재발행 때 포함

## D-8. 마무리

D-1·D-2만 끝나도 **데모 provenance 백본(Observation 근거 → 이상징후 → 상태전이 → set-alert)이 Foundry에서 완성**된다. D-3~D-6은 융합 완성도(전량 이관)용. 전부 반영 후 **OSDK 재발행**(D-7의 누락분 포함) → 코드측이 store_foundry 확장으로 이어받음.

---

# E부 — 완성도 라운드 (2026-07-04, §11~§13 배선 후 잔여 전량)

> 백본은 완성(8타입 write/read·Anomaly evidence 강제·confirm 전이). 아래는 **그래프 완성도·위생·dual 해소**.
> 순서 권장: E-1(안전 위생) → E-2(그래프) → E-3(속성) → **E-4(리네임, 몰아서)** → E-5(재발행 1회) → 코드측 재검증·갱신.

## E-1. 액션 위생 (코드 영향 없음 — 바로 해도 안전)

1. **`editrack` 삭제** — `edit-track`(표준)과 중복. 코드는 둘 다 참조 안 함(확인됨) → 커스텀 `editrack`을 지우고 표준 `edit-track` 유지.
2. **edit-aircraft의 `isMilitary` 타입 String→Boolean** — 객체 속성·create와 불일치.
3. **create-anomaly 가짜 에러 제거** — 실행마다 무해 ApplyActionFailed(코드가 흡수 중). 액션 Rules에서 **Create object + evidenced_by/involves Add-link 외의 부가 규칙**(알림·함수·잔여 add-link)을 하나씩 소거해 원인 제거. VALIDATE는 통과하고 EXECUTE만 실패하는 패턴 = 실행 단계 부가 규칙이 범인.

## E-2. 그래프 완성 (traverse 가능해지는 것)

1. **over**: OrbitPass의 `regionId` 속성 → **Region FK 링크로 지정** (§9부터 잔존 — 위성 통과↔지역 그래프 조인).
2. **within 채움**: create-observation(및 edit-observation)에 **`regionId` 파라미터 추가 + 속성 바인딩** — 링크 구조는 이미 있는데 채울 수단이 없음. 지오펜스 진입 판정 그래프 완성.
3. **correlated_with 채움**: create-anomaly에 `correlatedWith`(Anomaly, **Optional**) 링크 파라미터 추가(또는 별도 `add-correlation` 액션) — 은닉 정황 내러티브를 Foundry 그래프에도.
4. **create-news-event의 mentions 파라미터(aircraft·operators·regions) Optional화** — 현재 required라 뉴스 인제스트가 억지 ref 마찰(§11 best-effort). Optional이면 dual 단순화.
5. (선택·⚠️신중) create-anomaly `observations`를 단일→**다중(objectSet)** — 다중 근거 엣지 가능해짐. 단 §12에서 잘못 배선된 objectSet이 문제였던 전례가 있으니, 바꾸면 **즉시 재검증 요청**. 현행 단일도 코드(로컬 권위본)가 커버하므로 무리하지 않아도 됨.

## E-3. 속성 보강 (코드의 dual/폴백 제거 — 각 String/Timestamp 1개씩)

| Object | 추가 속성 | 해소되는 것 |
|---|---|---|
| WeatherState | `station`(String) | weatherId에서 역복원하는 꼼수 제거 |
| OrbitPass | `groundTrackJson`(String) | 지도 궤적 레이어를 Foundry read로 |
| SituationAssessment | `sentencesJson`(String) | 문장별 cites의 Foundry 보존(현재 로컬 권위본) |
| Anomaly | `createdAt`(Timestamp) · `explainerBackend`(String) | 미저장 필드 보존 |
| Observation | `attrsJson`(String) | origin_country 등 부가 필드(§5 잔존) |

## E-4. ⚠️ 리네임 라운드 (몰아서 한 번에 — 코드 동시 수정 필요)

`newParameter` → 각 create 액션의 실제 PK명으로: `icao24`(aircraft)·`obsId`(observation)·`anomalyId`(anomaly)·`id`(region)·`operatorId`·`noradId`·`passId`·`trackId`·`weatherId`·`newsId`·`assessmentId`.
**주의**: 코드(store_foundry.py) 16곳이 `newParameter`로 호출 중 → **부분 리네임 = 그 타입 write 즉시 파손.** 전부 몰아서 바꾼 뒤 "리네임 완료"라고 알려주면 코드 일괄 갱신 + 재검증한다.

## E-5. OSDK 재발행 (마지막 1회)

- Object 11종 + **Action 전부 체크 — 특히 `set-region-alert-level`**(0.5.0·0.6.0 연속 누락 → 발행 화면에서 체크 여부 직접 확인).
- 재발행 후 신호 → 코드측: introspection 재검증 → 리네임 반영 → 신규 속성/링크 배선 → 저수준 폴백 제거 검토.
