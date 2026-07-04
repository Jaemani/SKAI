# B2 Staged Human Review 배선 (방법 B) — 부분 완료 + Foundry 액션 수정 필요

- 날짜: 2026-07-05
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 목표: aip-integration §0 "함수 출력이 staged human review 후 온톨로지 edit로 적용" 실증 —
  AIP 산출 explanation을 **본 속성에 즉시 쓰지 않고** 제안(proposedExplanation, reviewStatus=pending)
  → 사람 승인(explanation←proposed, approved) 2단계로 분리(방법 B). OSDK 0.11.0 재발행분 배선·검증.
- **판정: 부분(PARTIAL).** 코드 배선은 완결·로컬 검증 통과, 그러나 **Foundry propose/approve-explanation
  액션의 Modify 규칙이 잘못된 속성을 대상**으로 해 플랫폼(Foundry Object Explorer) 레벨의 ★핵심 불변식
  ("pending 중 본 explanation 미변경")이 성립하지 않는다. 사용자 측 Foundry 규칙 수정 후 E2E 재실행 필요.

## 1. OSDK 0.11.0 introspection (실측)

재설치: `pip install --force-reinstall --no-deps --no-cache-dir --index-url $FOUNDRY_OSDK_INDEX
skai_osdk_sdk==0.11.0` (이미 0.11.0 설치돼 있었음 — 재확인). 토큰·인덱스 URL은 스크럽.

### 1-1. Anomaly 신규 속성 — **존재 확인** ✓
`AnomalyObjectType` 검색 속성: `anomaly_id, confidence, created_at, derived, evidence,
explainer_backend, explanation, lat, lon, proposed_explanation, review_status, status, ts, type`.
→ **`proposed_explanation`·`review_status` 속성이 스키마에 추가됨**(Foundry api_name: `proposedExplanation`·`reviewStatus`).

### 1-2. 신규 액션 3종 — **존재 확인** ✓ (가이드의 선택 reject 포함)
OSDK 타입드 액션 시그니처(파라미터 api_name, camelCase):

| 액션 | target | 그 외 파라미터 |
|---|---|---|
| `propose-explanation` | `anomaly`(str/Anomaly) | `proposedExplanation`(str, opt), `reviewStatus`(str, opt) |
| `approve-explanation` | `anomaly` | `proposedExplanation`(str, opt) |
| `reject-explanation`  | `anomaly` | (없음) |

> target 파라미터명은 confirm/dismiss-anomaly와 동일한 소문자 `anomaly`. (단, delete/edit 계열은
> 대문자 `Anomaly`/`Observation`/`Aircraft` — 정리 스크립트에서 실측·반영.)

### 1-3. ⚠️ 액션 Modify 규칙 **런타임 실측** — 가이드와 반대로 구현됨 (핵심 발견)
introspection은 파라미터명만 보여줄 뿐, **파라미터→속성 매핑(Modify 규칙)**은 실행해야 드러난다.
sentinel 문자열로 create→propose→approve→(별도)reject를 실호출해 매 단계 Anomaly 전 속성을 덤프한 결과:

| 단계(입력) | explanation(본 속성) | proposed_explanation | review_status |
|---|---|---|---|
| create-anomaly (explanation="MAIN_ORIGINAL") | `MAIN_ORIGINAL` | None | None |
| **propose-explanation** (proposedExplanation="PROPOSED_TEXT", reviewStatus="pending") | **`PROPOSED_TEXT`** ⚠️ | **None** ⚠️ | `pending` |
| **approve-explanation** (anomaly만) | **None** ⚠️(비워짐) | None | `approved` |
| reject-explanation (anomaly만, 별도 케이스) | 불변 | None | `rejected` ✓ |

**결론(가이드 대비 차이 — 파라미터명이 아니라 의미가 다름):**
- **propose-explanation 규칙이 `proposedExplanation` 파라미터를 `proposedExplanation` 속성이 아니라
  본 `explanation` 속성에 쓴다.** `proposedExplanation` 속성은 어떤 액션도 채우지 않는다(고아 속성).
  → 가이드 §39("explanation 본 속성은 건드리지 않음 = 스테이징의 핵심")와 **정반대**.
- **approve-explanation 규칙이 `explanation`을 `proposedExplanation` 파라미터로 덮어쓴다**(생략 시 null).
  가이드 §40의 "explanation ← proposedExplanation **속성** 복사"가 아니라 파라미터 기반 덮어쓰기라,
  target만 넘기면 **본 explanation이 지워진다**.
- reject-explanation은 `reviewStatus=rejected`만 세팅하고 explanation 보존 — **정상**.
- `edit-anomaly` 액션도 lon/anomaly/type/lat/ts만 노출 → `proposedExplanation` 속성을 우회로 쓸 수단 없음.

즉 **현재 Foundry 액션 구성으로는 "본 explanation을 안 건드리고 AIP 산출을 proposed로 스테이징"이
플랫폼 레벨에서 불가능**하다. 액션 자체의 Modify 규칙 수정이 선행돼야 한다(§7).

## 2. 배선 (코드 — 가이드/올바른 계약 기준, 게이트 off 기본)

게이트 `SKAI_REVIEW=staged`(기본 off = 현행 즉시 적용 불변, 데모 재현성). 배선은 **올바른 계약**
(propose→proposedExplanation 속성, approve→복사)을 가정한다. Foundry 규칙이 §7대로 고쳐지면 그대로 동작.

- `anomaly/actions.py`
  - `_review_staged()` 게이트, `create_anomaly` 스테이징 분기: staged면 본 explanation엔 결정적
    **template 베이스라인**을 두고(항상 사용가능·재현성) 구성 explainer(예: AIP) 산출은
    `store.propose_explanation(...)`로 제안(reviewStatus=pending) → 사람 승인 대기. **AIP 산출을 본
    속성에 즉시 쓰지 않음.**
  - 래퍼 `propose_explanation`/`approve_explanation`/`reject_explanation`(confirm/dismiss 패턴).
- `ontology/store_foundry.py`
  - 액션 상수 `ACTION_PROPOSE_EXPLANATION`·`ACTION_APPROVE_EXPLANATION`·`ACTION_REJECT_EXPLANATION`.
  - `FoundryOntologyStore.propose/approve/reject_explanation` — `_apply`로 실측 camelCase 파라미터 전송
    (confirm/dismiss와 동일 스파인 best-effort).
  - `HybridStore.propose/approve/reject_explanation` — 로컬=권위본 + Foundry=스파인(실패는 경고만).
- `ontology/store_local.py` — `propose/approve/reject_explanation`. proposed_explanation·review_status는
  **attrs(JSON)에 미러**(스키마 마이그레이션 회피). approve가 explanation←proposed 복사, **본
  explanation은 propose/reject 시 불변**(로컬 권위본에서 스테이징 불변식 집행).
- `ontology/store.py` — Protocol에 3 메서드 선언.
- `server/app.py` — `POST /api/anomalies/{id}/approve-explanation`(+대칭 `reject-explanation`),
  confirm/dismiss 패턴 복제. `_anomaly_to_dict`에 `review_status`·`proposed_explanation` 필드 노출
  (attrs 경유 자동 + 최상위 명시). **web/ 프론트 미수정**(필드만).

## 3. E2E 라이브 검증 (실 Foundry 쓰기 1건 + 정리)

`SKAI_STORE=foundry SKAI_REVIEW=staged SKAI_EXPLAINER=aip` + `.venv312`. 합성 스쿽(7700) 1건 →
create_anomaly(AIP 설명 산출) → propose → Foundry Anomaly 직독 → approve → 직독 → delete로 정리.

### ★핵심 결과 — **Foundry 레벨 실패(액션 규칙 문제), 로컬 레벨 성공**
- **Foundry 직독(propose 후):** reviewStatus=`pending`(✓ 전이는 됨) / 본 `explanation`=**AIP 산출문**
  (⚠️ 베이스라인을 덮어씀) / `proposedExplanation`=**빈값**. → **"본 속성 미변경" 불성립**(§1-3의
  propose 규칙이 본 explanation에 쓰기 때문).
- **Foundry 직독(approve 후):** reviewStatus=`approved` / 본 `explanation`=**빈값**(⚠️ 지워짐). →
  proposedExplanation 속성이 애초에 비어 approve가 그걸로 덮어써 explanation이 사라짐.
- **로컬(HybridStore 권위본, 앱 read 경로):** review_status=`pending`, 본 explanation=template
  베이스라인(불변 ✓), proposed_explanation=AIP 산출문. approve 시 explanation←proposed 복사·approved ✓.
  → **앱(SKAI UI/API)이 읽는 로컬 권위본에선 스테이징 불변식이 정확히 성립.** 깨지는 곳은 Foundry
  Object Explorer 직독뿐(액션 규칙 문제).
- 정리: create 역순 delete-anomaly/observation/aircraft(대문자 param api_name) — **6건(E2E 1 + 진단
  자산들) 전부 삭제 확인**. Foundry에 테스트 잔여물 없음.

## 4. 게이트 off 불변
`SKAI_REVIEW` 미설정이면 create_anomaly가 explainer 산출을 본 explanation에 즉시 적용(현행 그대로).
단위 테스트 2건으로 확인(create_anomaly·scan_and_create 모두 review_status 미생성·즉시 적용).

## 5. 테스트
- 신규 `tests/test_staged_review.py` **10건**(전부 fake 주입·네트워크 불요):
  - 게이트 off 불변 2, staged 라우팅(★본 속성 불변) 1, 승인 복사 1, 기각 보존 1,
    로컬 스토어 propose/approve 복사·KeyError 2, Foundry `_apply` 라우팅(액션명·파라미터 실측) 3.
- 결과: `.venv`(3.14 앱 스위트) **325 passed, 4 skipped**(기존 315 + 신규 10, 회귀 0). py_compile OK.
- 라이브 E2E는 위 §3(스크립트 검증, 자동 스위트엔 미포함 — Foundry 쓰기라 gated).

## 6. 정직 판정
- **완결(코드):** 스키마 신규 속성 2 + 액션 3종을 실측 파라미터로 배선. 게이트·로컬 권위본 스테이징
  불변식·서버 승인/기각 엔드포인트·필드 노출 완료. **로컬(앱 read 경로) 스테이징은 검증 통과.**
- **미완결(플랫폼):** Foundry `propose-explanation`·`approve-explanation`의 **Modify 규칙이 대상 속성을
  잘못 잡아**(propose가 `proposedExplanation` 속성 대신 본 `explanation`에 씀; approve가 파라미터로 본
  explanation을 덮어씀/지움) **Foundry Object Explorer 레벨의 ★핵심 불변식이 성립하지 않는다.**
- 정확한 어필 문구(현 시점): "**스키마(proposedExplanation·reviewStatus)와 액션(propose/approve/reject)으로
  staged review 워크플로를 구현**했고, **로컬 온톨로지 권위본에서 제안→승인 불변식(본 속성 미변경)을
  실증**했다. **단 Foundry 액션의 Modify 규칙 수정 후에야 플랫폼(Object Explorer) 레벨에서 동일 불변식이
  성립**한다." — "Foundry가 staged review를 강제한다"까지는 규칙 수정 전엔 과장.

## 7. 사용자 측 Foundry 수정 지시 (E2E 통과를 위한 선결 조건)
아래 두 액션의 Modify 규칙만 고치면 코드 무변경으로 §3 E2E가 ★핵심까지 통과한다:
1. **propose-explanation**: `proposedExplanation` 파라미터를 **`proposedExplanation` 속성**에 쓰도록
   (현재는 본 `explanation` 속성에 씀). `reviewStatus` 파라미터→`reviewStatus` 속성은 정상 유지.
   본 `explanation` 속성은 **건드리지 않도록**.
2. **approve-explanation**: 본 `explanation` 속성 = **객체의 `proposedExplanation` 속성 값**으로 복사
   (파라미터가 아니라 저장된 속성 참조), `reviewStatus`=approved. → target(anomaly)만 넘겨도 동작.
3. reject-explanation은 현재대로(reviewStatus=rejected, explanation 보존) 정상 — 수정 불요.

## 8. 되돌리기
- `anomaly/actions.py`: `import os`·`_review_staged`·create_anomaly staged 분기·래퍼 3 역편집.
- `ontology/store_foundry.py`: 액션 상수 3 + FoundryOntologyStore·HybridStore 메서드 3쌍 역편집.
- `ontology/store_local.py`: propose/approve/reject_explanation 3 역편집.
- `ontology/store.py`: Protocol 3 선언 역편집.
- `server/app.py`: approve/reject-explanation 엔드포인트 2 + import + dict 필드 2 역편집.
- `tests/test_staged_review.py` 삭제.
- 온톨로지 스키마·기본 동작(게이트 off) 불변 → SKAI_REVIEW 미설정이면 기존 동작 그대로.
