# 화면 정직성·설명 레이어 (DR-0013 #6·#9)

- 날짜: 2026-07-05
- 담당: 실행 에이전트(sonnet). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 대상: `web/index.html`, `copilot/assessment.py`. `server/app.py`는 **무수정** — 필요한 필드
  (`attrs.is_synthetic`, `correlations[].reason`, `/api/live`의 `source_last_poll`)가 이미
  내려오고 있어 프론트 소비만 추가하면 됐다.
- 범위 밖(다른 에이전트 병행 작업이라 손대지 않음): `connectors/`, `anomaly/`, `scripts/demo.sh`.

## 무엇을 바꿨나

### 1. 합성 라벨 전면 전파 (결정 #6)
- **지도 마커**(`drawAnomalies`): `is_synthetic`이면 원형 마커 테두리를 점선(`dashArray`)으로 —
  실데이터 마커와 시각적으로 구분. 툴팁에도 `[합성]` 표시. 범례에 항목 추가.
- **cite 배지**(`citeChip`): `_resolve_object`가 Anomaly에 `is_synthetic`을 새로 반환하도록
  `copilot/assessment.py`를 수정 → 배지에 `[합성]` 접두 + 점선 테두리(`.cite-chip.synth`) +
  title 툴팁("합성 시나리오 데이터(실측 아님)").
- **서브그래프 노드**(`renderSubgraph`): `build_subgraph`가 `_resolve_object`를 그대로 재사용하므로
  Anomaly 노드는 별도 배선 없이 `is_synthetic`을 상속. 점선 테두리(`.sg-node.synth circle`) +
  범례 항목 + 노드 클릭 상세에 "합성" 배지.
- **요약 헤드라인**(`_headline_sentence`): 이상징후 중 합성 건수를 세어
  `"이상징후 N건(합성 M건 포함)·항적 K대를 확인했습니다."` — cites 매핑·문장 조립 불변식은
  그대로(카운트는 이미 읽은 Fact에서 나온 사실이므로 무근거 아님).

### 2. 서브그래프 설명 레이어 (결정 #9)
- 모달 상단에 고정 안내 줄 추가(`#sg-intro`): "중심 = 이번 상황평가 … 모든 주장이 관측/출처까지
  역추적됩니다."
- 엣지 라벨 한국어 병기(`EDGE_LABEL_KO`) — `aggregates→포함`, `cites→인용`,
  `evidenced_by→근거`, `involves→관련 기체`, `correlated_with→교차 상관`. 원어는 SVG `<title>`
  자식 엘리먼트로 보존(hover 시 브라우저 네이티브 툴팁).
- **Anomaly cite의 `source_url` 공백 수정**: `copilot/assessment.py`에 `_first_evidence_source_url`
  추가 — Anomaly 자신은 provenance가 없는 파생 객체라 항상 `""`였던 것을, evidenced_by 근거 중
  첫 건(Observation 등)의 `source_url`로 대체. Anomaly는 근거를 참조할 뿐 근거 자체가 아니므로
  재귀는 1단만 내려간다(무한루프 없음).
- **Aircraft 노드 좌표 보강**: `build_subgraph`가 `store.query_latest_observations()`로
  항공기별 최신 좌표 맵을 만들어 Aircraft 노드에 `lat`/`lon`을 채운다(이전엔 항상 `None`이라
  "지도에서 보기" 버튼이 절대 뜨지 않았음).
- **"지도에서 보기" 버그 수정**: 기존 버튼은 `highlightCite(id)`를 호출했는데, 이 함수는
  `lastAssessment.cited_objects[id]`만 조회한다 — Aircraft 노드(`ac-{icao}`)는 문장 cites에
  없으므로 여기 없고, 버튼이 조용히 no-op였다. `focusMapPoint(obj)`를 추출해 지도 이동 로직을
  공용화하고, 서브그래프 쪽은 `sgNodesById`(렌더된 노드 전체 캐시) + `sgFocusNode(id)`로
  cited_objects를 거치지 않고 노드 자신의 좌표를 바로 쓰도록 분리. `highlightCite`는
  `focusMapPoint`의 얇은 래퍼로 남아 기존 cite-chip 동작은 그대로.

### 3. 소스 패널 설명·신선도
- 4개 소스 행(OpenSky/Celestrak/METAR/GDELT)에 `title` 툴팁으로 1줄 설명 추가.
- `/api/live`의 `source_last_poll`(키: `opensky`/`celestrak`/`metar`/`gdelt`,
  `connectors/opensky.py`의 `DEFAULT_LIVE_SOURCES`와 일치 확인)을 프론트가 처음으로 소비 —
  `drawSourceFreshness()`가 각 소스 옆에 "N초/분/시간 전" 배지를 그린다. **replay(폴러 없음)에서는
  표시하지 않음** — 고정 스냅샷에 "방금 갱신"은 오도이므로 `liveState.last_poll_ts == null`일 때
  공백 처리(실측: replay 서버의 `/api/live`는 `last_poll_ts: null`이라 배지가 뜨지 않음 — 의도대로).

### 4. 🔗 배지·상관 사유
- 타임라인 배지에 `title="교차소스 상관 N건 — 클릭해 상세"`.
- 상세 패널 상관 리스트를 `correlations[].reason`(Phase A가 이미 영속) 기반 문장으로:
  `anomaly_orbitpass`→"위성 통과와 ±N분, 같은 구역(R) 상공(최대앙각 E°)",
  `anomaly_news`→"뉴스와 ±N분, 공유 지역 R", `anomaly_anomaly`→"이상징후와 시간차 N분 · 거리 D km".
  `reason`이 `None`(마이그레이션 전 구링크)이면 타입·라벨만 표시하던 기존 동작으로 그대로 폴백.
  4건 이상이면 상위 3건 + `<details>` 접기(`.cluster` 재사용).

## 검증
- **전체 스위트**: `.venv/bin/python -m pytest -q` → **346 passed, 4 skipped**(작업 전과 동일 —
  회귀 0). 이 저장소에 프론트(index.html)를 대상으로 한 JS 테스트는 없다 — 문법 검증은 Node의
  `new Function()`으로 스크립트 블록 파싱만 확인(런타임 DOM 동작은 스모크로 별도 확인).
- **스모크(replay)**: `scripts/demo.sh replay` 기동 후 `/api/assess`·`/api/subgraph`·`/api/anomalies`·
  `/api/live`를 직접 호출해 신규 필드 확인.
  - 헤드라인: `"최근 30분(지금) 한국 방공식별구역 (KADIZ)에서 이상징후 13건(합성 13건 포함)·항적
    16대를 확인했습니다."`
  - `cited_objects`의 Anomaly 항목: `is_synthetic=True`, `source_url`이 실제
    `synthetic://…` 값으로 채워짐(이전엔 `""`).
  - `/api/subgraph`의 Aircraft 노드: `lat`/`lon`이 채워짐(이전엔 `None`).
  - `/api/live`(replay): `live=False, last_poll_ts=None` — 소스 신선도 배지가 뜨지 않아야 하는
    조건과 일치.
  - `/api/anomalies`의 `correlations[0].reason` 실측: `{'kind': 'anomaly_anomaly', 'gap_s': 0,
    'distance_km': 142.7}` — 프론트 `corrReasonText`가 그대로 소비 가능한 형태.
  - **재현성**: 서버 재기동(`stop` → `replay`) 후 동일 질의 재실행 → `summary`·`assessment_id`
    바이트 동일 확인(헤드라인에 합성 카운트를 추가한 변경이 replay 결정성을 깨지 않음).
  - 스모크 종료 후 `scripts/demo.sh stop`으로 정리.

## 한계
- 소스 신선도 배지는 **실 라이브 모드에서 미검증**(replay는 폴러가 없어 `source_last_poll`이
  항상 `None` — "표시 안 함" 분기만 실측 확인, "N초 전" 표시 자체는 라이브 기동 없이는 못 봤다).
  코드 경로는 `/api/live` 응답 스키마(`server/app.py:_live_view`)를 그대로 따르므로 라이브에서
  키가 존재하면 동작할 것으로 보이나 화면 스크린샷 확인은 아직 없음.
- `_first_evidence_source_url`은 **evidenced_by 링크 순서**(store가 반환하는 첫 건)를 그대로
  쓴다 — "가장 대표적인" 근거를 고르는 로직이 아니라 "저장 순서상 첫 건"이다. 위성 근접처럼
  근거가 OrbitPass 1건뿐인 유형은 문제 없지만, 근거가 여러 건이고 개중 신뢰도·시의성이 다른
  유형에서는 반드시 "가장 좋은" 근거가 걸리는 건 아니다.
- 소스 패널 설명 문구(OpenSky/Celestrak/METAR/GDELT)는 팀 지시 문구를 그대로 title 속성에
  넣은 것이라 새로 검증할 사실 주장은 없다.
- 상관 사유 문장의 "구역(region)"은 Region.id(예: `KADIZ`)를 그대로 노출한다 — 사람이 읽는
  지역명(`Region.name`)으로 바꾸려면 프론트가 별도 지역 맵을 받아야 하는데, 현재
  `correlations[].reason.region`은 id만 담고 있어 이번 범위에서는 id 그대로 뒀다(오도는 아니지만
  더 읽기 좋게 다듬을 여지는 있음).
- 온보딩 오버레이(`#onboard`) 문구는 이번 라운드에서 손대지 않았다 — "합성 라벨이 이제 지도·
  서브그래프에도 보인다"는 사실을 온보딩에 추가하면 더 좋겠지만 지시 범위(연결) 밖으로 판단해
  스킵했다.
