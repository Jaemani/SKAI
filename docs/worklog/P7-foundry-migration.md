# P7 — Foundry 하이브리드 이관 (실행 로그)

- 날짜: 2026-07-04
- 담당: opus 서브에이전트 (P7)
- 근거 결정: `docs/decisions/DR-0009-foundry-hybrid-store.md`
- 목표: DR-0009대로 코어 엔티티(Aircraft·Observation·observed_as)를 Foundry에, 나머지는 로컬에 두는
  HybridStore 구현 + 실측 introspection으로 갭 확정 + 왕복/실데이터 검증.
- 제약 준수: 시크릿 값 미출력 · Foundry 쓰기 최소 · 스키마 변경 시도 없음(갭은 기록만) ·
  OpenSky 1회 호출 · git commit 없음.

---

## 0. 판정: **부분(갭)** — 코드·read 완성/검증, **write는 Foundry 액션 블로커(사용자 UI 필요)**

- **완성·검증**: store_foundry(FoundryOntologyStore + HybridStore) 구현, 라우팅·provenance
  단위테스트 13개 통과, read 경로를 실 Foundry에 붙여 검증(Aircraft 3건 read-back).
- **블로커**: `create-aircraft`·`create-observation` 액션이 **실행 자체가 실패**(ApplyActionFailed).
  → 현재 Foundry에 실데이터 write 불가. 액션 재구성(UI)이 선결.

---

## 1. Introspection 결과 (저수준 `foundry_sdk` 1.97.0, 라이브 read — `scripts/p7_introspect.py`)

### 1-1. Object Type (사용자 제작 2종 — 나머지 예제 온톨로지 11종 제외)

**Aircraft** (PK=`icao24`, status=EXPERIMENTAL) — v0.1 스펙 6속성 **전부 존재**:
| 속성 | 타입 | P0B 대비 |
|---|---|---|
| icao24 (PK) | string | — |
| callsign | string | — |
| registration | string | — |
| **type** | string | **신규 추가** |
| **operatorRef** | string | **신규 추가** |
| **isMilitary** | **boolean** | P0B에선 string이었음 → **boolean으로 수정됨** |

→ **Aircraft Object Type은 스펙 정합 완료.** (사용자 보강 반영 확인)

**Observation** (PK=`obsId`, status=EXPERIMENTAL) — 신규 생성:
`obsId`(PK, string), `aircraftIcao24`(string, FK), `ts`(**timestamp**), `lat/lon/alt/velocity/heading`(double),
`squawk`(string), `onGround`(boolean), `source`(string), `sourceUrl`(string).
→ provenance 3필드(`source`·`sourceUrl`·`ts`) **속성으로 존재**(Foundry에 실제 저장 가능). 단 `ts`는
timestamp 타입(모델은 int Unix → 변환 필요), model의 `attrs`(dict)에 대응하는 속성은 **없음**.

### 1-2. Link (observed_as의 실제 형태) — **FK 기반**
| 링크 API name | 방향 | 카디널리티 | FK |
|---|---|---|---|
| `observations` | Aircraft → Observation | MANY | (역방향) |
| `aircraft` | Observation → Aircraft | ONE | `aircraftIcao24` |

→ ontology.md의 "observed_as"는 Foundry에서 **`aircraftIcao24` FK로 구현된 링크쌍**(이름은
`observations`/`aircraft`). **별도 링크 생성 액션이 필요 없고**, Observation의 `aircraftIcao24`를
채우면 자동으로 연결되는 구조. **문제는 그 FK를 채울 방법이 없다는 것**(§2 갭 2).

### 1-3. Action Type + OSDK 발행 여부
- **create-aircraft 파라미터**: `callsign`(req)·`registration`(req)·`isMilitary`(opt bool)·`type`(opt)·
  `operatorRef`(opt) + **`newParameter`(req str)·`newParameter1`(req bool)** ← UI 자동생성 고아 파라미터.
  **icao24(PK) 파라미터 없음** → PK는 서버가 UUID 자동부여.
- **create-observation 파라미터**: `sourceUrl·squawk·onGround·heading·alt·lon·source·velocity·lat·ts`
  (전부 req) + **`newParameter`(req str)** 고아. **`obsId`(PK)·`aircraftIcao24`(FK) 파라미터 없음.**
- **OSDK 발행물(`skai_osdk_sdk` 0.1.0)**: 설치본은 **Aircraft만 담긴 stale 스냅샷**(재발행 이전분,
  Observation 클래스·Action 없음). 재설치엔 private index URL 필요 → `.env`에 없음(앱 Overview=사용자측).
  → **read를 저수준 `foundry_sdk`(dict)로 전환**(라이브 스키마를 재발행 없이 읽음, 더 견고).

**요약 답(반환 3문항):**
- **icao24 파라미터? → 없음** (create-aircraft, VALIDATE_ONLY로 `ParametersNotFound` 확인). PK UUID 자동.
- **Action 포함 발행? → 아니오** (OSDK엔 Object만, Action 0개. 게다가 설치본은 stale=Aircraft만).
- **observed_as 형태? → FK 링크**(`aircraftIcao24`). 단 create/edit-observation이 그 FK를 파라미터로
  안 받아 **어떤 액션으로도 채울 수 없음**.

---

## 2. 갭 목록 (전부 Ontology Manager UI 수정 필요 — 코드로 불가, 스키마 변경 금지 제약)

| # | 갭 | 영향 | 실측 근거 |
|---|---|---|---|
| **0** | **create-aircraft·create-observation 실행 실패**(`ApplyActionFailed`) | **Foundry 쓰기 전면 불가** | VALIDATE_ONLY는 통과, VALIDATE_AND_EXECUTE는 파라미터 무관 실패. P0B에선 create-aircraft 성공 → 최근 편집으로 깨짐 |
| 1 | create-aircraft에 **icao24(PK) 파라미터 없음** | 엔티티 해소 불가(write마다 새 UUID Aircraft) | `ParametersNotFound` (VALIDATE_ONLY) |
| 2 | create/edit-observation에 **obsId(PK)·aircraftIcao24(FK) 파라미터 없음** | 자연키 dedup 불가 + **observed_as 링크 생성 불가** | `ParametersNotFound` (VALIDATE_ONLY) |
| 3 | 두 create 액션에 **고아 required 파라미터**(newParameter·newParameter1) | 무의미 값 강제 + 갭 0의 유력 원인 | introspection |
| 4 | create-observation이 nullable 텔레메트리(alt·velocity·heading·squawk)도 **required=True** | 결측값 placeholder(0.0/"") 강제(손실) | introspection |
| 5 | Foundry Observation에 **`attrs` 속성 없음** | model.attrs(origin_country 등) 저장 안 됨 | introspection |

**갭 0 원인 후보(사용자 확인용)**: (a) 어떤 속성에도 매핑 안 되는 고아 required 파라미터
`newParameter`/`newParameter1`(갭 3), (b) 액션 편집 중 `icao24`(PK) 자동생성 전략 손상.
→ 액션의 CreateObjectRule과 파라미터-속성 바인딩을 Ontology Manager에서 재점검·재구성 필요.

**사용자 UI 작업 권장 순서:**
1. **[최우선]** create-aircraft·create-observation의 고아 파라미터(newParameter·newParameter1) 제거 →
   실행 실패(갭 0) 해소되는지 확인.
2. create-aircraft에 `icao24` 파라미터 추가(PK를 실 ADS-B hex로 — 엔티티 해소 복구).
3. create-observation에 `obsId`(PK)·`aircraftIcao24`(FK) 파라미터 추가(자연키 dedup + observed_as 복구).
4. create-observation의 nullable 텔레메트리를 required=False로.
5. (선택) Observation에 `attrs`(구조화/JSON) 속성 추가.
6. 위 반영 후 **OSDK 재발행**(Object+Action 포함) → 필요 시 read도 OSDK로 되돌릴 수 있음(현재는 저수준 SDK).

---

## 3. 이관 검증 수치 (`scripts/p7_migrate_validate.py`)

| 항목 | 값 |
|---|---|
| Foundry Aircraft (검증 전/후) | **3 / 3** (변동 없음 — write 블록) |
| Foundry Observation (검증 전/후) | **0 / 0** |
| create 액션 시도 | 6회(왕복 2 + 실데이터 4) → **성공 0회** (전부 ApplyActionFailed) |
| observed_as 링크 시도/생성 | 3회 시도 / **0건 생성**(갭 2, 전부 드롭·계측) |
| OpenSky 호출 | 1회 (HTTP 200, x-rate-limit-remaining=398) |
| read 검증 | Aircraft 3건 read-back OK(icao24=UUID·callsign·registration 정상 매핑), counts OK |

- Foundry 오염 없음: 모든 write 시도가 실행 단계에서 실패 → 객체 미생성(3/0 유지). 디버그 포함 전 과정 무오염.
- read 경로는 실 Foundry에 정상 연결(`FoundryOntologyStore.query_aircraft/counts/query_all_observations`).

---

## 4. 구현 산출물

| 파일 | 내용 |
|---|---|
| `ontology/store_foundry.py` | **FoundryOntologyStore**(write=액션, read=저수준 SDK dict→dataclass) + **HybridStore**(라우팅) + `make_store()` 팩토리. 스텁 전면 교체. |
| `connectors/opensky.py` | run_poller가 `LocalOntologyStore` 대신 `make_store()` 사용(기본 local 불변). |
| `tests/test_foundry_store.py` | 단위 13개(라우팅·provenance·counts·팩토리, fake Foundry 주입) + 라이브 2개(토큰+SDK 있을 때만 skip 마커). |
| `scripts/p7_introspect.py` | 라이브 스키마 introspection(read-only). |
| `scripts/p7_probe.py` | 액션 파라미터 수용 실측(VALIDATE_ONLY, 쓰기 없음). |
| `scripts/p7_migrate_validate.py` | 왕복 + 실데이터 1사이클 검증(write 실패 시 BLOCKED 보고). |

### 설계 결정
- **read = 저수준 `foundry_sdk`** (P0B §8-3 "read=OSDK" 대비 변경). 이유: 발행 OSDK가 stale(Observation
  없음)이고 재설치 index URL이 `.env`에 없음. 저수준 `OntologyObject.list/get`은 라이브 스키마를 dict로
  읽어 재발행 없이 Aircraft·Observation 양쪽 read. 하이브리드 정신(write=액션, read=타입드 클라)은 유지.
- **write = 액션**(`create-aircraft`/`create-observation`), 배치 API(`apply_batch`) 존재 확인(향후 폴링당
  호출 절감용). 현재는 단건 apply.
- **observed_as link()** = FK 미설정으로 생성 불가 → **드롭 + 계측**(`dropped_observed_as`), 크래시 대신
  경고. 인제스트 루프가 멈추지 않게.
- **provenance**: HybridStore·FoundryStore 양쪽에서 `validate_provenance` 강제(백엔드 무관 불변식).
- **lazy import**: `foundry_sdk`는 메인 `.venv`(3.14)에 없음 → 모듈 import는 SDK 없이 통과, 클래스 생성
  시에만 로드. 단위테스트는 fake 주입으로 SDK·네트워크 없이 통과.

---

## 5. 사용법

```bash
# 기본(미설정) = 순수 로컬 SQLite (데모 재현성 — 기존 동작 그대로)
python -m connectors.opensky

# 하이브리드 활성화: Aircraft·Observation·observed_as → Foundry, 나머지 → 로컬
#   (Python 3.12 환경 .venv312 + .env의 FOUNDRY_TOKEN·FOUNDRY_HOSTNAME 필요)
SKAI_STORE=foundry .venv312/bin/python -m connectors.opensky
```

- `make_store(db_path)` 팩토리가 `SKAI_STORE`로 백엔드 분기(`foundry`→HybridStore, 그 외→LocalOntologyStore).
- **⚠️ 현재 SKAI_STORE=foundry로 인제스트하면 write가 갭 0로 전부 실패**한다(read만 동작). 갭 0·1·2가
  UI에서 해소된 뒤에야 실 인제스트가 충실히 돈다.
- 테스트: 메인 `.venv`(3.14) `pytest` → 단위 통과·라이브 skip. `.venv312`(SDK+토큰) → 라이브도 실행.

---

## 6. 남은 이슈 / 인계

1. **[블로커] 갭 0** — create-aircraft·create-observation 실행 실패. §2 순서 1로 사용자 UI 수정 필요.
   해소 전엔 Foundry 쓰기 불가.
2. **갭 1·2** — PK/FK 파라미터 부재. 해소 전엔 엔티티 해소·observed_as·자연키 dedup 불가(하이브리드가
   "충실한 백엔드"가 되려면 필수). 스캐폴드는 갭 해소 시 인터페이스 불변인 채 즉시 동작.
3. **크로스백엔드 결합** — Track custody/Anomaly 스캔은 로컬에 남고 Observation은 Foundry에 있음.
   observed_as·자연키가 복구되기 전까지 SKAI_STORE=foundry로 full ingest_cycle을 돌리면 track/anomaly는
   빈 결과(Foundry obs에 aircraft_ref FK·안정 PK 없음). 검증은 Aircraft·Observation write/read 슬라이스로 한정.
4. **OSDK 재발행 시** Object+Action 포함하면 저수준 SDK 대신 OSDK 타입드 read/write로 되돌릴 수 있음(선택).
5. **git commit 없음** — 메인 스레드가 검증 후 커밋.

---

## 7. Tier1 재검증 (2026-07-04, opus 서브에이전트)

사용자가 UI 갭 수정 + Tier1 스키마(Region·Anomaly·링크·액션) 추가 + OSDK 0.3.0 재발행을 마친 뒤
실측 재검증. **저수준 `foundry_sdk` 1.97.0 + 재발행 OSDK `skai_osdk_sdk` 0.3.0** 양쪽으로 확인.

### 7-0. ⚠️ 핵심 발견 — `newParameter`는 junk가 아니라 **PK 바인딩 파라미터**였다

`create-aircraft`/`create-observation`/`create-anomaly`의 required 파라미터 `newParameter`(string)는
UI가 자동 오명명한 이름일 뿐 **실제로는 PK에 바인딩**된다(값을 주면 그 값이 그대로 PK가 됨).

- `create-aircraft {..., newParameter:"p7t1-aircraft"}` → 생성된 Aircraft의 **icao24(PK)=`p7t1-aircraft`**.
- `newParameter:""`(빈 문자열, = **store_foundry가 보내던 `_JUNK_STR`**) → 빈 PK → **ApplyActionFailed**.
- **즉 구 §2 "갭 0(ApplyActionFailed, 파라미터 무관 실패)"의 정체는 스키마 파손이 아니라 store가 PK
  파라미터에 빈 문자열을 넣던 것.** 실 PK값을 주면 4종 create 액션 전부 정상 EXECUTE.

이 발견으로 갭 0·1·3의 진단이 뒤집힌다(아래 표).

### 7-1. 갭 0~5 재판정 (실측)

| # | 구 진단 | 재판정 | 실측 근거 |
|---|---|---|---|
| **0** | ApplyActionFailed(쓰기 전면 불가) | **해소** | newParameter에 실 PK 주면 create-aircraft/observation/anomaly/region 전부 EXECUTE 성공(왕복 확인). 빈 문자열이 원인이었음 |
| **1** | create-aircraft에 icao24(PK) 파라미터 없음 | **기능적 해소** (오명명 잔존) | `newParameter`가 icao24 PK에 바인딩 → PK 지정·엔티티 해소 가능. 같은 PK 재생성 시 **ObjectAlreadyExists**(dedup 강제 확인). 잔여: 이름이 `icao24`가 아님 |
| **2** | create-observation에 obsId(PK)·aircraftIcao24(FK) 없음 | **해소** | `aircraftIcao24` 파라미터 신설(required) → **observed_as FK 링크 형성 확인**. `newParameter`=obsId PK 바인딩 → 자연키 dedup 가능. 잔여: obsId도 `newParameter` 오명명 |
| 3 | 고아 junk 파라미터 newParameter·newParameter1 | **오진단 정정 + 부분** | `newParameter1` **제거됨**. `newParameter`는 junk 아니라 PK 파라미터(위). 남은 이슈=오명명뿐 |
| 4 | nullable 텔레메트리 required=True | **해소** | alt·velocity·heading·squawk 전부 **required=False**. 4종 생략하고 write → 전부 **None** 저장 확인 |
| 5 | Observation에 `attrs` 속성 없음 | **잔존**(우선순위 낮음) | Observation 속성에 attrs 없음(origin_country 등 미저장). 선택 항목 |

### 7-2. Tier1 신규 스키마 인벤토리 (introspection)

**Object Type (사용자 제작 4종, 전부 EXPERIMENTAL):**
- **Region** (PK=`id`): `id`·`name`·`classification`·`geoJson`(string). ⚠️ PK 바인딩 파라미터 없음
  → id 자동 UUID(dedup 불가). model.Region의 `geo`(폴리곤 list)는 **`geoJson` 문자열로 직렬화 필요**. **outgoing 링크 없음**.
- **Anomaly** (PK=`anomalyId`): `anomalyId`·`type`·`ts`(timestamp)·`lat`·`lon`·`confidence`(double)·
  `status`(string)·`explanation`(string). model.Anomaly의 `explainer_backend`·`created_at`·`attrs`는 없음.
- Aircraft·Observation: §1과 동일(+ 갭4 해소 반영).

**Link Type (observed_as만 FK, Tier1 3종은 전부 MANY-MANY non-FK):**
| ontology.md 링크 | Foundry 구현 | 형태 |
|---|---|---|
| observed_as (Aircraft↔Observation) | `aircraft`(Obs→AC, ONE, **fk=aircraftIcao24**) / `observations`(AC→Obs, MANY) | **FK** |
| **evidenced_by** (Anomaly→Observation) | `observations`(Anomaly→Obs, MANY) / `anomalies`(역) | **MANY-MANY** |
| **involves** (Anomaly→Aircraft) | `aircraft`(Anomaly→AC, MANY) / `anomalies`(역) | **MANY-MANY** |
| **correlated_with** (Anomaly→Anomaly) | `correlatedWithAnomalies`(MANY) | **MANY-MANY** |

**Action Type (create/edit/delete × {aircraft, observation, region, anomaly} = 12개):**
- `create-anomaly` 파라미터: `type`·`ts`·`lat`·`lon`·`newParameter`(=anomalyId PK). **evidence·confidence·
  status·explanation 파라미터 없음** → 생성 시 근거 링크·신뢰도·상태·설명을 **못 채운다**(항상 None).
- `edit-anomaly`도 `status`·`confidence`·`explanation` 파라미터 없음 → 어떤 액션으로도 못 설정.
- **`confirm-anomaly`·`dismiss-anomaly`·`set-region-alert-level` = 전부 미구현**(NotFoundError).
- Tier1 링크(evidenced_by·involves·correlated_with)를 채우는 **링크 생성 액션이 없다**(create-anomaly에
  링크 파라미터도 없음) → Anomaly의 provenance 링크를 **어떤 액션으로도 생성 불가**.

### 7-3. write 왕복 결과 (`scripts/p7_tier1_roundtrip.py`, 항목당 1건·끝에 delete 정리)

| 항목 | 결과 |
|---|---|
| create-aircraft (newParameter=PK) | **OK** — icao24 PK=요청값 일치, callsign/isMilitary/type/operatorRef 정상 read-back |
| dedup (같은 PK 재생성) | **ObjectAlreadyExists** — PK dedup 강제됨 |
| create-observation (obsId PK + aircraftIcao24 FK) | **OK** — obsId=요청값, aircraftIcao24=Aircraft PK 일치(**observed_as 링크 형성**), 텔레메트리 4종 생략→None(갭4) |
| create-anomaly (evidence 없이) | **OK** — evidence 없이 생성 성공. status/confidence/explanation=None(설정 불가) |
| create-region | **OK** — id 자동 UUID, geoJson/name/classification 정상 |
| OSDK 0.3.0 네이티브 왕복 | **OK** — `actions.create_aircraft/create_observation` write, `objects.Observation.get()` read, 타입드 `.aircraft` 링크 accessor 존재, `delete_*` 정리 |
| Foundry 오염 | **없음** — 모든 테스트 객체 delete, 카운트 before==after. (디버그 중 생성된 PK='x' 2건도 삭제 완료) |

### 7-4. evidence 강제 여부 (데모 핵심) — **강제 없음**

- `create-anomaly`가 **evidence 파라미터 자체를 안 받음**(evidence·observations 파라미터 부재=BadRequest 확인).
- **evidence 없이 Anomaly 생성이 성공**한다 → **ontology.md §3 "근거 없는 Anomaly는 액션이 거부" / §0
  스멜테스트 4(provenance 그래프)가 Foundry 온톨로지 레벨에서 미구현**.
- 게다가 evidenced_by 링크를 채울 액션도 없어(7-2), Foundry Anomaly는 **근거 링크가 구조적으로 빈 채로만
  존재 가능**. → 이 프로젝트의 승부처(provenance 강제)가 Foundry측에서 빠져 있음. **UI 수정 최우선 후보.**

### 7-5. OSDK 재발행물 (`skai_osdk_sdk` 0.1.0 → **0.3.0**, FOUNDRY_OSDK_INDEX로 재설치)

- **Object 클래스 4개**: Aircraft, Observation, **Region**, **Anomaly**(0.1.0은 Aircraft만 담긴 stale였음).
- **Action 메서드 12개**: create/edit/delete × {aircraft, observation, region, anomaly}. **Action 포함 발행 확정**
  (0.1.0은 Action 0개였음). confirm/dismiss-anomaly는 OSDK에도 없음(스키마에 없으므로).
- 타입드 링크 accessor: Observation.aircraft, Anomaly.observations/aircraft/correlated_with_anomalies,
  Aircraft.observations/anomalies.
- **판단**: OSDK 0.3.0으로 write=액션(타입드)·read=타입드 클라 **양쪽 네이티브 전환 가능**(P0B 원래 설계 복원).
  단 링크 채우기·evidence 강제는 **스키마 한계**(액션 부재)로 OSDK로도 불가 — 코드가 아니라 UI 문제.
- 재현: `.env`에 `FOUNDRY_OSDK_INDEX`(토큰은 `$FOUNDRY_TOKEN` 치환형, gitignore) 추가.
  `pip install --upgrade --index-url "$(치환된 URL)" skai_osdk_sdk` (pip 로그 토큰은 sed 스크럽).

### 7-6. 남은 수정 목록

**사용자 UI 작업 (Ontology Manager):**
1. **[데모 최우선] evidence 강제 복구** — create-anomaly에 evidence(Observation) 링크 파라미터 추가 +
   필수화(또는 evidenced_by 링크를 채우는 액션 신설). 현재 provenance 백본이 Foundry에서 미구현.
2. **confirm-anomaly·dismiss-anomaly 액션 신설** — status 전이(candidate→confirmed/dismissed,
   human-on-the-loop, ontology.md §3). 현재 부재 + edit-anomaly에도 status 파라미터 없음.
3. create-anomaly에 `confidence`·`status`·`explanation` 파라미터 추가(현재 설정 불가 → 항상 None).
4. involves(Anomaly→Aircraft)·correlated_with(Anomaly→Anomaly) 링크를 채우는 액션 or create-anomaly 링크 파라미터.
5. (권장) `newParameter` 파라미터를 `icao24`/`obsId`/`anomalyId`로 리네임 — 기능은 되나 혼동·자기문서화 저해.
6. (선택) Observation에 `attrs` 속성(갭5) / Region에 `id` PK 바인딩 파라미터(dedup) + `within`(Observation→Region) 링크 /
   set-region-alert-level 액션 + Region alertLevel 속성(ontology.md §3, 부재).

**코드측 후속 (store_foundry.py — 메인 스레드 결정, 이번 세션 미수정):**
1. `write_aircraft`: `newParameter=""`→**`aircraft.icao24`**(실 PK), **`newParameter1` 제거**(스키마에서
   삭제됨 → 현재 BadRequest로 write 실패). ← 지금 store로 인제스트하면 이것 때문에 전부 실패.
2. `write_observation`: **`aircraftIcao24=aircraft_ref` 추가**(required), `newParameter=""`→**`obs.id`**(obsId PK).
   텔레메트리 placeholder(0.0) 제거 가능(optional됨).
3. `link(observed_as)`: FK로 자동 형성되므로(write_observation의 aircraftIcao24) **`dropped_observed_as`
   드롭 계측 폐기** → observed_as는 이미 형성. HybridStore.link의 observed_as 분기 no-op화.
4. (신규 가능) `write_region`·`write_anomaly` 구현 가능: Region `geo`→`geoJson` 직렬화(PK는 자동 UUID로
   dedup 불가), Anomaly는 evidence/confidence/status/explanation **손실 감수**(스키마 파라미터 부재).
5. read 경로(`_dict_to_aircraft` 등)는 정상 — PK가 실 hex면 엔티티 해소도 정상 작동.

**검증 산출물**: `scripts/p7_introspect.py`(스키마), `scripts/p7_tier1_roundtrip.py`(왕복+evidence 프로브).
`scripts/p7_migrate_validate.py`는 store 코드 의존 → 위 코드측 1·2 수정 전엔 실패(실행 안 함).
**Foundry 실데이터 카운트(검증 후, 정리 완료)**: Aircraft 3(P0B 기존 UUID-PK), Observation 0, Region 0, Anomaly 0.

---

## 8. 실데이터 이관 검증 (2026-07-04, sonnet 서브에이전트)

§7-6 코드측 후속 1~3 집행 + `scripts/p7_migrate_validate.py` 실행 결과.

### 8-1. store_foundry.py 수정 내용

| 항목 | 이전 | 이후 |
|---|---|---|
| `write_aircraft` — PK | `newParameter=""` (빈 문자열 → BadRequest) | `newParameter=aircraft.icao24` (실 PK 바인딩) |
| `write_aircraft` — 고아 파라미터 | `newParameter1=False` (스키마 삭제됨 → BadRequest 원인) | **제거** |
| `write_observation` — PK | `newParameter=""` | `newParameter=obs.id` (obsId PK 바인딩) |
| `write_observation` — FK | 없음 (observed_as 링크 불가) | `aircraftIcao24=obs.aircraft_ref` (FK → 자동 링크) |
| `write_observation` — optional 텔레메트리 | `alt=0.0`, `velocity=0.0` 등 placeholder(손실) | None이면 파라미터 생략 |
| `FoundryOntologyStore.link(observed_as)` | 드롭 + `dropped_observed_as` 계측 | **no-op** (FK 자동 형성) |
| `HybridStore.link(observed_as)` | `foundry.link()` 호출 | **no-op** (FK 자동 형성) |
| `write_anomaly`, `write_region` | `_unsupported` | `_unsupported` + 사유·UI 선행조건 주석 |
| dedup | 프로세스 내만 | 프로세스 내 + ObjectAlreadyExists catch·skip (크래시 금지) |

### 8-2. pytest 결과

| | 수 |
|---|---|
| 전체 통과 | **126** (기존 121 → +5 신규 파라미터 매핑 테스트) |
| skip(라이브) | 2 |
| 실패 | 0 |

신규 테스트 5건: `test_write_aircraft_uses_real_pk`, `test_write_aircraft_dedup_no_double_call`,
`test_write_observation_params`, `test_write_observation_none_telemetry_omitted`,
`test_write_observation_telemetry_included_when_set`, `test_write_observation_already_exists_no_crash`,
`test_foundry_link_observed_as_noop`.

### 8-3. 실데이터 이관 검증 (`scripts/p7_migrate_validate.py`)

**실행 명령**: `SKAI_STORE=foundry PYTHONPATH=/Users/ma/SKAI .venv312/bin/python scripts/p7_migrate_validate.py`

| 항목 | 값 |
|---|---|
| Foundry Aircraft (before → after) | **3 → 6** (+3: 테스트 1건 + 실 icao24 hex 2건) |
| Foundry Observation (before → after) | **0 → 3** (+3: 테스트 1건 + 실 2건) |
| OpenSky 호출 | 1회 (HTTP 200, x-rate-limit-remaining=396) |
| 서브샘플 | 117건 수신 → 상위 2건만 write |
| create 액션 시도 | 4회 (Aircraft 2 + Observation 2) |
| Aircraft PK 바인딩 | **OK** — `icao24=p7t2test` (요청값 일치, UUID 아님) |
| Observation FK | **OK** — `aircraft_ref='p7t2test'` (빈값 아님) |
| observed_as FK traverse | **OK** — `query_observations_for('p7t2test')` 1건 반환 |
| dedup(재실행) | **OK** — 같은 Aircraft·Observation 재실행 시 카운트 불변 |
| observed_as link() no-op | **OK** — 호출 후 크래시 없음, 별도 액션 불필요 |

**판정: INGEST-OK**

---

## 9. 전량 스키마 재검증 (2026-07-04, opus 서브에이전트)

사용자가 foundry-build-guide.md의 A부(기존 수정)+B부(신규 7객체)를 구축하고 OSDK를 재발행(0.3.0→0.4.0)했다고 하여 **전수 실측 재검증**. 저수준 `foundry_sdk` 1.97.0 라이브 introspection + write 왕복. 스키마 변경/store 수정 없음. Foundry 오염 0(생성분 전부 delete, before==after).

**검증 산출물**: `scripts/p7_full_introspect.py`(11객체·전링크·전액션), `scripts/p7_full_roundtrip.py`·`p7_happypath_exec.py`(왕복), `scripts/p7_validate_probe.py`(validation.result 정밀 프로브), `scripts/p7_anomaly_isolate.py`(create-anomaly 에러 격리).

### 9-0. 종합 판정 — **Object層 완성 / Action層 다수 결함**

- **Object 11/11 속성 정합 완료.** 11종 전부 존재, PK·속성·타입이 build-guide 스펙과 일치(A부 보강분 confidence/status/explanation·alertLevel·trackId 반영 확인).
- **Action層은 미완성.** ⓐ 신규 7타입 create에 **PK 파라미터 전무**(전부 UUID 자동 → 엔티티 해소 불가), ⓑ **evidence가 잘못 배선**(Observation-evidence 파라미터 없음), ⓒ **of/over/within/composed_of 채움 수단 부재**, ⓓ **set-region-alert-level가 modify가 아니라 create로 오작동**(빈 Region 양산), ⓔ **create-anomaly가 valid refs로도 ApplyActionFailed**(객체는 half-생성).
- **evidence 강제(★데모 핵심) = 오배선.** provenance가 이제 *필수*가 되긴 했으나(§7 대비 진전) 잘못된 링크(aircraft/news/orbitpass)에 걸렸고 **정작 핵심인 Observation-evidence는 어떤 파라미터로도 못 붙인다** → 데모 서사(ADS-B 관측을 근거로 이상징후 생성)를 Foundry에서 재현 불가.

### 9-1. OSDK 재발행물 (`skai_osdk_sdk` 0.3.0 → **0.4.0**, FOUNDRY_OSDK_INDEX 재설치, pip 로그 토큰 스크럽)

- **Object 클래스 11개**(0.3.0은 4개): aircraft·observation·region·anomaly·operator·track·satellite·orbit_pass·weather_state·news_event·situation_assessment. Satellite·WeatherState는 `__links.py` 없음(outgoing 링크 0 — live와 일치).
- **Action 메서드 35개.** create×11·edit×11·delete×10·confirm_anomaly·dismiss_anomaly·set_region_alert_level. **⚠️ live에는 37개**(delete×11 + editrack) → OSDK 0.4.0은 **`delete-orbit-pass`와 `editrack`(커스텀 track 커스터디 연장) 2건 누락**. build-guide C-2 "Action 전부 체크"가 완전치 않음. 코드가 OSDK 타입드로 delete-orbit-pass를 부르려면 저수준 SDK 폴백 필요.

### 9-2. Object Type 정합 (11/11, 전부 EXPERIMENTAL) — **전 항목 스펙 일치**

| Object | PK | 속성(실측) | build-guide 대비 |
|---|---|---|---|
| Aircraft | icao24 | callsign·registration·type·operatorRef·isMilitary(bool) | 정합 |
| Observation | obsId | aircraftIcao24·ts·lat·lon·alt·velocity·heading·squawk·onGround·source·sourceUrl·**trackId** | 정합(trackId=composed_of FK 추가됨) |
| Region | id | name·classification·geoJson·**alertLevel** | 정합(alertLevel 추가) |
| Anomaly | anomalyId | type·ts·lat·lon·**confidence·status·explanation** | 정합(A-2 3속성 추가) |
| Operator | operatorId | name·kind·country | 정합 |
| Track | trackId | aircraftIcao24·startTs·endTs·hasGap·pathJson | 정합 |
| Satellite | noradId | name·operatorRef·objectType·tleEpoch | 정합 |
| OrbitPass | passId | satelliteNoradId·regionId·startTs·endTs·maxElevation | 정합 |
| WeatherState | weatherId | regionId·ts·wind·visibilitySm·ceilingFt·conditions·rawText·source·sourceUrl | 정합 |
| NewsEvent | newsId | source·url·ts·title·summary·entitiesJson·confidence·lat·lon | 정합 |
| SituationAssessment | assessmentId | regionId·windowStart·windowEnd·summary·confidence·producedBy·createdAt | 정합 |

### 9-3. Link Type 정합 — FK 3종 형성 / MANY-MANY 12종 형성 / **of·over·within 미형성**

**FK 링크(속성으로 자동 형성):**
| ontology 링크 | 실측 | 채움 수단 |
|---|---|---|
| observed_as (Aircraft↔Observation) | Observation.aircraft→Aircraft (fk=aircraftIcao24) | create-observation.aircraftIcao24 ✓ |
| operated_by (Aircraft→Operator) | Aircraft.operator→Operator (fk=operatorRef) | create-aircraft.operatorRef ✓ (단 Operator PK=UUID라 매칭 난망) |
| composed_of (Track↔Observation) | Observation.track→Track (fk=trackId) | **채울 액션 없음** — create/edit-observation에 trackId 파라미터 부재(BadRequest) ✗ |

**MANY-MANY 링크(액션 링크 파라미터로 채움 — 실측 12종 형성됨):** evidenced_by(Anomaly↔Observation)·involves(Anomaly↔Aircraft)·correlated_with(Anomaly↔Anomaly)·evidenced_by_news(Anomaly↔NewsEvent)·evidenced_by_orbitpass(Anomaly↔OrbitPass)·aggregates(Assessment↔Anomaly)·cites_observation/news/orbitpass·mentions_aircraft/operator/region.

**미형성(속성만 있고 링크 미지정):**
| 링크(스펙) | 상태 | 근거 |
|---|---|---|
| OrbitPass —of→ Satellite | **미형성** | satelliteNoradId 속성 존재하나 OrbitPass outgoing 링크는 anomalies·situationAssessments뿐. Satellite outgoing 0 |
| OrbitPass —over→ Region | **미형성** | regionId 속성 존재하나 링크 미지정. Region outgoing은 newsEvents뿐 |
| Observation —within→ Region | **미형성** | Observation→Region 링크 자체 없음(ontology.md §2 백본, 지오펜스 진입 판정) |
| Track → Aircraft | **미형성** | aircraftIcao24 속성만, 링크 미지정 |
| WeatherState → Region / Assessment → Region | **미형성** | regionId 속성만 |

→ 위성-지역 시공간 상관(ontology.md §2·§4 "위성 근접/통과")과 지오펜스 진입 판정이 Foundry 그래프로 traverse 불가. 속성 값은 있으나 링크가 아니라 조인 불가.

### 9-4. Action Type 정합 + 파라미터 (37 액션 전수)

**confirm/dismiss/set-region-alert-level 존재 여부(★반환 항목):**
| 액션 | 존재 | 작동 |
|---|---|---|
| confirm-anomaly | ✓ | **정상** — anomaly(object) 파라미터로 status→confirmed 전이, read-back 확인 |
| dismiss-anomaly | ✓ | 구조 동일(anomaly 파라미터). 미실행 검증(대칭이므로 confirm으로 갈음) |
| set-region-alert-level | ✓(등록) | **오작동** — 대상 Region 파라미터가 **없다**(alertLevel:String optional 1개뿐). 파라미터 전무로도 VALIDATE=VALID. EXECUTE하면 기존 Region을 수정하는 게 아니라 **alertLevel만 채운 새 Region을 생성**(빈 UUID Region 양산). 실측: 실행 후 alertLevel=RED인 팬텀 Region 1건 생성됨(즉시 삭제 정리). = modify 규칙이 아니라 create 규칙으로 배선됨 |

**create-* PK 파라미터(newParameter=PK 바인딩) 실측:**
| create 액션 | PK 파라미터 | 판정 |
|---|---|---|
| create-aircraft·observation·anomaly·region | newParameter ✓ | PK 지정·dedup 가능(단 이름이 newParameter로 오명명 잔존) |
| **create-operator·satellite·orbit-pass·track·weather-state·news-event·situation-assessment** | **없음**(newParameter 넣으면 BadRequest) | **신규 7타입 전부 PK=UUID 자동 → 엔티티 해소·dedup 불가**. 스멜테스트 #2 위반. Satellite(noradId)·Operator 등 실세계 안정키를 PK로 못 씀 |

**create-anomaly 파라미터(실측):** type·ts·lat·lon(req) / confidence·status·explanation(**opt, 추가됨**✓) / newParameter(=anomalyId PK) / **newParameter1(req, orphan)** / **aircraft·newsEvents·orbitPasses(전부 req, object)**.
- **evidence(Observation) 파라미터 부재**: `observations`·`evidence` 둘 다 BadRequest. → 근거 Observation을 붙일 수단이 아예 없음.
- newParameter1: object-query 제약 아닌 순수 required string orphan(생략 시 INVALID). 값을 넣어도 anomalyId·다른 속성에 안 들어감(SENTINEL 미반영) → junk 강제.

**over-constrained(필수여야 할지 의문인 링크 파라미터가 전부 required):**
- create-anomaly: aircraft·newsEvents·orbitPasses 전부 req → emergency_squawk처럼 뉴스·위성통과가 없는 이상징후도 억지 ref를 만들어 붙여야 생성 가능.
- create-news-event: aircraft·operators·regions + **newsEvents(self-link, req)** → 첫 NewsEvent도 존재하지 않는 self ref를 넣어야 함(실측: 존재X ref "x"로도 EXECUTE 성공 — 즉 self 파라미터는 **present-only junk**, 링크는 안 맺힘). **PK 파라미터도 없음**.
- create-situation-assessment: anomalies·newsEvents·observations·orbitPasses + **situationAssessments(self-link, req)** 전부 req. PK 파라미터 없음.

**edit 액션 특이사항:** edit-aircraft.isMilitary가 **string**(객체 속성·create는 boolean) 타입 불일치. edit-anomaly에 status/confidence/explanation 없음(생성 후 수정 불가, 단 status는 confirm/dismiss로 커버). `editrack`(track·endTs·hasGap·pathJson optional)=커스터디 연장용 커스텀 액션 별도 존재(edit-track과 중복 명명).

### 9-5. evidence 강제 여부 (★★데모 최우선 항목) — **오배선(mis-wired), 해소 아님**

- **evidence 없이 거부되나? → 부분적으로 예**: create-anomaly는 aircraft·newsEvents·orbitPasses가 전부 required라 **provenance 링크 없이는 생성 거부**(VALIDATE=INVALID, ObjectQueryResultConstraint). §7의 "강제 전무"보다는 진전.
- **그러나 강제되는 링크가 틀렸다**: 데모의 실제 근거는 **ADS-B Observation**(ontology.md §1 "증거 객체")인데, create-anomaly에 **Observation-evidence 파라미터가 없어**(observations/evidence 둘 다 BadRequest) 근거 관측을 붙일 방법이 없다. 대신 involves(aircraft)·evidenced_by_news·evidenced_by_orbitpass를 강제 → 관측 기반 이상징후에 무관한 뉴스·위성통과를 억지로 매달아야 함.
- **게다가 create-anomaly가 valid refs로도 실패**: aircraft·newsEvents·orbitPasses에 실존 객체 PK를 다 줘도 **ApplyActionFailed(INVALID_ARGUMENT, parameters:{} — 상세 없음)**가 발생. 다만 **Anomaly 객체 자체는 생성됨**(anomalyId·status=candidate·confidence=0.9·explanation·type read-back 정상). 즉 액션이 **비원자적**(스칼라 생성은 성공 / 부가 링크 edit에서 실패해 에러 반환). 2회 재현. 데모 버튼이 매번 에러를 뱉으면서 반쪽 객체를 남김.
- **결론**: ontology.md §3 "근거 없는 Anomaly 거부 = provenance 온톨로지 강제"는 **아직 올바르게 구현되지 않음**. 오히려 §7("강제 없음, 생성은 깔끔")보다 데모 리스크가 큼(정상 anomaly를 깔끔히 못 만듦).

### 9-6. 왕복(write) 결과 요약 (VALIDATE 우선 + happy-path EXECUTE, 전건 정리)

| 검증 | 결과 |
|---|---|
| create-aircraft/region (newParameter=PK) | **OK** — PK=요청값, dedup 가능 |
| create-operator/orbit-pass (PK 없음) | 생성은 OK, **PK=UUID 자동**(dedup 불가) |
| create-news-event (self newsEvents="존재X ref") | **OK 생성**(self 파라미터는 present-only, 링크 미형성). PK=UUID |
| **create-anomaly (모든 필수 valid refs)** | **ApplyActionFailed** — 그러나 Anomaly 객체 생성됨(스칼라 read-back 정상). 비원자적 |
| Observation-evidence 파라미터 | **부재**(observations·evidence → BadRequest) |
| **confirm-anomaly** | **OK** — status candidate→**confirmed** 전이 read-back 확인 |
| **set-region-alert-level** | **오작동** — 대상 Region 파라미터 없음. 실행 시 기존 Region 무변화, 대신 **팬텀 Region 생성**(정리 완료) |
| Observation→Region within / OrbitPass of·over 형성 | **불가** — 링크 자체 미존재(§9-3) |
| NewsEvent mentions(aircraft/operator/region) | 파라미터 존재·required. 단 self newsEvents junk 강제 |
| composed_of(Observation.trackId) 채움 | **불가** — create/edit-observation에 trackId 파라미터 부재 |
| Foundry 오염 | **0** — 생성분 전부 delete, 최종 카운트 Aircraft 6·Observation 3·나머지 0 (= 세션 시작 baseline. 6/3은 §8 이전 세션 잔존분, 이번 세션 순증감 0) |

### 9-7. 남은 갭 목록

**A. 사용자 UI 작업 (Ontology Manager — 코드로 불가):**
1. **[데모 최우선] create-anomaly evidence 재배선** — (a) `evidence`(→evidenced_by, Observation) 링크 파라미터 **신설**(현재 부재), (b) aircraft·newsEvents·orbitPasses를 **Optional로** 강등(관측 기반 anomaly가 뉴스·위성 없이 생성되게), (c) newParameter1 orphan 제거, (d) **ApplyActionFailed 원인 제거**(부가 링크 edit 규칙 점검 — valid refs로도 실패·객체는 half-생성).
2. **[데모] set-region-alert-level 재작성** — 현재 create로 오작동(빈 Region 양산). **대상 Region(object) 파라미터 추가 + Modify object 규칙**으로 alertLevel 전이하게. (confirm/dismiss-anomaly는 정상이라 그 패턴 복제.)
3. **신규 7타입 create-*에 PK 파라미터 추가** — operator·satellite·orbit-pass·track·weather-state·news-event·situation-assessment 전부 newParameter(PK) 부재 → UUID 자동. 특히 Satellite(noradId)·Operator·OrbitPass는 자연키 dedup 필수(엔티티 해소). create-aircraft/observation처럼 newParameter를 PK에 바인딩.
4. **링크 FK 지정 완료** — OrbitPass.satelliteNoradId→`of`(Satellite)·regionId→`over`(Region), Observation→`within`(Region), Track.aircraftIcao24→Aircraft. 속성은 있으나 링크 미지정이라 그래프 traverse 불가(위성통과·지오펜스 서사 재현 불가).
5. **composed_of 채움 수단** — create/edit-observation에 trackId 파라미터 추가(현재 Observation.track FK 링크는 있으나 어떤 액션도 trackId를 못 채움).
6. **self-link required 제거** — create-news-event.newsEvents·create-situation-assessment.situationAssessments(자기참조 필수 junk)를 Optional화 또는 제거.
7. (권장) newParameter → icao24/obsId/anomalyId/id 리네임, edit-aircraft.isMilitary 타입 boolean 정정, editrack↔edit-track 중복 정리.
8. 위 반영 후 **OSDK 재발행**(delete-orbit-pass·editrack 포함 — 0.4.0에 누락).

**B. 코드측 후속 (store_foundry.py — 다음 세션 구현 대상, 이번 세션 미수정):**
> 아래는 UI 갭(A) 중 해당분이 해소된 뒤 store_foundry에 배선할 작업의 열거만. 지금은 스키마가 못 받으므로 구현 불가.
1. `write_anomaly` — A-1 해소 후: anomalyId PK + evidence(Observation 링크) + confidence/status/explanation(스칼라는 이미 파라미터 존재). 현재는 evidence 파라미터 부재·ApplyActionFailed로 write 불가.
2. `write_operator/satellite/orbit_pass/track/weather_state/news_event/assessment` — A-3(PK 파라미터) 해소 후 각 write + 자연키 dedup. 현재 PK=UUID라 dedup 불가.
3. `write_orbit_pass`의 of/over, `write_observation`의 within/tracko(composed_of) — A-4·A-5 링크 지정 해소 후 배선.
4. confirm/dismiss-anomaly·set-region-alert-level 호출부 — confirm/dismiss는 지금도 호출 가능(정상), set-region-alert-level은 A-2 재작성 후.
5. NewsEvent mentions·Anomaly correlated_with 링크 채움 — 파라미터는 존재(required). self-link junk(A-6) 정리 후 깔끔해짐.

**핵심 3줄 요약**: ⓐ Object 11종은 스펙 완성. ⓑ Action층은 신규 7타입 PK 부재 + evidence 오배선(Observation 근거 못 붙임, 필수 링크 틀림, create-anomaly가 ApplyActionFailed) + set-region-alert-level가 create로 오작동 + of/over/within/composed_of 채움 불가 → **데모 provenance 백본이 Foundry에서 아직 미완**. ⓒ 정상 작동 확인: confirm-anomaly(status 전이), 11객체 read/write, FK observed_as/operated_by. 다음 세션은 A(UI)를 먼저 닫아야 B(코드)가 의미 있음.

---

## 10. D1~D6 수정 재검증 (2026-07-04, opus 서브에이전트)

사용자가 foundry-build-guide.md D부(D-1~D-6 액션 규칙 배선 수정)를 반영하고 OSDK를 **0.4.0→0.5.0**으로 재발행했다고 하여 **전수 실측 재검증**. 저수준 `foundry_sdk` 1.97.0 라이브 introspection + VALIDATE 우선·EXECUTE 최소 왕복(항목당 1건, 끝에서 전부 delete). 스키마/store 수정 없음. **Foundry 오염 0** (순증감 0 — 단, ⓔ create-anomaly가 ApplyActionFailed면서도 half-Anomaly를 남겨 별도 삭제 1회 필요했음, 최종 확인 카운트 Aircraft 6·Observation 3·나머지 0 = 세션 baseline).

**검증 산출물**: `scripts/p7_full_introspect.py`(재사용, 11객체·전링크·전액션), `scripts/p7_d_reverify.py`(신규, D-1~D-6 EXECUTE 왕복+traverse).

### 10-0. 종합 판정 — **Action층 대폭 진전, 그러나 데모 백본(D-1)은 여전히 미완**

D-2·D-3·D-5·D-6은 **해소**. D-4는 **부분**(4/6 링크 traverse 가능, over 미형성·within 채움불가). **★D-1(create-anomaly evidence 강제)은 부분** — evidence 파라미터가 신설·필수화되고 근거 없는 생성이 거부되긴 하나(§9 대비 진전), **EXECUTE 시 ApplyActionFailed가 재발**하고 evidenced_by 엣지 형성이 불안정하며 half-Anomaly가 남는다. → 데모 provenance 백본(Observation 근거 → 이상징후, 깔끔 생성 + traverse)은 **아직 Foundry에서 재현 불가**.

### 10-1. §9 결함별 재판정 표

| §9 결함 | 판정 | 실측 근거 |
|---|---|---|
| ⓐ 신규 7타입 create PK 파라미터 전무 | **해소** | 7종 전부 `newParameter`(req string PK) 보유. satellite 직접 검증: `noradId==요청값`(UUID 아님) + 같은 PK 재생성 → **ObjectAlreadyExists**(dedup 강제). operator·orbit-pass·track·weather-state·news-event·situation-assessment 전부 newParameter로 VALIDATE=VALID(없으면 INVALID) |
| ⓑ evidence 오배선(Observation-evidence 파라미터 없음) | **부분** | `observations`(object=Observation, req) + `newParameter1`(objectSet=Observation, req) 두 파라미터 신설. evidence 링크 파라미터가 **생기고 필수화**됨(§9=전무). 단 아래 ⓔ로 실행 실패 |
| ⓒ of/over/within/composed_of 채움 수단 부재 | **부분** | of·composed_of **해소**, over **잔존**, within **부분**(구조만) — §10-3 표 |
| ⓓ set-region-alert-level가 create로 오작동(팬텀 Region) | **해소** | `region`(object, req) 파라미터 신설 + Modify 규칙. 실행: 기존 Region alertLevel `None→RED` 전이, **Region count 1→1(팬텀 없음)** |
| ⓔ create-anomaly가 valid refs로도 ApplyActionFailed(half-생성) | **잔존** | evidence(observations+newParameter1) 다 줘도 **ApplyActionFailed 재발**. Anomaly 스칼라는 생성됨(status/confidence/evidence 스칼라 read-back 정상) = **비원자적 그대로**. 2가지 objectSet 인코딩 모두 실패 |

### 10-2. ★★ evidence 강제 최종 판정 — **부분(해소 아님)**

create-anomaly 파라미터 실측: `aircraft·newsEvents·orbitPasses`(전부 **Optional 강등 ✓**) / `observations`(Observation 단일, **req**) / `newParameter1`(Observation **objectSet, req**) / `evidence`(**string**, opt) / confidence·status·explanation(opt) / newParameter(=anomalyId PK).

- **evidence 파라미터 생겼나?** → **예**. Observation을 근거로 붙일 수단이 두 개(단일 `observations` + 세트 `newParameter1`) 신설됨. §9의 "Observation-evidence 파라미터 전무·observations/evidence 둘 다 BadRequest"에서 명백히 진전.
- **Required인가 / 근거 없이 거부되나?** → **예**. `observations`·`newParameter1` 둘 다 required → 이 둘을 생략하면 VALIDATE=**INVALID**. "근거 없는 Anomaly 거부"가 검증 레벨에서 작동(ontology.md §3 방향).
- **evidence 포함 → 깔끔히 성공?** → **아니오**. EXECUTE 시 **ApplyActionFailed(BadRequest, INVALID_ARGUMENT, parameters:{} — 상세 없음)** 재발(§9 ⓔ와 동일). **Anomaly 스칼라는 커밋**되나(status=candidate·confidence·evidence 스칼라·explanation read-back 정상) 매번 에러+반쪽 객체.
- **evidenced_by→Observation traverse?** → **불안정**. `newParameter1=filter_def`(단건 필터)일 때 `Anomaly.observations` 엣지 **빈 채로([]) half-생성**. `newParameter1=base_def`(전체 세트)일 때는 `observations` 단일 파라미터發 엣지가 **1건 형성**됨(traverse=[obs]). 즉 **단일 `observations` 파라미터의 Add-link 규칙은 엣지를 형성할 수 있으나**, **`newParameter1`(required objectSet) 규칙이 ApplyActionFailed의 범인**으로 원자성을 깨고 엣지 형성을 좌우한다.
- **⚠️ 핵심**: D-1 항목 3 "**newParameter1 orphan 제거**"가 **집행 안 됨** — 제거 대신 `required objectSet<Observation>`로 **재타입**됐고, 이게 실행 실패의 진짜 원인. `evidence`(string) 파라미터는 Anomaly의 신규 **스칼라 문자열 속성**에만 바인딩(그래프 링크 아님) — 근거 traverse에 무의미.
- **결론**: 검증(VALIDATE) 레벨에서 provenance 필수화는 달성. 그러나 **정상 EXECUTE(에러 없이 evidenced_by 엣지 형성)는 미달성** → 데모에서 "관측 근거로 이상징후 생성" 버튼은 매번 에러를 뱉고 반쪽 객체를 남긴다. **여전히 데모 최우선 리스크.**

### 10-3. D-4 링크 traverse 실측 (EXECUTE 1건씩 + traverse)

| 링크(스펙) | 판정 | 실측 |
|---|---|---|
| OrbitPass —of→ Satellite | **해소** | `satellite`(fk=satelliteNoradId) 링크 형성. create-orbit-pass.satelliteNoradId로 채움 → `OrbitPass.satellite` traverse=[sat] ✓ |
| Track → Aircraft | **해소** | `aircraft`(fk=aircraftIcao24) 형성. `Track.aircraft` traverse=[ac] ✓ |
| WeatherState → Region | **해소** | `region`(fk=regionId) 형성. `WeatherState.region` traverse=[rg] ✓ |
| SituationAssessment → Region | **해소** | `region`(fk=regionId) 형성(introspection 확인) |
| Observation —within→ Region | **부분(구조만)** | `Observation.region`(fk=regionId) FK 링크 + regionId 속성 **형성됨**. **그러나 create/edit-observation 어느 쪽에도 regionId 파라미터가 없어** 채울 수 없음 → traverse=[] (composed_of가 §9에서 겪던 것과 동일: 링크는 있으나 채울 액션 없음) |
| OrbitPass —over→ Region | **잔존** | regionId 속성은 있으나 **OrbitPass→Region 링크 자체 미형성**(outgoing=satellite·anomalies·situationAssessments뿐). `over` traverse=NotFound |

### 10-4. D-5 composed_of — **해소**

edit-observation에 `trackId`(opt) 파라미터 신설 확인. EXECUTE: obs2를 edit-observation(trackId=trk)으로 갱신 → `Observation.trackId` read-back=trk, `Track.observations` traverse=[obs2], `Observation.track` traverse=[trk]. **composed_of 양방향 형성 ✓.** (단 create-observation엔 trackId 없음 — custody 확정 시 edit로만 귀속, 스펙 의도대로.)

### 10-5. D-6 self-link required 해제 — **해소**

create-news-event.`newsEvents`·create-situation-assessment.`situationAssessments` 둘 다 **required=False**(introspection). self ref 생략하고 VALIDATE → 둘 다 **VALID**(§9=req junk 강제였음). self-link junk 해소 확인.

### 10-6. OSDK 0.5.0 재발행물 (`skai_osdk_sdk` 0.4.0 → **0.5.0**, FOUNDRY_OSDK_INDEX 재설치, pip 로그 토큰 스크럽)

- **Object 클래스 11개**(변동 없음): aircraft·observation·region·anomaly·operator·track·satellite·orbit_pass·weather_state·news_event·situation_assessment.
- **Action 메서드 36개.** ✅ **§9에서 누락됐던 `delete_orbit_pass`·`editrack`이 이번엔 포함**(D-7 지적 반영). ⚠️ **그러나 `set-region-alert-level`이 OSDK 0.5.0에 누락**(live 37 vs OSDK 36). D-2로 정상화된 액션인데 재발행 대상에서 빠짐. → 코드가 OSDK 타입드로 set-alert를 부르려면 저수준 SDK 폴백 필요(단, set-region-alert-level은 저수준 SDK로 정상 실행됨 = 스키마 아닌 발행 갭).

### 10-7. 남은 갭 목록

**A. 사용자 UI 작업 (Ontology Manager — 코드로 불가):**
1. **[데모 최우선] create-anomaly ApplyActionFailed 제거** — ⑴ **`newParameter1`(required objectSet<Observation>) 제거 또는 재배선**(이게 실행 실패의 범인, D-1 항목3 미집행). 근거 세트가 필요 없으면 삭제, 필요하면 단일 `observations`와 규칙 중복 없이 정리. ⑵ 근거 링크는 **단일 `observations`(req) 하나로 충분**(엣지 형성 확인됨). ⑶ 정리 후 evidence 포함 EXECUTE가 **에러 없이 evidenced_by 엣지를 형성**하는지 재확인(현재 half-Anomaly + 빈/불안정 엣지). ⑷ `evidence`(string) 스칼라 파라미터는 그래프 근거가 아님 — 혼동 방지 위해 명명/용도 정리(선택).
2. **over(OrbitPass→Region) 링크 지정** — regionId 속성은 있으나 링크 미형성. Object Type Links에서 FK 링크로 지정(위성 통과-지역 서사 traverse).
3. **within(Observation→Region) 채움 수단** — 링크는 형성됐으나 **create/edit-observation에 regionId 파라미터가 없어** 채울 수 없음. 파라미터 추가(지오펜스 진입 판정 백본).
4. (권장·D-7 잔여) edit-aircraft.isMilitary **여전히 string**(객체 속성·create는 boolean) 타입 정정 / `newParameter`→icao24·obsId·anomalyId·id 리네임 미집행 / editrack↔edit-track 중복 명명 잔존.
5. 위 반영 후 **OSDK 재발행 시 set-region-alert-level 포함**(0.5.0 누락분).

**B. 코드측 후속 (store_foundry.py — 이번 세션 미수정, 열거만):**
> A의 해당분이 닫힌 뒤에 배선. 지금 스키마 상태 기준 가능/불가만 표시.
1. `write_operator/satellite/orbit_pass/track/weather_state/news_event/assessment` — **지금 배선 가능**(7종 create PK 파라미터 해소 → newParameter=자연키로 write + ObjectAlreadyExists dedup).
2. `write_anomaly` — **A-1 해소 전엔 불가**(ApplyActionFailed·엣지 불안정). anomalyId PK·confidence·status·explanation·evidence 스칼라는 파라미터 존재하나 evidenced_by 엣지가 실행 실패로 불안정.
3. `write_orbit_pass`의 of(satelliteNoradId), `write_track`의 aircraftIcao24, `write_weather_state`/`write_assessment`의 regionId → **지금 배선 가능**(FK 링크 형성·채움 확인). `write_observation`의 within(regionId)·composed_of는 A-3 해소 후(regionId 파라미터)·edit-observation.trackId(지금 가능).
4. `set_region_alert_level` 호출부 — **지금 저수준 SDK로 가능**(정상 실행 확인). OSDK 타입드는 0.5.0 누락으로 저수준 폴백 필요.
5. confirm/dismiss-anomaly — 지금도 정상(status 전이).

**핵심 3줄 요약**: ⓐ D-2·D-3·D-5·D-6 **해소**(set-alert Modify화, 신규7타입 PK+dedup, composed_of, self-link 해제). ⓑ D-4 **부분**(of·Track→AC·Weather→Region·Assessment→Region 4종 traverse OK / over 미형성·within 채움불가 2종 잔존). ⓒ **★D-1 부분** — evidence 파라미터 신설·필수화·거부검증은 됐으나(진전) **create-anomaly가 ApplyActionFailed 재발 + half-Anomaly + evidenced_by 엣지 불안정**(범인=미제거된 required objectSet `newParameter1`) → **데모 provenance 백본은 여전히 미완, UI 재수정 최우선**. OSDK 0.5.0은 delete-orbit-pass·editrack 포함(진전)이나 set-region-alert-level 누락.

---

## 11. store_foundry 전량 확장 (2026-07-04, opus 서브에이전트)

§10-7 B목록("지금 배선 가능")을 `store_foundry.py`에 집행. 저수준 `foundry_sdk` 1.97.0 라이브 왕복으로 검증. **판정: [EXTEND-OK]** — write 7종·composed_of·set-alert·delete-orbit-pass 전부 통과, Foundry 순증 0(KADIZ Region은 데모 자산으로 유지).

**검증 산출물**: `scripts/p7_extend_validate.py`(신규, HybridStore write → Foundry read-back·traverse → delete). 파라미터 형태는 `scripts/p7_full_introspect.py` 재실행 실측으로 그라운딩(37 액션 required 플래그 전수).

### 11-1. 라우팅 설계 (스키마 결함이 강제한 3분류)

Foundry Object의 **속성 부분집합**이 model 필드를 잃는 곳이 있어(실측), 정보 소재를 3가지로 나눴다:

| 소재 | 타입 | write | read | 근거 |
|---|---|---|---|---|
| **Foundry**(write+read) | Aircraft·Observation·Operator·Satellite·OrbitPass·Track·WeatherState·NewsEvent | Foundry create 액션 | 저수준 SDK dict→dataclass | Foundry가 충실히 보관(경미한 손실은 복원/폴백) |
| **로컬**(권위본) | Region·Anomaly + provenance MANY-MANY 링크(evidenced_by·involves·correlated_with·mentions·aggregates·cites) + SituationAssessment **문장 cites** | 로컬 | 로컬 | write_anomaly Foundry 미구현(D-1), Foundry MANY-MANY 불안정(§9-4) |
| **dual-write** | SituationAssessment(스칼라)·NewsEvent(mentions) | Foundry 스칼라 + 로컬 문장/링크 | 로컬(문장 보존) | Foundry SituationAssessment에 sentences 속성 없음(§9-2) → 문장은 로컬만 |

**필드 손실(문서화, 코드로 못 고침 — 스키마 갭):**
- **WeatherState.station 속성 없음** → `weatherId`(f"wx-{station}-{ts}") PK에서 복원(`_station_from_weather_id`). model의 wind_dir/wind_speed는 Foundry `wind` 단일 문자열("200/8")로 합성/역파싱.
- **OrbitPass.ground_track 속성 없음** → read 시 빈 리스트(지도 궤적 레이어 폴백 필요). ceilingFt None(무제한)은 sentinel 99999로 왕복.
- **SituationAssessment sentences 없음** → dual-write로 로컬이 문장 cites 권위본.
- Observation.attrs(§5) 잔존.

### 11-2. write 7종 라이브 왕복 (`p7_extend_validate.py`, 항목당 1건·끝에 delete)

| write | 결과 | traverse/확인 |
|---|---|---|
| write_operator | **OK** | operatorId=요청값 PK, kind read-back |
| write_satellite | **OK** | noradId=요청값 PK(UUID 아님) + 같은 PK 재호출 dedup 불변 |
| write_orbitpass | **OK** | `of`: OrbitPass.satellite → [sat] ✓. `over`: **미형성**(§10-3 잔존, NotFound — 예상) |
| write_track | **OK** | Track.aircraft → [aircraft] ✓ (FK) |
| write_weatherstate | **OK** | WeatherState.region → [KADIZ] ✓. conditions←flight_category("MVFR"), wind="200/8" 합성 |
| write_newsevent | **OK** | 객체 Foundry(url←source_url, confidence 0.9→**0.4 clamp**). mention 링크는 **로컬 권위본**(query_mentions 로컬) |
| write_assessment | **OK** | Foundry 스칼라 assessmentId ✓ + **로컬 문장 cites 보존** ✓ (dual-write) |

**링크·정리·전이:**
- **composed_of**: `edit-observation.trackId`로 형성 → Observation.track → [track] / Track.observations → [obs] **양방향 ✓** (custody 확정 후 edit 경로, §10-5).
- **observed_as**: write_observation.aircraftIcao24 FK로 자동 → Observation.aircraft → [aircraft] ✓.
- **set_region_alert_level**: 임시 Region alertLevel None→**RED** ✓, Region count 불변(팬텀 없음). 저수준 SDK(OSDK 0.5.0 누락 회피).
- **delete_future_orbitpasses_for**: 미래 통과창 1건 delete-orbit-pass로 삭제 ✓ (재계산 정리 배선).

**write_anomaly는 미구현 유지**(D-1 미해소: create-anomaly ApplyActionFailed·half-Anomaly·evidenced_by 불안정, 범인=required objectSet `newParameter1`). 주석에 §10 근거 갱신.

### 11-3. 단위 테스트 + 회귀

- `tests/test_foundry_store.py`: 파라미터 매핑(6 write × PK/FK/clamp/provenance)·라우팅(Foundry vs 로컬)·dedup·ObjectAlreadyExists skip·composed_of edit·set-alert·delete-pass·counts 병합 등 **신규 ~30건 추가**. FakeFoundry에 7타입 write/read + set-alert + delete-pass 확장.
- 구 `test_write_track_and_anomaly_route_to_local`은 **write_track이 이제 Foundry 소재**라 2개로 분할(track→Foundry / anomaly→로컬).
- **전체 pytest: 165 passed, 2 skipped**(라이브 skip). 회귀 0.

### 11-4. Foundry 상태 (검증 후)

정리 완료 — 순증 0. 현 카운트: **Aircraft 6·Observation 3**(§8 이전 세션 잔존 baseline) + **Region 1(KADIZ, 데모 자산 유지)**, 나머지 0. `p7_extend_validate.py`는 임시 로컬 db(`/private/tmp/...`)를 써 실 `skai.db` 미오염.

### 11-5. 남은 코드측 갭 (UI 선행조건 대기)

1. **write_anomaly** — D-1(create-anomaly ApplyActionFailed) UI 해소 후 배선. 현재 로컬 권위본.
2. **over(OrbitPass→Region)·within(Observation→Region)** — 링크 미형성/채움불가(§10-3). 지역 traverse는 regionId 속성값으로만 가능(그래프 조인 불가).
3. **Foundry-primary read의 필드 손실**(station 복원·ground_track 폴백·assessment 문장 로컬) — 스키마에 속성 추가되면 dual/폴백 제거 가능.
4. SituationAssessment·NewsEvent mentions는 dual/asymmetric — Foundry MANY-MANY·문장 속성이 정상화되면 단일화.

---

## 12. D-1 최종 재검증 (2026-07-04, opus 서브에이전트)

사용자가 D-1 수정(create-anomaly의 `newParameter1` 제거) + OSDK 재발행(0.5.0→**0.6.0**)을 마쳤다고 하여 **최종 실측 재검증**. 저수준 `foundry_sdk` 1.97.0 라이브 introspection + create-anomaly EXECUTE 왕복(3회 반복 + 3변형 진단, 매건 delete 정리). **Foundry 오염 0** (모든 iteration delta 0, 최종 카운트 Aircraft 6·Observation 3·Anomaly 0·Region 0 = 세션 baseline).

**검증 산출물**: `scripts/p7_full_introspect.py`(재사용), `scripts/p7_d1_final.py`(신규, D-1 evidence 강제 EXECUTE+traverse).

### 12-0. 종합 판정 — **★D-1 부분 해소(그래프 재현 가능 / 액션 spurious error 잔존)**. §10 대비 실질 진전.

`newParameter1` 제거로 **evidenced_by 엣지가 §10의 "불안정/빈 half-Anomaly"에서 "안정적 완전 Anomaly"로 개선**됐다. 그러나 §10이 지목한 "범인 newParameter1"을 제거해도 **create-anomaly EXECUTE는 여전히 ApplyActionFailed(non-fatal)를 던진다** → **ApplyActionFailed의 진짜 원인은 newParameter1이 아니었다**. 다만 이 에러는 데이터를 손상시키지 않는다(Anomaly 스칼라 + evidenced_by 엣지 + involves 모두 정상 커밋).

### 12-1. create-anomaly 최종 파라미터 (introspection)

`type·ts·lat·lon`(req) / `observations`(**object=Observation 단일, required** — 근거) / `confidence·status·explanation`(opt) / `aircraft·newsEvents·orbitPasses`(opt object) / `newParameter`(=anomalyId PK, req).

- **`newParameter1` 제거 확인** ✓ (§10의 required objectSet<Observation> orphan 삭제됨).
- **`evidence`(string 스칼라) 제거 확인** ✓ (§10의 그래프 무의미 스칼라 삭제됨).
- 근거 경로는 **단일 `observations`(req) 하나로 정리**됨 — §10-7 A-1 권고대로.

### 12-2. ★★ evidence 강제 최종 판정

| 검증 항목 | 결과 | 근거 |
|---|---|---|
| **evidence 없이 거부되나** | **OK (거부됨)** | `observations` 생략 시 VALIDATE=**INVALID**. 근거 없는 Anomaly = provenance 강제 ✓ |
| **evidence 포함 → ApplyActionFailed 재발?** | **예 (잔존)** | EXECUTE 시 매번 `BadRequestError / INVALID_ARGUMENT / ApplyActionFailed / parameters:{}`. VALIDATE는 VALID인데 EXECUTE만 실패 |
| **그러나 Anomaly 완전 생성?** | **예** | 스칼라(anomalyId·status=candidate·confidence·type·explanation) read-back 전부 정상 |
| **evidenced_by→Observation traverse** | **OK (안정)** | `Anomaly.observations → [근거 obs]` **양방향** 형성(역방향 `Observation.anomalies → [anomaly]`도). **3회 반복 + 3변형 = 6/6 전부 형성**(§10 "불안정"과 반대) |
| **half-Anomaly?** | **해소** | §10은 스칼라만/엣지 빈 반쪽. 지금은 스칼라 + evidenced_by 엣지 + (aircraft 주면) involves 엣지까지 완전 |

### 12-3. ApplyActionFailed 원인 좁히기 (진단 3변형)

`observations`만 / `+aircraft(optional)` / `+confidence·status·explanation` **세 변형 모두** ApplyActionFailed 발생 + Anomaly 완전 생성 + evidenced_by 엣지 형성(aircraft 주면 involves 엣지도 `['847114']` 형성). → **에러는 어떤 optional 파라미터 때문이 아니라 create-anomaly 액션 규칙 자체의 후처리(post-edit 함수/알림/부가규칙 추정)에서 발생**하며, 모든 edit이 커밋된 뒤 터져 데이터에 무해(non-atomic·spurious). = **UI 액션 config 문제**(코드로 회피 가능: catch 후 read-back 성공 판정).

### 12-4. D-4 잔여 재확인 (§10 대비 변동 없음)

- **over (OrbitPass→Region)**: **잔존(미형성)**. OrbitPass outgoing 링크 = anomalies·satellite·situationAssessments뿐. regionId 속성만 있고 링크 미지정.
- **within (Observation→Region)**: **부분(구조만)**. `Observation.region`(fk=regionId) 링크는 형성됐으나 **create/edit-observation 어느 쪽에도 regionId 파라미터가 없어 채울 수 없음**(edit-observation 파라미터 재확인: regionId 없음). traverse=[].
- (데모 필수 아님 — 상태만 기록.)

### 12-5. OSDK 0.6.0 재발행물 (`skai_osdk_sdk` 0.5.0 → **0.6.0**, FOUNDRY_OSDK_INDEX 재설치, pip 로그 토큰 스크럽)

- **Object 클래스 11개**(변동 없음).
- **Action 메서드 36개**: create×11·edit×11·delete×11·editrack·confirm_anomaly·dismiss_anomaly. delete-orbit-pass·editrack **포함**(진전 유지).
- **⚠️ `set-region-alert-level` OSDK 0.6.0에도 여전히 누락**(live 37 vs OSDK 36). §10에서 지적된 0.5.0 누락분이 재발행에 **미반영**. set-region-alert-level 자체는 라이브 스키마·저수준 SDK로 정상 작동(D-2 Modify 규칙 확인) = 스키마 아닌 **발행 갭 지속**. 코드가 OSDK 타입드로 set-alert 부르려면 저수준 SDK 폴백 필요.

### 12-6. 데모 provenance 백본 Foundry 재현 — **조건부 가능**

- **그래프/데이터 관점: 가능.** evidence 강제(거부검증 INVALID) + Observation 근거 → Anomaly `evidenced_by` 엣지 **안정 양방향 형성** + `involves`(aircraft) 형성 + traverse 정상 + Foundry 오염 0. **§10의 "재현 불가"에서 실질 전환.** ontology.md §3·§4 provenance 서사(관측 근거→이상징후, 근거 역추적)를 Foundry 그래프로 재현·traverse 가능.
- **액션 UX 관점: 흠 잔존.** create-anomaly가 매 EXECUTE마다 non-fatal ApplyActionFailed를 반환 → 데모 버튼이 raw error를 노출하면 흠. **코드 회피 가능**: store_foundry의 `write_anomaly`가 ApplyActionFailed를 catch하고 read-back(객체 존재 + evidenced_by 엣지)으로 성공 판정하면 데모 백본 완성(§10에선 엣지가 불안정해 이 회피조차 불가였음 — 지금은 엣지가 안정이라 read-back 판정이 신뢰 가능).

### 12-7. 남은 갭 (우선순위)

**A. 사용자 UI 작업 (Ontology Manager):**
1. **[선택·데모 품질] create-anomaly ApplyActionFailed 제거** — 액션 규칙의 후처리(부가 add-link 규칙/함수/알림) 점검. 데이터는 무손상이므로 데모 필수는 아니나, 버튼 raw error 제거하면 깔끔. (VALIDATE=VALID·EXECUTE만 실패 → 실행 단계 부가규칙이 원인.)
2. **over(OrbitPass→Region) 링크 지정** + **within 채움 수단**(create/edit-observation에 regionId 파라미터) — §10 잔여, 데모 필수 아님.
3. **OSDK 재발행 시 set-region-alert-level 포함**(0.5.0·0.6.0 연속 누락).
4. (권장·D-7 잔여) `newParameter`→PK명 리네임, edit-aircraft.isMilitary string→boolean, editrack↔edit-track 중복.

**B. 코드측 (store_foundry.py — 이번 세션 미수정, 별도 에이전트 작업 중이라 read-only):**
1. `write_anomaly` — **지금 배선 가능**: anomalyId PK + observations(근거 obs PK) + confidence/status/explanation. **단 create-anomaly의 spurious ApplyActionFailed를 catch + read-back 검증으로 성공 처리** 필요(에러 무시하되 엣지 형성 확인). §10 "불가"에서 "가능(에러 흡수 조건)"으로 전환.
2. `set_region_alert_level` 호출부 — 저수준 SDK로 정상(OSDK 0.6.0 누락 → 저수준 폴백).

**핵심 3줄 요약**: ⓐ **★D-1 부분 해소** — `newParameter1` 제거로 evidenced_by 엣지가 **불안정→안정 완전 형성**(6/6), half-Anomaly 해소, evidence 없으면 거부(provenance 강제). **그러나 create-anomaly EXECUTE는 여전히 ApplyActionFailed(non-fatal, 데이터 무손상)를 던짐** → 범인은 newParameter1이 아니었음이 판명. ⓑ **데모 provenance 백본 = 조건부 재현 가능** — 그래프/traverse는 완전 재현, 남은 것은 액션의 spurious error뿐이고 코드에서 read-back으로 흡수하면 데모 백본 완성(§10 "재현 불가"에서 실질 전환). ⓒ D-4 over·within 잔존(데모 필수 아님), OSDK 0.6.0은 set-region-alert-level 여전히 누락(발행 갭 지속), Foundry 오염 0.

---

## 13. write_anomaly 배선 (2026-07-04, opus 서브에이전트)

§12-7 B-1("에러 흡수 조건 하 배선 가능")을 `store_foundry.py`에 집행 — Anomaly의 마지막 조각.
저수준 `foundry_sdk` 1.97.0 라이브로 실 store 코드 경로를 태워 검증. **판정: [ANOMALY-OK]** —
§12 무해 ApplyActionFailed 흡수·evidenced_by/involves traverse·confirm 전이 전부 통과, Foundry 순증 0.

**검증 산출물**: `scripts/p7_anomaly_wire_validate.py`(신규, HybridStore.write_anomaly → read-back·
traverse → confirm 전이 → delete). §12 `p7_d1_final.py`의 에러 흡수·traverse 패턴 재사용.

### 13-1. 구현 내용 (store_foundry.py)

| 항목 | 내용 |
|---|---|
| `FoundryOntologyStore.write_anomaly` | `_unsupported` → **실배선**. create-anomaly 파라미터: type·ts·lat·lon·**observations(첫 근거 Observation, req)**·newParameter(anomalyId PK) + opt confidence·status·explanation·**aircraft(첫 involves)**·newsEvents/orbitPasses(해당 근거 있을 때). |
| **§12 에러 흡수** | `_create_anomaly_absorbing`: create-anomaly apply를 try/except. ObjectAlreadyExists=dedup skip. 그 밖 예외 → **`_anomaly_written_ok`(객체 존재 + `_traverse`로 evidenced_by 엣지 확인)이 참이면 무해 에러로 흡수**, 거짓이면 진짜 실패로 예외 전파. |
| **단일 링크 파라미터** | create-anomaly의 근거는 단일 `observations`뿐 → **첫 Observation만 Foundry 엣지**, 나머지 근거·OrbitPass/NewsEvent 근거·involves 다건·correlated_with는 **로컬 권위본**. involves는 첫 Aircraft만 opt `aircraft`로. |
| **EvidenceError** | `validate_evidence`로 빈 evidence 거부(백엔드 무관 불변식 유지) — FoundryStore·HybridStore 양쪽. |
| Observation 근거 부재 시 | 타입드 근거(OrbitPass 등)만 있고 Observation이 없으면 Foundry required observations를 못 채움 → **Foundry 스킵(경고)·로컬 권위본만**. |
| `set_anomaly_status`(신규) | confirmed→`confirm-anomaly`, dismissed→`dismiss-anomaly` 액션(`anomaly` object 파라미터). candidate 등은 no-op. |
| `HybridStore.write_anomaly` | **dual-write**: 로컬 먼저(EvidenceError 강제 + 전체 링크 권위본) → Foundry best-effort(실패는 경고, 로컬 권위본 유지). |
| `HybridStore.set_anomaly_status` | dual: 로컬 전이·**Anomaly 반환**(권위본) + Foundry confirm/dismiss 동기(실패 경고). read는 로컬. |

### 13-2. dual 라우팅 최종표 (Anomaly 반영)

| 소재 | write | read | 비고 |
|---|---|---|---|
| Aircraft·Observation·Operator·Satellite·OrbitPass·Track·WeatherState·NewsEvent | Foundry create 액션 | Foundry 저수준 SDK | §11 (변동 없음) |
| **Anomaly** | **dual-write** — Foundry 스칼라 + 단일 observations(evidenced_by)·단일 aircraft(involves) 엣지(§12 에러 흡수) / **로컬 = 권위본**(전체 evidenced_by·involves·correlated_with 링크) | **로컬** | 이번 세션 신규 배선 |
| **Anomaly 상태 전이** | **dual** — 로컬 권위본(반환) + Foundry confirm/dismiss-anomaly | 로컬 | 이번 세션 신규 |
| Region | 로컬 | 로컬 | Foundry Region은 FK 타깃(KADIZ)만 |
| SituationAssessment | dual(Foundry 스칼라 + 로컬 문장 cites) | 로컬 | §11 |
| NewsEvent mentions | dual(Foundry required-param best-effort + 로컬 권위 링크) | 로컬 | §11 |

### 13-3. 라이브 검증 (`p7_anomaly_wire_validate.py`, 근거=기존 잔존 Observation 재사용)

| 검증 | 결과 |
|---|---|
| write_anomaly 예외 전파 | **없음** — `[store_foundry] create-anomaly: … 무해 ApplyActionFailed 흡수 (read-back: 객체+evidenced_by 엣지 확인)` 경고 후 정상 반환 |
| Anomaly 스칼라 read-back | **OK** — anomalyId·status=candidate·confidence=0.9 정상 |
| evidenced_by traverse | **OK** — `Anomaly.observations → [근거 obs]`, 역방향 `Observation.anomalies → [anomaly]` 양방향 |
| involves traverse | **OK** — `Anomaly.aircraft → [847114]` |
| confirm 전이 | **OK** — Foundry status candidate→**confirmed**, 로컬 권위본도 confirmed 동기 |
| Foundry 정리 | **순증 0** — delete-anomaly 후 Aircraft 6·Observation 3·Anomaly 0 (= baseline) |

### 13-4. 단위 테스트

- `tests/test_foundry_store.py`: FoundryOntologyStore 레벨 §12 에러 흡수(ApplyActionFailed→read-back 흡수 / 객체 미존재→예외 / 엣지 미형성→예외) · 파라미터 매핑(observations 첫 근거·aircraft·스칼라) · 빈 evidence EvidenceError · 타입드 근거만→Foundry 스킵 · dedup · ObjectAlreadyExists skip · confirm/dismiss 액션 매핑. HybridStore 레벨 dual-write(로컬 권위본 + Foundry 스파인) · Foundry 실패 흡수 · confirm/dismiss dual 전이 · 전이 실패 흡수.
- FakeFoundry에 `write_anomaly`·`set_anomaly_status` 추가. 구 `test_write_anomaly_routes_to_local`은 dual-write로 재작성.
- **전체 pytest: 178 passed, 2 skipped**(라이브 skip). 회귀 0 (§11의 165 → +13 신규 anomaly 테스트).

### 13-5. 남은 갭 (Anomaly 관련)

1. **correlated_with·다중 근거·다중 involves는 로컬 권위본만** — create-anomaly의 링크 파라미터가 단수라 Foundry 그래프엔 첫 근거·첫 involves만. Foundry MANY-MANY 링크 채움 액션이 생기면 단일화 가능(스키마 UI 작업).
2. **create-anomaly의 무해 ApplyActionFailed** — 코드에서 흡수하므로 인제스트/데모엔 무영향이나, 액션 후처리 규칙 정리(§12-7 A-1)는 UI 잔여(데모 필수 아님). Anomaly의 `explainer_backend`·`attrs`·`created_at`은 Foundry 속성 부재로 미저장(로컬 보존).
3. **over(OrbitPass→Region)·within(Observation→Region)** — §12-4 잔존(링크 미형성/채움불가, 데모 필수 아님).

**핵심 3줄 요약**: ⓐ **write_anomaly Foundry 배선 완료** — create-anomaly가 매번 던지는 무해 ApplyActionFailed를 catch + read-back(객체+evidenced_by 엣지)으로 흡수, Anomaly는 dual-write(Foundry 스칼라·첫 근거/involves 엣지 + 로컬 전체 링크 권위본). ⓑ **confirm/dismiss 상태 전이도 dual 배선** — 로컬 권위본 반환 + Foundry confirm/dismiss-anomaly 동기. ⓒ 라이브에서 에러 흡수·evidenced_by/involves traverse·confirm 전이 전부 OK, pytest 178 passed, Foundry 순증 0. **데모 provenance 백본이 코드 경로로 완성**(§12 "조건부 가능"→집행).

---

## 14. 데모 라이브 Foundry 세그먼트 배선 + 크로스런 dedup 수정 (2026-07-04, opus 서브에이전트)

demo.md 스텝 ⑥을 "준비 완료·크리덴셜 대기" 문구에서 **실 Foundry 실연**으로 교체(발표 원커맨드
`scripts/demo_foundry.sh` 신설). 리허설 실행 중 **store_foundry의 크로스런 dedup 버그 2건을 실측·수정**.

**산출물**: `scripts/demo_foundry.sh`(발표 원커맨드 래퍼, `.venv312`+`SKAI_STORE=foundry` 자동) +
`scripts/demo_foundry.py`(드라이버: (a)OpenSky 1사이클 인제스트·(b)합성 비상 스쿽→write_anomaly·
(c)confirm, `cleanup` 서브커맨드). demo.md §0·§1⑥·§3·부록A, P6-demo.md §6-F·§8-5 갱신.

### 14-1. ★ store_foundry 실측 버그 2건 (리허설이 노출 — 첫 실행은 통과, 2회차부터 실패)

| # | 증상 | 원인 | 수정 |
|---|---|---|---|
| **1** | 2회차 실행 phase A에서 실 항공기 재인제스트가 `write 실패(ConflictError)` 스팸 (dedup 안 됨) | 실 Foundry는 크로스런 PK 중복을 **`ConflictError`**(타입명)로 던지고 errorName은 메시지 JSON에 **`ObjectAlreadyExists`**(구분자 없는 연결형)로만 실림. `_is_already_exists`가 `already_exists`(밑줄)·`already exists`(공백)만 봐 놓침 → write_aircraft/observation의 §13 문서화된 크로스런 dedup이 **실제론 작동 안 했음**(§8 "dedup OK"는 동일프로세스 캐시였을 뿐, 실 ObjectAlreadyExists 미실험) | `store_foundry._is_already_exists`에 **`objectalreadyexists` 부분매칭 추가**(가법적). `LinkAlreadyExists`는 의도적으로 제외(객체중복 아닌 실패 → read-back 경로로). **pytest 178 통과**(테스트의 이름/메시지 매칭 불변) |
| **2** | 2회차 실행 phase B에서 anomaly read-back FAIL (`ConflictError/LinkAlreadyExists`, 객체 미생성) | 비상 스쿽 anomaly_id = `anomaly-emergency_squawk-{aircraft_ref}-{window}`(window=ts//600). 합성 aircraft_ref가 고정이면 **같은 10분 창 내 재실행이 동일 anomaly PK를 삭제→재생성**하고, Foundry가 evidenced_by **링크 tombstone**을 남겨 재생성이 LinkAlreadyExists로 실패(§12 무해 에러와 별개의 진짜 실패) | **데모측 수정**(store 무변경): 합성 식별자를 실행마다 유니크하게(`icao24=f"skaidemo{ts}"`) → anomaly PK 매 실행 유니크 → PK 재사용 churn 제거. 정리는 접두(`skaidemo`) 매칭 |

- #1은 store 코어 수정(1줄 가법). **CHANGELOG/DR 후보** — Fable 종합 판단(SKAI_STORE=foundry로 반복
  인제스트하는 모든 경로에 영향, 현재는 데모만 해당). #2는 데모 스크립트 국소 수정.

### 14-2. demo_foundry 설계 (발표 안전)

- **(a)** OpenSky **1회/실행** fetch → 상위 3건만 write(Aircraft/Observation, observed_as FK). 실 항공기는
  이제 #1 수정으로 조용히 dedup(+0).
- **(b)** 합성 비상 스쿽(squawk=7500, `source=synthetic` 명시) 주입 → `scan_and_create`(룰 엔진) →
  write_anomaly(근거 강제 EvidenceError + §12 에러 흡수) → Foundry Anomaly + evidenced_by/involves 엣지.
- **(c)** confirm_anomaly → dual(로컬 권위본 + Foundry confirm-anomaly) → status confirmed read-back.
- **데모 자산 정책**(P7 §13 "순증 0"과 **명시적 구분**): (b)(c) 산출물은 Object Explorer 시연용으로 **남긴다**.
  누적 방지 = 실행마다 유니크 PK + 매 실행 시작 시 직전 합성 자산(접두 매칭) 자동 정리 + `cleanup` 서브커맨드.
  실 hex 인제스트분은 실데이터로 보존(dedup).
- **실패 안전**: Foundry 연결 실패=하드중단(exit 3)+폴백 안내(demo.sh replay 전환). OpenSky/네트워크만
  실패=(a) 스킵하고 (b)(c) 진행. 셸 래퍼가 `.venv312`/`.env` 부재도 폴백 처리. **토큰·호스트네임 미출력**.

### 14-3. 리허설 실측 (2026-07-04)

- **back-to-back 2회 실행 [DEMO-FOUNDRY-OK]**: (a) 실 항적 dedup 인제스트(Aircraft +0·Observation +3,
  observed_as FK traverse OK), (b) evidenced_by(→Observation)·involves(→Aircraft) 엣지 형성(§12 무해
  ApplyActionFailed 흡수 경고 후 정상), (c) confirm→confirmed 전이. 2회차가 1회차 합성 자산(각 1건) 자동 정리.
- **정리 후 합성 순증 0**(잔여 skaidemo anomaly/obs/aircraft = 0). 실 hex 인제스트분은 보존(문서화).
- **회귀**: pytest **178 passed·2 skipped**(store #1 수정 후). `demo.sh replay`(SKAI_STORE 미설정=순수 로컬)
  무변경 — 이상징후 9·상관 55·/api/stats 정상, 서버 클린 정지.

### 14-4. 남은 갭

1. store #1 수정의 CHANGELOG/DR 반영은 Fable 판단(위 14-1).
2. 실 hex 인제스트 누적(리허설로 Foundry Aircraft 6→15·Observation 3→17): 실 ADS-B라 보존했으나, 완전
   pristine 복원을 원하면 최근 ts 관측 삭제 필요(데모엔 populated Object Explorer가 오히려 유리 — 미실행).
3. create-anomaly 무해 ApplyActionFailed·over/within 링크 잔존(§12-7·§13-5, 데모 필수 아님).

---

## 15. 0.7.0 재검증·동기화 (E부 반영 후, 2026-07-04, opus 서브에이전트)

사용자가 **create-anomaly 규칙 수정**(§12 가짜 에러 원인 = 링크가 신규 객체가 아니라 `anomalies`
입력 파라미터에 연결되던 것) + **E부 반영**(E-1~E-4 대부분) + **OSDK 0.7.0 재발행**을 마친 뒤 전수
실측 재검증 + **코드(파라미터명)를 실측에 동기화**. 저수준 `foundry_sdk` 1.97.0 introspection +
OSDK 0.7.0 강제 재설치(--no-cache-dir, pip 로그 토큰 스크럽) + 라이브 왕복. 스키마 변경 없음.
**Foundry 오염 0**(모든 생성분 delete, before==after: Aircraft 15·Observation 17·Region 1[KADIZ] 유지).

**검증 산출물**: `scripts/p7_full_introspect.py`(재사용, 11객체·전링크·36액션), `scripts/p7_e_reverify.py`
(신규, create-anomaly 클린 실행 raw-SDK 판정), `scripts/p7_e_store_validate.py`(신규, **실 store 코드
경로** 타입 왕복·anomaly 클린 생성·confirm·정리).

### 15-0. 종합 판정 — **★create-anomaly 클린 실행 해소 / E-4 리네임 전면 완료 / 코드 동기화 완료**

- **create-anomaly 클린 실행 = 예.** 사용자 수정으로 EXECUTE가 **ApplyActionFailed 없이 깔끔 성공**
  (err=None). §12~§13의 "무해 ApplyActionFailed + 코드 흡수" 국면 종료. 흡수 경로는 방어용으로 유지하되
  라이브에서 **미발동 확인**(store 경로 stderr 감시: "무해 ApplyActionFailed 흡수" 경고 0회).
- **E-4 리네임 = 전면(create 11종 전부).** `newParameter` → 실 PK명(icao24·obsId·anomalyId·operatorId·
  noradId·passId·trackId·weatherId·newsId·assessmentId + region은 `Id`). **부분 리네임 아님** →
  store_foundry 16 호출 중 10 create 호출을 실 PK명으로 일괄 동기. **단 `edit-observation`만 리네임
  제외** — 유일하게 `newParameter`(required) 잔존 → composed_of 경로(`_set_observation_track`)는 그대로 유지.
- **E-2/E-3 대부분 반영.** over·within 링크 형성(E-2.1·E-2.2), correlatedWith 파라미터 추가(E-2.3, 단
  **required**), mentions Optional화(E-2.4), 6개 신규 속성 전부 객체에 존재(E-3). 단 **채울 액션
  파라미터가 없는 신규 속성 4개**(station·groundTrackJson·sentencesJson·attrsJson)는 여전히 미배선.

### 15-1. OSDK 0.7.0 포함범위 (강제 재설치·토큰 스크럽)

- **Object 클래스 11개**(변동 없음).
- **Action 36개**: create×11·edit×11·delete×11·confirm-anomaly·dismiss-anomaly·**set-region-alert-level**.
  ✅ **set-region-alert-level 포함 확정**(0.5.0·0.6.0 **연속 누락 해소** — E-5 "발행 화면 직접 체크" 반영).
  live 스키마와 정합(editrack은 E-1.1로 삭제됨 → live·OSDK 모두 edit-track만).

### 15-2. E-4 리네임 현황 (create-* PK 파라미터 실측)

| create 액션 | 구 PK 파라미터 | **실측 PK 파라미터** | store 동기 |
|---|---|---|---|
| create-aircraft | newParameter | **icao24** | ✓ |
| create-observation | newParameter | **obsId** | ✓ |
| create-anomaly | newParameter | **anomalyId** | ✓ |
| create-operator | newParameter | **operatorId** | ✓ |
| create-satellite | newParameter | **noradId** | ✓ |
| create-orbit-pass | newParameter | **passId** | ✓ |
| create-track | newParameter | **trackId** | ✓ |
| create-weather-state | newParameter | **weatherId** | ✓ |
| create-news-event | newParameter | **newsId** | ✓ |
| create-situation-assessment | newParameter | **assessmentId** | ✓ |
| create-region | newParameter | **Id**(대문자 — 미배선, write_region=로컬) | n/a |
| **edit-observation** | newParameter | **newParameter (잔존!)** | 유지(리네임 금지) |

→ **전면 리네임**(부분 아님) → store 파손 없음. edit-observation만 예외라 composed_of 경로는 newParameter 유지.
잔여: create-region PK는 소문자 `id`가 아니라 대문자 `Id`(write_region은 로컬 권위본이라 코드 무영향).

### 15-3. E-2 그래프 / E-3 속성 반영 현황

**E-2 (그래프):**
| 항목 | 반영 | 실측 |
|---|---|---|
| E-2.1 over(OrbitPass→Region) | **반영** | `OrbitPass.region`(FK=regionId) 링크 형성 → 라이브 traverse=[KADIZ] ✓. write_orbitpass.regionId가 이제 그래프 조인 |
| E-2.2 within(Observation→Region) | **부분** | `Observation.region`(FK) 링크 + create/edit-observation.regionId 파라미터 **둘 다 생김**(채움 가능). 단 Observation **모델에 region_ref 필드 없음** → 코드 배선은 지오펜스 판정 로직 필요(미배선, 잔여) |
| E-2.3 correlatedWith(Anomaly→Anomaly) | **반영(단 required)** | create-anomaly에 `correlatedWith`(object) 추가 — **required=True**(E-2.3 "Optional" 의도와 상이). 실 ref 주면 correlatedWithAnomalies 엣지 형성 ✓, placeholder("none")면 엣지 미형성(present-only). 코드는 placeholder로 required 충족(다건 correlated_with는 로컬 권위본) |
| E-2.4 mentions Optional화 | **반영** | create-news-event.aircraft·operators·regions 전부 **Optional 강등** 확인 → 코드가 실 ref 없으면 파라미터 생략(구 "none" placeholder 제거) |

**E-3 (속성):** 6개 신규 속성 **전부 객체에 존재**(station·groundTrackJson·sentencesJson·createdAt·
explainerBackend·attrsJson). 단 **create 액션에 채울 파라미터가 있는 것은 Anomaly의 createdAt·
explainerBackend뿐** → 이 둘만 write_anomaly에 **배선**(라이브 read-back 확인). 나머지 4개(station·
groundTrackJson·sentencesJson·attrsJson)는 해당 create 액션에 파라미터가 없어 **여전히 미배선** →
station=weatherId PK 복원·ground_track=로컬 폴백·assessment 문장=로컬 권위본·obs.attrs=미저장 유지.

### 15-4. ★★ create-anomaly 클린 실행 판정 (raw-SDK `p7_e_reverify.py` + store `p7_e_store_validate.py`)

| 검증 | 결과 |
|---|---|
| evidence 없이(observations 생략) → 거부? | **INVALID (거부 유지)** — provenance 강제 ✓ |
| observations + correlatedWith 포함 EXECUTE → **ApplyActionFailed?** | **아니오 (깔끔 성공, err=None)** ★ |
| evidenced_by(→Observation) traverse | **OK** — `Anomaly.observations→[obs]` 양방향 |
| involves(→Aircraft) traverse | **OK** — `Anomaly.aircraft→[ac]` |
| correlatedWith 실 ref → 엣지 | **OK** — `Anomaly.correlatedWithAnomalies→[ref]` 형성 |
| correlatedWith placeholder("none") → 엣지 | **빈([]) = 정상**(present-only, EXECUTE 통과) |
| optional(newsEvents·orbitPasses·aircraft) 생략 | **가능** ✓ |
| **correlatedWith 생략** | **INVALID (required)** — E-2.3 "Optional" 의도와 다름, placeholder 필수 |
| confirm-anomaly 전이 | **OK** — candidate→confirmed |
| **store 경로 §12 흡수 경고 발동?** | **아니오 (클린 실행)** — 흡수 방어 코드 미발동 확인 |
| Foundry 오염 | **0** — 전 생성분 delete, delta 0, KADIZ 유지 |

### 15-5. store_foundry.py 동기화 결과

| 함수 | 변경 |
|---|---|
| write_aircraft/observation/operator/satellite/orbitpass/track/weatherstate/newsevent/assessment | PK 파라미터 `newParameter` → 실 PK명(icao24·obsId·operatorId·noradId·passId·trackId·weatherId·newsId·assessmentId) |
| write_anomaly | `newParameter`→`anomalyId` + **`correlatedWith`=placeholder(required 충족)** + **E-3 `createdAt`·`explainerBackend` 배선**(값 있을 때) |
| write_newsevent | mention 파라미터(aircraft/operators/regions) **Optional화** — 실 ref 없으면 생략(placeholder 제거) |
| write_orbitpass | over 코멘트 갱신(regionId=FK→over, E-2.1 형성) |
| `_set_observation_track`(edit-observation) | **무변경** — edit-observation은 리네임 제외라 `newParameter` 유지(경고 코멘트 추가) |
| 에러 흡수(`_create_anomaly_absorbing`) | **유지**(제거 안 함) — 방어용, 정상 경로 미발동. 도크스트링에 클린 실행 반영 |
| 모듈 도크스트링·잔여 이슈 목록 | §15 실측으로 갱신(E-4 완료·E-3 미배선 4종·set-alert 발행 해소) |

**테스트**: FakeFoundry/mock 파라미터 매핑 단위 전부 실 PK명으로 동기, `test_write_anomaly_e3_attrs_wired`
신규 추가. **전체 pytest: 179 passed, 2 skipped**(라이브 skip). 회귀 0(§13의 178 → +1 E-3 테스트).

### 15-6. 남은 갭

**A. 사용자 UI (Ontology Manager — 코드로 불가, 데모 필수 아님):**
1. **correlatedWith Optional화**(E-2.3 의도) — 현재 required라 상관관계 없는 anomaly도 placeholder 강제.
   Optional이면 코드가 placeholder를 안 보내도 됨(마찰 감소). 단 현행 placeholder로도 클린 동작.
2. **신규 속성 채움 파라미터 부재** — create-weather-state.station·create-orbit-pass.groundTrackJson·
   create-situation-assessment.sentencesJson·create-observation.attrsJson **파라미터 추가**해야 코드의
   dual/폴백(PK 복원·로컬 권위본) 제거 가능. 속성은 있으나 채울 수단이 없음.
3. **within 채움 배선** — create/edit-observation.regionId 파라미터·Observation.region 링크 준비됐으나
   Observation 모델에 region_ref 없음 → 지오펜스 진입 판정 로직 필요(코드측, 스키마 아님).
4. create-region PK가 `Id`(대문자) — write_region이 로컬이라 무영향이나 컨벤션 불일치.

**B. 코드측 (선택):**
1. ~~within 배선 시 Observation 모델에 region_ref 추가 + 지오펜스 판정(A-3 선행).~~ **§16에서 완료.**
2. §12 에러 흡수(`_create_anomaly_absorbing`)는 클린 실행 확인 후에도 방어용 유지 — 회귀 재발 시 경고로 감지.

**핵심 3줄 요약**: ⓐ **★create-anomaly 클린 실행 해소** — 사용자가 가짜 에러 원인(링크가 `anomalies`
입력 파라미터에 연결)을 고쳐 EXECUTE가 err 없이 성공, evidenced_by/involves/correlatedWith 엣지 형성,
evidence 없으면 거부. §12 흡수 코드는 방어용 유지(라이브 미발동 확인). ⓑ **E-4 전면 리네임 + 코드
동기화 완료** — 11 create 액션 실 PK명, edit-observation만 newParameter 잔존(유지). E-3 createdAt·
explainerBackend 배선, correlatedWith placeholder·mentions Optional화 반영. ⓒ OSDK 0.7.0에 set-region-
alert-level 포함(발행 갭 해소), pytest 179 passed, Foundry 순증 0. 잔여: correlatedWith required·신규
속성 4종 채움 파라미터 부재·within 지오펜스 배선(전부 데모 필수 아님).

---

## 16. within(Observation→Region) 지오펜스 배선 완료 (§15-6 B-1)

- **날짜**: 2026-07-04
- **판정**: WITHIN-OK

### 실측 사전 확인

| 항목 | 결과 |
|---|---|
| Foundry KADIZ Region PK (라이브 read) | `"KADIZ"` |
| create-observation.regionId required | `False` (optional) |

### 구현 (모델 변경 없음)

`ontology/store_foundry.py` — `write_observation`에 write 시점 지오펜스 판정 추가:
- `KADIZ_BBOX` import + `_KADIZ_BBOX` tuple 파생(SSOT=model.py, 중복 정의 금지)
- `point_in_bbox(obs.lat, obs.lon, _KADIZ_BBOX)` → True면 `params["regionId"] = "KADIZ"` 포함, False면 생략.
- `Observation` dataclass 미변경 — 판정은 write 시점 계산.

### 단위 테스트

`tests/test_foundry_store.py`에 2개 추가:
- `test_write_observation_within_kadiz_includes_region_id` — (36.0, 124.0) → regionId='KADIZ' 포함
- `test_write_observation_outside_kadiz_omits_region_id` — (0.0, 0.0) → regionId 생략

전체 pytest: **181 passed, 2 skipped** (구 179 → +2, 회귀 0).

### 라이브 검증

`scripts/p7_within_validate.py` 실행:

| 검증 | 결과 |
|---|---|
| create-observation(regionId='KADIZ') EXECUTE | OK |
| read-back regionId 속성 | `'KADIZ'` ✓ |
| Observation.region traverse | `['KADIZ']` ✓ |
| 정리 후 순증 | 0 ✓ |

---

## 17. 0.8.0 최종 재검증 (신규 파라미터 배선, 2026-07-04, opus 서브에이전트)

사용자가 UI 마무리 배치(correlatedWith Optional화 + 신규 속성 채움 파라미터 4종 + create-observation.trackId
+ region PK 리네임)를 마치고 **OSDK 0.8.0을 재발행**했다고 하여 전수 실측 재검증 + **새로 열린 파라미터를
코드에 배선**. 저수준 `foundry_sdk` 1.97.0 introspection + OSDK 0.8.0 강제 재설치(`--force-reinstall
--no-deps --no-cache-dir`, pip 로그 토큰 스크럽) + **실 store 코드 경로** 라이브 왕복. 스키마 변경 없음.
**Foundry 오염 0**(모든 생성분 delete, before==after: Aircraft 15·Observation 17·Region 1[KADIZ] 유지).

**검증 산출물**: `scripts/p7_full_introspect.py`(재사용, 11객체·전링크·36액션), `scripts/p7_v08_validate.py`
(신규, §17 배선 실 store 왕복 — 전 타입 write·신규속성 read-back·within/over/correlatedWith traverse·
anomaly 클린·confirm·정리).

### 17-0. 종합 판정 — **★Foundry 완성도 라운드 종결** (①~⑤ 전부 반영 + 4종 신규 배선 완료)

사용자 UI 배치가 **①~⑤를 전부 반영**했고, 그 중 코드가 채울 수 있는 것(①②③)을 store에 배선했다. §15에서
"채울 파라미터 부재"로 남았던 **속성 손실 갭 4종(sentencesJson·station·groundTrackJson·attrsJson)이
종결**됐고, correlatedWith placeholder 강제도 사라졌다. 라이브 왕복 14개 검증 전부 OK, 순증 0.

### 17-1. OSDK 0.8.0 포함범위 (강제 재설치·토큰 스크럽)

- **Object 클래스 11개**(변동 없음): aircraft·observation·region·anomaly·operator·track·satellite·
  orbit_pass·weather_state·news_event·situation_assessment.
- **Action 36개**: create×11·edit×11·delete×11·confirm-anomaly·dismiss-anomaly·**set-region-alert-level**.
  ✅ set-region-alert-level **포함 유지**(0.7.0에서 해소된 발행 갭이 0.8.0에도 유지). delete-orbit-pass 포함,
  editrack 중복 없음(edit-track만). **live=OSDK=36 완전 정합**(발행 갭 없음).

### 17-2. ①~⑤ 반영표 (create/edit 액션 파라미터 실측)

| # | 사용자 배치 예상 | 실측 | 반영 | 근거 |
|---|---|---|---|---|
| ① | correlatedWith Optional화 | create-anomaly.`correlatedWith` **required=False** | **반영** | §15는 required=True(placeholder 강제). 이제 생략 가능 |
| ② | 속성 채움 파라미터(sentencesJson 최우선) | create-situation-assessment.`sentencesJson`·create-weather-state.`station`·create-orbit-pass.`groundTrackJson`·create-observation.`attrsJson` **4종 전부 신설(opt)** | **반영 (4/4)** | §15는 4종 모두 파라미터 부재 |
| ③ | create-observation.trackId 바인딩 or 삭제 | create-observation에 `trackId`(opt) **추가**(바인딩). edit-observation에도 trackId 유지 | **반영 (바인딩)** | 코드는 edit 경로 유지(custody 확정 후 귀속, 주석) |
| ④ | create-anomaly evidence(string) 삭제 | create-anomaly 파라미터에 `evidence` **없음** | **반영/이미 반영** | evidence string 스칼라 부재(§12에서 제거된 상태 유지). 코드도 미사용 |
| ⑤ | region PK 리네임 | create-region PK = **`id`**(소문자) | **반영** | §15는 대문자 `Id`. write_region은 로컬이라 코드 무영향(검증 스크립트만 `id`로 갱신) |

**예상 밖 변화(전수 확인):**
- **edit-aircraft.isMilitary: string→boolean 정정됨**(§9/§15 잔여 타입 불일치 해소). create-aircraft.isMilitary도 boolean. 코드 영향 없음(edit-aircraft 호출부 없음).
- **edit-observation.newParameter(required=True) 잔존**(유일한 리네임 예외 — §15와 동일). trackId·regionId(둘 다 opt)도 존재. `_set_observation_track`은 계속 newParameter를 보내야 함(무변경).
- create-anomaly.observations(required=True) 유지(evidence 강제 불변). create-situation-assessment의 anomalies·newsEvents·observations·orbitPasses는 여전히 required object(present-only placeholder 유지). PK 파라미터명 10종 전부 §15와 동일(파손 없음).

### 17-3. 코드 배선 (반영된 것만 — `ontology/store_foundry.py`)

| 함수 | 배선 |
|---|---|
| `write_anomaly` | **correlatedWith placeholder(`_ABSENT_REF`) 제거**(① Optional → 파라미터 생략). 실 상관관계는 write 시점 미상이고 다건 correlated_with는 로컬 권위본이므로, 이제 아무것도 안 보냄(엣지는 로컬). |
| `write_assessment` | **sentencesJson 배선**(② 최우선): `_sentences_json()`으로 문장별 `{text,cites,confidence,kind}` 직렬화 → Foundry 스파인에 문장 cites 보존. read 권위본은 로컬 유지(dual의 Foundry측 완성). |
| `write_weatherstate` | **station 배선**(②): `params["station"]=weather.station`. read는 station 속성 우선·PK 복원(`_station_from_weather_id`) 폴백 유지. |
| `write_orbitpass` | **groundTrackJson 배선**(②): 비어있지 않으면 `json.dumps(ground_track)`. read는 groundTrackJson 우선·`[]` 폴백. |
| `write_observation` | **attrsJson 배선**(②): attrs 있으면 `json.dumps(attrs)`. read는 attrsJson 우선·`{}` 폴백. **create-observation.trackId(③)는 미사용**(주석) — composed_of는 custody 확정 후 edit-observation 경로 유지. |
| read(`_dict_to_obs`·`_dict_to_orbitpass`·`_dict_to_weather`) | 신규 속성 우선 + 구 객체(속성 부재) 폴백 — 하위호환(§8 baseline 객체는 폴백으로 무회귀). |
| 모듈 docstring·잔여 이슈 §37-49 | §17 실측으로 갱신(속성 손실 갭 종결·correlatedWith Optional·within §16 완료·set-alert/isMilitary 정합). |

- **placeholder 제거 여부**: correlatedWith placeholder **제거 완료**(write_anomaly). assessment의 anomalies/newsEvents/observations/orbitPasses는 아직 required object라 present-only placeholder 유지(스키마 미변경분).
- **sentencesJson Foundry 보존**: **OK** — 라이브 read-back에서 문장 cites `[[obs],[orbitpass]]` 그대로 보존 확인. 로컬은 여전히 read 권위본.

**read 경로 판단 기록**: 신규 속성 3종(station·ground_track·attrs)의 read를 "실 속성 우선 + 기존 폴백"으로 개선(write-only 대신). 안전 근거 = 구 객체는 `d.get(...)`가 None → 폴백(PK 복원/`[]`/`{}`)으로 무회귀, 신규 객체만 실값. 이로써 write 소재의 Foundry-primary read가 station 복원 꼼수 없이도 정확.

### 17-4. 라이브 왕복 결과 (`p7_v08_validate.py`, 실 store 경로, 전건 정리)

| 검증 | 결과 |
|---|---|
| OrbitPass.groundTrackJson read-back(②) | **OK** — 점열 3개 원본 일치 |
| over: OrbitPass.region traverse | **OK** — `['KADIZ']`(§10 잔존이었던 over가 E-2.1로 형성·채움) |
| WeatherState.station 직접 write(②) | **OK** — read-back `'RKSI'`(PK 복원 아님), store.read도 'RKSI' |
| Observation.attrsJson read-back(②) | **OK** — `origin_country='Republic of Korea'` 보존 |
| within: Observation.region traverse | **OK** — `['KADIZ']`(§16 지오펜스 배선) |
| observed_as / composed_of traverse | **OK** — Observation.aircraft·track 양방향 |
| SituationAssessment.sentencesJson(②) | **OK** — Foundry 문장 cites `[[obs],[orbitpass]]` 보존 + 로컬 권위본 유지 |
| **Anomaly 클린 생성(①)** | **OK** — §12 흡수 경고 **미발동**, Foundry 실패 없음, correlatedWith placeholder 없이 생성 |
| evidenced_by / involves traverse | **OK** — Anomaly.observations→[obs], Anomaly.aircraft→[ac] |
| correlatedWith 생략 → 엣지 없음(①) | **OK** — `[]`(생략=엣지 미형성, 에러 없음) |
| correlatedWith 실 ref → 엣지(원시 프로브) | **OK** — 실 ref 주면 correlatedWithAnomalies 형성(Optional 양방향 확인) |
| confirm-anomaly 전이 | **OK** — Foundry·로컬 둘 다 candidate→confirmed |
| Foundry 오염 | **0** — 전 생성분 delete, delta 전부 0, KADIZ 유지 |

### 17-5. 테스트

- `tests/test_foundry_store.py`: `test_write_anomaly_params_mapping`을 correlatedWith **생략 단언**으로 교정. 신규 3건 추가(`test_write_observation_attrs_json`·`test_write_orbitpass_ground_track_json`·`test_write_assessment_sentences_json`) + `test_write_weatherstate_params`에 station 단언. `json` import 추가.
- **전체 pytest: 184 passed, 2 skipped**(라이브 skip). 회귀 0(§16의 181 → +3 신규).

### 17-6. Foundry 완성도 라운드 **종결 판정 — [종결]**

- **Object 11종**: 스펙 완성(§9 이래 불변).
- **Action층 데모 백본**: create-anomaly 클린 실행(§15)·evidence 강제·evidenced_by/involves/correlatedWith 안정 형성·confirm/dismiss 전이·set-region-alert-level Modify — 전부 정상.
- **속성 손실 갭 종결**: station·groundTrackJson·sentencesJson·attrsJson **4종 배선 완료**(§11~§15에서 "PK 복원/로컬 폴백/미저장"으로 남았던 것). Anomaly createdAt·explainerBackend는 §15에서 이미 배선.
- **링크 그래프**: observed_as·operated_by·of·over·within·composed_of·Track→AC·Weather/Assessment→Region **전부 형성·traverse 가능**(§10 잔존이던 over·within 종결).
- **발행 정합**: live=OSDK=36 액션(발행 갭 없음), edit-aircraft.isMilitary boolean 정정.

**잔여(데모 필수 아님·의도된 설계):**
1. **다중 근거·involves·correlated_with는 로컬 권위본** — create-anomaly의 링크 파라미터가 단수(observations·aircraft·correlatedWith 각 1)라 Foundry 그래프엔 첫 근거/첫 involves만. Foundry MANY-MANY 링크를 다건 채우는 액션이 없는 한 구조적 한계(스키마 UI 작업이지 코드 아님).
2. **SituationAssessment·NewsEvent mentions dual-write** — Foundry에 스칼라+sentencesJson·mention 파라미터를 밀지만 read 권위본은 로컬(문장 객체·aggregates/cites·mentions 링크). Foundry MANY-MANY가 정상화되면 단일화 가능.
3. **edit-observation.newParameter(required)** — 유일한 리네임 예외, `_set_observation_track`이 계속 보냄(무해).
4. create-region PK가 `id`지만 write_region은 로컬 권위본(무영향).

**핵심 3줄 요약**: ⓐ **①~⑤ 전부 반영** — correlatedWith Optional화·신규 속성 파라미터 4종·trackId 바인딩·evidence(string) 부재·region PK `id`. 예상 밖 긍정 변화로 edit-aircraft.isMilitary가 boolean 정정, live=OSDK=36 완전 정합. ⓑ **코드 배선 완료** — correlatedWith placeholder 제거 + sentencesJson·station·groundTrackJson·attrsJson write 배선(+read 폴백 유지) → §15의 속성 손실 갭 4종 종결, sentencesJson으로 문장 cites가 Foundry에도 보존(dual의 Foundry측 완성). ⓒ 라이브 왕복 14검증 전부 OK(anomaly 클린·within/over/correlatedWith traverse·confirm·순증0), pytest 184 passed. **→ Foundry 완성도 라운드 종결**(잔여는 전부 데모 비필수·의도된 dual/단수 파라미터 한계).
