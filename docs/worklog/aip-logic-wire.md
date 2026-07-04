# AIP Logic explain-anomaly 배선 (EVALUATION OVERSOLD#1 종결)

- 날짜: 2026-07-04
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 목표: Foundry AIP Logic 함수 `explain-anomaly`(사용자 발행) + OSDK 0.9.0 재발행분을 **실제로 배선**해,
  이상징후 "설명"이 AIP Logic 경로를 타게 한다. `AipLogicExplainer`의 NotImplementedError 스텁 제거.

## 1. OSDK 0.9.0 함수 노출 형태 — **타입드 OSDK 쿼리** (실측)

재설치: `pip install --force-reinstall --no-deps --no-cache-dir --index-url $FOUNDRY_OSDK_INDEX skai_osdk_sdk==0.9.0`
(토큰/인덱스 URL은 스크럽). 재발행분에 `explain-anomaly`가 **타입드 쿼리**로 포함됨 — 저수준 `foundry_sdk`
직접 호출이 아니라 OSDK 생성 클래스로 노출된다.

- 위치: `skai_osdk_sdk/ontology/query_types/explain_anomaly/`, 등록: `...QueryTypes.explain_anomaly`
- 호출: `client.ontology.queries.explain_anomaly(...)` (client = `skai_osdk_sdk.FoundryClient`)
- **api_name**: `explainAnomaly` (내부 매핑; 파이썬 메서드는 `explain_anomaly`)
- **입력 파라미터**(실측 시그니처):
  | OSDK 인자 | 타입 | 비고 |
  |---|---|---|
  | `evidence` | `Union[str, Observation]` | **단일 Observation 객체** 또는 str. ObjectSet 아님 |
  | `anomaly_type` | `str` | required |
  | `region_name` | `str` (optional, `Empty` 기본) | 생략 가능 |
  | `callsign` | `str` (optional, `Empty` 기본) | 생략 가능 |
- **출력**: `ExplainAnomalyResponse` = **beta StructType**, 필드 `explanation: str`, `confidence: float`,
  `recommendation: str`.
- ⚠️ **함정(실측)**: 응답이 beta StructType라 그냥 부르면 `BetaWarning` **예외**가 난다.
  반드시 `with AllowBetaFeatures(): ...`(from `foundry_sdk_runtime`) 안에서 호출해야 한다.
- 크리덴셜: `FoundryClient(auth=UserTokenAuth(token=...), hostname=..., config=Config(timeout=30))`
  또는 `FOUNDRY_TOKEN`/`FOUNDRY_HOSTNAME` env. `Config(timeout=)`로 HTTP 타임아웃 지정 가능(데모 안전).

## 2. 배선 (`anomaly/explainer.py` — `AipLogicExplainer`)

NotImplementedError 제거 → 실호출 구현. 요지:

1. **evidence = 온톨로지 객체 참조(해자)**: `candidate.observation.id`로
   `client.ontology.objects.Observation.get(obs_id)`를 fetch해 evidence로 넘긴다. → AIP가 그 객체의
   실제 속성(squawk·on_ground·lat/lon·alt·ts)을 **온톨로지 위에서** 읽어 추론한다(단순 LLM 호출과의 차이).
2. **String 폴백(한계 명시)**: 객체가 Foundry에 없거나(get→None) 조회 실패 시, 관측 요약 **String**으로 폴백
   (함수 시그니처가 `str`도 허용). 이때는 온톨로지 참조가 아니므로 backend 라벨을 `aip_logic(string-evidence)`로
   구분해 정직하게 표기한다.
3. **매핑**: `anomaly_type=candidate.type`, `callsign=signal.callsign or observation.aircraft_ref`,
   `region_name=signal.region`(있을 때만). 응답의 `explanation`을 쓰고, `recommendation`은 설명문 말미에
   `\n권고: …`로 덧붙여 보존.
4. **폴백(DR-0004 패턴)**: 크리덴셜 미설정·네트워크·타임아웃·함수 실패·빈 응답 등 **어떤 예외든**
   `TemplateExplainer`로 폴백(backend=`template(aip_logic 폴백)`). SKAI_EXPLAINER=aip 명시 opt-in일 때만 이 경로.
5. **lazy import**: `skai_osdk_sdk`/`foundry_sdk`/`foundry_sdk_runtime`는 `explain()` 호출 시점에 import
   (store_foundry와 동일 규율 — 메인 `.venv`(3.14)엔 OSDK 없음). `AllowBetaFeatures`는 미설치 환경에서
   `contextlib.nullcontext()`로 강등 → 주입 fake client로 단위 테스트 가능.

### confidence 설계 (DR-0004 대비 이 백엔드 한정 편차 — **메인 판단 요망**)
`ExplainerResult.confidence`를 **AIP 응답값**으로 채운다(방어적 [0,1] 클램프). 즉 이 백엔드에선 신뢰도도 AIP가
산출한다. template/claude는 confidence를 룰(스쿽 코드)이 확정하고 LLM은 서술만 강화하는데, AIP 백엔드는
"AIP가 설명을 **생성**한다"를 사실로 만들기 위해 AIP의 confidence를 채택했다(팀리드 지시). DR-0004의
"confidence=룰" 원칙과의 의도적 편차 — 발표·문서 반영 여부는 메인이 결정.

## 3. 라이브 검증 (실호출 성공)

`SKAI_EXPLAINER=aip` → `get_explainer()` → `AipLogicExplainer`(env로 client 생성) → 실 Foundry Observation
(`p7t2test-1783136699`, squawk 7700)을 **객체 근거**로 explain-anomaly 실호출. 결과(backend=`aip_logic`,
즉 객체 참조 경로):

```
backend    : aip_logic
confidence : 0.95
explanation: 관측 obs_id p7t2test-1783136699에서 항공기 p7t2test가 2026-07-04T03:44:59Z에 squawk 7700을
             송신했습니다. 7700은 일반 비상 스쿽으로, 위치 36.5, 124.5에서 고도 9500 ft, airborne 상태로
             관측된 하드 신호이므로 이상 신호로 판단됩니다.
             권고: 즉시 해당 항적을 우선 모니터링하고, 관제/인접 센서 또는 원천 source 및 source_url을 통해
             비상 상태 지속 여부를 교차확인하십시오.
```

- 설명이 Observation **객체의 실제 속성**(squawk·airborne·lat/lon·alt·ts)을 근거로 생성됨을 확인 = 온톨로지 위 추론.
- 반복 호출 시 설명문이 매번 미세하게 다름 = 진짜 LLM 추론(비결정적)임을 확증. → **기본 template 불변**이 정당
  (replay 결정성). aip는 명시 opt-in.

## 4. 폴백 확인

- 크리덴셜 없음(env 미설정) → `template(aip_logic 폴백)`, 룰 신뢰도(0.9대) 유지. (단위 테스트로 확인)
- 함수 호출 예외 / 빈 explanation → `template(aip_logic 폴백)`. (단위 테스트로 확인)
- Observation 객체 Foundry 미존재 → String 근거 폴백(`aip_logic(string-evidence)`). (단위 테스트로 확인)

## 5. 테스트

- 신규 `tests/test_aip_explainer.py`: **단위 6**(FakeAip 주입, 네트워크 없이 매핑·폴백) + **라이브 통합 1**
  (OSDK 설치 + 크리덴셜 있을 때만 실행, 그 외 skip).
  - 단위: 객체근거 매핑·String 폴백·confidence 클램프·쿼리에러 폴백·빈응답 폴백·무크리덴셜 폴백.
  - 라이브: 실 Foundry Observation 근거로 explain-anomaly 실호출 → explanation 비어있지 않음·confidence∈[0,1].
- 결과:
  - `.venv`(3.14, 앱 스위트): **247 passed, 3 skipped**(기존 241 passed·2 skipped 유지 + 신규 6 + 라이브 1 skip).
  - `.venv312`(3.12, OSDK): 라이브 통합 테스트 **1 passed**(실 AIP 호출).

## 6. 정직 판정 — "설명은 AIP Logic이 생성"이 이제 사실인가?

**사실이다(범위 한정).** 이상징후 **설명(explanation)과 신뢰도·권고**는 이제 `SKAI_EXPLAINER=aip`일 때
Foundry AIP Logic 함수 `explain-anomaly`가 온톨로지 Observation 객체를 읽어 생성한다(라이브 실호출 확인).
단, 정직하게 다음을 구분한다:

- **AIP가 하는 것**: 이상징후 *설명 서술 + 신뢰도/권고 산출*(온톨로지 객체 근거 위에서 LLM 추론).
- **AIP가 하지 않는 것**: 탐지 룰·상관·평가는 여전히 우리 엔진(rules.py/correlation.py). "전부 AIP가 한다"는 과장.
- **기본값은 여전히 template**: replay 결정성 때문에 SKAI_EXPLAINER 기본은 template. aip는 명시 opt-in.
  따라서 "설명은 AIP가 생성"은 **aip 모드에서 참**이며, 데모에서 그 모드를 켜야 실제로 AIP 경로를 탄다.
- **P5 확장 유형(dropout·로이터링·군용·위성)**: 이들은 `explain_draft`(템플릿) 경로라 아직 AIP를 타지 않는다.
  현재 AIP 배선은 **비상 스쿽(AnomalyCandidate) 경로 한정**. explain-anomaly가 `anomalyType`을 받으므로
  드래프트 경로 확장은 가능하나 이번 범위 밖(후속).

## 7. EVALUATION.md OVERSOLD#1 상태 갱신 제안 (메인 반영)

- 기존: "OVERSOLD: 'AIP가 추론' / GAP: AIP Logic 미사용(스텁)".
- 제안 갱신: **"설명 생성 경로는 AIP Logic 실사용(OSDK 0.9.0 타입드 쿼리 `explainAnomaly`, 온톨로지 Observation
  객체 근거). 단 (a) 탐지·상관·평가는 자체 엔진, (b) 기본값 template·aip는 opt-in, (c) 비상 스쿽 경로 한정."**
  → "AIP가 이상징후 **설명을** 생성한다"까지만 어필(전부라고 하면 다시 과장).

## 8. 되돌리기
- `anomaly/explainer.py`의 `AipLogicExplainer`를 스텁으로 역편집 + `tests/test_aip_explainer.py` 삭제.
- 온톨로지 스키마·기본 동작(template) 불변 → SKAI_EXPLAINER 미설정/≠aip이면 기존 동작 그대로.
