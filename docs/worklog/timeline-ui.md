# timeline-ui.md — 이상징후 타임라인 시간창·해소(resolved) 표시 + 트랙 경로 정리

- 날짜: 2026-07-06
- 담당: 실행 에이전트(sonnet). 대상 파일: `web/index.html` **만**(팀리드 지시 — anomaly/·connectors/·ontology/·server/ 비접촉, :8000 비접근).
- 배경: (1) 백엔드 에이전트가 병행 작업 중인 신규 상태값 `resolved`(dropout 주장이 기체 복귀 관측으로 반증되어 시스템이 자동 해소, `attrs.resolution = {kind:"return_observed", obs_id, resolved_at}`)를 프론트가 깨지지 않게 수용 + 타임라인 시간창 UX 정리. (2) 사용자 추가 요구 — 라이브에서 188기+ 트랙 폴리라인이 전부 그려져 지도가 뒤덮이는 문제 정리.

---

## 1. 변경 요지

1. **상태 어휘**: `STATUS_LABEL.resolved = "해소(복귀 확인)"`, `STATUS_COLOR.resolved = "#5c8a72"`(차분한 회녹색 — confirmed 빨강·dismissed 회색과 구분). 배지 `.b-resolved`, 타임라인 아이템 `.anom-item.st-resolved`(테두리색만 구분, 취소선은 안 씀 — dismissed=사람이 틀렸다고 판단, resolved=시스템이 반증돼 스스로 닫음, 의미가 다름) 추가. 지도 마커(`drawAnomalies`)도 resolved를 회녹색·낮은 fillOpacity(0.4)로 dismissed와 별도 처리(기존 3항 연산자를 깨지 않게 확장).
2. **타임라인 기본 시간창**: `TIMELINE_WINDOW_S = 6*3600`(상수, 주석에 근거 — 장시간 운용 시 기각/해소 이력이 쌓여 목록이 뒤덮이는 것 방지). 창 밖 **dismissed**는 기본 숨김. candidate/confirmed는 시간창과 무관하게 항상 표시(미결·확정 누락 방지 — 요구사항 그대로). 헤더(`.panel-hd` 안 `#tl-toggle`)에 "이전 N건 더 보기" 토글 — 클릭 시 `showAllTimeline` 플립 후 재렌더, 문구도 "▴ 최근 6시간만 보기"로 전환.
3. **해소건 접기**: resolved는 시간창과 별개로 **항상** 접힌 그룹(`<details class="cluster">"해소됨 N건"`)으로 — 기존 상관관계 더보기(`corrItemHtml`)와 동일한 `.cluster`/`.cbdy` 패턴 재사용(신규 CSS 없이). 메인 목록에는 후보/확정/기각(창 안)만.
4. **상세 패널**: `renderDetail`에 `resolutionHtml` 블록 추가 — "기체 복귀가 관측되어 자동 해소됨"(+ resolved_at 있으면 시각) 문구, `attrs.resolution.obs_id`가 있으면 `evidence`/`evidence_objects`에서 id 매치를 찾아 **기존 근거 카드(.ev) 마크업을 그대로 재사용**해 복귀 관측 링크 표시. 매치 실패 시(백엔드가 아직 evidence 링크에 안 넣은 경우) obs_id만 표시하는 폴백(추측 필드 생성 안 함 — CLAUDE.md provenance 원칙). confirm/dismiss 버튼 비활성화는 기존 `done = a.status !== "candidate"` 로직이 resolved도 자동으로 커버해 코드 변경 불필요(확인만 함).
5. 리팩터: 타임라인 아이템 DOM 생성 로직을 `buildAnomItem(a)`로 추출 — 메인 목록·해소됨 그룹 양쪽에서 재사용(중복 제거).

## 1b. 트랙 경로 화면 가림 정리 (추가 요구)

- **기본값 전환**: `drawTracks`가 non-gap 트랙 폴리라인을 기본 숨김으로 변경(`showAllTracks=false`가 기본). 항공기 마커·잔상(dead-reckoning)만 상시 표시.
- **gap 트랙은 예외 — 항상 표시**: `t.has_gap`인 트랙(빨강 점선)은 시각화 정책과 무관하게 계속 그려짐. 팀리드 지시대로 "gap 트랙은 이상징후 서사의 일부"라 replay 스토리보드와의 정합을 위해 숨기지 않음.
- **선택 시 풀 경로**: 신규 상태 `selectedTrackIcao` — (a) 항공기 마커 클릭(`selectAircraftTrack`, 신규 — 재클릭 시 해제 토글) 또는 (b) 이상징후 선택(`selectAnomaly`가 `a.involves[0].icao24`를 지정, 없으면 해제)으로 채워지며, 해당 기체의 트랙만 파랑(#6fb4ff) 강조로 그려짐. 기존 근거 흐름선(`drawEvidenceFlow`)·gap 트랙 강조는 변경 없이 그대로 유지.
- **"전체 트랙" 토글 추가**: `#tog-tracks`(기본 미체크) — 기존 `tog-ac`/`tog-sat`/`tog-trail`과 동일한 체크박스+change 리스너 패턴. 켜면 종전처럼 전체 트랙을 상시 표시.
- **통계는 표시 여부와 무관**: `n-track`/`n-gap` 카운터는 숨김 여부와 상관없이 항상 전수 집계(기존 동작 보존).
- **범례 문구 갱신**: "트랙 경로" 항목에 "(선택 기체만 · '전체 트랙'으로 상시 표시)", "ADS-B gap 트랙" 항목에 "(이상징후 서사 — 항상 표시)" 추가.

## 2. 검증 방법

- **문법**: `<script>` 블록을 추출해 `node --check` — 통과.
- **정적 렌더 확인** (무리한 E2E 대신 지시된 범위): `renderTimeline`/`renderDetail`/`buildAnomItem`/`toggleTimelineWindow` 함수 원문을 `index.html`에서 그대로 발췌해 Node에 로드하고, 최소 fake DOM(`getElementById`/`createElement`/`innerHTML`/`querySelector(".cbdy")`)을 붙인 하니스로 실행. 모의 API 응답(resolved+evidence 매치, resolved+매치 실패, candidate/confirmed/dismissed 최근·오래됨 혼합, 0건)에 대해 23개 어서션 전부 통과:
  - candidate·confirmed는 8시간 전이어도 메인 목록에 항상 노출, dismissed(10시간 전)는 기본 숨김·토글 ON 시 노출.
  - resolved는 메인 목록에서 제외되고 "해소됨 N건" 그룹에만 존재.
  - 상세 패널 resolved 문구 + evidence 재사용 링크 렌더, confirm/dismiss 버튼 disabled 확인.
  - obs_id 매치 실패 시 크래시 없이 obs_id만 표시하는 폴백 확인.
- **`drawTracks` 가시성 로직**: 함수 원문(index.html)을 그대로 발췌해 fake `L.polyline`/`document` 스텁으로 실행하는 별도 하니스. 7개 어서션 통과 — 기본 상태에서 non-gap 숨김·gap만 표시, 선택 기체는 non-gap이어도 강조색으로 표시, "전체 트랙" on 시 전부 표시, `showAc=false`면 전부 숨김, 1점짜리 경로는 기존처럼 스킵.
- 서버(:8000) 접근·실행은 하지 않음(라이브 운영 중, 백엔드 미완성 가능성 — 지시대로 회피).

## 3. 한계

- 지도 마커(Leaflet `L.circleMarker`) 쪽 resolved 스타일링, 항공기 마커 클릭(`selectAircraftTrack`) 바인딩은 코드 리뷰로만 확인(Leaflet 의존이라 Node 하니스로는 렌더 불가 — jsdom 미설치, 신규 설치는 범위 밖으로 판단해 생략). `drawTracks` 자체의 가시성 로직은 위 하니스로 검증했지만, 실제 Leaflet 지도 위에서 클릭→강조 전환이 눈으로 보이는지는 브라우저 실기 확인 전까지 미검증.
- `attrs.resolution.obs_id`가 `evidence`/`evidence_objects` 중 어디에 실릴지는 백엔드 계약에 명시되지 않아 양쪽 다 탐색하는 방어적 구현으로 대응. 실제 배포 후 백엔드 응답 스키마가 확정되면(어느 배열에 들어있는지) 이 부분 재검증 필요.
- 트랙 선택 강조는 이상징후당 `involves[0]` 1기만 지정(다수 기체 관련 이상징후는 첫 기체만 강조) — 현재 이상징후 유형들이 대부분 단일 기체 기준이라 실질적 제약은 낮다고 판단.
- 브라우저 실기 확인(스크린샷)은 하지 않음 — :8000 라이브 서버 비접근 지시에 따름.
