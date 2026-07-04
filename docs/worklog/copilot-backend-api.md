# 코파일럿 백엔드 API 계약 (DR-0011) — 프론트 인계 문서

이 문서 하나로 프론트가 렌더 가능하도록 `/api/assess`(의도분류+cites 강제 답변)와 LIVE
상태(`/api/live`·`/api/stats`) 계약을 못박는다. 백엔드 구현: `copilot/intent.py`(분류)·
`copilot/tools.py`(툴 read)·`copilot/assessment.py`(조립)·`server/app.py`(엔드포인트)·
`connectors/opensky.py`(연속 폴러)·`server/live_status.py`(LIVE 사이드카).

> **불변식(프론트가 신뢰해도 되는 것)**: 모든 문장은 근거 객체 id(`cites`)를 갖는다. cites
> 없는 문장은 서버가 애초에 만들지 않는다(무근거 주장 금지 — CLAUDE.md 원칙 4). 기본 모드는
> 결정적(같은 질의·같은 스냅샷 = 같은 답). LLM은 옵션이며 서술만 다듬고 cites는 불변.

---

## 1. POST `/api/assess` — 자연어 질의 → 상황평가

### 요청
```json
{ "query": "군용기 몇 대야?", "focus_id": "civ001" }
```
| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `query` | string | ✔ | 자연어 질의(한국어). 빈 문자열이면 400. |
| `focus_id` | string \| null | 선택 | 프론트가 지도/타임라인에서 **선택한 객체 id**. "이거 뭐야"류 지시어를 그 객체로 확정. 미전송이면 질의문에 박힌 id를 사용. |

`focus_id`에 넣는 값 = cited 객체 id와 동일 체계: `anomaly-...`, `pass-...`, `wx-...`,
`news-...`, 또는 관측 id `"{icao24}-{ts}"`, 또는 그냥 `icao24`.

### 응답 (200) — 공통 스키마
```jsonc
{
  "assessment_id": "assess-KADIZ-1783000000-ab12cd",  // no_evidence면 null
  "no_evidence": false,          // true면 근거 못 찾음(sentences=[], 아래 참고)
  "query": "군용기 몇 대야?",
  "intent": "count",             // 분류된 의도(§2)
  "slots": { "target": "military" },   // 의도별 파라미터(§2)
  "intent_meta": {               // 분류 투명성(파서의 matched처럼)
    "confidence": 0.9,           // 규칙 매칭 강도(0~1, UI 필수 아님)
    "matched": ["몇 대"],         // 걸린 키워드/ id
    "backend": "rule"            // "rule" | "rule(default)" | "claude" | "injected"
  },
  "region": { "id": "KADIZ", "name": "한국 방공식별구역 (KADIZ)" },
  "window": {                    // 파싱된 시간창(투명성 — 파서 그대로)
    "start": 1782998200, "end": 1783000000,
    "seconds": 1800, "label": "최근 30분(기본)",
    "start_iso": "2026-07-...Z", "end_iso": "2026-07-...Z",
    "matched_region_alias": null, "matched_window_phrase": null,
    "defaulted": ["region", "window"]   // 기본값으로 채운 항목
  },
  "summary": "…",                // 헤드라인(sentences[0].text와 동일)
  "sentences": [                 // 문장별 cites 강제 — 각 원소:
    {
      "text": "최근 30분(기본) …에서 군용 추정 항적 1대를 확인했습니다(근거 객체 1건 인용).",
      "cites": ["synthx-1783000000"],   // 근거 객체 id들(≥1, 불변)
      "confidence": 0.55,               // 이 문장 하나의 신뢰도(0~1)
      "kind": "summary"                 // 섹션/배지 분류(§3)
    }
  ],
  "confidence": 0.55,            // 종합 신뢰도(요약 문장 제외 평균)
  "produced_by": "template",     // "template" | "claude"(폴백 시 template 표기)
  "created_at": 1783000000,
  "counts": { "flights": 1, "anomalies": 1, "passes": 0, "weather": 0, "news": 0 },
  "cited_objects": {             // cites에 쓰인 모든 id → 표시 상세(배지·지도 하이라이트)
    "synthx-1783000000": {
      "type": "Observation", "label": "관측 TEST77 sq7700",
      "lat": 36.5, "lon": 127.0, "source_url": "synthetic://x"
    }
  }
}
```

### `no_evidence: true` (근거 못 찾음 — 정직 보고)
```jsonc
{
  "assessment_id": null, "no_evidence": true, "query": "…",
  "intent": "count", "slots": {...}, "intent_meta": {...},
  "region": {...}, "window": {...},
  "summary": "최근 30분 … 요청하신 근거 객체를 찾지 못했습니다(의도=count) — …",
  "sentences": [], "confidence": 0.0, "produced_by": "template",
  "created_at": 1783000000, "counts": {...}, "cited_objects": {}
}
```
> 프론트: `no_evidence`면 `summary`만 표시(문장/배지 없음). "0건"·"해당 없음" 상태.

### 렌더 가이드
- **배지/하이라이트**: 각 문장의 `cites`를 순회 → `cited_objects[id]`로 라벨·좌표·`source_url`
  획득. 클릭 시 지도의 그 좌표로 이동하거나 `source_url`(원 출처)로 이동.
- `cited_objects[id].type` ∈ `Observation | Anomaly | OrbitPass | WeatherState | NewsEvent`.
  Anomaly는 `status`(candidate/confirmed/dismissed)도 포함.
- **서브그래프**: `assessment_id`로 `GET /api/subgraph?assessment_id=…`(기존, 무변경).

---

## 2. 의도(intent)와 슬롯(slots)

규칙 1차 분류(결정적) + `SKAI_COPILOT_LLM=claude`일 때만 **모호 질의**를 claude로 재분류
(실패 시 규칙 유지). 우선순위: why → count → filter → entity_explain → correlation →
요약마커 → weather → news → (모호)situation_summary.

| intent | 트리거 예 | slots | 답변 형태(문장 kind) |
|---|---|---|---|
| `situation_summary` | "지금 KADIZ 상황", "…요약해줘", 마커 없는 질의 | `{}` | 헤드라인 + 이상징후 + 상관/위성 + 기상 + 뉴스(현행 전체) |
| `count` | "몇 대/몇 건/개수" | `{target: "flights"\|"anomalies"\|"passes"\|"news"\|"military"}` | 집계 1문장(근거 객체 인용) |
| `filter` | "군용기만", "미국 국적", "기종 …", "…소속" | `{military?: bool, origin_country?: str, operator?: str, aircraft_type?: str}` | 헤드라인 + 항적 상세 최대 8건(`kind:"flight"`) |
| `entity_explain` | "이 이상징후 뭐야", `focus_id` 지정, id 박힘 | `{entity_id: str\|null, entity_kind: "anomaly"\|"satellite"\|"weather"\|"news"\|"flight"\|null}` | 단건 설명 1문장(엔티티+provenance 인용) |
| `why` | "왜 이상해/위험해", "근거는" | `{entity_id: str\|null, entity_kind}` | 유형 서술 + "판단 근거:"(저장된 explanation) |
| `correlation` | "은닉 정황", "숨은 연관" | `{}` | correlated_with 상관 문장(`kind:"correlation"`) + 위성 맥락 |
| `weather` | "기상/날씨"(뉴스 언급 없이) | `{}` | 기상 문장만 |
| `news` | "뉴스/OSINT"(기상 언급 없이) | `{}` | 뉴스 요약 1문장 |

- **국적 필터**: `origin_country`는 OpenSky 영문 국가명(예 `"United States"`, `"South Korea"`).
  질의에 "국적" 키워드가 있어야 발동(지역 별칭 "한국"과 충돌 방지).
- **군용 판정은 저신뢰**: `Aircraft.is_military` 플래그 ∪ 콜사인·ICAO 대역 휴리스틱. 문장에
  "군용 추정"으로 표기(단정 아님). 군용 관련 문장 confidence는 낮다(≈0.5).
- **entity_explain/why의 명시 id가 없으면(지시어만)** 가장 두드러진 대상(최고신뢰 이상징후 등)을
  고른다. 명시 id가 **해소 안 되면** `no_evidence`(엉뚱한 객체로 대체하지 않음).

### 실제 응답 예시 3종

**① count** — `"KADIZ에 항적 몇 대야?"`
```jsonc
{ "intent": "count", "slots": {"target":"flights"},
  "intent_meta": {"confidence":0.9,"matched":["몇 대"],"backend":"rule"},
  "sentences": [{ "text":"최근 30분(기본) 한국 방공식별구역 (KADIZ)에서 항적 2대를 확인했습니다(근거 객체 2건 인용).",
    "cites":["civ001-1783000000","synthx-1783000000"], "confidence":0.9, "kind":"summary" }] }
```

**② filter(군용)** — `"군용기만 보여줘"`
```jsonc
{ "intent": "filter", "slots": {"military": true},
  "sentences": [
    {"text":"최근 30분(기본) … 군용 추정 조건에 맞는 항적 1대.","cites":["synthx-1783000000"],"confidence":0.85,"kind":"summary"},
    {"text":"TEST77 — United States, 군용 추정, 위치 36.50, 127.00.","cites":["synthx-1783000000"],"confidence":0.55,"kind":"flight"}
  ] }
```

**③ why** — `"이 이상징후 왜 위험해?"`
```jsonc
{ "intent": "why", "slots": {"entity_id": null, "entity_kind": "anomaly"},
  "sentences": [
    {"text":"왜 이상징후인가 — [합성] 항공기 TEST77가 비상 스쿽 7700(일반 비상)를 송신 — 상태 미검토, 신뢰도 0.93, 근거 관측 1건.",
     "cites":["anomaly-emergency_squawk-synthx-1","synthx-1783000000"],"confidence":0.93,"kind":"anomaly"},
    {"text":"판단 근거: 비상 스쿽 7700 송신.","cites":["anomaly-emergency_squawk-synthx-1","synthx-1783000000"],"confidence":0.93,"kind":"anomaly"}
  ] }
```

---

## 3. 문장 kind(섹션·배지 렌더)

`sentences[].kind` ∈ 아래. 프론트는 kind로 아이콘/색/섹션을 나눈다.

| kind | 의미 | 신규(DR-0011)? |
|---|---|---|
| `summary` | 헤드라인/집계 | 기존 |
| `anomaly` | 이상징후 서술(why 포함) | 기존 |
| `satellite` | 위성 통과 맥락 | 기존 |
| `correlation` | correlated_with 상관("은닉 정황") | 기존 |
| `weather` | 기상 | 기존 |
| `news` | OSINT 뉴스 | 기존 |
| `flight` | 항적 1건 상세(filter/entity) | **신규** |

---

## 4. LIVE 상태 (지속 폴링 인디케이터)

연속 폴러(`connectors/opensky.py`)가 사이클마다 `<db>.live.json` 사이드카에 상태를 기록한다.
서버가 이를 읽어 노출. **replay/정적 모드엔 폴러가 없어 `live:false`**.

### GET `/api/live`
```jsonc
{
  "live": true,                 // 프론트 LIVE 배지 on/off 판정(신선도 포함)
  "last_poll_ts": 1783159477,   // 마지막 폴링 Unix 시각(초). null이면 폴러 없음
  "mode": "live",               // "starting" | "live" | "stopped" | null
  "interval": 25,               // 폴링 간격(초)
  "cycle": 42,                  // 누적 사이클 수
  "last_poll_status": "ok",     // "ok" | "error" | "stopped"
  "last_cycle": { "obs": 101, "aircraft": 101, "new_anomalies": 0 },
  "server_now": 1783159480      // 서버 현재 시각 → 경과 = server_now - last_poll_ts
}
```
- **`live` 판정**: `mode=="live"` AND `last_poll_ts`가 `max(interval*3, 90)`초 이내.
  프론트는 `live`를 그대로 쓰거나, `server_now - last_poll_ts`로 "N초 전 갱신"을 표시.
- **자동 갱신 권장**: 프론트가 `interval`초마다 지도 레이어(`/api/observations` 등) + `/api/live`를
  폴링. `last_poll_ts`가 바뀌면 새 데이터 도착 → 마커/항적 갱신 + LIVE 점멸.

### GET `/api/stats` (하위호환 확장)
기존 카운트 dict(`{aircraft, observation, …}`)에 두 키만 추가:
```jsonc
{ "aircraft": 102, "observation": 177, "…": "…",
  "last_poll_ts": 1783159477, "live": true }
```
> 한 번의 폴링으로 카운트 + LIVE를 함께 읽는 용도. 상세 상태는 `/api/live`.

---

## 5. 환경변수

| 변수 | 기본 | 효과 |
|---|---|---|
| `SKAI_COPILOT_LLM` | (없음) | `claude`면 (a)모호 질의 의도 재분류 + (b)문장 서술 다듬기. **기본 off=결정적**. 실패·타임아웃 시 규칙/템플릿 폴백. |
| `SKAI_EXPLAINER` | `template` | (하위호환) `claude`면 서술 다듬기만. `SKAI_COPILOT_LLM`이 우선. |
| `SKAI_POLL_INTERVAL` | `25` | 연속 폴러 간격(초). **하한 10초**(그 미만은 10초로 상향 — 크레딧 안전). 하위호환 `POLL_INTERVAL`도 인식. |
| `MAX_CYCLES` | `0` | 폴러 사이클 수. `0`=무한(라이브 기본). 유한값=검증용. |
| `SKAI_NOW_ANCHOR` | (없음) | replay: '지금'을 스냅샷 시각에 고정(재현성). 라이브는 미설정. |
| `SKAI_DB` | `data/skai.db` | store DB 경로. 사이드카는 `<db>.live.json`. |

---

## 6. 결정성·안전 요약(프론트가 알아야 할 계약)

- **기본 결정적**: LLM 미설정 시 같은 질의·같은 `SKAI_NOW_ANCHOR`는 바이트 단위 동일 응답
  (replay 백본 — 검증됨). 새 의도 필드(`intent`/`slots`/`intent_meta`)도 결정적.
- **citation 강제**: 모든 문장 `cites`≥1. `no_evidence`가 아니면 반드시 근거가 붙는다.
- **LIVE는 폴러 있을 때만**: replay/정적 페이지는 `live:false`, `last_poll_ts:null`.
- **하위호환**: 기존 `/api/assess` 필드(sentences·cites·window·counts·cited_objects·subgraph)
  전부 유지. `intent`/`slots`/`intent_meta`와 `focus_id`(요청)만 추가.
- **지도 레이어 엔드포인트**(무변경): `/api/observations`·`/api/tracks`·`/api/regions`·
  `/api/anomalies`(+confirm/dismiss)·`/api/orbitpasses`·`/api/weather`·`/api/news`·`/api/counts`.
