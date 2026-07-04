# 위성 근접 경고 스팸 + 상관 폭주 억제 (ISR 허용목록 게이트)

- 날짜: 2026-07-05
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 목표: ISR(정찰/이미징)이 아닌 위성(ISS·밝은 위성)의 KADIZ 통과가 전부 저신뢰 Anomaly로
  승격되고, 그 OrbitPass 홍수가 correlation 시공간 버킷과 곱해져 상관이 폭주하던 문제를
  **공개 문서화된 ISR 위성 허용목록**으로 게이트해 억제한다.

## 1. 문제(검증된 진단)
- `connectors/celestrak.py` 기본 GROUP = `stations,visual` → ISS·육안관측용 밝은 위성.
  ISR이 아닌데 `detect_satellite_proximity`가 이들 통과를 전부 저신뢰 Anomaly로 승격.
- `anomaly/correlation.py` 시공간 버킷(±60분)이 이 OrbitPass 홍수와 곱해져 한 이상징후에
  `correlated_with` 수십 건(실측 🔗73) → 상관이 노이즈.

## 2. 변경 (내 담당 파일만)
| 파일 | 변경 |
|---|---|
| `anomaly/isr_satellites.py` (신규) | ISR 허용목록(48기) + `is_isr_satellite`·`is_signal_promotable_pass` 판정 + TLE 수급/표시 결정 근거 docstring |
| `anomaly/rules.py` | `detect_satellite_proximity`에 허용목록 게이트(합성 우회) |
| `anomaly/correlation.py` | Anomaly↔OrbitPass 상관을 허용목록/합성만 생성 + 3종 엣지에 "왜 상관인가" 사유(시간차·공간관계) 부착 |
| `ontology/store_local.py` | `link` 테이블 `attrs_json` 컬럼(마이그레이션) + `link(attrs=)` upsert + `query_correlations`/`query_all_correlations`가 `reason` 반환 |
| `connectors/celestrak.py` | 기본 GROUP을 `isr_satellites.CELESTRAK_ISR_GROUPS`(=resource)로 교체 + 표시층 분리 결정 주석 |
| `server/app.py` | 이상징후 상세 API의 correlations에 `reason` 노출(이후 UI가 "왜 상관인가" 표시) |
| `tests/test_p4.py` | `_seed` 픽스처의 위성을 ISS(25544)→Sentinel-1A(39634)로 교체(비-ISR은 이제 상관 안 됨) |
| `tests/test_sat_allowlist.py` (신규) | 허용목록·게이트·합성 우회·사유 저장·하위호환 12개 테스트 |

## 3. 결정과 근거
### 3.1 허용목록 (48기)
- 공개 문서화된 EO/SAR/정찰 위성만: KOMPSAT(한), Sentinel-1/2(ESA), WorldView/GeoEye(Maxar),
  SkySat(Planet), Pleiades/SPOT(Airbus), COSMO-SkyMed(이), RadarSat-2(캐), TerraSAR-X(독),
  Yaogan·Gaofen(중, 공개보도상 정찰).
- **모든 NORAD ID는 추측이 아니라 Celestrak resource GROUP GP 데이터에서 직접 추출**
  (2026-07-05 fetch, `https://celestrak.org/NORAD/elements/gp.php?GROUP=resource&FORMAT=tle`).
  확인 안 된 위성은 넣지 않았다.

### 3.2 TLE 수급 = resource GROUP 1회 fetch (CATNR 개별 질의 아님)
- 허용목록 전량이 Celestrak `resource`(Earth Resources) 단일 GROUP에 있다(확인 완료).
  따라서 위성당 CATNR 1회(=40~60회 HTTP) 대신 **GROUP 1회 fetch**로 커버 → 레이트리밋 존중,
  12h 파일 캐시 유지. 기본 GROUP을 `stations,visual`에서 `resource`로 교체.
- `CELESTRAK_GROUPS` 환경변수 override는 그대로 동작(기존 게이트 패턴 보존).

### 3.3 표시층 분리 (권장안 채택)
- **표시(지도 지상궤적 레이어) = 전체 카탈로그**: celestrak.py가 받아온 GROUP의 모든 통과를
  OrbitPass로 write. 커넥터는 필터링하지 않는다(주변 위성 전부가 상황인식 맥락).
- **신호 승격(Anomaly)·상관(correlated_with) = 허용목록만**: rules·correlation에서 게이트.
- 코드 주석 근거: `connectors/celestrak.py` 헤더, `anomaly/isr_satellites.py` docstring.

### 3.4 합성 우회 (수락 기준)
- `source=="synthetic"` 통과는 게이트를 우회 → `scripts/scenarios.py`의 가상 위성
  (NORAD 9000x)이 replay 데모에서 계속 발화. `is_signal_promotable_pass`가 이 판정의 SSOT.

### 3.5 게이트는 기본 on
- "기본값이 문제"였으므로 허용목록 게이트는 기본 켜짐. 디버깅·구 무게이트 동작 복원용
  탈출구로 `SKAI_SAT_ALLOWLIST=off`(또는 0/false/no)를 두었다.

### 3.6 상관 사유 저장 (하위호환)
- `link` 테이블에 `attrs_json`(nullable) 컬럼 추가 — 기존 DB는 `_init`의 `ALTER TABLE`로
  마이그레이션(구링크는 `attrs_json=NULL`로 그대로 읽힘, 읽기 안 깨짐).
- `link(attrs=)` 지정 시 충돌해도 `attrs_json`을 갱신(ON CONFLICT DO UPDATE) → 재실행마다
  결정적 사유 재계산, 마이그레이션 전 링크도 다음 실행에서 사유 충전. `attrs` 미지정 경로는
  기존 `INSERT OR IGNORE` 그대로(다른 링크 타입 멱등 동작 보존).
- 사유 스키마: OrbitPass=`{kind, dt_s, region, max_elevation, norad_id}`,
  News=`{kind, dt_s, shared_regions}`, Anomaly=`{kind, gap_s, distance_km}`.

## 4. 테스트
- 전체: `.venv/bin/python -m pytest` → **344 passed, 4 skipped**(py3.14),
  `.venv312` → **347 passed, 1 skipped**(py3.12). 0 실패.
  (주: 세션 중 다른 팀원이 같은 트리에서 copilot 시간정직성 작업을 병행 → test_rss 등 일부
  테스트 수 증가분은 내 것이 아님. 내가 추가한 것은 test_sat_allowlist.py 12개.)
- 신규 `tests/test_sat_allowlist.py`(12): 허용목록 멤버십, 승격 판정, 게이트 off 탈출구,
  detect 게이트(비-ISR 차단/ISR 허용/합성 우회), correlation 게이트+합성 우회,
  사유 저장(OrbitPass·News·Anomaly), upsert 멱등, 구스키마 하위호환.
- replay 데모 자산: `test_p5.py`의 `test_scan_all_narrative_end_to_end`(합성 은닉정황:
  dropout+위성근접+뉴스 상관) 계속 통과 → 합성 우회로 데모 발화 유지 확인.

## 5. 한계 (정직)
- **허용목록에 없는 실제 정찰위성은 못 잡는다**: 목록은 공개 문서화된 대표 플랫폼만 담는다.
- **기밀·미분류 위성은 공개 TLE가 없다**: 전용 SIGINT/ELINT(미 NOSS/Intruder 등)·기밀 광학
  정찰은 Celestrak resource에 없거나 궤도요소가 비공개라 제외된다.
- **ICEYE·Capella 등 SAR 스몰샛 군집은 resource GROUP에 없다**(대개 `active` 대량 목록에만).
  단일 경량 fetch 설계를 지키려고 제외 — 필요 시 GROUP 추가로 확장 가능.
- **표시 볼륨은 여전히 큼**: resource GROUP(167기)의 KADIZ 통과가 전부 지도에 그려진다
  (설계상 표시=전체). 이는 신호가 아니라 표시 데이터라 스팸/상관 문제와 분리됨.
- 중국 Yaogan/Gaofen의 "정찰" 성격은 공개 보도 기반이지 단정이 아니다(임무 상세 비공개).

## 6. 미커밋
- 오케스트레이터 검토 후 커밋(팀 규칙). Foundry(`store_foundry.py`) 경로 미수정.
