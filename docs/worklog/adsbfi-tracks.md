# adsbfi-tracks.md — adsb.fi 항적 대체 소스 (실행 로그)

- 날짜: 2026-07-05
- 담당: opus 실행 에이전트 (팀리드 긴급 지시 — OpenSky 크레딧 소진 대응)
- 근거: OpenSky 익명 크레딧 소진(429, UTC 자정까지 복구 불가)으로 라이브 항적 단절 · CLAUDE.md 기술기준(라이브 우선·재현성) · `crosscheck_live`/`mil_enrich_live` 자매 패턴(같은 소스 adsb.fi)
- 판정: **LIVE-WIRED** (adsb.fi 반경 질의, `SKAI_POLL_SOURCES`에 `adsbfi` 포함 시 동작 — opensky와 병렬·공존)

---

## 0. 결론 (발표용 정직 문구)

- **OpenSky가 끊겨도 라이브 항적을 잇는다.** 무료·공개 2차 ADS-B 네트워크 adsb.fi의 **반경 질의**로 KADIZ 부근 항적을 받아 OpenSky와 **동일한 Observation/Aircraft 규약**(단위까지 변환)으로 온톨로지에 write한다. 라이브 1회 실검에서 **KADIZ 부근 81기 수신**을 확인했다(단일 질의) / 전체 2점 파이프 실검 106기·134관측.
- **OpenSky와 공존한다.** 같은 icao24는 기존 dedup/custody가 흡수하므로, 둘을 동시에 켜도 Track이 icao24로 묶인다. OpenSky 대체(단독)로도, 보완(공존)으로도 쓸 수 있다.
- **기본은 보수적(opt-in).** 기본 라이브 소스(`opensky,gdelt,metar,celestrak`)는 불변. `SKAI_POLL_SOURCES`에 `adsbfi`를 넣을 때만 켜진다. opensky.py는 **추가만**(기존 동작 무변경) — resolve_sources의 known 집합에 `adsbfi` 추가 + run_poller에 병렬 base-cycle 브랜치.
- **정직한 한계(핵심)**: (a) 실제 커버리지 한계는 질의 기하가 아니라 **커뮤니티 수신기 밀도**다 — 서해 너머 중국 방향은 항적·수신이 희소. (b) 무료 API라 **과거 window 조회 불가**(현재 스냅샷만). (c) OpenSky와 필드 셋이 다르다(origin_country 미제공 등). 항적 위치·고도·속도·스쿽 자체는 동등 수준.

---

## 1. 실측 (2026-07-05, 실호출)

### 1.1 엔드포인트·상한
- `GET https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}` — 반경 `{dist}` **해리(nm)** 내 항적. 무인증, 1 req/s(crosscheck/mil과 동일).
- **dist 상한 = 250nm** (실측: `dist/250` → HTTP 200·49.6KB / `dist/300` → **HTTP 400**(빈 바디)). → 250을 상한으로 코드 상수(`MAX_DIST_NM`).

### 1.2 응답 구조 (`/v2/hex`·`/v2/mil`과 다름 — 주의)
- top-level: `{aircraft: [...], now: float, ptime: float, resultCount: int}`.
  - **배열 키가 `aircraft`**(≠ `ac`), **count 키 `resultCount`**(≠ `total`), `msg` 없음. → 크로스체크/밀리터리 파서를 재사용 못 함(별도 매핑 필요). 이걸 실호출로 확정하지 않았으면 `ac`로 파싱해 0건이 나왔을 함정.
- entry 필드(union 실측): `hex, flight, lat, lon, alt_baro, gs, track, squawk, seen, seen_pos, t, type, dbFlags, r, dst, category, baro_rate, geom_rate, mlat, ...`.
- **`alt_baro` 이질성**: 정수(피트) **또는 문자열 `"ground"`**(지상기). 실측 81기 중 23기가 `"ground"`. → 숫자 가드(`_num`) + `"ground"`→on_ground 매핑.
- `gs`=지상속도(**노트**), `track`=진북 기준 도(°), `seen`=마지막 메시지 후 경과 초(0.0~44.5 관측), `dst`=질의 중심 거리(nm, 관측 최대 245.1 < 250), `r`=등록기호, `t`=기종.
- 결측: 81기 중 gs None 7·track None 17·flight None 7·squawk None 9 — 전부 None 허용(스킵 아님). 위치(lat/lon)는 81/81 존재하나 코드는 결측 시 스킵(OpenSky와 동일 규율).

### 1.3 라이브 수신 수치
- 단일 질의(중심 35.5,127 dist 250): **81기**(lat 32.6~37.66, lon 126.15~131.6 — 관측 분포는 수신기 커버리지에 좌우).
- 전체 파이프 실검(2점 질의, 임시 DB): **obs 처리 179 / 저장 관측 134 / 항공기 106 / Track 106 / 신규 Anomaly 0**. 179→134 차이(45)는 2원 겹침 구역의 (icao24, ts) 동일 관측 자연 dedup. Anomaly 0은 이 스냅샷에 비상 스쿽·급기동 등이 없었고 게이트(crosscheck/mil) 미설정(Null)이라 정상.

### 1.4 ToS·리밋 (crosscheck_live와 동일)
개인·비상업/교육용만. 라이선스·판매·임대 금지. **adsb.fi 인용 + 홈페이지 링크 필수**. 해커톤 데모=비상업·교육 → 허용. 1 req/s는 코드가 강제(사이클당 2호출, 호출 간 1.05s).

---

## 2. 설계

### 2.1 단위 변환 (필수 — 안 하면 룰 임계가 어긋남)
`anomaly/rules.py::_maneuver_segment`는 alt(미터)·velocity(m/s)로 변화율(수직률·가속)을 계산하고 임계(6000ft/min·3m/s²)와 비교한다(rules.py:124 "단위 — 고도=미터·속도=m/s(OpenSky)"). adsb.fi는 피트·노트라, **여기서 OpenSky 단위로 변환**해 Observation 계약을 소스 간 한결같게 만든다:

| adsb.fi 필드 | 단위 | Observation 필드 | 변환 |
|---|---|---|---|
| `alt_baro` | 피트 | `alt` | ×0.3048 (`"ground"`→None+on_ground) |
| `gs` | 노트 | `velocity` | ×0.514444 |
| `track` | 도(°) | `heading` | 그대로(OpenSky true_track과 동일) |
| `baro_rate` | ft/min | attrs.vertical_rate | ×0.00508 |
| `seen` | 경과 초 | `ts` | fetched_at − round(seen) |

검산(실측 KAL486): 7525ft→2293.6m ✓, 266.8kt→137.3m/s ✓.

### 2.2 커버리지 — 2점 분할
KADIZ bbox(lat 32~39=420nm, lon 122~132≈488nm@35.5°) 코너는 중심(35.5,127)에서 ≈322nm > 250nm → **1원으로 불가**. 더 긴 EW축을 둘로 갈라 두 원으로 덮는다:
- 서 (35.5, **124.5**) · 동 (35.5, **129.5**), 각 dist 250nm.
- 검증: 원거리 코너(예 39,122)까지 서center 거리 = √(210²+122²)=242.9nm ≤ 250(여유 7nm). 두 원이 중앙 위도극단에서 겹쳐 bbox 전역 커버.
- 실 한계: 위 기하는 "질의가 닿는 범위"일 뿐, 실제 수신은 커뮤니티 수신기 밀도에 좌우(서해 방향 희소).

### 2.3 폴러 등록 (opensky.py 추가만)
- resolve_sources: `known`에 `adsbfi` 추가(base-cycle track 소스라 SOURCE_INTERVALS의 due 스케줄엔 미등록 → due_sources가 자연 스킵).
- run_poller: opensky 브랜치 뒤에 **병렬 base-cycle 브랜치** 추가(`if "adsbfi" in sources`). 폴러의 crosscheck/mil_enrich 재사용 인스턴스를 그대로 주입. opensky 없이 단독일 땐 adsbfi가 사이클 상태(status)를 반영, 공존 시엔 opensky가 primary. 실패는 격리(로깅만).
- 커넥터 `ingest_cycle(store, client, crosscheck, mil_enrich)`은 **opensky.ingest_cycle과 같은 인터페이스**: fetch(2점)→write(Aircraft/Observation/observed_as)→rebuild_tracks→scan_and_create_all. 이상탐지는 store 전체를 스캔하므로 adsb.fi 관측도 같은 룰로 판정된다(opensky 유무 무관 자기완결).

### 2.4 오류 격리
- fetch_point: 429·비200·타임아웃·네트워크예외·비JSON·비dict → 모두 None(그 질의만 skip, gdelt.fetch_articles 패턴). 한 점 실패해도 다른 점은 기여.
- ingest_cycle 전체는 폴러 브랜치의 try/except로 옆 소스와 격리.

---

## 3. 매핑 상세 (OpenSky 대비)

- **Aircraft**: icao24=hex(소문자)·callsign=flight.strip()·is_military=False(관측 플래그로 단정 안 함, 군용 판정은 mil_enrich/휴리스틱 경로). **추가로 registration=`r`·type=`t` 보강**(OpenSky엔 없는 실데이터 — UI 표시·엔티티 해소에 유용). REPLACE라 opensky/adsbfi가 같은 icao24를 써도 실값은 동일.
- **Observation**: id=`{icao24}-{ts}`(자연 dedup)·source=`adsbfi`·source_url=실제 질의 URL(provenance 강제 통과). squawk는 str 보존. attrs에 origin_country/position_source(None, OpenSky 키 병렬)·vertical_rate(변환)·adsb.fi 고유(msg_type·aircraft_type·registration·category·dst_nm·seen_s).

---

## 4. 테스트

- **신규**: `tests/test_adsbfi_tracks.py` — **24 케이스**, MockTransport(네트워크 0).
  - 순수 매핑: 단위변환·ground·결측 허용·위치/hex 결측 스킵·hex 소문자·squawk 0 보존.
  - response_to_pairs: aircraft null/부재/None·비dict·무위치 스킵.
  - fetch_point 격리: 200/429/비200/네트워크예외/비JSON/비dict + UA 전송.
  - ingest_cycle: 2점 write+링크+Track+카운트, 한 점 429 격리, 양쪽 실패=0, 2점 질의 확인, 겹침 dedup.
  - 폴러 등록: resolve_sources가 adsbfi 유지·bogus 드롭, due_sources가 adsbfi 미스케줄.
- **회귀**: 전체 `.venv/bin/python -m pytest -q` → **390 passed, 4 skipped**(기존 366 + 신규 24). 회귀 0.
- **라이브 실검**: `SKAI_DB=<임시> python -m connectors.adsbfi_tracks` → 106기·134관측(실 네트워크, 임시 DB — 런타임 data/skai.db 미접촉).

---

## 5. 한계·후속 (정직)

- **수신기 커버리지가 실 한계** — 질의는 KADIZ 전역을 덮지만 실제 항적은 커뮤니티 수신기 밀도에 좌우. 서해/황해 서쪽(중국 방향)은 희소할 수 있음. OpenSky의 (센서 네트워크가 다른) 커버리지와 완전 동일하진 않다.
- **과거 window 조회 불가** — 무료 API는 현재 스냅샷만. dropout 판정은 여전히 "지금 보이는가"의 근사(crosscheck와 같은 한계).
- **필드 셋 차이** — origin_country 미제공(attrs None). position_source 없음. dbFlags는 반경 응답에도 실리지만 이 커넥터는 군용 판정을 하지 않음(mil_enrich 경로가 담당) — 여기선 is_military=False 고정.
- **DEFAULT_LIVE_SOURCES 미변경** — 기본 데모 소스는 그대로. OpenSky 복구 후에도 공존 폴백으로 남길지, 데모 기본에 넣을지는 사용자/팀 결정(현재는 `SKAI_POLL_SOURCES=adsbfi,gdelt,metar,celestrak` 등으로 명시 활성). demo.sh 기본 sources·UI 크레딧 문구 반영은 후속(프론트/데모 몫).
- **UI 크레딧** — adsb.fi 인용 요건. 기존 crosscheck용 크레딧이 같은 소스라 커버되나, "항적 대체 소스" 용도 확대를 크레딧 문구에 반영 권장(프론트 몫).

---

## 6. 변경 파일

- 신규: `connectors/adsbfi_tracks.py`, `tests/test_adsbfi_tracks.py`, `docs/worklog/adsbfi-tracks.md`
- 수정(추가만): `connectors/opensky.py`(import + resolve_sources known + run_poller 병렬 브랜치), `data-sources.md`(§1 항적 용도 확대 기록)
- 미변경(의도): opensky `ingest_cycle`·기존 동작, `DEFAULT_LIVE_SOURCES`, demo.sh, data/skai.db
