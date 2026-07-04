# Foundry Automation 가이드 — 신규 이상징후 자동 반응 (B3)

> 목적: "AIP 얕지 않음"의 마지막 조각 — **플랫폼이 스스로 반응**하는 것을 실증.
> 지금까지는 전부 우리 코드가 Foundry를 호출했다(pull). Automation은 반대 방향:
> **Foundry가 조건을 감시하다가 스스로 행동**(push). 이게 되면 "우리 앱이 꺼져 있어도
> 온톨로지에 이상징후가 생기면 플랫폼이 반응한다"를 시연할 수 있다.
> 담당: **당신(Foundry UI, ~15-20분)**. 코드 배선은 필요 없거나 최소(알림은 Foundry 자체 기능).

## 만들 것: Automation 1개 — "신규 비상 스쿽 → 알림"

### 개념
Foundry **Automate**(구 Object Monitoring)는 온톨로지 객체의 조건을 감시하다가
조건 충족 시 **효과(알림·액션 실행·함수 호출)**를 자동 실행하는 기능입니다.

### 진입
앱 런처(⠿) → **Automate** 검색 (또는 "Automation", 구명 "Object Monitor") → New automation.

### 조건 (Condition / Trigger)
- **대상**: Anomaly 객체
- **트리거 유형**: "객체 추가됨(object added)" 류 — 새 Anomaly가 온톨로지에 생길 때
- **필터**: `type = emergency_squawk` (비상 스쿽만 — 데모 서사가 가장 명확)
  - 필터를 `status = candidate`로 잡는 변형도 가능("미검토 이상징후 발생 시")
  - ⚠️ 너무 넓게(전체 Anomaly) 잡으면 데모 중 소음 — 1개 유형으로 좁게

### 효과 (Effect) — 셋 중 하나, 위에서부터 권장
1. **알림(Notification)**: 당신 Foundry 계정으로 인앱 알림 — **가장 간단, 이걸로 충분**.
   데모: demo_foundry.sh 실행 → 수초 내 Foundry 알림 벨이 울림 → "플랫폼이 스스로 감지했다"
2. (선택) **액션 실행**: set-region-alert-level을 자동 실행 — "비상 스쿽 발생 → KADIZ 경보등급 자동 상향".
   ⚠️ 단, ontology.md §3의 human-on-the-loop 원칙(경보등급은 결정 액션)과 긴장 관계 —
   한다면 "제안(suggest)" 모드가 있으면 그걸로. 없으면 1번 알림만 하는 게 원칙 정합.
3. (선택·고급) **Logic 함수 호출**: explain-anomaly를 자동 실행해 설명 필드 채움 —
   되면 "탐지→AIP 설명 자동 생성" 루프. 설정 복잡하면 스킵.

### 저장·활성화
이름 예: `alert-on-emergency-squawk`. 저장 후 **Enabled** 상태 확인.

## 검증 (당신 또는 신호 주면 제가)
1. 터미널에서 `scripts/demo_foundry.sh` 1회 실행(비상 스쿽 Anomaly가 Foundry에 생성됨)
2. 수초~수분 내 Foundry 알림 도착 확인 (알림 벨 아이콘)
3. 데모 대본 스텝 ⑥에 "알림이 실시간으로 뜨는 것"을 추가할 수 있음

## 완료 후
"Automation 생성·활성화 완료 (+ 알림 수신 확인 여부)"라고 알려주세요.
- 코드측: demo_foundry.sh 출력에 "Foundry Automation이 이 생성을 감지해 알림을 발생시킵니다" 안내 추가 + 데모 대본 반영은 제가.

## 정직 선
이건 "플랫폼 반응 루프 1개를 실증"한 것 — "전체 파이프라인이 Automation으로 돈다"고 하면 과장.
어필 문구: "신규 이상징후가 온톨로지에 생기면 Foundry Automation이 자동 감지·통지합니다 —
분석가 워크플로에 플랫폼이 능동 참여하는 최소 단위를 구현했습니다."

## 막히면
- Automate/Object Monitor 메뉴가 안 보임 → Dev Tier 권한/앱 이름 다를 수 있음. 보이는 메뉴 목록 알려주세요.
- 트리거에 Anomaly가 안 뜸 → 온톨로지 선택이 mayh Ontology인지 확인.
- 효과에 알림이 없음 → 사용 가능한 효과 목록 스크린샷 주시면 대안 짚어드립니다.
