# P0A-sources.md — 공개소스 4종 생존 검증 (2026-07-04)

실행: `scripts/p0a_probe.py`  
검증 시각: 2026-07-03 15:11 UTC  
KADIZ bbox: lat 32~39, lon 122~132

---

## 1. OpenSky Network

### 호출
```
GET https://opensky-network.org/api/states/all
    ?lamin=32&lomin=122&lamax=39&lomax=132
인증: 익명 (무료 계정 없음)
```

### 상태
**OK** — HTTP 200, 34개 항적 수신

### 응답 헤더
| 헤더 | 값 |
|---|---|
| `x-rate-limit-remaining` | 398 (익명 일일 크레딧 잔여) |

rate-limit 관련 헤더는 `x-rate-limit-remaining` 하나만 존재. `Retry-After`, `X-RateLimit-Reset` 등 없음.

### 응답 스키마

최상위 구조:
```json
{
  "time": 1783091489,   // API 처리 시각 (Unix, UTC)
  "states": [...]       // 상태벡터 배열. null 가능 (항적 0건 시)
}
```

`states` 항목은 배열-of-배열. 각 항목의 인덱스 의미:

| 인덱스 | 필드명 | 타입 | 설명 |
|---|---|---|---|
| 0 | `icao24` | str | ICAO 24비트 주소 (hex) |
| 1 | `callsign` | str\|null | 콜사인 (공백 패딩 있음 — strip 필요) |
| 2 | `origin_country` | str | 등록 국가 |
| 3 | `time_position` | int\|null | 마지막 위치 수신 Unix 시각 |
| 4 | `last_contact` | int | 마지막 ADS-B 수신 Unix 시각 |
| 5 | `longitude` | float\|null | 경도 (°) |
| 6 | `latitude` | float\|null | 위도 (°) |
| 7 | `baro_altitude` | float\|null | 기압고도 (m) |
| 8 | `on_ground` | bool | 지상 여부 |
| 9 | `velocity` | float\|null | 대지속도 (m/s) |
| 10 | `true_track` | float\|null | 진북 기준 방향 (°) |
| 11 | `vertical_rate` | float\|null | 수직속도 (m/s, 양수=상승) |
| 12 | `sensors` | list\|null | 수신 센서 ID 목록 (익명 시 null) |
| 13 | `geo_altitude` | float\|null | 기하고도 GPS (m) |
| 14 | `squawk` | str\|null | 스쿽 코드 (4자리 문자열) |
| 15 | `spi` | bool | 특수 목적 지시 (SPI) |
| 16 | `position_source` | int | 0=ADS-B, 1=ASTERIX, 2=MLAT, 3=FLARM |
| 17 | `category` | int\|null | 항공기 카테고리 (A1~A7 등) |

### 샘플 (2건)
```
icao24=84d283  callsign='JJP11'    lon=131.3442  lat=32.1258  alt=10363m  squawk=3647  country=Japan
icao24=896182  callsign='UAE323'   lon=126.6050  lat=36.8032  alt=4655m   squawk=4101  country=United Arab Emirates
```

### 리밋·주의사항
- 익명: ~400 credits/day (`x-rate-limit-remaining`으로 확인). 1회 bbox 조회 = 1 credit 소모.
- 갱신 주기: 5~10초 (익명은 4초 해상도 미지원).
- `callsign`에 공백 패딩 포함 → `.strip()` 필수.
- `squawk`는 str, `sensors`는 익명 시 항상 null.
- 군용기는 필터링될 수 있음 (transponder off 또는 OpenSky 정책).

### 온톨로지 매핑 참고
- `icao24` → `Aircraft.icao24` (PK)
- `callsign` → `Aircraft.callsign`
- `[6],[5]` (lat, lon) + `[7]` (baro_altitude) → `Observation.lat / lon / alt`
- `[4]` (last_contact) → `Observation.ts`
- `[14]` (squawk) → `Observation.attrs.squawk` — 7500/7600/7700 = 비상 탐지 트리거
- `[16]` (position_source) → `Observation.attrs.position_source`
- `[10]` (true_track) + `[9]` (velocity) → 이상탐지 로이터링 계산 기반

---

## 2. Celestrak TLE + sgp4

### 호출
```
GET https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle
인증: 없음
```

### 상태
**OK** — HTTP 200, 3864 bytes, 23개 TLE 파싱 성공

### 응답 스키마

TLE 텍스트 포맷 (3줄 그룹 반복):
```
ISS (ZARYA)
1 25544U 98067A   26182.50817465  .00006185  00000+0  11827-3 0  9996
2 25544  51.6311 229.1989 0004224 255.0896 104.9625 15.49503254573972
```

- 줄1 (`1 ...`): 위성번호, 국제지정번호, epoch(년+일수), 항력계수, 탄도계수, BSTAR 항력, 원소 번호
- 줄2 (`2 ...`): 위성번호, 경사각(°), 승교점적경(°), 이심률, 근지점편각(°), 평균근점이각(°), 평균운동(rev/day), 누적 공전횟수

### sgp4 계산 결과 (ISS, 2026-07-03T15:11:43Z)
```
ECI (km): x=-5870.8  y=-2979.3  z=-1694.5
위도: -14.43°   경도: 57.32°   고도: 427.1 km
KADIZ bbox 내: False
```

bbox 통과 판정 로직 (`lamin ≤ lat ≤ lamax and lomin ≤ lon ≤ lomax`) 동작 확인 완료.

### 전체 위성 목록 (23개)
ISS (ZARYA), POISK, CSS (TIANHE), ISS (NAUKA), FREGAT DEB, CSS (WENTIAN), CSS (MENGTIAN), HRC MONOBLOCK CAMERA 등 우주정거장 관련 객체 포함.

### 리밋·주의사항
- TLE는 하루 1~2회 갱신으로 충분 (캐시 권장). 과도한 호출 금지.
- `sgp4` 라이브러리: `Satrec.twoline2rv(l1, l2)` → `sat.sgp4(jd, fr)` 반환 `(error_code, r_km, v_km/s)`.
- `error_code != 0` = 위성 수명 만료 또는 궤도 소멸 → 스킵 처리.
- ECI → 위경도 변환 시 GMST(Greenwich Mean Sidereal Time) 보정 필수.
- stations 그룹은 소규모(23개)이므로 예의 있는 테스트 용도로 적합. 실 운용 시 `active` 그룹 사용.

### 온톨로지 매핑 참고
- TLE 이름 → `Satellite.name` (+ NORAD 번호 = PK)
- 계산된 lat/lon/alt → `OrbitPass.lat / lon / alt_km`
- 통과 시각 윈도우 → `OrbitPass.entry_ts / exit_ts`
- `in_kadiz_bbox` → `OrbitPass.over_region` 링크

---

## 3. METAR (aviationweather.gov)

### 호출
```
GET https://aviationweather.gov/api/data/metar?ids=RKSI&format=json
인증: 없음
```

### 상태
**OK** — HTTP 200, 1건 반환

### 응답 스키마

JSON 배열, 각 항목 필드:

| 필드 | 타입 | 값 (샘플) | 설명 |
|---|---|---|---|
| `icaoId` | str | `"RKSI"` | ICAO 공항 코드 |
| `receiptTime` | str (ISO8601) | `"2026-07-03T15:04:28.540Z"` | 서버 수신 시각 |
| `obsTime` | int | `1783090800` | 관측 Unix 시각 |
| `reportTime` | str (ISO8601) | `"2026-07-03T15:00:00.000Z"` | METAR 보고 시각 |
| `temp` | int | `23` | 기온 (°C) |
| `dewp` | int | `22` | 이슬점 (°C) |
| `wdir` | int | `200` | 풍향 (°, 진북) |
| `wspd` | int | `8` | 풍속 (노트) |
| `visib` | float | `4.35` | 시정 (sm, statute miles) |
| `altim` | int | `1010` | 기압 (QNH, hPa) |
| `qcField` | int | `16` | QC 플래그 |
| `metarType` | str | `"METAR"` | 유형 (METAR / SPECI) |
| `rawOb` | str | `"METAR RKSI 031500Z 20008KT 7000 BKN010 BKN020 23/22 Q1010 NOSIG"` | 원문 METAR |
| `lat` | float | `37.469` | 공항 위도 |
| `lon` | float | `126.451` | 공항 경도 |
| `elev` | int | `7` | 표고 (m) |
| `name` | str | `"Seoul/Incheon Intl, 28, KR"` | 공항명 |
| `cover` | str | `"BKN"` | 운량 코드 (FEW/SCT/BKN/OVC) |
| `clouds` | list | `[{"cover":"BKN","base":1000},{"cover":"BKN","base":2000}]` | 운고 상세 (피트 단위) |
| `fltCat` | str | `"MVFR"` | 비행 기상 범주 (VFR/MVFR/IFR/LIFR) |

### 원문 METAR 해독
```
METAR RKSI 031500Z  → 3일 1500Z (UTC)
20008KT             → 풍향 200° 8노트
7000                → 시정 7000m
BKN010 BKN020       → 운고 1000ft, 2000ft (구름 많음)
23/22               → 기온 23°C, 이슬점 22°C
Q1010               → 기압 1010 hPa
NOSIG               → 유의 기상 변화 없음
```

### 리밋·주의사항
- 인증 불필요, 레이트리밋 명시 없음 (합리적 사용 권장).
- `visib` 단위는 statute miles (sm), 미터로 환산: × 1609.34.
- `clouds` 배열의 `base`는 **피트(ft)** — 온톨로지 매핑 시 단위 명시 필수.
- `fltCat` = MVFR이면 가시거리/운고 제한 — ISR 임무 가용성 지표로 활용 가능.

### 온톨로지 매핑 참고
- `icaoId` + `obsTime` → `WeatherState` PK
- `temp`, `dewp`, `wdir`, `wspd`, `visib`, `altim` → `WeatherState.attrs.*`
- `fltCat` → `WeatherState.attrs.flight_category` (VFR=임무 가능 지표)
- `lat`, `lon` → `WeatherState.lat / lon`
- `rawOb` → citation 소스 원문 보존용

---

## 4. GDELT

### 호출
```
GET https://api.gdeltproject.org/api/v2/doc/doc
    ?query=("KADIZ" OR "Korea Air Defense Identification Zone" OR "한국방공식별구역")
    &mode=artlist&maxrecords=5&format=json&timespan=3d
인증: 없음
```

### 상태
**PARTIAL — 레이트리밋(429)**

엔드포인트 자체는 살아있음. 초기 프로브 실행에서 복수 요청이 5초 내에 버스트되어 IP 레벨 제한 발동. HTTP 200 응답은 수신했으나 JSON 파싱 전 텍스트 레벨 오류 메시지 반환 (첫 시도 = 쿼리 문법 오류, 이후 = 429).

### 쿼리 문법 주의사항 (확인된 gotcha)
- OR 연산자 사용 시 전체 쿼리를 괄호로 감싸야 함:
  - 잘못: `"KADIZ" OR "Korea Air Defense Zone"`
  - 올바름: `("KADIZ" OR "Korea Air Defense Zone")`
- 오류 응답은 HTTP 200이지만 body가 JSON이 아닌 텍스트 오류 메시지 → `resp.json()` 직접 호출 금지, 파싱 전 확인 필요.

### 응답 스키마 (문서 기반 — 실 응답 미수신)
```json
{
  "articles": [
    {
      "url": "https://...",
      "url_mobile": "https://...",
      "title": "기사 제목",
      "seendate": "20260703T150000Z",
      "socialimage": "https://...",
      "domain": "example.com",
      "language": "Korean",
      "sourcecountry": "South Korea"
    }
  ]
}
```

기사 0건 시 `{"articles": null}` 반환 (배열이 아닌 null — 처리 주의).

### 리밋·주의사항
- **요청 간격: 최소 5초** (IP 레벨 적용, 버스트 시 연장 제한).
- `timespan` 파라미터: `3d`, `7d`, `1w`, `1m` 등 — 미지정 시 전체 검색 (느림).
- `seendate` 포맷: `"YYYYMMDDTHHmmssZ"` — 파싱 시 `strptime("%Y%m%dT%H%M%SZ")` 사용.
- GDELT는 영문 기사 편향 — 한국어 키워드 단독 검색 시 결과 부족할 수 있음. 영문 동의어 OR 조합 권장.
- `articles` 필드가 없거나 null인 경우를 명시적으로 처리할 것 (`data.get("articles") or []`).

### 온톨로지 매핑 참고
- `url` → `NewsEvent.source_url` (citation PK)
- `title` → `NewsEvent.attrs.title`
- `seendate` → `NewsEvent.ts`
- `sourcecountry` → `NewsEvent.attrs.source_country`
- `domain` → `NewsEvent.attrs.domain`
- `confidence` → 0.3 고정 (뉴스는 확증 아님, 하드 소스 교차검증 필요)

---

## 종합 요약

| 소스 | 상태 | HTTP | 비고 |
|---|---|---|---|
| OpenSky | **OK** | 200 | 34항적 수신, 익명 크레딧 398/400 잔여 |
| Celestrak + sgp4 | **OK** | 200 | ISS 위치 계산 완료, KADIZ 통과 판정 로직 검증 |
| METAR (RKSI) | **OK** | 200 | 전 필드 수신, 운항기상범주 MVFR |
| GDELT | **PARTIAL** | 429 | 버스트→IP 제한. 엔드포인트 생존 확인, 5초 간격 준수 필요 |

## P1 진입 전 핵심 gotcha

1. **OpenSky `callsign` 공백 패딩**: DB 저장 또는 비교 전 `.strip()` 필수.
2. **OpenSky `squawk`는 str**: 정수 비교(`== 7700`) 아닌 문자열 비교(`== "7700"`) 또는 명시적 캐스팅.
3. **Celestrak ECI→위경도 GMST 보정**: `atan2(y, x)` 만으로는 경도 오차 수백 도 — GMST 빼야 함.
4. **METAR `clouds[*].base` 단위는 피트**: 미터 혼용 금지.
5. **GDELT OR 쿼리 괄호 필수**: 미감싸면 HTTP 200 + 텍스트 오류 (JSON 파싱 실패).
6. **GDELT 요청 간격 5초**: P1 이후 폴링 루프에 `time.sleep(5)` 삽입 필수. 동일 IP 버스트 시 복구에 수분 소요.
7. **GDELT `articles` null 처리**: `data.get("articles") or []` 패턴 사용.
