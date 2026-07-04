# Foundry Staged Human Review 가이드 (B2 — 초기계획 마지막 조각)

> 근거: aip-integration.md §0 "함수 출력이 **staged human review** 후 온톨로지 edit로 적용 가능" ·
> §2(4) "출력은 staged review 후 Assessment 객체로".
> 목적: **AI가 제안하고, 사람이 승인해야만 온톨로지가 바뀐다**를 플랫폼 레벨에서 실증.
> 지금 confirm/dismiss는 human-on-the-loop이지만 "AI 출력 → 스테이징 → 승인 → 적용" 단계는 미설정.
> 소요 ~15-20분. ⚠️ 저는 이 화면을 직접 본 적이 없어 **메뉴 명칭이 다를 수 있습니다** — 개념 구조 기준으로 찾으시고, 다르면 화면 상태를 알려주세요.

---

## 개념: 어디에 "스테이징"을 끼우나

우리 파이프라인에서 AI(AIP Logic)가 온톨로지를 바꾸는 지점은 사실상 하나입니다:

```
[AIP Logic explain-anomaly] → explanation·confidence 산출
        ↓ (지금: 코드가 받아서 create-anomaly에 그대로 넣음 = 즉시 적용)
[Anomaly 객체 생성]
```

Staged review = 이 사이에 **"검토 대기" 상태**를 끼워, 분석가가 AI 산출물을 보고 승인해야 객체/속성이 확정되게 하는 것.

## 방법 A — AIP Logic 함수의 "Use with ontology" / Apply 단계 (정석, 있으면 이것)

AIP Logic 빌더에는 함수 출력을 온톨로지 edit로 연결하는 옵션이 있고, 이때 **적용 전 사람 검토(review before apply)** 단계를 둘 수 있습니다:

1. AIP Logic에서 `explain-anomaly` 함수 열기 → 출력(Output) 섹션 또는 "Use in ontology"/"Apply edits" 계열 옵션 탐색
2. 출력 → **Anomaly 객체의 `explanation`·`confidence` 속성 edit**로 매핑
3. 적용 방식에서 **"Staged/Review required"** 계열 선택(즉시 apply 아님)
4. 저장 → 이제 함수가 실행되면 edit가 **제안(pending)** 상태로 쌓이고, 검토 큐에서 사람이 승인해야 실제 반영

> 이 옵션이 안 보이면(Dev Tier 제약 가능) 방법 B로.

## 방법 B — 액션 2단계 분리 (어느 티어에서든 가능한 우회)

스키마 레벨에서 "제안 → 승인" 상태를 명시합니다. 이미 있는 Anomaly status 패턴의 확장이라 익숙합니다:

1. **Anomaly에 속성 2개 추가**: `proposedExplanation`(String) · `reviewStatus`(String — pending/approved/rejected)
2. **액션 신설 `propose-explanation`**: 대상 Anomaly + explanation 파라미터 → Modify 규칙으로 `proposedExplanation`에 쓰고 `reviewStatus=pending` (⚠️ **`explanation` 본 속성은 건드리지 않음** — 이게 스테이징의 핵심)
3. **액션 신설 `approve-explanation`**: 대상 Anomaly → Modify 규칙으로 `explanation ← proposedExplanation` 복사 + `reviewStatus=approved` (confirm-anomaly 만들 때와 같은 패턴)
4. (선택) `reject-explanation`: `reviewStatus=rejected`
5. **OSDK 재발행** (신규 속성·액션 포함)

코드측(제 몫, 재발행 후): AipLogicExplainer 산출을 `propose-explanation`으로 보내도록 배선(게이트: `SKAI_REVIEW=staged`일 때만 — 기본은 현행 즉시 적용 유지, 데모 재현성).

## 데모에서 보여줄 것 (~10초)

> "AI가 쓴 설명은 **바로 반영되지 않습니다.** 제안 상태로 스테이징되고 — [Object Explorer에서 reviewStatus=pending인 Anomaly를 보여주며] — 분석가가 승인해야 확정됩니다. AI 산출물에도 human-on-the-loop를 거는 겁니다."

## 정직 선

- 방법 B로 하면 "Foundry의 내장 staged review 기능을 썼다"가 아니라 "**스키마·액션으로 staged review 워크플로를 구현했다**"가 정확한 표현입니다(둘 다 유효한 어필이지만 다른 문장).
- 방법 A가 되면 "AIP Logic의 staged apply를 활성화했다"까지 말할 수 있습니다.

## 완료 후

- 방법 A: "A로 설정 완료" → 제가 검증(함수 실행 → pending edit 확인)
- 방법 B: "B로 속성·액션 추가 + 재발행(0.11.0)" → 제가 introspection → propose/approve 배선 → E2E 검증

어느 쪽이 가능한지 화면에서 막히면 보이는 옵션을 알려주세요.
