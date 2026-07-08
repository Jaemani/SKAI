# data-sources.md — 공개·합법 소스 카탈로그 (Air ISR)

전부 공개/무료. 각 소스: 용도 · 접근 · 인증 · 리밋 · 주의. 에이전트는 붙이기 전 이 표로 검증하고, 실제 응답 스키마는 첫 호출로 확인해 갱신할 것.

> 합법성: 공개 API의 정상 사용만. 무단 스크래핑·레이트리밋 우회·유료 군용 피드 금지. robots/ToS 준수.

---

## 1. 항공기 항적 (Flight Tracks)

### OpenSky Network — 1순위
- 용도: 실시간 ADS-B 상태벡터(위치·고도·속도·스쿽·콜사인).
- 엔드포인트: `https://opensky-network.org/api/states/all?lamin=&lomin=&lamax=&lomax=` (바운딩박스).
- 인증: 익명 가능(리밋 빡셈). 무료 계정 권장(OAuth2 client_credentials, 4초 해상도).
- 리밋: 익명 ~400 credits/day, 계정 ~4000+. 바운딩박스로 크레딧 절약.
- 주의: 상태벡터 필드 순서 배열(문서 필드 인덱스 확인). 갱신 5~10초.

#### 실응답 확인 (2026-07-04)
- **상태**: OK (HTTP 200, 익명, KADIZ bbox)
- **헤더**: `x-rate-limit-remaining: 398` (잔여 크레딧)
- **스키마**: `{time: int, states: [[icao24, callsign, origin_country, time_position, last_contact, lon, lat, baro_altitude, on_ground, velocity, true_track, vertical_rate, sensors, geo_altitude, squawk, spi, position_source, category], ...]}`  인덱스 순서 고정 (상세는 worklog/P0A-sources.md)
- **샘플 수신**: 34항적 (KADIZ bbox, 2026-07-03 15:11 UTC)
- **gotcha**: `callsign` 공백 패딩 → `.strip()` 필수. `squawk`는 str 타입 → `== "7700"` 문자열 비교. `sensors`는 익명 시 null.

### ADS-B Exchange / adsb.fi / airplanes.live
- 용도: 군용기 포함(필터 완화), OpenSky 공백 보완.
- 접근: 공개 미러/피드 존재. ToS 확인.
- 주의: 밀리터리 필터는 여기서. dropout 비교용 교차소스로 유용.

#### 실검·배선 확인 (2026-07-04)
- **adsb.fi 채택** — dropout 교차확인 2차 소스로 라이브 배선(`SKAI_CROSSCHECK=live` 게이트, 기본 off). 비상업·교육용 허용(라이선스·판매·임대 금지), 인증 불요, **1 req/s**(코드 강제 1.05s + 30s TTL 캐시). 단일 hex 조회.
  **Attribution**: 데이터 제공 [adsb.fi](https://adsb.fi) — 인용 요건 준수.
- airplanes.live: 옵션(SKAI_CROSSCHECK_SOURCE) — ToS 원문 미확인(봇차단)이라 기본 아님.
- ADS-B Exchange: **탈락** — 무료 API 폐지(2025-03, RapidAPI 유료 전환).
- 한계(정직): 무료 2차 API는 현재 스냅샷만 — "지금 2차 소스가 보는가"의 근사 교차이지 window 전체 부재 단정 아님. 판정: 신선 관측=dropout 아님 / 미관측=부재 교차확인 / 오류·stale=미확인(단정 금지).

#### 사용 확대 — 군용 식별 보강 (2026-07-05, DR-0013 결정 5)
- **adsb.fi `/v2/mil` 채택** — 라이브 항적의 군용 여부를 **공개 커뮤니티 DB 플래그**로 저신뢰 보강(`SKAI_MIL_ENRICH=live` 게이트, 기본 off; 데모 live 모드는 기본 on). 크로스체크와 같은 소스·같은 리밋(1 req/s), 다른 질문("이 hex가 군용으로 DB 플래그돼 있나?").
  - **실측(2026-07-05)**: `/v2/mil` HTTP 200, `ac` 94기, 각 entry에 `dbFlags` 필드, 전부 `dbFlags==1`. `/v2/hex/<hex>` 응답에도 동일 `dbFlags` 실림. `dbFlags` 의미는 readsb `README-json.md`: `military = dbFlags & 1`(bit0=military). → **bit0만** 군용 신호로 채택.
  - **설계**: `/v2/mil`은 전 세계 군용 기체를 1회 호출로 전량 반환 → 60s TTL로 스냅샷 폴링, 군용 hex 집합을 캐시하고 bbox 항적을 O(1) 집합조회로 대조(호출량 최대 1 req/60s, hex 수 무관). 코드는 `connectors/mil_enrich_live.py`.
  - **한계(정직)**: 커뮤니티 DB라 오탐·미탐 존재(실측 스냅샷에 민간 Piper `N342TA`가 dbFlags=1로 섞여 있었음 = 오탐). 그래서 confidence ≤0.65(저신뢰). 트랜스폰더 OFF 군용기는 여전히 안 보임 → dropout(부재) 경로 담당(이중 경로).
  - **Attribution**: 데이터 제공 [adsb.fi](https://adsb.fi) — 인용 요건 준수(UI 크레딧 표기 필요).

#### 사용 확대 — 항적 대체 소스 (2026-07-05, OpenSky 크레딧 소진 대응)
- **adsb.fi 반경 질의 채택** — OpenSky 익명 크레딧 소진(429) 시 라이브 항적을 잇는 **대체/공존 track 소스**. `SKAI_POLL_SOURCES`에 `adsbfi` 포함으로 켠다(기본 미포함, opensky와 병렬 base 사이클). 같은 소스·ToS·1 req/s. 코드는 `connectors/adsbfi_tracks.py`.
  - **엔드포인트**: `GET .../v2/lat/{lat}/lon/{lon}/dist/{dist}` — 반경 `{dist}`**해리(nm)** 내 항적. **dist 상한 250nm**(실측: 250→HTTP 200, 300→HTTP 400).
  - **실측(2026-07-05)**: 응답 구조가 `/v2/hex`·`/v2/mil`과 **다름** — top 배열 키 `aircraft`(≠`ac`), count 키 `resultCount`(≠`total`). entry 실측 필드: `hex, flight, lat, lon, alt_baro, gs, track, squawk, seen, t, r, dst, baro_rate, type, category`. `alt_baro`는 정수(**피트**) 또는 문자열 `"ground"`. `gs`는 **노트**, `seen`은 마지막 메시지 이후 경과 초. 라이브 1회 실검(중심 35.5,127 dist 250) → KADIZ 부근 **81기** 수신.
  - **단위 변환(필수)**: Observation 계약 = OpenSky 단위(alt=m·velocity=m/s)라, `alt_baro`(ft)→m(×0.3048)·`gs`(kt)→m/s(×0.514444)·`baro_rate`(ft/min)→m/s로 변환해 write. 안 하면 급기동 룰 임계가 소스별로 어긋남. ts는 `seen` 기반 보정(fetched_at − seen)으로 OpenSky last_contact와 같은 관측시각 의미.
  - **커버리지(정직)**: KADIZ bbox(lat 32~39, lon 122~132) 코너는 중심에서 ≈322nm > 250nm → 1원으로 못 덮음. **2점 분할**(서 35.5,124.5 · 동 35.5,129.5, 각 250nm)로 bbox를 덮음(코너 여유 ≈7nm, 사이클당 2호출·간격 1.05s). 실제 저해상 한계는 질의 기하가 아니라 **커뮤니티 수신기 커버리지**(서해 너머 중국 방향 희소). OpenSky 대비 과거 window 조회 불가·필드 셋 차이는 있으나 항적 자체는 동등 수준.
  - **공존·dedup**: 같은 icao24는 기존 dedup/custody가 흡수 → opensky와 동시 폴링 가능. 2원 겹침 구역의 동일(icao24, ts) 관측은 자연 dedup(INSERT OR IGNORE).
  - **Attribution**: 데이터 제공 [adsb.fi](https://adsb.fi) — 인용 요건 준수(기존 crosscheck 크레딧이 같은 소스라 커버되나 항적 용도 확대 반영 권장).

## 2. 위성 궤도 (Satellite Orbits)

### Celestrak — 1순위
- 용도: TLE(궤도요소) 카탈로그. 특정 위성 머리 위 통과·지상궤적 계산.
- 엔드포인트: `https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=tle` (active, visual, resource 등).
- 인증: 없음. 리밋: 캐시 존중, TLE는 하루 1~2회 갱신이면 충분.
- 계산: `sgp4` 라이브러리로 TLE→위치. 관심지역 상공 통과창 계산.

#### 실응답 확인 (2026-07-04)
- **상태**: OK (HTTP 200, stations 그룹 23개 TLE 파싱)
- **스키마**: 3줄 텍스트 블록 반복 — 이름줄 / Line1(`1 ...`) / Line2(`2 ...`). Line1: epoch·항력·원소번호. Line2: 경사각·승교점적경·이심률·평균운동·공전횟수.
- **sgp4 검증**: `Satrec.twoline2rv(l1, l2)` → `sat.sgp4(jd, fr)` → ECI (km) 반환. ISS 위치 계산 완료 (lat -14.4°, lon 57.3°, alt 427km).
- **KADIZ 통과 판정**: `lamin ≤ lat ≤ lamax and lomin ≤ lon ≤ lomax` bool 판정 동작 확인.
- **gotcha**: ECI → 위경도 변환 시 GMST(Greenwich Mean Sidereal Time) 보정 필수. `error_code != 0` = 위성 만료 → 스킵. 실 운용 시 `active` 그룹 사용 (`stations`는 소규모 테스트용).

### Space-Track.org
- 용도: 정밀 카탈로그·conjunction 데이터.
- 인증: 무료 가입 필수. 리밋 엄격(쿼리 절약).

## 3. 기상 (Weather)

### aviationweather.gov (NOAA AWC) — 1순위
- 용도: METAR/TAF(공항 실황·예보), SIGMET.
- 엔드포인트: `https://aviationweather.gov/api/data/metar?ids=<ICAO>&format=json`.
- 인증: 없음. 항공 표준 포맷.

#### 실응답 확인 (2026-07-04)
- **상태**: OK (HTTP 200, RKSI 1건)
- **스키마**: JSON 배열. 주요 필드 — `icaoId`, `obsTime`(Unix), `reportTime`(ISO8601), `temp`(°C), `dewp`(°C), `wdir`(°), `wspd`(노트), `visib`(statute miles), `altim`(hPa), `rawOb`(원문), `lat/lon`(공항좌표), `clouds`([{cover, base_ft}]), `fltCat`(VFR/MVFR/IFR/LIFR).
- **샘플**: `rawOb = "METAR RKSI 031500Z 20008KT 7000 BKN010 BKN020 23/22 Q1010 NOSIG"` (fltCat=MVFR)
- **gotcha**: `visib` 단위는 statute miles (×1609.34 = m). `clouds[*].base` 단위는 **피트(ft)** — 미터와 혼용 금지.

### Open-Meteo
- 용도: 격자 기상(바람·가시거리·구름) 지역 단위.
- 엔드포인트: `https://api.open-meteo.com/v1/forecast?...`. 인증 없음, 관대한 리밋.

## 4. 뉴스 / OSINT

### GDELT — 1순위
- 용도: 글로벌 이벤트·뉴스 실시간 인덱스. 지역·키워드 필터.
- 엔드포인트: `https://api.gdeltproject.org/api/v2/doc/doc?query=&mode=artlist&format=json`.
- 인증: 없음.

#### 실응답 확인 (2026-07-04)
- **상태**: PARTIAL (엔드포인트 생존 확인, 레이트리밋 발동)
- **원인**: 초기 프로브에서 5초 내 복수 요청 버스트 → IP 레벨 429. 엔드포인트 자체는 정상.
- **스키마**: `{"articles": [{url, title, seendate, domain, language, sourcecountry, socialimage, url_mobile}]}`. 결과 없으면 `articles: null`.
- **쿼리 문법**: OR 사용 시 전체 쿼리 괄호 감싸기 필수 — `("KADIZ" OR "Korea Air Defense Identification Zone")`. 미감싸면 HTTP 200 + 텍스트 오류(JSON 아님).
- **gotcha**: 요청 간격 최소 5초 강제. `seendate` 포맷 `"YYYYMMDDTHHmmssZ"`. `articles` null 처리 필수 (`data.get("articles") or []`). 영문 기사 편향 — 영문 키워드 OR 조합 권장.

### NewsAPI / RSS / 웹검색
- 용도: 사건 맥락 보강(citation 소스). 신뢰도 낮게 가중.
- 주의: 뉴스는 확증 아님 → 신뢰도 스코어 낮춤, 항적·궤도 등 하드 소스로 교차검증.

### StealthMole (OSINT 위협 모니터링) — 특수상황 트랙
- 용도: 정부·기업·랜섬웨어 위협 게시글, 텔레그램 공개채널의 공역·항공 관련 언급을 **저신뢰 증거**로 이상징후 맥락 보강(예: ADS-B dropout 시각대 위협 언급 교차).
- 인증: `STEALTHMOLE_ACCESS_KEY` + `STEALTHMOLE_SECRET_KEY` → **요청마다 JWT 생성**(HS256, 재사용 시 401). Base URL은 해커톤 전용. 키는 `~/SKAI/.env`에만(gitignore).
- **사용 모듈(해커톤 제공 ∩ 가드레일 통과)**: **GM**(정부 위협)·**RM**(랜섬웨어)·**LM**(기업 위협)·**TT**(텔레그램 공개채널). ※ **DT(darkweb)·UB는 이번 해커톤 미제공**.
- 응답 → NewsEvent 매핑: `source="stealthmole"`, `confidence=0.25`(교차검증 전).
- **합법 가드레일**: CL/CDS/CB/CDF(개인 크리덴셜·유출계정·감염기기·유출파일) **사용 금지**. 개인정보 포함 응답 DB 적재 금지. 산출은 상황인식까지.
- **NDA**: 매뉴얼은 공유 금지 문서 → 엔드포인트·필드 상세는 **public 저장소 커밋 금지**. 상세 노트는 로컬 전용(`docs/worklog/stealthmole-manual-notes.md`, gitignore).
- 결정: DR-0010.

---

## 관심지역 시드 (데모 고정 후보)
- **KADIZ 근방** (한국 방공식별구역) — 서사·군 적합성 최상. bbox 예시: lat 32~39, lon 122~132.
- 대안: 특정 공항 반경, 또는 국제적으로 주목받는 해협.
> 데모는 **1곳 고정**. 좌표는 코드 상수로 두고 쉽게 교체 가능하게.

## 정규화 스키마 (융합 공통 포맷 → 온톨로지 매핑)
모든 소스를 이 Event 형태로 정규화한 뒤 **온톨로지 객체로 매핑**(aircraft→Observation/Aircraft, satellite→OrbitPass, weather→WeatherState, news→NewsEvent). 온톨로지 정의는 `ontology.md`, Foundry 적재는 `aip-integration.md`. Event는 온톨로지 이전의 공통 중간표현:
```
Event {
  id, source, source_url, fetched_at,
  kind: "aircraft" | "satellite" | "weather" | "news",
  ts (UTC), lat, lon, alt,
  attrs: { ... 소스별 원필드 ... },
  confidence: 0..1
}
```
citation은 `source` + `source_url` + `fetched_at`로 항상 역추적 가능해야 함.
