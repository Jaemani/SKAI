# mil-enrich.md — 라이브 군용 식별 보강 (실행 로그)

- 날짜: 2026-07-05
- 담당: opus 실행 에이전트 (팀리드 지시 — DR-0013 결정 5 배선)
- 근거: **DR-0013 결정 5**(라이브 군용 신호 보강: adsb.fi DB 플래그 실측 후 배선, 확정 실행) · CLAUDE.md 정체성(OSINT 위험징후 탐지) · `anomaly/crosscheck.py` ↔ `connectors/crosscheck_live.py` 자매 패턴
- 판정: **LIVE-WIRED** (adsb.fi `/v2/mil` dbFlags, 게이트 off 기본 — 데모 live만 기본 on)

---

## 0. 결론 (발표용 정직 문구)

- **실측이 성공했다.** adsb.fi `/v2/mil` 응답에 **군용 식별 필드가 실제로 존재**(`dbFlags`)하고, 그 의미는 문서로 확정된다(readsb `README-json.md`: `military = dbFlags & 1`). 추측이 아니라 실호출 + 공식 문서 근거다.
- 이 필드로 **라이브 항적의 군용 여부를 저신뢰 보강**한다. 기존 콜사인·대역 휴리스틱(`military_db`)에서 "휴리스틱"→"**공개 커뮤니티 DB 플래그**"로 근거를 한 단계 상향했다.
- **기본값은 여전히 off(Null·신호 없음)** — `SKAI_MIL_ENRICH=live` 게이트를 켤 때만 호출한다. 데모 `live` 모드에서만 기본 on(`SKAI_COPILOT_LLM` 패턴과 동일). 즉 "라이브 군용 식별 **능력**을 갖췄고, 코드 기본은 보수적"이 정직한 표현.
- **한계(정직, 핵심)**: 이건 **트랜스폰더를 켠 채 진입한 군용기만** 잡는다. 실제 군용기는 대개 ADS-B를 끄므로 이 경로엔 안 잡히고, 그건 **dropout(부재) 탐지 경로**가 담당한다(이중 경로 서사, DR-0013 결정 4). 또 커뮤니티 DB라 **오탐·미탐이 존재**한다(아래 §1 실측 오탐 사례).

---

## 1. 실측 (2026-07-05, 출처·응답 샘플 인용)

### 1.1 엔드포인트 존재·응답 구조 (실호출)
`curl https://opendata.adsb.fi/api/v2/mil` → **HTTP 200, 38.9KB**.
- top-level 키: `ac`(94기 배열), `msg`("No error"), `now`, `total`, `ctime`, `ptime`.
- entry 키(실측): `hex, flight, dbFlags, t, desc, r, lat, lon, alt_baro, gs, track, squawk, seen, category, ...`
- **`dbFlags` 필드 전 entry에 존재, 전부 `dbFlags == 1`** (분포: `{1: 94}`).
- 샘플:
  ```
  ae0679 | RCH348  | dbFlags 1 | C17  | Boeing C-17A Globemaster III
  ae10e9 | C2003   | dbFlags 1 | C30J | Lockheed Martin HC-130J Hercules
  a3c666 | N342TA  | dbFlags 1 | P28A | PIPER PA-28-140/150/160/180   ← 민간 등록기(오탐 사례)
  ```

### 1.2 `dbFlags` 비트 의미 (문서 근거 — 추측 금지)
readsb `README-json.md`(github.com/wiedehopf/readsb): dbFlags는 비트필드.
> `military = dbFlags & 1;`
- bit0(1)=**military** · bit1(2)=interesting · bit2(4)=PIA · bit3(8)=LADD.
- → 우리는 **bit0(dbFlags & 1)만** 군용 신호로 채택. `/v2/mil` 엔드포인트가 이미 군용 필터지만, 문서화된 비트로 코드에서 이중 확인(방어).

### 1.3 `/v2/hex`에도 dbFlags 실림 (옵션 B 가능성 확인)
`curl .../v2/hex/ae0679` → HTTP 200, `ac` 1기, `dbFlags 1`. → hex별 질의(옵션 B)로도 가능하지만 아래 §2에서 옵션 A 채택.

### 1.4 ToS·리밋 (adsb.fi opendata 공식 문서 인용)
- 엔드포인트 목록에 `/v2/mil` — "Returns aircraft marked as military" (문서 명시).
- 리밋: "public endpoints are rate limited to **1 request per second**". 400/401/403/404/429도 리밋에 카운트.
- ToS: "personal, **non-commercial** use only. You may not license, sell, rent, or lease..." + "**You must cite adsb.fi and include a link to our home page**."
- 해커톤 데모 = 비상업·교육 → 허용. **인용 요건 → UI 크레딧 필요**(프론트 에이전트 몫, 아래 §5).

---

## 2. 설계 결정 — 옵션 A(스냅샷 폴링) 채택

| 옵션 | 방식 | 호출량 | 판정 |
|---|---|---|---|
| **A (채택)** | `/v2/mil` 스냅샷을 60s TTL 폴링 → 군용 hex **집합** 캐시 → bbox 항적을 O(1) 대조 | **최대 1 req / 60s** (추적 hex 수와 무관) | ✅ |
| B | 후보 hex별 `/v2/hex` 질의(crosscheck 패턴) | 후보 수 × req | ✗ |

- 근거: `/v2/mil`은 전 세계 군용 기체를 **1회 호출로 전량**(≈94기, 38KB) 반환한다. 우리가 bbox에서 추적하는 hex와의 교집합만 쓰면 되므로, hex별 질의가 불필요하다. 호출량이 압도적으로 적고(리밋 여유), 구현도 단순(집합 멤버십).
- 실패 격리: 스냅샷 fetch 실패(429·5xx·네트워크·이상응답)면 **직전 스냅샷 유지**, 최초 실패면 빈 집합 → `lookup`은 None(신호 없음) → 콜사인·대역 휴리스틱이 그대로 판정. 폴링은 성공·실패 무관하게 60s 간격 강제(에러 폭주 방지).

### 구조 — 크로스체크 자매 패턴 (레이어 분리)
- `anomaly/mil_enrich.py` — `MilEnrichmentSource` 프로토콜 + `NullMilEnrichment`(기본). (= `anomaly/crosscheck.py`)
- `connectors/mil_enrich_live.py` — `LiveMilEnrichment` + `make_mil_enrichment` 게이트. (= `connectors/crosscheck_live.py`)
- 이유: `anomaly/rules.py`(anomaly 레이어)가 `connectors`를 import하지 않도록 인터페이스·Null을 anomaly에 둔다. `rules.py`는 `from anomaly.mil_enrich import ...`만.

---

## 3. 판정 통합 (우선순위·신뢰도)

`anomaly/rules.py::detect_military_approach`에 `mil_enrich` 주입(기본 Null). 우선순위(강→약):

| 순위 | 신호 | confidence | mil_source | 근거(mil_reason) |
|---|---|---|---|---|
| 1 | **공개 DB 플래그**(adsb.fi dbFlags&1) | **0.65** | `db_flag` | "adsb.fi 커뮤니티 ADS-B DB 군용 플래그(dbFlags & 1)" |
| 2 | 콜사인·대역 휴리스틱(`military_db`) | 0.5~0.65 | `heuristic` | 군 콜사인 프리픽스 / 예약 대역 |
| 3 | 관측 소스 명시 `is_military`(합성) | 0.55 | `explicit` | "관측 소스 is_military 플래그" |

- DB 플래그(0.65)를 콜사인 휴리스틱(0.55)보다 위·저신뢰 상한(≤0.65)에 뒀다(DR-0013: "DB플래그는 콜사인 휴리스틱보다 높게 가능, 상한 ≤0.65 유지").
- **provenance 문장 전파**: `mil_reason`이 explainer의 `{mil_reason}`으로 그대로 서술되고, `mil_source=db_flag`면 caveat도 "공개 커뮤니티 ADS-B DB(adsb.fi) 플래그 기반 — 커뮤니티 DB라 오탐·미탐 가능"으로 바뀐다. 실제 렌더 확인:
  > 군용 추정 항공기 RCH348(icao24 ae0679)가 작전구역 …에 진입했습니다. 근거: **adsb.fi 커뮤니티 ADS-B DB 군용 플래그(dbFlags & 1)**(저신뢰 공개 DB 플래그, 신뢰도 0.65). 군용 판정은 공개 커뮤니티 ADS-B DB(adsb.fi) 플래그 기반 — 커뮤니티 DB라 오탐·미탐 가능, 교차검증 요망.
- **합성 경로 불변**: `ac.is_military=True`(합성 주입) 분기는 그대로. 합성 hex는 adsb.fi 실피드에 없으므로 DB 경로와 충돌 없음. `military_incursion` 시나리오 회귀 0.

### 배선 경로
`connectors/opensky.py::run_poller` → `mil_enrich_live.make_mil_enrichment()`(사이클 간 1 인스턴스 재사용, 스냅샷·리밋 상태 보존) → `ingest_cycle(..., mil_enrich=)` → `scan_and_create_all(..., mil_enrich=)` → `detect_military_approach(..., mil_enrich)`.

---

## 4. 테스트

- **신규**: `tests/test_mil_enrich.py` — 20 케이스, MockTransport(네트워크 0). 게이트 팩토리 / lookup 의미론(군용·미확인·오류) / **dbFlags 비트 필터(0·2·누락·비정수 제외)** / 스냅샷 60s TTL·오류 시 직전 유지·엔드포인트 URL / detect_military_approach 통합(DB>휴리스틱 우선, 게이트 off 무변화, OpArea 공간게이트 불변).
- **회귀**: 전체 스위트 `.venv/bin/python -m pytest -q` → **366 passed, 4 skipped**(기존 346 passed + 신규 20). 회귀 0.

---

## 5. 한계·후속 (정직)

- **트랜스폰더 OFF 군용기는 여전히 안 보임** — 이 보강은 "ADS-B 켠 채 진입" 케이스만. 실제 은밀 진입은 dropout(부재) 경로가 담당(DR-0013 결정 4 이중 경로).
- **커뮤니티 DB 오탐·미탐** — 실측 스냅샷에 민간 Piper(N342TA)가 dbFlags=1로 섞여 있었다. DB 미수록 군용기는 미탐. → confidence 저신뢰 상한(0.65) 정당.
- **copilot 라이브 툴 경로 미보강(범위 밖)** — `copilot/tools.py::_effective_military`(query_flights 필터·region 요약이 쓰는 병렬 군용 판정)는 콜사인·대역 휴리스틱만 사용. 이번 배선은 **이상탐지 경로**(detect_military_approach→Anomaly)에만 통합. copilot 문장/판정 로직 수정은 이 작업 제약상 금지(다음 에이전트/별도 결정). `copilot/assessment.py`의 군용 서술도 `mil_reason`은 전파받지만 "저신뢰 휴리스틱" 라벨이 하드코딩돼 있어 db_flag여도 그 라벨로 표시됨(경미, copilot 소유).
- **UI 크레딧 필요(프론트 몫)** — adsb.fi 인용 요건. 현재 UI에 crosscheck용 adsb.fi 크레딧이 있으면 "군용 식별 보강"도 같은 소스라 커버되지만, `/v2/mil` 사용을 크레딧 문구에 반영 권장. **프론트 에이전트가 처리.**

---

## 6. 변경 파일

- 신규: `anomaly/mil_enrich.py`, `connectors/mil_enrich_live.py`, `tests/test_mil_enrich.py`
- 수정: `anomaly/rules.py`(detect_military_approach + DB플래그 신뢰도 상수), `anomaly/actions.py`(scan_and_create_all 배선), `connectors/opensky.py`(ingest_cycle·run_poller 배선), `anomaly/explainer.py`(military 분기 caveat 출처화), `scripts/demo.sh`(live 게이트 기본 on), `data-sources.md`(사용 확대 기록)
