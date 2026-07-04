# rapid-maneuver.md — 급기동(rapid maneuver) 이상탐지 룰

architecture.md §3 이상탐지 표의 마지막 미구현 유형인 **급기동**을 구현했다. 이로써 §3의
7유형이 모두 코드로 존재한다(비상 스쿽·ADS-B dropout·군용기 접근·로이터링·**급기동**·
위성 근접·항적↔뉴스 상관).

- 유형 문자열: `rapid_maneuver` (`anomaly.rules.ANOMALY_TYPE_RAPID_MANEUVER`)
- 신호(그래프 패턴): 같은 Track의 **연속 Observation 시퀀스**에서 고도·속도 변화율이 임계 초과
- 근거 객체(evidence): 급변 구간의 Observation들(≥2), 주체(involves): Aircraft
- 신뢰도: 0.5(정황·휴리스틱), 고도+속도 동시 급변 시 0.62로 상향 — **단정 아님**

## 1. 임계값과 근거 (보수적 — 민항 정상 기동 배제)

단위는 온톨로지가 OpenSky 원값을 그대로 보존한다(`ontology/mapping.py`): 고도 `alt`=미터
(baro_altitude), 속도 `velocity`=m/s. 판정은 SI 단위로 하고, 서술만 ft/min으로 환산한다.

| 상수 | 값 | 근거 |
|---|---|---|
| `MANEUVER_VERTICAL_RATE_FPM` | 6000 ft/min (≈30.5 m/s) | 민항기 정상 상승/강하는 대략 1500~3000 ft/min, 통상 비상강하도 6000 ft/min 부근. **6000 이상**만 후보(회피·전투기동·비상강하) → 정상 순항 기동 배제 |
| `MANEUVER_ACCEL_MPS2` | 3.0 m/s² (≈0.3g) | 정상 순항 종방향 가속은 ~0.5 m/s² 이하. 3 m/s² 지속을 속도 급변으로 본다 |
| `MANEUVER_MIN_OBSERVATIONS` | 4 | 구간 ≥3 확보 — 미만이면 판정 유보(노이즈) |
| `MANEUVER_MIN_RUN` | 2 | 연속 초과 구간 ≥2(같은 방향) 필요 — 단일점 방어 |
| `MANEUVER_MAX_VERTICAL_MPS` | 150 m/s (≈29,500 ft/min) | 초과 = 물리적 불가 → 기압고도 스파이크(글리치)로 보고 구간 무효화 |
| `MANEUVER_MAX_GROUND_SPEED_MPS` | 600 m/s (≈2160 km/h) | 위치 점프가 함의하는 지상속도가 초과 = GPS 튐 → 구간 무효화 |

임계는 전부 `anomaly/rules.py` 상단 상수로 분리(튜닝 지점 단일화). confidence 상수
(`MANEUVER_CONFIDENCE_BASE=0.5`·`_STRONG=0.62`)도 0.5~0.65 범위로 분리.

## 2. 노이즈 방어 (과잉 탐지 경계)

민항 정상 기동 오탐을 막기 위해 네 겹으로 방어한다:

1. **최소 관측 수**: 4건 미만 트랙은 판정 유보(짧은 트랙의 노이즈 배제).
2. **연속 ≥2 구간·같은 방향**: 단일 구간 급변은 후보로 삼지 않는다. `_longest_run`이
   부호가 바뀌면(상승↔강하) 런을 끊으므로, 한 점만 튀는 글리치(위→아래 스파이크)는
   길이 1짜리 런 둘로 쪼개져 임계 길이(2)에 미달 → 탈락.
3. **비물리적 수직률 제외**: 구간 수직률이 150 m/s 초과면 기압고도 글리치로 보고 그
   구간을 무효화(런에서 배제).
4. **GPS 튐 제외**: 두 관측 사이 위치 점프가 함의하는 지상속도가 600 m/s 초과면 위치
   글리치로 보고 그 구간을 무효화 — 고도값이 급변처럼 보여도 그 구간은 신뢰하지 않는다.

결과: 아래 합성 회귀에서 정상 상승(2625 ft/min)·정상 통과·기압 스파이크·GPS 튐 모두
음성 처리(false positive 0).

## 3. 배선

- `anomaly/rules.py`: 상수 + `detect_rapid_maneuver(tracks, observations_by_ac, now)` +
  헬퍼 `_maneuver_segment`(구간별 수직률·가속 계산, 글리치 무효화) · `_longest_run`
  (같은 방향 연속 초과 런 탐색).
- `anomaly/actions.py::scan_and_create_all`: `query_all_observations()`를 기체별로 묶어
  `observations_by_ac`를 만들어 룰에 전달(최신 1건이 아니라 시퀀스가 필요) → 라이브
  폴러가 자동 포함.
- `anomaly/explainer.py::explain_draft`: 급기동 서술 분기(수직/속도/동시, ft/min·m/s²).
- `copilot/assessment.py::_anomaly_sentence_text`: 상황요약 문장 분기.
- `scripts/scenarios.py`: `climb` 패턴 신설(관측별 alt/velocity 램프) + `apply_scenario`가
  관측별 alt/velocity를 싣도록 확장(**하위호환**: line/gapline/circle은 관측별 값이 없어
  기존 산출 그대로 = 기존 시나리오 무변경). 시나리오 2건 추가:
  - `rapid_climb`(양성, 수직률 ≈9186 ft/min) · `normal_climb`(음성, ≈2625 ft/min).
- `eval/run_eval.py`: `T_MANEUVER`를 `ALL_TYPES`·`TYPE_LABELS_KO`에 추가(P/R 표 편입).
- `web/`: **무수정**(제약). 기존 아이콘 로직이 `TYPE_ICON[type] || "⚠"`·
  `TYPE_LABEL[type] || type` 폴백을 갖고 있어 신 유형을 깨지 않고 표시(⚠ + 원문 라벨).

## 4. 검증

- **pytest 전체**: 315 passed, 4 skipped (신규 10건 포함 — 양성 climb·양성 speed·음성
  정상상승·최소관측·단일구간·기압스파이크 제외·GPS튐 제외·dedup+evidence≥2·E2E 주입·
  음성 정상상승 E2E). P6 replay 결정성 테스트의 하드코딩 카운트 9→10 갱신(급기동 1건 추가).
- **탐지 P/R**(`eval.run_eval --no-llm`): 급기동 P=1.00·R=1.00, 전체 micro P/R 1.00,
  14 시나리오 전부 OK(false positive 0).
- **replay 결정성**: `inject_synthetic --scenario all --now 1783000000`을 2회 빌드 →
  stdout 바이트 동일(`PYTHONHASHSEED=0`). demo.sh replay가 이 경로를 그대로 쓴다.
- **E2E**(주입→탐지→근거): `rapid_climb` 주입 → `scan_and_create_all` → 유형 집합
  `{rapid_maneuver}`(오탐 0), Anomaly `anomaly-rapid_maneuver-r1zoom-2971666`
  conf=0.5, **evidence=Observation 8건(≥2)**, involves=Aircraft, 설명문·상황요약 문장
  정상 렌더.

## 5. architecture §3 7유형 완성 선언

architecture.md §3 이상탐지 표의 7유형이 모두 룰로 구현·배선·회귀되었다. 급기동이
마지막 칸이었다. 전 유형이 `scan_and_create_all`에 등록되어 라이브 폴러·replay 양쪽에서
동작하며, 각 유형은 근거 객체(evidence) 링크 없이는 생성되지 않는다(provenance 강제).
