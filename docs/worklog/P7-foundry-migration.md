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
