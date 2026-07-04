# 라이브 다중소스 폴링 (DR-0012 갭#3 종결)

- 날짜: 2026-07-04
- 근거: DR-0012 #3 "반쪽 실시간 / 원소스 연결 안 됨" — 연속 폴러가 OpenSky만 돌던 것을
  뉴스·기상·위성까지 각자 주기로 연속 폴링하게 확장. 실 GDELT 기사 URL로 "원소스 연결"을
  사실로 전환.
- 범위: `connectors/opensky.py`(폴러), `server/app.py`(`_live_view` 필드 노출),
  `scripts/demo.sh`(순수 라이브/데모 분리), `tests/test_copilot_intent.py`(스케줄링 테스트).
  **web/ 미수정**(프론트 후속 — 필드만 backend에 노출).

## 1. 소스별 due 스케줄링

폴러 루프는 이제 소스별 마지막 폴 시각(`last_poll` dict)을 추적하고, 각 소스의 주기가
도래한 것만 fetch한다. 항적(OpenSky)은 base 사이클마다, 보조 소스는 각자 주기로.

| 소스 | 온톨로지 산출 | 주기(SSOT) | 상수 |
|---|---|---|---|
| opensky | Aircraft·Observation·Track·Anomaly | 매 사이클(base 25s, 하한 10s) | `DEFAULT_POLL_INTERVAL` |
| gdelt | NewsEvent(저신뢰, 실 기사 URL)·mentions | 5분 | `gdelt.GDELT_POLL_INTERVAL` |
| metar | WeatherState(실황) | 30분 | `metar.METAR_POLL_INTERVAL` |
| celestrak | Satellite·OrbitPass(+미래 pass stale 정리) | 12h | `celestrak.TLE_POLL_INTERVAL` |
| stealthmole | NewsEvent(선택) | 30분 | `STEALTHMOLE_POLL_INTERVAL` |

- **due 판정은 순수 함수** `due_sources(last_poll, now, intervals, sources)` — 마지막 폴 이후
  `intervals[src]`가 경과한 보조 소스만 반환(OpenSky는 base 경로라 제외). 미폴(0)이면 즉시 due
  → 라이브 기동 첫 사이클에 각 소스가 1회씩 fetch된다.
- 각 커넥터의 **기존 간격 규율 그대로 유지**: GDELT 5초 강제(`_rate_limit_guard`)·429 시 우회 없이
  스킵, Celestrak 12h 파일 캐시, METAR 단일 스테이션. 폴러는 상위 주기만 얹고 커넥터 규율을
  건드리지 않는다. OrbitPass 미래 pass stale 정리(`delete_future_orbitpasses_for`)는 커넥터
  `celestrak.ingest` 내부 로직을 그대로 재사용한다.
- **소스 dispatch**: `_ingest_source(src, store)`가 기존 커넥터 `ingest()`를 호출(재구현 없음).
  StealthMole은 선택 소스라 지연 import(PyJWT·dotenv 의존을 기본 경로에서 배제) + 키 없으면
  커넥터가 자체적으로 no-op. 개인정보 모듈은 커넥터 가드 그대로(신규 코드 없음).

### 폴링 소스 설정 / 되돌리기

- 소스는 `SKAI_POLL_SOURCES`(쉼표구분)로 지정. **커맨드 레이어 기본 = `opensky,gdelt,metar,celestrak`**
  (다중소스 — 갭#3 종결). OpenSky-only 회귀는 `SKAI_POLL_SOURCES=opensky`.
- **함수 레이어(`run_poller(sources=None)`) 기본은 OpenSky-only**로 유지. 이유: 기존 유닛 테스트·
  프로그래밍 호출·오프라인/replay 경로의 하위호환 불변(다중소스는 `main()`이 명시적으로 켠다).
  DR-0012 되돌리기 노트의 "미설정 시 기존 동작 불변"은 이 함수 API 레벨에서 지켜진다. 커맨드
  레벨 기본을 다중소스로 둔 것은 갭#3의 종결 요구(팀리드 지시)에 따른 의도적 결정이며,
  `SKAI_POLL_SOURCES=opensky`로 즉시 되돌릴 수 있다.

## 2. 순수 라이브 vs 데모(합성) 분리

`scripts/demo.sh live`가 항상 `narrative_hidden` 합성을 주입하던 것을 **플래그로 분리**:

- `scripts/demo.sh live` → **순수 라이브**. 실데이터만(항적+뉴스+기상+위성). 합성 주입 없음.
- `scripts/demo.sh live --inject` → 라이브 + 내러티브 합성 1건(데모 서사 보장, 발표 임팩트용).

라이브 폴러는 `SKAI_POLL_SOURCES`(기본 4소스)로 기동. 도움말(헤더 주석)·기동 로그 갱신.
replay() 경로는 **바이트 무변경**(합성 주입 분리는 live()에만 적용) → replay 결정성 회귀 없음.

## 3. 소스별 신선도 필드 (프론트 인계)

폴러가 사이드카(`<db>.live.json`)에 소스별 신선도를 기록하고, `/api/live`가 노출한다.

- `write_status`에 추가된 키:
  - `sources`: 활성 폴링 소스 리스트.
  - `source_last_poll`: `{src: last_poll_ts}` — 소스별 마지막 폴 시각(미폴은 0).
  - `source_last_status`: `{src: "ok"|"error"|"pending"}` — 소스별 마지막 폴 결과.
- `/api/live`(`_live_view`)가 위 3필드를 그대로 통과 노출. 구 단일소스 사이드카면 `None`
  (하위호환) → **프론트는 없으면 전체 `last_poll_ts`로 폴백**하면 됨.
- 기존 `last_poll_ts`(전체)·`live` 배지·`/api/stats` 하위호환 키는 불변.

### 프론트 후속 작업(web/, 이 태스크 범위 밖)
- 소스별 신선도 뱃지: `source_last_poll[src]` + `server_now`로 경과시간 표시(예 "뉴스 3m 전",
  "기상 12m 전"). `source_last_status[src]=="error"`면 경고 표시.
- `source_last_poll`이 `null`이면(구 사이드카/replay) 전체 `last_poll_ts`로 폴백.

### 알려진 뉘앙스
- `source_last_status`는 **ingest 호출이 예외 없이 완료됨**을 뜻한다(행 저장 성공이 아님).
  GDELT가 429로 스킵하거나 기사가 0건이어도 `gdelt.ingest`는 `(0,0)`을 정상 반환하므로
  status="ok"·`source_last_poll` 갱신된다(폴은 시도됨). 429/무기사 여부는 폴러 로그에 남는다.
  429를 status로 구분하려면 커넥터 반환 시그니처를 바꿔야 해 범위 밖으로 두었다.

## 4. 러너웨이·격리 (불변 유지)

- `max_cycles`(0=무한 라이브, 유한=검증) + SIGTERM/SIGINT 정리 종료 + 인터럽트 가능 대기 — 기존
  규율 그대로. 자동 스케줄 없음(명시 실행만).
- **각 소스 실패는 개별 격리**: OpenSky도 보조 소스도 try/except로 감싸 한 소스가 죽어도
  루프·타 소스는 지속하고 로깅만 한다(`source_last_status[src]="error"`, `last_poll` 미갱신).

## 5. 검증 (2026-07-04 실행)

1. **다중소스 폴러 1사이클(opensky,gdelt,metar)** — 임시 DB. OpenSky 200(항공기 122),
   METAR 200(RKSI VFR, WeatherState 1건 적재). GDELT는 첫 시도 429(커넥터가 우회 없이 스킵),
   재시도(25s 후)에서 200 → **NewsEvent 6건 적재**. `source_last_poll`에 opensky·gdelt·metar
   각각 ts 기록 확인.
2. **실 URL 뉴스 확인**: `/api/news` 6건 전부 실 http 기사 URL(synthetic 0건). 예:
   `aa.com.tr/.../morning-briefing-...`, `manilatimes.net/...`, `news.az/...`. → "실 GDELT URL로
   원소스 연결" 사실 전환 확인.
3. **`/api/live` 소스별 필드**: `source_last_poll`·`source_last_status` 노출 확인.
   `/api/stats` 하위호환(`last_poll_ts`·`newsevent`·`weatherstate`) 불변.
4. **Celestrak dispatch**(캐시 stale라 조건부 — `_ingest_source('celestrak')`, groups=stations):
   Satellite 23·OrbitPass 38 적재, `delete_future` 로직 정상.
5. **demo.sh**: `live`(순수) → "합성 주입 없음" + 폴러 로그에 합성 흔적 0 확인. `live --inject`
   → narrative_hidden 주입 확인. `stop`/`status` 정리 정상. replay()는 바이트 무변경.
6. **테스트**: 전체 스위트 **241 passed, 2 skipped**(기존 237 + 신규 4). 신규 스케줄링 테스트:
   `test_resolve_sources_default_and_normalize`·`test_due_sources_scheduling`·
   `test_poller_multisource_records_per_source_freshness`·`test_poller_source_failure_isolated`.
7. 검증에 쓴 임시 DB·런타임 DB(data/skai.db)는 실행 후 원상 복구(백업 대조 바이트 동일).

> data/skai.db는 gitignore된 런타임 산출물이며, 이번 작업 이전부터 옛 합성+실데이터가 혼재해
> 있었다(DR-0012 병행 사항 "누적 skai.db 정리"의 별도 백로그). 이번 검증은 비파괴로 진행하고
> 스냅샷을 복구했다.
