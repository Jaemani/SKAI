# Foundry AIP Logic 함수 생성 가이드 — AnomalyExplainer

> 목적: EVALUATION.md의 최대 갭(OVERSOLD#1 "AIP가 추론한다" — 실제 AIP Logic 미사용)을 **사실로 전환**.
> 이 함수를 만들면 "이상징후 설명을 **Palantir AIP Logic이 생성**한다"가 과장이 아니라 진실이 된다.
> 담당: **당신(Foundry 노코드 UI)**. 함수 생성 후 신호 주면 코드측 배선(AipLogicExplainer)은 내가 한다.

## 이게 왜 중요한가
지금 이상징후 "설명"은 우리 파이썬 템플릿이 만든다(GPT-wrapper도 할 수 있는 것 = 해자 아님).
이걸 **Foundry AIP Logic 함수**가 하도록 옮기면, 추론이 실제로 Palantir 위에서 도는 유일한 지점이 생긴다.
심사위원(Palantir·Morph)이 "AIP Logic 실제로 쓰나?"라고 물을 때 방어되는 단 하나.

## AIP Logic이란
Foundry의 **노코드 LLM 함수 빌더**. 온톨로지에 built-in 접근하고, 프롬프트로 LLM 추론을 만들어
입출력을 스키마로 고정한다. 출력은 staged review 후 온톨로지 edit로 적용 가능. 코딩 불필요.

---

## 만들 함수: `explain-anomaly`

### 진입
Foundry 앱 런처 → **AIP Logic** (또는 Developer Console에서 Logic 함수 생성) → New function → 이름 `explain-anomaly`.

### 입력 파라미터 (Inputs)
| 이름 | 타입 | 설명 |
|---|---|---|
| `anomalyType` | String | 이상징후 유형 (emergency_squawk·adsb_dropout·loitering·military_approach·satellite_proximity) |
| `evidence` | **Object Set: Observation** (또는 단일 Observation) | 근거 관측 — AIP가 이 객체의 속성(callsign·squawk·lat·lon·ts·source)을 읽어 맥락 파악 |
| `callsign` | String (optional) | 대상 콜사인 |
| `regionName` | String (optional) | 관심지역 (KADIZ 등) |

> 핵심: `evidence`를 **온톨로지 객체 참조**로 받아야 AIP가 "온톨로지 위에서 추론"하는 게 된다
> (단순 텍스트 입력이면 그냥 LLM 호출과 다를 바 없음).

### 출력 (Outputs) — 스키마로 고정
| 이름 | 타입 | 설명 |
|---|---|---|
| `explanation` | String | 자연어 설명(2~3문장). ISR 분석가용, 왜 이상한지 |
| `confidence` | Double | 0~1 신뢰도 |
| `recommendation` | String (optional) | 권고 조치(교차검증·모니터링 등) |

### 프롬프트 (LLM 지시 — 예시, 다듬어 쓰세요)
```
너는 공중 ISR 분석 보조다. 주어진 이상징후 유형과 근거 관측(Observation 객체)을 바탕으로,
왜 이것이 이상 신호인지 2~3문장으로 설명하고 0~1 신뢰도를 산출하라.

규칙:
- 근거 관측의 실제 속성(스쿽 코드·위치·시각·소스)만 사용하라. 없는 사실을 지어내지 마라.
- 단일 소스 결측(dropout)은 "송신기 문제일 수 있음 — 교차검증 필요"로 신중히. 단정 금지.
- 비상 스쿽(7500 하이재킹·7600 통신두절·7700 일반비상)은 하드 신호로 높은 신뢰도.
- 군용기 판정은 저신뢰 휴리스틱임을 명시.
- confidence는 신호의 하드함에 비례(스쿽>군용기>dropout미확인).

유형: {anomalyType}, 콜사인: {callsign}, 지역: {regionName}
근거: {evidence}
```

### 테스트 (AIP Logic 빌더 내장 테스트)
- 근거 Observation 하나 물려서 실행 → explanation·confidence가 스키마대로 나오는지.
- 여러 유형(스쿽·dropout)으로 신뢰도가 합리적으로 다른지.

### 발행
함수를 **발행(publish)**해야 OSDK/SDK에서 호출 가능. Developer Console 앱의 리소스에 이 Logic
함수를 포함해 **OSDK 재발행**(0.9.0) 하면 코드가 타입드로 부를 수 있다. (재발행 어려우면 저수준
SDK로 함수 직접 호출도 가능 — 함수 rid만 알려주면 됨.)

---

## 완료 후
"explain-anomaly 발행 완료 (+ 재발행 여부 / 함수 api_name·rid)" 라고 알려주면 내가:
1. `AipLogicExplainer.explain()`을 이 함수 호출로 배선(현재 NotImplementedError 스텁)
2. `SKAI_EXPLAINER=aip`로 이상탐지 설명이 AIP Logic 경로를 타게
3. 라이브 검증(근거 물려 실행 → explanation read-back) → 데모에서 "설명=AIP Logic" 사실로

## 정직 주의
이 함수가 하는 건 **설명 생성**이다. 탐지 룰·상관·평가는 여전히 우리 엔진 — 그건 그대로 정직하게.
"AIP가 이상징후 **설명을** 생성한다"까지만 어필(전부를 AIP가 한다고 하면 다시 과장).
