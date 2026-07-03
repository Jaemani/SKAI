# data-sources.md — 공개·합법 소스 카탈로그 (Air ISR)

전부 공개/무료. 각 소스: 용도 · 접근 · 인증 · 리밋 · 주의. 에이전트는 붙이기 전 이 표로 검증하고, 실제 응답 스키마는 첫 호출로 확인해 갱신할 것.

> ⚠️ 합법성: 공개 API의 정상 사용만. 무단 스크래핑·레이트리밋 우회·유료 군용 피드 금지. robots/ToS 준수.

---

## 1. 항공기 항적 (Flight Tracks)

### OpenSky Network — 1순위
- 용도: 실시간 ADS-B 상태벡터(위치·고도·속도·스쿽·콜사인).
- 엔드포인트: `https://opensky-network.org/api/states/all?lamin=&lomin=&lamax=&lomax=` (바운딩박스).
- 인증: 익명 가능(리밋 빡셈). 무료 계정 권장(OAuth2 client_credentials, 4초 해상도).
- 리밋: 익명 ~400 credits/day, 계정 ~4000+. 바운딩박스로 크레딧 절약.
- 주의: 상태벡터 필드 순서 배열(문서 필드 인덱스 확인). 갱신 5~10초.

### ADS-B Exchange / adsb.fi / airplanes.live
- 용도: 군용기 포함(필터 완화), OpenSky 공백 보완.
- 접근: 공개 미러/피드 존재. ToS 확인.
- 주의: 밀리터리 필터는 여기서. dropout 비교용 교차소스로 유용.

## 2. 위성 궤도 (Satellite Orbits)

### Celestrak — 1순위
- 용도: TLE(궤도요소) 카탈로그. 특정 위성 머리 위 통과·지상궤적 계산.
- 엔드포인트: `https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=tle` (active, visual, resource 등).
- 인증: 없음. 리밋: 캐시 존중, TLE는 하루 1~2회 갱신이면 충분.
- 계산: `sgp4` 라이브러리로 TLE→위치. 관심지역 상공 통과창 계산.

### Space-Track.org
- 용도: 정밀 카탈로그·conjunction 데이터.
- 인증: 무료 가입 필수. 리밋 엄격(쿼리 절약).

## 3. 기상 (Weather)

### aviationweather.gov (NOAA AWC) — 1순위
- 용도: METAR/TAF(공항 실황·예보), SIGMET.
- 엔드포인트: `https://aviationweather.gov/api/data/metar?ids=<ICAO>&format=json`.
- 인증: 없음. 항공 표준 포맷.

### Open-Meteo
- 용도: 격자 기상(바람·가시거리·구름) 지역 단위.
- 엔드포인트: `https://api.open-meteo.com/v1/forecast?...`. 인증 없음, 관대한 리밋.

## 4. 뉴스 / OSINT

### GDELT — 1순위
- 용도: 글로벌 이벤트·뉴스 실시간 인덱스. 지역·키워드 필터.
- 엔드포인트: `https://api.gdeltproject.org/api/v2/doc/doc?query=&mode=artlist&format=json`.
- 인증: 없음.

### NewsAPI / RSS / 웹검색
- 용도: 사건 맥락 보강(citation 소스). 신뢰도 낮게 가중.
- 주의: 뉴스는 확증 아님 → 신뢰도 스코어 낮춤, 항적·궤도 등 하드 소스로 교차검증.

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
