# P1-vertical.md — 수직관통 실행 로그 (OpenSky → 온톨로지 → 지도)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P1)
- 근거: PROMPTS.md P1 · DR-0003 (저장 어댑터 분리) · P0A-sources.md (실측 스키마 + gotcha 8건)
- 상태: **완료** (테스트 14/14 통과 · 라이브 항적 지도 렌더 확인)

---

## 1. 무엇을 만들었나

OpenSky 항적 → 온톨로지 객체(Aircraft/Observation/Track) → 지도까지 끝단 한 줄.
저장은 DR-0003대로 **어댑터 뒤에 숨겨** SQLite("보험")로 구현. Foundry가 뚫리면 구현 교체만.

```
connectors/opensky.py       KADIZ bbox 폴링 → Event 정규화 → store write + Track custody
ontology/model.py           ontology.md §1 미러 dataclass (Region/Aircraft/Observation/Track/Event) + KADIZ 상수
ontology/mapping.py         Event → 온톨로지 객체 (P0A 필드 인덱스 + gotcha 반영)
ontology/store.py           OntologyStore 인터페이스(Protocol) + provenance 강제(validate_provenance)
ontology/store_local.py     SQLite 구현 ("보험" 명시) — ontology.md 스키마 미러 + generic link 테이블
ontology/store_foundry.py   스텁 (NotImplementedError + 크리덴셜 도착 시 구현 TODO, P0B 참조)
ontology/custody.py         icao24로 Observation 묶어 Track, 간격 >90초면 has_gap
server/app.py               FastAPI: /api/observations /api/tracks /api/regions /api/stats + web/ 정적 서빙
web/index.html              vanilla Leaflet 단일 페이지, 30초 자동 갱신 (빌드 없음)
scripts/run_p1.sh           폴러+서버 기동/중지 (start|stop|status)
tests/test_p1.py            provenance 거부 · Track gap · Event→객체 매핑 (14 케이스)
```

## 2. 실행법

환경: 기존 `/Users/ma/SKAI/.venv` (Python 3.14). 추가 설치: `fastapi`, `uvicorn[standard]`, `pytest`.
(`.venv312`는 OSDK 전용 예약이라 건드리지 않음.)

### 개발 기동/중지
```bash
scripts/run_p1.sh start     # 서버(지도) + 폴러(항적 수집, 기본 4사이클 후 자동 종료)
scripts/run_p1.sh status    # 실행 상태
scripts/run_p1.sh stop      # 둘 다 중지
```
- 지도: **http://localhost:8000**
- 환경변수: `POLL_INTERVAL`(기본 15초) · `MAX_CYCLES`(기본 4, **0=무한**은 명시적 opt-in) · `SKAI_PORT`(기본 8000).
- **러너웨이 방지**: 폴러는 기본 4사이클 후 자동 종료. 연속 수집을 원할 때만 `MAX_CYCLES=0`으로 켜고 반드시 `stop`으로 중지.

### 개별 실행 (검증에 사용한 방식)
```bash
MAX_CYCLES=3 POLL_INTERVAL=15 .venv/bin/python -m connectors.opensky   # 폴러 3사이클 후 자동 종료
.venv/bin/python -m server.app                                          # 서버 (Ctrl-C로 종료)
.venv/bin/python -m pytest tests/test_p1.py -v                          # 테스트
```

## 3. 검증 결과

### 성공기준 4항목
| # | 기준 | 결과 |
|---|---|---|
| 1 | 단위 테스트 (provenance 거부 + Track gap + Event→객체 매핑) | **OK** — 14/14 통과 (0.04s) |
| 2 | 라이브 폴러 3사이클 → SQLite에 실데이터 | **OK** — Aircraft 32 · Observation 71 · Track 32 · Region 1 · Link 142 |
| 3 | `curl /api/observations` 실데이터 JSON | **OK** — 32건, 콜사인(SDM6647 등)·provenance(source_url) 포함 |
| 4 | 지도 렌더 스크린샷 | **OK** — `docs/worklog/p1_map.png` (항적 32마커 + KADIZ 폴리곤) |

### 객체 카운트 (폴러 3사이클, interval 15s)
- **Aircraft 32 · Observation 71 · Track 32 · Region 1 · Link 142**
- Observation dedup 작동: 3사이클 × ~32 = 95건이지만 (icao24, ts) 자연키로 71건만 저장(같은 last_contact 중복 제거).
- Link 142 = observed_as(Aircraft→Observation) 71 + composed_of(Track→Observation) 71.
- Track 32개 중 **20개가 2점 이상**(폴리라인 렌더 가능), has_gap=0 (45초 수집창엔 90초 초과 공백 없음 — 정상).

### provenance 강제 (핵심 — 환각방지 백본 선행 구현)
- `store.write_observation`이 `validate_provenance`로 source·source_url·ts 누락 write를 **ProvenanceError로 거부**.
- 테스트 증명: `test_store_rejects_write_without_provenance`(거부 후 저장 0건) + `test_provenance_missing_*` 3종.
- `LocalOntologyStore`가 `OntologyStore` Protocol을 만족(runtime `isinstance` True) → Foundry 교체 시 커넥터·API 무변경.

### OpenSky 크레딧
- 익명 크레딧 `x-rate-limit-remaining`: 396 → 392 (3사이클 = bbox 조회당 2 credit 소모 확인). 최소 사용.

### 스크린샷
- **`docs/worklog/p1_map.png`** (1400×900). KADIZ 폴리곤(파란 점선) 위에 항공기 32개(노란 점, 콜사인 툴팁), 우상단 패널(카운트·범례·갱신시각).
- headless Brave `--headless=new --virtual-time-budget=9000`로 캡처. CARTO 다크 타일 + Leaflet CDN 로드됨.

## 4. 루트 기획문서와의 정합 (어긋남 아님 — 기록만)

- architecture.md §5·PROMPTS.md P1은 프론트가 "OSDK로 read"라 명시하지만, Foundry BLOCKED 상태라 **FastAPI가 store_local을 read**하는 방식으로 대체. → **DR-0003이 승인한 편차**(저장 어댑터 뒤 SQLite 보험). Foundry 도착 시 store_foundry 교체로 원설계 복귀. 온톨로지 객체·링크 정의는 ontology.md를 그대로 따름(어긋남 없음).
- 루트 기획문서(ontology.md 등) **무수정**.

## 5. P2에 넘길 이슈 / 발견사항

1. **비상 스쿽 훅 이미 심어둠**: `web/index.html`이 squawk ∈ {7500,7600,7700}이면 마커를 빨강 처리(P2 미리보기). P2는 이걸 룰(`anomaly/rules.py`)로 승격 → `CreateAnomaly` Action + evidence 링크로 정식화.
2. **provenance 강제 재사용**: P2 `CreateAnomaly`의 "evidence 없으면 거부"는 이미 만든 `validate_provenance` 패턴을 그대로 Anomaly에 적용하면 됨(store에 `validate_evidence(anomaly)` 추가).
3. **라이브 비상 스쿽 희소**: 실 KADIZ에 7700이 상시로 뜨지 않음 → PROMPTS.md P2대로 **합성 스쿽 주입기** 필수. 주입기는 커넥터를 우회해 store에 직접 Observation(source="synthetic", source_url 명시)을 write하는 형태 권장(provenance 강제는 그대로 통과해야 함 = 합성도 출처를 남김).
4. **is_military 미결**: OpenSky 익명은 군용기를 필터링하는 경우가 많아 P1에서 `is_military=False` 고정. P5 "군용기 접근" 룰 전에 교차소스(adsb.fi/airplanes.live) 또는 icao24 대역 룩업 필요.
5. **Track 시각화 개선 여지**: 짧은 수집창(45초)에선 폴리라인이 마커에 가려 줌6에서 거의 안 보임. 데모 시 수집 사이클을 늘리거나(`MAX_CYCLES` ↑) 초기 줌을 높이면 경로가 드러남. gap 시각화(빨강 점선)는 코드에 준비됨 — dropout 시나리오 주입 시 확인 가능.
6. **link 테이블 조회 미노출**: observed_as/composed_of 관계를 저장은 하지만 API로는 안 뱉음(P1 지도는 track.path로 충분). P4 온톨로지 그래프 뷰에서 link 조회 엔드포인트 필요.

## 6. 되돌리기
- 신규 디렉터리(`ontology/` `connectors/` `server/` `web/` `tests/`) + `scripts/run_p1.sh` + `data/` 삭제.
- `.gitignore`에 `data/`·`*.db` 추가(런타임 산출물 커밋 방지). 루트 문서 무변경.
- 설치 패키지(fastapi/uvicorn/pytest)는 `.venv`에만 추가(`.venv312` 무영향).
