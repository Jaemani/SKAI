# OSDK 타입드 read 전환 (DR-0012 #4, EVALUATION OVERSOLD#3 종결)

- 날짜: 2026-07-04 (opus 서브에이전트)
- 대상: `ontology/store_foundry.py` read 경로 — 저수준 `foundry_sdk`(dict) → 생성 OSDK `skai_osdk_sdk` 0.9.0 타입드 클래스.
- 범위 제약: write 경로 무변경 · 기본 로컬/replay 불변 · Foundry write 0 · `anomaly/explainer.py` 미접촉(동시 작업).

## 판정: TYPED-OK (외부 read API 전면 전환) + 잔여 저수준 = write-내부 read-back(사유 명시)

## 1. 0.9.0 실측 (사유 소멸 확인)

P7 §4/§11 당시 "read=저수준" 선택 사유는 **발행 OSDK가 stale(Observation 객체 없음)**. 0.9.0
introspection(라이브)으로 그 전제가 소멸했음을 실측:

- **11 Object Type + 36 Action + Logic 함수** 발행됨(`skai_osdk_sdk/ontology/objects/` 11개 디렉터리).
- read 8종 전부 타입드 클래스 존재. ObjectSet API: `client.ontology.objects.<Type>.get(pk)`(단건 or None)·
  `.iterate()`(전량 지연)·`.take(n)`·`.count().compute()`(서버측 집계, float)·`.where(...)`.
- 속성은 **snake_case 타입드 어트리뷰트**로 노출(예: `is_military`·`aircraft_icao24`·`on_ground`).
- **§17 신설 속성 전부 타입드 노출 확인**(핵심 검증 포인트):
  | 속성(API) | OSDK 타입드 속성 | 객체 |
  |---|---|---|
  | attrsJson | `attrs_json` | Observation |
  | groundTrackJson | `ground_track_json` | OrbitPass |
  | station | `station` | WeatherState |
  | sentencesJson | `sentences_json` | SituationAssessment(write측) |
- **타입드 링크 accessor 존재**: `Anomaly.observations()`·`Aircraft.observations()/operator()` 등
  (search-around 기반 ObjectSet 반환).
- **함정 2건 실측**:
  1. `objectType`은 OSDK가 `object_type_`(**뒤 밑줄**)로 리네임 — `object_type`이 ObjectSet 클래스
     attr과 충돌하기 때문. Satellite read에서 반드시 `o.object_type_` 사용.
  2. timestamp 속성(`ts`·`start_ts`·`tle_epoch`)은 OSDK가 **`datetime`으로 반환**(저수준 dict는 ISO
     문자열이었음). 기존 `_iso_to_unix`가 datetime을 이미 처리하므로 read는 무변경 통과. 단
     `tle_epoch`는 표시용 ISO 문자열이라 datetime이면 `isoformat()`으로 정규화(구 str 동작 보존).

## 2. 전환 범위

### 전환된 read (전부 OSDK 타입드 경유)
`_osdk_iter(type)`(=`objects.<Type>.iterate()`) + `_osdk_get(type, pk)`(=`.get(pk)`) 헬퍼 신설,
`_dict_to_*`(8종) → `_obj_to_*`(dict.get → 타입드 어트리뷰트)로 재작성 후 아래를 재배선:

- `query_aircraft` · `aircraft_map`
- `query_all_observations` · `query_observations_for` · `query_latest_observations` · `get_observation`
- `query_operators`
- `query_satellites` · `satellite_map`
- `query_orbitpasses`
- `query_tracks`
- `query_weather_latest`
- `query_news`
- `counts` → **OSDK `.count().compute()` 집계**(서버측 count, float→int; 구 "전량 list 후 len" 대체)
- `_traverse` → **OSDK 타입드 링크 accessor**(`Anomaly.observations()`; 구 저수준
  `LinkedObject.list_linked_objects` 대체). 유일 호출은 `_anomaly_written_ok`의 evidenced_by read-back.

기존 폴백·변환 로직 **전부 보존**: attrs_json/ground_track_json 부재 시 `{}`/`[]`, station 부재 시
weatherId PK 복원(`_station_from_weather_id`), ceilingFt 99999 sentinel→None, wind 문자열 파싱,
aircraft_ref/region_ref None→"" 등.

### 잔여 저수준 read (전환 안 함 — 사유)
`_list_objects`/`_get_object`(저수준 `OntologyObject.list/get`, dict 반환)는 **write-내부 read-back
전용**으로만 존치. write 경로 무변경(DR-0012 범위 밖)이라 이들과 얽힌 read는 그대로 둔다:

- `_set_observation_track` — edit-observation의 required 필드(현재 Foundry 값)를 되읽어 재공급(composed_of write).
- `delete_future_orbitpasses_for` — 삭제 대상 OrbitPass 스캔(정리 write).
- `_anomaly_written_ok` — Anomaly 객체 존재확인(§12 방어 read-back; 그 안의 링크 traverse만 OSDK로 전환).

이는 "무리한 전면 전환 금지" 지침에 따른 **의도적 경계**지 커버리지 갭이 아니다. OVERSOLD#3이 말하는
"read"는 앱·데모·코파일럿이 쓰는 **외부 read API**(query_*·counts)이며, 그건 전면 OSDK 타입드가 됐다.

## 3. 실익 / 리스크 판정

**얻는 것**
- 발표 문구 사실화: "OSDK로 타입드하게 읽습니다"가 이제 참(OVERSOLD#3 종결).
- 타입 안전: 속성 오타/누락이 `d.get("오타")`→None으로 조용히 새지 않고 어트리뷰트로 드러남.
  §17 신설 속성이 스키마 계약(생성 클래스)에 박혀 있어 read/write 정합이 코드로 강제됨.
- counts가 서버측 집계로 전환(전량 materialize 불요).

**잃는 것 / 리스크**
- **재발행 의존**: 스키마가 바뀌면 OSDK 재발행 없이는 새 속성을 못 읽음(저수준 dict는 라이브 스키마를
  재발행 없이 읽었음 — 이게 P7의 원래 저수준 선택 이유였다). 완화: write는 여전히 저수준 Action.apply라
  액션 파라미터 변화에는 재발행 없이 대응 가능. read만 재발행에 묶임.
- OSDK 클라이언트가 내부적으로 저수준 클라를 한 번 더 감싸므로 foundry 모드에서 클라 2개 생성(write=`_pf`,
  read=`_osdk`). foundry 모드 opt-in 경로에서만이라 무해.

판정: 이 프로젝트는 D4D(Palantir 심사)라 "생성 OSDK 실사용"의 시연 가치 > 재발행 의존 비용. TYPED-OK.

## 4. 검증

- **단위** (메인 `.venv` 3.14, SDK 미설치 — lazy import 게이트 검증 포함): `FakeOsdk` 주입 5개 신규 테스트
  (타입드 read·신규속성·station PK 폴백·counts 집계·traverse 링크 accessor) + mock 헬퍼에 `skai_osdk_sdk`
  추가. **전체 스위트 252 passed / 3 skipped**(기존 247 → +5, 회귀 0).
- **라이브** (`.venv312` + .env, read-only):
  - `@live` 유닛 2건(query_aircraft·counts) OSDK 경로로 통과.
  - `scripts/verify_foundry_read.py` → **[VERIFY-FOUNDRY-READ OK]**. counts(OSDK 집계) aircraft=15·
    observation=17, /api/observations 12건(query_latest_observations 경유), /api/assess cites 6/6 전부
    Foundry Observation id 인용. 로컬 대조 db=0으로 Foundry발 확정.
  - 8종 query_* + get_observation(타입드 Observation) + traverse(anomaly 0 → [] 무오류) 라이브 무오류
    (현재 Foundry는 aircraft 15·observation 17, 나머지 0인 sparse 상태 — read 경로는 빈 타입도 정상).
- **replay 결정성**: 순수 로컬(SKAI_STORE 미설정) 테스트 통과. 전체 스위트가 SDK 미설치 .venv에서 252
  통과 = 로컬 경로가 foundry_sdk/skai_osdk_sdk를 import하지 않음을 입증(했다면 ImportError).
- **Foundry write 0**: read 전환 코드는 iterate/get/count/링크 accessor(전부 read)만. verify 로그의
  `create-situation-assessment ... 이미 존재 skip`은 verify 스크립트가 호출하는 `assess()` 코파일럿의
  **기존 dual-write**(내 변경과 무관)이며 dedup skip이라 Foundry 순증 0.

## 5. EVALUATION.md 갱신 제안 (Fable 적용 — 소유 분리)

OVERSOLD#3이 사실 전환됐으므로 아래 갱신을 제안:

- **§5 OSDK 행**: `부분(PARTIAL) … 실제 read는 저수준 foundry_sdk(dict)` →
  `실사용(MET) … read=생성 OSDK 0.9.0 타입드 클래스(query_*·counts·traverse), write=액션 apply(저수준).
  잔여 저수준 read는 write-내부 read-back 전용` (근거: 이 worklog).
- **OVERSOLD#3**: `[교정됨: 문서 표기]` → `[종결: read를 OSDK 타입드로 실제 전환, osdk-typed-read.md]`.
- **§"하면 안 될 말"**: `❌ "OSDK로 타입드하게 읽습니다"` 삭제(이제 사실). "할 말"에 "온톨로지 객체를
  생성 OSDK 타입드 클래스로 읽고, evidence는 액션 게이트로 강제" 추가 가능.
- **완성도 % (Foundry 이관)**: "read가 OSDK 아닌 저수준 SDK" 미달분 제거 → 상향 여지.

주의: 여전히 **read 권위본은 로컬**(dual-write 스파인은 Foundry). "OSDK 타입드 read"와 "read 권위가
Foundry"는 별개 축 — OVERSOLD#3(전자)만 종결, GAP "read 권위 로컬"(DR-0012 #2, 후자)은 별개로 유지.
