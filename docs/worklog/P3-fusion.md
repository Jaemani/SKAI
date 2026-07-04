# P3-fusion.md — 융합 확장 실행 로그 (위성·기상·뉴스 → 한 온톨로지)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P3)
- 근거: PROMPTS.md P3 · DR-0005(융합 스코프) · ontology.md §1~§2 · data-sources.md 실응답 · P0A-sources.md gotcha · P2-anomaly.md §6
- 상태: **완료** (테스트 58/58 통과 · 4종 소스 온톨로지 공존 · 통합 화면 렌더 확인)

---

## 1. 무엇을 만들었나

P1(OpenSky)에 더해 **나머지 3소스를 온톨로지 객체로 통합** → 4종 소스가 한 store에 시공간 정렬.
저장은 기존 `OntologyStore` 어댑터 뒤(provenance 강제 통과). 온톨로지 스키마는 ontology.md §1~§2 정의 그대로.

```
connectors/celestrak.py   TLE(12h 캐시) → sgp4 → KADIZ 상공 통과창(OrbitPass) + 지상궤적. GMST 보정.
connectors/metar.py       aviationweather.gov → WeatherState (단위 명시: sm/ft/kt).
connectors/gdelt.py       괄호 OR 쿼리 → NewsEvent(저신뢰) + mentions→Region 키워드 링킹. 5초 규율 강제.
ontology/model.py         + Satellite·OrbitPass·WeatherState·NewsEvent·Operator dataclass, NEWS_MAX_CONFIDENCE
ontology/store.py         validate_provenance 덕타이핑화(뉴스·기상도 증거 객체) + Protocol 확장
ontology/store_local.py   + 5개 테이블(CREATE TABLE IF NOT EXISTS·비파괴)·write/query·counts 확장
ontology/store_foundry.py + 동일 메서드 스텁(Protocol 정합)
server/app.py             + GET /api/orbitpasses · /api/weather · /api/news · /api/counts(소스별)
web/index.html            + 위성 지상궤적 레이어(토글)·기상 카드·뉴스 패널·소스별 카운트 (P1/P2와 공존)
tests/test_p3.py          26 케이스 (통과창·GMST·METAR·GDELT·confidence 상한·provenance·4종 공존)
```

### 온톨로지 깊이 (억지 아님)
- **OrbitPass —of→ Satellite / —over→ Region**: 위성-지역 시공간 상관(P5 correlated_with "은닉 정황"의 토대).
- **NewsEvent —mentions→ Region/Aircraft**: OSINT↔실체 엔티티 링킹(별칭 사전 키워드 매칭).
- 네 소스가 같은 `Region(KADIZ)`를 공유 → "같은 시공간 창의 서브그래프"(ontology.md §0 스멜테스트 1·2 통과).

## 2. 커넥터 사용법 · 폴링 주기

각 커넥터는 `python -m connectors.<name>`로 **1 ingest 사이클**을 돈다(러너웨이 없음). 폴링 주기는 상수로 명시(architecture.md), 데모 폴러 배선은 P4/P6에서.

| 커넥터 | 폴링 주기 상수 | 소스 | 산출 객체 | 환경변수 |
|---|---|---|---|---|
| celestrak | `TLE_POLL_INTERVAL=12h`, `CACHE_TTL=12h` | Celestrak TLE + sgp4 | Satellite, OrbitPass | `CELESTRAK_GROUPS`(기본 `stations,visual`), `SKAI_DB` |
| metar | `METAR_POLL_INTERVAL=30m` | aviationweather.gov | WeatherState | `METAR_ICAOS`(기본 `RKSI`) |
| gdelt | `GDELT_POLL_INTERVAL=5m`, `GDELT_MIN_REQUEST_INTERVAL=5s` | GDELT doc API | NewsEvent | (쿼리는 코드 상수) |

```bash
# 위성 통과창 계산 (첫 호출은 TLE fetch+캐시, 이후 12h 재사용)
.venv/bin/python -m connectors.celestrak
# 기상 (RKSI)
.venv/bin/python -m connectors.metar
# 뉴스 (5초 규율 자동 준수)
.venv/bin/python -m connectors.gdelt
# 서버 + 지도
.venv/bin/python -m server.app   # → http://localhost:8000
```

- **캐시 규율(celestrak)**: `data/cache/celestrak_<group>.tle`에 저장, mtime<12h면 재fetch 안 함(로그 "캐시 사용 … fetch 생략"으로 확인).
- **5초 규율(gdelt)**: `_rate_limit_guard()`가 호출마다 직전 요청 이후 5초를 강제(우회 아님, 준수 자동화). 프로세스·폴링 사이클 간 유지.

## 3. 검증 결과 (성공기준 4항목)

| # | 기준 | 결과 |
|---|---|---|
| 1 | tests/test_p3.py(통과창·GMST·METAR·GDELT·confidence 상한) + 기존 32 유지 | **OK** — 58/58 통과 (P1 14 + P2 18 + P3 26) |
| 2 | 라이브 1회씩 → 4종 소스 객체 공존 + /api/counts | **OK** — 아래 카운트 |
| 3 | 통합 화면(위성 궤적 + 기상 카드 + 뉴스 패널 + 기존 항적/이상징후) | **OK** — `docs/worklog/p3_fusion.png` |
| 4 | 검증 후 프로세스 정리 + OpenSky 신규 호출 최소화 | **OK** — 프로세스 clean, OpenSky 신규 호출 0(기존 P1/P2 DB 재사용) |

### 소스별 객체 카운트 (기준 2) — `/api/counts`
```
by_source: opensky {aircraft 35, observation 74}   ← P1 재사용(신규 호출 0)
           celestrak {satellite 94, orbitpass 99}  ← stations+visual, 12h 통과창
           metar {weatherstate 1}                  ← RKSI MVFR
           gdelt {newsevent 14}                     ← KADIZ/한반도 공역, 4건 mentions→Region
objects:   ... anomaly 3(P2) · track 35 · region 1 · link 356
```
- **라이브 호출 로그**: Celestrak HTTP 200(stations 3864B·visual 24864B)→캐시 생성, 재호출 시 "캐시 사용" 확인. METAR HTTP 200(RKSI). GDELT: 좁은 인용구 쿼리는 `articles:null`(P0A 예측대로), 버스트 시 429를 **규율대로 건너뜀**(우회 안 함), 백오프 후 1회 성공 → 14건.
- **5초 규율 실증**: back-to-back guard 호출 → 정확히 5.0초 대기 강제 로그 확인.

### 화면 (기준 3) — docs/worklog/p3_fusion.png (1400×900)
한 프레임에 4종 소스 + P1/P2 공존:
- **위성 지상궤적(청록 점선)**: KADIZ 폴리곤을 가로지르는 99개 통과창(over Region). 툴팁=위성명·통과창·최대 앙각. 우상단 체크박스로 토글.
- **기상 카드(우하단)**: RKSI · MVFR(제한) · 바람 200°/8kt · 시정 3.73sm · 실링 1000ft · rawOb 원문.
- **뉴스 패널(좌하단)**: GDELT 저신뢰 리스트("확증 아님 · 신뢰도 ≤ 0.4"). "Why did Russian and Chinese aircraft enter South Korea air defense zone?"에 **KADIZ 언급** 배지(mentions→Region) + 신뢰도 0.35, 나머지 0.30.
- **소스별 카운트(우상단)**: OpenSky 74obs/35기 · Celestrak 99통과/94위성 · METAR 1 · GDELT 14.
- **P1/P2 공존**: 노란 항적 점, KADIZ 폴리곤, 빨강 이상징후 마커 3개 + 좌측 타임라인(비상 스쿽 후보/근거/confirm·dismiss).

### 시공간 정렬 확인 (기준: 같은 Region·겹치는 window의 4종 객체 1건)
```
[Region]      KADIZ (lat 32~39, lon 122~132)
[Observation] CES7500 @ (35.20, 125.80)  2026-07-03 15:50 UTC  squawk=7500   (opensky/synthetic)
[Weather]     RKSI(KADIZ 내)             2026-07-03 16:00 UTC  MVFR·실링 1000ft (metar)
[OrbitPass]   H-2A R/B (norad 43682)     2026-07-04 00:47~00:48 UTC  최대앙각 87.8°(천정 근접)  (celestrak)
[NewsEvent]   "…enter South Korea air defense zone"  2026-06-27  conf=0.35  mentions→Region KADIZ  (gdelt)
```
네 객체 모두 **Region KADIZ를 공유** = flat table 아닌 서브그래프. 통과창 앙각 분포(max 87.8°/중앙 68.6°/min 31.3°)가 subpoint-in-bbox=near-overhead를 확증 → **GMST 보정이 옳다**(P0A ISS 기준값과도 일치: lat -14.45, lon 57.31, alt 427.1km).
> 시간 정합: 관측·기상·통과는 하루 안, 뉴스는 OSINT 회고(7d 창) — 라이브 스냅샷의 자연스러운 폭. window 완전중첩은 P5 correlated_with(시공간 버킷)가 담당.

## 4. GMST 보정 (P0A gotcha 3 — 핵심 함정)
`eci_to_subpoint`에서 경도 = `atan2(y,x) - GMST`. 보정 없으면 수백 도 오차(예: ISS 계산 시 보정 없으면 -153°, 보정 후 +57.3°). GMST는 P0A와 동일한 1차 다항식(`280.46061837 + 360.98564736629·(JD-2451545)`). sgp4 라이브러리는 GMST 함수를 노출하지 않아 자체 계산. 앙각은 ECI→ECEF(-GMST 회전) 후 관측점 국소 상방과 시선 벡터로 산출 → 테스트 `test_gmst_correction_shifts_longitude`(+x축 벡터, GMST=90°→lon=-90°)로 증명.

## 5. 루트 기획문서와의 정합 (어긋남 아님 — 기록만)

- **data-sources.md §정규화(Event 중간표현)**: OrbitPass는 **창(window) 객체**라 Event(단일 점)로 표현 불가 → 위성/기상/뉴스는 소스→온톨로지 객체 **직접 매핑**(P2 이상탐지도 Event 우회). Event는 aircraft 점 데이터용 공통포맷으로 유지. provenance는 각 객체가 직접 보유.
- **GDELT 쿼리·timespan 조정**: DR-0005의 "괄호 OR 쿼리"는 준수하되, 좁은 인용구(`"Korea airspace"`)는 artlist에서 상시 `null`(실측) → KADIZ/한반도 공역 기사를 실제 회수하는 키워드 조합 + `7d` + **단일 쿼리/사이클**로 조정. 이유: GDELT가 1req/5s를 엄격 적용 → 2쿼리 버스트가 429를 부름. 5초 규율은 그대로 강제·증명. **DR-0005 스코프 내 튜닝**(우회 아님).
- **REGION_ALIASES 확장**: 쿼리가 이미 한반도 공역으로 스코프되므로 `air defense zone`·`defense identification zone`을 KADIZ 별칭에 추가(실 기사 제목 회수). 좁은 KADIZ-only 사전은 관련 기사를 놓쳤음.
- **Satellite 저장 범위**: KADIZ 상공을 지나는 위성만 write(카탈로그 전체 아님) — 지역 관련 엔티티에 집중(스코프 규율).
- **provenance 강제 확장**: `validate_provenance`를 덕타이핑화해 NewsEvent·WeatherState에도 적용(ontology.md "NewsEvent=증거 객체"). OrbitPass는 파생 계산 객체라 source_url만 보존(강제는 라이브 증거 객체 한정).
- 루트 기획문서(ontology.md 등) **무수정**.

## 6. P4/P5에 넘길 이슈 / 발견사항

1. **OrbitPass 재계산 누적(중요)**: 통과창은 "now 이후 12h"를 계산 → 폴러가 반복 실행하면 `start_ts`가 이동해 **매 실행마다 신규 id**가 쌓인다(과거 계산의 미래 통과가 stale로 잔존). 이번 검증에서도 celestrak 2회 실행 시 orbitpass 97→196으로 배증(정리 후 재실행). P4/P6 폴러 배선 시 "재계산 전 해당 위성 future-pass 삭제" 또는 TTL 필요.
2. **NewsEvent mentions→Aircraft = 0매칭**: 콜사인 exact match만 지원 → 군용 인시던트 뉴스(Chinese/Russian aircraft)엔 DB의 상용 콜사인(CES/UAE 등)이 안 나옴. P5 correlated_with는 콜사인 의존 대신 **시공간 버킷**으로 뉴스↔항적 상관을 잡을 것(ontology.md §2 예시). 콜사인 링킹은 상용기 사건에서만 발화.
3. **Operator 객체 미배선**: 스키마·저장 경로만 확보(write_operator·테이블). P5에서 satop/airforce 시드 + NewsEvent mentions→Operator 확장(현재 mentions는 Region/Aircraft만).
4. **involves→Satellite 확장 지점**: P2 §6-2 예고대로, 위성 낀 이상탐지(위성 근접/통과=OrbitPass over Region during window)는 P5. `store.write_anomaly`의 involves는 generic link라 대상 타입(Satellite)만 늘리면 됨. evidenced_by도 OrbitPass를 근거로 받을 수 있게 generic(이미 dst_type 파라미터화).
5. **GDELT 라이브 취약성**: IP 1req/5s 엄격 + 간헐 `RemoteProtocolError`(연결 끊김, 이번에 방어 추가) + 좁은 쿼리 null. **데모 중 라이브 실호출은 백오프 필수** → P6 스냅샷 재생 모드(라이브 부재 대비)에 뉴스도 포함 권장. NewsEvent/WeatherState 주입기는 미구현(P2 스쿽 주입기 패턴 재사용 가능).
6. **앙각 의미**: max_elevation은 **지역 중심 관측점 기준**. bbox 코너만 스치는 통과는 앙각 낮음(min 31.3°). "머리 위 통과" 판정을 앙각 임계로 좁히려면(P5 위성 근접 룰) 이 값 활용. subpoint-in-bbox는 앙각과 무관하게 over Region으로 기록(정의 준수).
7. **METAR ceiling/wind 특이값**: 가변풍(VRB)은 wind_dir=None+attrs 보존, 실링은 최저 BKN/OVC/VV. FEW/SCT-only는 무제한(None). VV(수직시정) 케이스는 이번 라이브에 없었음 — P5 저시정 임무영향 룰에서 실검증 권장.

## 7. 되돌리기

- 신규: `connectors/celestrak.py`·`metar.py`·`gdelt.py` + `tests/test_p3.py` + `docs/worklog/p3_fusion.png` + `data/cache/` 삭제.
- 기존 파일 역편집: `ontology/model.py`(P3 dataclass·NEWS_MAX_CONFIDENCE), `ontology/store.py`(validate_provenance 덕타이핑·Protocol 확장), `ontology/store_local.py`(5 테이블·write/query·counts), `ontology/store_foundry.py`(스텁), `server/app.py`(4 엔드포인트·title), `web/index.html`(위성 레이어·기상/뉴스 카드·소스 카운트).
- 온톨로지 스키마 v0.1 유지(신규 테이블은 `CREATE TABLE IF NOT EXISTS`라 기존 DB 비파괴). 런타임 산출물(data/·*.db·data/cache/)은 gitignore.
- 루트 문서 무변경.
