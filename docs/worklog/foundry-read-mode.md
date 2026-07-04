# Foundry-primary read 모드 (DR-0012 #2 — EVALUATION OVERSOLD#2 종결)

- 날짜: 2026-07-04
- 근거: DR-0012 #2("Foundry 위에서 돈다"는데 실행 서버 read는 로컬 SQLite) · EVALUATION.md §5·OVERSOLD#2
- 제약: **로컬 기본·오프라인 replay 결정성 불변(DR-0008 최우선)** · Foundry read-only · 스키마 변경 0 · web/ 무수정

## 한 줄 요약

`SKAI_STORE=foundry`일 때 화면(server API)·코파일럿(/api/assess)이 **실제로 Palantir Foundry에서
read**하도록 배선했다. **문제는 코드가 아니라 배선 한 곳**이었다: `server/app.py._store()`가
`make_store()`를 우회하고 `LocalOntologyStore`를 하드코딩해, `SKAI_STORE=foundry`여도 화면은
항상 로컬 SQLite를 읽었다. HybridStore의 read 라우팅(Aircraft·Observation 등 8종→Foundry)은
**이미 구현돼 있었고**, 서버가 그걸 안 쓰고 있었을 뿐이다.

## 무엇을 바꿨나

### 1. `server/app.py._store()` → `make_store()` 경유 (핵심)
- 기존: `return LocalOntologyStore(DB_PATH)` (SKAI_STORE 무시 — 항상 로컬 read).
- 변경: `current_backend()=='foundry'`면 프로세스 1회 구성한 **HybridStore를 재사용**, 아니면
  기존대로 `LocalOntologyStore(DB_PATH)`를 매 요청 새로.
- `assess(store, ...)`가 서버가 넘긴 store를 그대로 쓰므로, `_store()` 한 곳을 고치면
  **화면 API와 코파일럿이 동시에** Foundry read로 전환된다.
- **foundry 모드 캐시**(`_foundry_store`): 요청마다 FoundryClient(인증 핸드셰이크)를 새로 만들지
  않도록 1회만 구성. **기본·replay 경로에선 절대 생성 안 됨** → foundry_sdk를 import조차 안 함.

### 2. `ontology/store_foundry.current_backend()` 신설 (SSOT)
- `make_store`와 **동일 게이트**(SKAI_STORE=foundry만 foundry)를 값으로만 판정. 크리덴셜·SDK 불요.
- 서버가 read 소스를 노출할 때 참조하는 단일 진실.

### 3. `store_backend` 필드 노출 (프론트 인계)
- `/api/stats` → `{... , "store_backend": "local"|"foundry"}`
- `/api/live` → `{... , "store_backend": "local"|"foundry"}` (폴러 유무 두 분기 모두)
- 프론트는 후속에서 이 필드로 "지금 로컬 SQLite / Palantir Foundry에서 read 중" 배지를 그릴 수 있다.
  **본 작업은 필드만 제공**(web/ 무수정).

## Foundry read로 전환된 객체 (HybridStore가 Foundry에서 read — 저수준 SDK)

Aircraft · Observation · Operator · Satellite · OrbitPass · Track · WeatherState · NewsEvent (8종).
→ `/api/observations`·`/api/tracks`·`/api/orbitpasses`·`/api/weather`·`/api/news`·`/api/counts`·
`/api/stats`의 해당 카운트/객체가 foundry 모드에서 Foundry발.

## 로컬 보강(hybrid) 잔여 — 화면이 깨지지 않게 로컬이 채우는 것

설계상(P7 §11·§17-6) Foundry에 온전히 없거나 단수 파라미터 한계인 provenance는 **로컬 권위본**:
- **Region**(KADIZ 폴리곤/지오펜스) — `query_regions` 로컬. assess의 지역 필터가 이걸 쓴다.
- **Anomaly** 목록·status + **evidenced_by 다건·involves 다건·correlated_with** — 로컬.
- **문장 cites**(SituationAssessment sentences·aggregates/cites) — 로컬(Foundry엔 sentencesJson
  dual-write만, read 권위본은 로컬).
- **NewsEvent mentions** 링크 — 로컬(Foundry MANY-MANY 불안정, §9-4).

판별 근거(어느 필드가 Foundry발/로컬발인지): `HybridStore`의 read 섹션은 명시 메서드(→Foundry)와
`__getattr__` 위임(→로컬)으로 갈린다 = 라우팅 자체가 SSOT. 요약하면 **정보 소재(관측·항적·기상·
뉴스·위성) = Foundry발, provenance 그래프(근거·상관·문장 cites·지역) = 로컬 보강**.

## 검증 결과

| 검증 | 방법 | 결과 |
|---|---|---|
| 단위 — 팩토리·백엔드 판정 | `make_store` foundry→HybridStore(SDK mock)·`current_backend` 3케이스 | **OK** |
| 단위 — read 소스 분리 | `test_hybrid_read_separates_foundry_and_local_sources`(fake foundry) — 관측·항공기=Foundry발, Region·Anomaly·evidence=로컬보강 | **OK** |
| 단위 — 서버 계약 | `/api/stats`·`/api/live`에 `store_backend=="local"`(기본) | **OK** |
| 전체 회귀 | `pytest` | **237 passed, 2 skipped**(기존 232 → +5 신규, 회귀 0) |
| 라이브 — 실 Foundry read | `.venv312` + `SKAI_STORE=foundry` + .env, `scripts/verify_foundry_read.py` | **OK** |
| replay 결정성 | `SKAI_STORE` 미설정 `demo.sh replay` **2회 바이트 동일**(SHA-256 일치) + `store_backend=="local"` | **OK(불변)** |

라이브 실측(`verify_foundry_read.py`): 로컬 db 빈 상태(aircraft 0·obs 0)인데 `store.counts()`가
**aircraft 15·observation 17**(Foundry발 확정), `/api/observations` equiv **12건**(실 opensky
source_url), `/api/assess` equiv → 문장 cites **6개 전부 Foundry Observation id 인용**
(예: `682231-1783151594`). **Foundry write 0**(read-only + 로컬 Region seed만) → Foundry 순증 0.

## 프론트 인계 (표시용 — 후속 web/ 작업)

- `GET /api/stats` → `store_backend`: `"local"` | `"foundry"`.
- `GET /api/live` → `store_backend`: 동일. (프론트가 LIVE 배지 옆에 read 소스 배지를 함께 표시 가능.)
- 권장 표기: `"foundry"`면 "Palantir Foundry read", `"local"`이면 "로컬 SQLite read". replay·기본
  데모는 항상 `"local"`(결정성). **주의: 이 필드는 read 소스이지 "AIP가 추론한다"는 뜻이 아니다**
  (추론은 여전히 로컬 엔진 — EVALUATION 정직성 프레이밍 유지).

## 되돌리기

환경변수 게이트라 코드 revert 없이 무력화 가능: `SKAI_STORE` 미설정이면 전부 기존 로컬 동작.
코드 revert 시 `server/app.py._store()`를 `return LocalOntologyStore(DB_PATH)`로, store_backend
필드 3곳·`current_backend` 제거.
