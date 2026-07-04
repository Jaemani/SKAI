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
