# Foundry AIP Logic 함수 #2 — region-situation-summary

> 초기 계획(aip-integration.md §2(4))의 AIP Logic 함수 2개 중 남은 하나.
> explain-anomaly(개별 이상징후 설명)와 짝: 이 함수는 **지역 단위 종합** — "지역+시간창의 이상징후들을
> aggregate해 상황평가 텍스트 생성". 완성 시 코파일럿의 상황요약 서술을 AIP가 담당 가능.
> explain-anomaly를 만들어봤으니 같은 요령입니다 (~15분).

## 만들 함수: `region-situation-summary`

### 입력 (Inputs)
| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `regionName` | String | ✔ | 지역명 (예: "한국 방공식별구역 (KADIZ)") |
| `anomalies` | **Object set: Anomaly** (다중!) | ✔ | ⭐ 창 안의 이상징후들 — **Object set(복수)**으로. explain-anomaly의 evidence는 단일이었지만 이건 여러 개를 aggregate하는 함수라 set이어야 함 |
| `windowLabel` | String | 선택 | 시간창 라벨 (예: "최근 30분") |
| `weatherSummary` | String | 선택 | 기상 한 줄 (예: "RKSI MVFR·실링 1200ft") |

> ⭐ 핵심은 `anomalies`를 **Anomaly Object set**으로 받는 것. AIP가 각 이상징후의
> type·confidence·status·explanation·lat/lon을 직접 읽어 종합합니다.
> Object set 타입이 UI에서 안 보이면: 단일 Anomaly로라도 만들고 알려주세요(코드에서 상위 1건 요약으로 폴백 설계).

### 출력 (Outputs)
| 이름 | 타입 | 설명 |
|---|---|---|
| `summary` | String | 지역 상황 종합 3~5문장 |
| `overallAssessment` | String | 한 줄 판정 (예: "주의 — 비상 스쿽 1건 활성, 교차검증 진행 권고") |
| `confidence` | Double | 0~1 종합 신뢰도 |

### 프롬프트 (다듬어 쓰세요)
```
너는 공중 ISR 당직 상황장교 보조다. 주어진 지역·시간창의 이상징후 객체들(Anomaly object set)을
종합해 지역 상황평가를 작성하라.

규칙:
- 각 이상징후의 실제 속성(type·confidence·status·explanation)만 사용. 없는 사실 지어내기 금지.
- 심각도 순으로: 비상 스쿽 > 군용기 접근 > dropout(교차 미확인이면 신중히) > 로이터링 > 위성 근접.
- confirmed(확인됨)와 candidate(미검토)를 구분해 서술하라.
- dropout이 교차 미확인이면 "송신기 문제 가능성 — 단정 금지"를 유지하라.
- 이상징후가 0건이면 "특이사항 없음"을 명확히(억지 위협 서술 금지).
- summary 3~5문장, overallAssessment 한 줄, confidence는 구성 이상징후들의 신뢰도·심각도 반영.

지역: {regionName}, 시간창: {windowLabel}, 기상: {weatherSummary}
이상징후: {anomalies}
```

### 테스트
- Foundry에 있는 Anomaly 1~2건 물려 실행 → summary가 실제 속성 기반인지, 0건일 때 "특이사항 없음"이 나오는지.

### 발행 + OSDK 재발행(0.10.0)
Publish 후 Developer Console에서 이 함수 포함해 재발행. 완료되면 **"region-situation-summary 발행 + 0.10.0 재발행"**이라고 알려주세요 → 코드 배선(코파일럿 상황요약의 AIP 경로)은 제가 합니다.

## 정직 선
이 함수가 해도 **사실 확정·citation은 여전히 룰**입니다(문장별 cites는 조립이 담당). AIP는 종합 **서술**을 생성 — "상황평가 서술을 AIP Logic이 생성"까지가 어필 범위.
