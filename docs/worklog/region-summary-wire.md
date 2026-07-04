# AIP Logic region-situation-summary 배선 (AIP Logic 2종 완성)

- 날짜: 2026-07-04
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 목표: Foundry AIP Logic 함수 `region-situation-summary`(사용자 발행) + OSDK 0.10.0 재발행분을
  **실제로 배선**해, 코파일럿 상황요약(situation_summary)의 **요약(헤드라인) 서술**이 AIP Logic
  경로를 타게 한다. explain-anomaly(#1)와 짝 → 초기 계획(aip-integration.md §2(4))의 AIP Logic 2종 완성.

## 1. OSDK 0.10.0 함수 노출 형태 — **타입드 OSDK 쿼리** (실측)

재설치 불요(0.10.0 이미 `.venv312`에 설치됨). 재발행분에 `region-situation-summary`가 타입드 쿼리로 포함.

- 위치: `skai_osdk_sdk/ontology/query_types/region_situation_summary/`, 등록: `...QueryTypes.region_situation_summary`
- 호출: `client.ontology.queries.region_situation_summary(...)` (client = `skai_osdk_sdk.FoundryClient`)
- **api_name**: `regionSituationSummary` (파이썬 메서드 `region_situation_summary`)
- **입력 파라미터**(실측 시그니처, 전부 keyword-only):
  | OSDK 인자 | 타입 | 비고 |
  |---|---|---|
  | `region_name` | `str` | required |
  | `anomalies2` | **`AnomalyObjectSet`** | ⭐ **Object set(복수)**. Foundry 함수 입력명이 `anomalies2`(가이드의 `anomalies`가 아님 — 실측). explain-anomaly의 evidence가 `Union[str, Observation]`였던 것과 달리 **str 폴백 불가**: 반드시 Object set. |
  | `window_label` | `str` (OSDK상 optional, `Empty` 기본) | ⚠️ 아래 함정 참조 |
  | `weather_summary` | `str` (OSDK상 optional, `Empty` 기본) | ⚠️ 아래 함정 참조 |
- **출력**: `RegionSituationSummaryResponse` = **beta StructType**, 필드 `summary: str`,
  `overall_assessment: str`, `confidence: float`.
- ⚠️ **함정 1(explain-anomaly와 동일)**: 응답이 beta StructType라 그냥 부르면 `BetaWarning` 예외.
  반드시 `with AllowBetaFeatures(): ...` 안에서 호출.
- ⚠️ **함정 2(신규·실측)**: OSDK 시그니처는 `window_label`·`weather_summary`를 optional(Empty 기본)로
  노출하지만, **배포된 AIP Logic 함수가 프롬프트에서 두 파라미터를 참조**하므로 값이 **부재(Empty)**면
  런타임 `QueryRuntimeError`(`ValidationError.ReferenceHasNoValue`)로 실패한다. **빈 문자열 `""`은
  "값 있음"으로 통과**. → 코드가 None이어도 생략하지 않고 `""`로 넘긴다. (조합 실측: `both`·`both_empty_str`
  =OK / `no_weather`·`no_window`·`neither`=FAIL.)

## 2. AnomalyObjectSet 빌드 방법 (실측)

Anomaly PK = `anomaly_id`(str). id 목록으로 객체집합을 만드는 법:

```python
from skai_osdk_sdk.ontology.search import AnomalyObjectType
obj_set = client.ontology.objects.Anomaly.where(AnomalyObjectType.anomaly_id.in_(ids))
```

`in_(values: list)`는 각 값이 프로퍼티 타입(str)인지 검증. `.where()`는 `AnomalyObjectSet`을 반환 →
그대로 `anomalies2`로 넘긴다. **이게 해자**: AIP가 각 Anomaly의 실제 속성(type·status·confidence·
explanation·lat/lon)을 온톨로지 위에서 읽어 종합한다(단순 LLM 프롬프트 주입과의 차이).

## 3. 배선 (통합 지점 = situation_summary 헤드라인 **서술만**)

- 신규 `copilot/region_summary.py` — `AipRegionSummarizer.summarize(region_name, anomaly_ids,
  window_label, weather_summary) -> RegionSummaryResult | None`. client 주입 가능(테스트).
- `copilot/assessment.py` `assess()`에 백엔드 분기 추가:
  - `SKAI_COPILOT_LLM=aip`(우선) 또는 `SKAI_EXPLAINER=aip`일 때, **그리고** `intent=situation_summary`,
    **그리고** `current_backend()=="foundry"`일 때만 `_aip_region_summary(...)` 호출.
  - `_aip_region_summary`: 헤드라인(kind=summary) 문장이 있고 **이상징후가 있을 때만** AIP 호출
    (0건이면 스킵 — 프롬프트의 0건 규칙과 이중 안전 + 호출 절약). AIP `summary`로 헤드라인 **text만**
    교체하고 **cites·kind는 불변**(집계 Anomaly id 등 provenance 보존). confidence는 AIP 종합값
    (explainer aip 백엔드와 동일 규율). `overallAssessment`는 응답 메타(`overall_assessment` 필드)로
    노출 + assessment.attrs에 영속(프론트 후속용).
- **anomalies2 = Foundry 참조(해자)**: `reads.anomalies`의 로컬 anomaly id로 Foundry
  `Anomaly.where(anomaly_id.in_(ids))` 객체집합을 만들어 넘긴다. anomalies2가 Object set 전용이라
  str 근거 폴백이 없으므로, **Foundry 소재가 아닌 로컬 전용 모드에선 호출하지 않고 template 유지**
  (게이트 = `current_backend()=="foundry"`). 로컬 전용/비요약 의도 → `produced_by="template(aip 미적용)"`.
- 공용 헬퍼 추출(SSOT·최소): `anomaly/explainer.py`에 `make_foundry_osdk_client()`·`allow_beta_features()`
  모듈 함수를 신설하고 `AipLogicExplainer`가 이를 위임하도록 리팩터(동작 무변경, 기존 라이브 테스트 통과 확인).
  region summary도 같은 헬퍼 사용.

### citation 불변식 (절대 유지)
- 사실 확정·문장별 cites 조립은 **기존 그대로**(룰). AIP는 요약 **서술**(summary·overallAssessment)만 생성.
- 헤드라인 cites = 기존 규칙(집계 이상징후 cites + 대표 항적 표본) 그대로 **불변**. cites 없는 문장 거부 불변.
- AIP summary 텍스트의 주장(스쿽·위치·icao)은 cites의 Anomaly+근거 Observation으로 역추적 가능 → provenance 유지.

## 4. 라이브 검증 (실호출 성공)

### 4-1. summarizer 직접(실 데모 자산)
`SKAI_STORE=foundry` + 실 Foundry 데모 자산 `anomaly-emergency_squawk-skaidemo1783170072-2971950`
(confirmed, conf 0.95, squawk 7500)로 `region-situation-summary` 실호출. 출력:
```
summary: 최근 30분 한국 방공식별구역(KADIZ) 내에서 확인됨(confirmed) 상태의 비상 스쿽 1건이
         관측되었습니다. 해당 객체는 DEMO7500(icao24 skaidemo1783170072)로, 2026-07-04
         13:01:12 UTC에 위치 36.800, 124.200에서 스쿽 7500(불법 간섭/하이재킹)을 송신한
         것으로 설명되어 있습니다. … 개별 신뢰도는 0.95입니다.
overall_assessment: 주의 — 확인된 비상 스쿽 7500 1건 활성, 즉시 상황 확인 및 교차검증 권고
confidence: 0.95
```
→ 설명이 Foundry Anomaly **객체의 실제 속성**(squawk·status·position·confidence)을 근거로 생성됨을 확인
= 온톨로지 위 추론. 반복 호출 시 서술이 매번 미세하게 다름 = 진짜 LLM 추론(비결정적) → **기본 template
불변**이 정당(replay 결정성). aip는 명시 opt-in.

### 4-2. assess() 전체 경로(E2E)
데모 자산을 임시 로컬에 미러(관측·이상징후) + 실 Foundry 어댑터로 `HybridStore` 구성 → 
`assess(store, "지금 KADIZ 이상한거 있어?", explainer="aip")`:
```
produced_by : aip_logic
overall_assessment: 비상 — confirmed 비상 스쿽 7500 1건 확인, 즉시 상황 확인 및 교차검증 권고
summary(헤드라인): 최근 30분 내 KADIZ에서 confirmed 이상징후 1건이 확인되었습니다. 해당 건은
   항공기 DEMO7500(icao24 skaidemo1783170072)의 비상 스쿽 7500 송신으로, 불법 간섭(하이재킹)을
   의미하는 하드 신호입니다. 관측 시각은 2026-07-04 13:01:12 UTC이며 위치는 36.800, 124.200입니다. …
head.cites : ['anomaly-emergency_squawk-skaidemo1783170072-2971950', 'skaidemo1783170072-1783170072', …]  (template 모드와 **동일**)
```
같은 질의를 `explainer="template"`으로 돌리면 헤드라인은 `"…이상징후 1건·항적 4대를 확인했습니다."`
(produced_by=template, overall_assessment=None) — **cites 동일**, 서술만 교체됨을 확인.

> ⚠️ 데이터 상태 주의(배선 버그 아님): 현재 로컬 DB에는 데모 자산(skaidemo)이 없고 Foundry엔 그것 1건만
> 있어(둘이 disjoint), **자연 assess 흐름은 SKAI_STORE=foundry라도 skaidemo를 못 집는다**(query_anomalies가
> foundry 모드에서 로컬을 읽는 HybridStore 설계 때문). E2E는 데모를 임시 로컬에 미러해 검증했다(Foundry write
> 없음, read-only). 실제 데모에선 이상징후가 dual-write로 로컬+Foundry 양쪽에 존재해야 이 경로가 자연히 돈다.

## 5. 폴백 (DR-0004 패턴 — 데모 안전)
- 크리덴셜 미설정·네트워크·타임아웃·빈 summary → `summarize()`가 None → 헤드라인 template 유지
  (`produced_by="template(aip 폴백)"`).
- 로컬 전용 모드·비요약 의도 → AIP 미호출, template 유지(`"template(aip 미적용)"`).
- 이상징후 0건 → AIP 호출 없이 template(0건 규칙 이중 안전).
- **기본값 여전히 template**: SKAI_COPILOT_LLM/SKAI_EXPLAINER 미설정이면 기존 동작 그대로(replay 결정성).

## 6. 테스트
- 신규 `tests/test_region_summary.py`: **단위 12**(fake 주입, 네트워크·OSDK 없이) + **라이브 통합 1**(gated).
  - summarizer 매핑(anomalies2 객체집합·region/window/weather 전달)·confidence 클램프·빈 summary 폴백·
    0건 스킵·쿼리에러 폴백·optional 파라미터 `""` 기본.
  - `_aip_region_summary` 통합: 헤드라인 text 교체·**cites 불변**·폴백 유지·0건 스킵·비요약 스킵.
  - assess() 게이트: 로컬 스토어=미적용 / foundry=aip_logic(+attrs·응답 메타 노출).
  - 라이브(.venv312+creds): 실 Foundry Anomaly 객체집합으로 실호출 → summary 비어있지 않음·conf∈[0,1].
- 결과:
  - `.venv`(3.14, 앱 스위트): **280 passed, 4 skipped**(기존 268 + 신규 12, 라이브 1 skip). 회귀 0.
  - `.venv312`(3.12, OSDK): region_summary + aip_explainer + p4 = **36 passed**(라이브 2건 실 Foundry 호출 포함).
  - explainer 리팩터(공용 헬퍼 위임) 후 explain-anomaly 라이브 테스트 통과 유지.

## 7. 정직 판정 — "AIP Logic 2종 완성"인가?

**사실이다(범위 한정).** 초기 계획(aip-integration.md §2(4))의 AIP Logic 함수 2종이 모두 배선됨:
- **explain-anomaly**(#1): 이상징후 개별 **설명**을 AIP가 생성(SKAI_EXPLAINER=aip, 비상 스쿽 경로).
- **region-situation-summary**(이번): 지역 상황요약의 **헤드라인 서술**을 AIP가 생성(SKAI_(EXPLAINER|COPILOT_LLM)=aip
  + Foundry 스토어 + situation_summary 의도). Foundry Anomaly Object set을 종합.

단, 정직하게 구분:
- **AIP가 하는 것**: (a) 이상징후 설명 서술+신뢰도/권고, (b) 지역 상황요약 서술+한줄판정+종합신뢰도.
  둘 다 온톨로지 객체(Observation/Anomaly 참조) 근거 위에서 LLM 추론.
- **AIP가 하지 않는 것**: 탐지·상관·평가는 여전히 자체 엔진(rules.py/correlation.py). 사실 확정·문장별
  cites 조립도 여전히 룰(assessment.py). "전부 AIP가 한다"는 과장.
- **기본값 template**: replay 결정성 때문에 기본은 template, aip는 명시 opt-in. 데모에서 그 모드를 켜야 AIP 경로.
- **범위 한정**: region summary는 **situation_summary 헤드라인 한정**(다른 의도·본문 문장은 룰/claude 경로).
- **어필 문구**: "지역 상황평가 **서술**을 AIP Logic이 생성"까지(문장별 근거·cites는 룰). 그 이상은 과장.

## 8. 되돌리기
- `copilot/region_summary.py`·`tests/test_region_summary.py` 삭제.
- `copilot/assessment.py`의 `elif name == "aip"` 분기·`_aip_region_summary`·`_weather_one_liner`·
  `overall_assessment` 3곳(변수·attrs·응답) 역편집 + `current_backend`/`AipRegionSummarizer` import 제거.
- `anomaly/explainer.py`의 공용 헬퍼(`make_foundry_osdk_client`·`allow_beta_features`) 추출은 유지해도
  무해(explain-anomaly만 사용). 완전 원복 시 `_get_client`/`_allow_beta` 인라인으로 역편집.
- 온톨로지 스키마·기본 동작(template) 불변 → aip 미설정이면 기존 동작 그대로.
