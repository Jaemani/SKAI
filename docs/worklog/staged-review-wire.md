# B2 Staged Human Review 배선 (방법 B) — 완료 (Foundry 규칙 수정 후 플랫폼 레벨 성립)

- 날짜: 2026-07-05
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 목표: aip-integration §0 "함수 출력이 staged human review 후 온톨로지 edit로 적용" 실증 —
  AIP 산출 explanation을 **본 속성에 즉시 쓰지 않고** 제안(proposedExplanation, reviewStatus=pending)
  → 사람 승인(explanation←proposed, approved) 2단계로 분리(방법 B). OSDK 0.11.0 재발행분 배선·검증.
- **판정(최종, §9): STAGED-OK.** §1~§8은 **규칙 수정 전** 기록이다(그 시점 판정=부분/PARTIAL —
  코드는 올바른 계약대로 배선됐으나 Foundry propose/approve-explanation 액션의 Modify 규칙이 잘못된
  속성을 대상으로 해 플랫폼 레벨 ★핵심 불변식 불성립). **사용자가 §7 지시대로 두 액션 규칙을 수정하고
  OSDK를 재발행(0.11.0→0.12.0)한 뒤, §9 최종 E2E에서 Foundry Object Explorer 레벨의 ★핵심 불변식
  ("pending 중 본 explanation 미변경 + proposedExplanation 채워짐", approve 복사, reject 보존)이
  성립함을 실측 확인**했다. 코드는 무수정(파라미터 스키마 불변).

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

## 6. 정직 판정 (규칙 수정 후 갱신 — §9 반영)
- **완결(코드):** 스키마 신규 속성 2 + 액션 3종을 실측 파라미터로 배선. 게이트·로컬 권위본 스테이징
  불변식·서버 승인/기각 엔드포인트·필드 노출 완료. **로컬(앱 read 경로) 스테이징은 검증 통과.**
- **성립(플랫폼):** 사용자가 §7대로 `propose-explanation`·`approve-explanation`의 Modify 규칙을
  수정한 뒤, **Foundry Object Explorer 레벨의 ★핵심 불변식이 성립한다**(§9 실측): propose는
  `proposedExplanation` 속성에만 쓰고 본 `explanation`은 건드리지 않으며(pending 중 본 속성 미변경),
  approve는 target만으로 저장된 `proposedExplanation`을 본 `explanation`으로 복사(+reviewStatus=approved),
  reject는 본 explanation을 보존(+rejected). 코드는 무수정(파라미터 스키마 불변, OSDK 0.11.0→0.12.0).
- 정확한 어필 문구(현 시점): "**스키마(proposedExplanation·reviewStatus)와 액션(propose/approve/reject)의
  Modify 규칙으로 staged human review 워크플로를 구현**했고, **로컬 온톨로지 권위본과 Foundry 플랫폼
  (Object Explorer) 양쪽에서 제안→승인/기각 불변식(승인 전 본 속성 미변경, 승인 시 복사, 기각 시 보존)을
  실증**했다." — 얻은 이점: AIP가 산출한 서술이 사람 승인 전까지 본 explanation에 반영되지 않고
  proposedExplanation에 스테이징돼, 플랫폼 레벨에서 human-on-the-loop가 강제된다(환각·오판 즉시반영 차단).
  포장 주의: template 베이스라인은 **결정성/데모 재현성**을 위한 것이지 필수 종속이 아니며, 본
  워크플로가 성립하는 곳은 propose/approve/reject **액션 3종 + 신규 속성 2**에 한정된다.

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

## 9. 최종 E2E (규칙 수정 후) — **STAGED-OK**
- 날짜: 2026-07-05. 실행 에이전트(opus). 사용자가 §7 지시대로 propose/approve-explanation Modify
  규칙 수정 + OSDK 재발행(**0.11.0 → 0.12.0**). 코드 무수정(규칙 수정은 Modify 매핑만 바꾸고
  파라미터·속성 스키마는 불변 → 코드 영향 없음이 정상이었고, 실측으로 확인).
- 절차: 합성 스쿽(7700) → `create_anomaly(SKAI_STORE=foundry, SKAI_REVIEW=staged, SKAI_EXPLAINER=aip)`
  → 내부 propose → approve/reject. 각 단계 저수준 dict SDK로 Foundry Anomaly **전 속성 직독**
  (= Object Explorer가 보는 값). AIP explainer는 **라이브**(backend=`aip_logic`, 제안문이 template
  베이스라인과 상이함을 실측 — string 폴백 아님).

### 9-1. OSDK 0.12.0 introspection — 파라미터 불변 (§1-2 대비)
재발행으로 버전만 오르고 staged review 3액션의 파라미터 시그니처는 §1-2와 **동일**:

| 액션 | target | 그 외 파라미터 |
|---|---|---|
| `propose-explanation` | `anomaly`(object, req) | `proposedExplanation`(string, opt), `reviewStatus`(string, opt) |
| `approve-explanation` | `anomaly`(object, req) | `proposedExplanation`(string, opt) |
| `reject-explanation`  | `anomaly`(object, req) | (없음) |

Anomaly 속성 실측: `explanation`·`proposedExplanation`·`reviewStatus` 전부 존재. → 규칙 수정은
**속성/파라미터 스키마 불변, Modify 매핑만 교정** = 코드 무영향(무수정 원칙 유지 정당).

### 9-2. 전이표 — Foundry Object Explorer 직독 (수정 전 §1-3 표와 대비)
**approve 케이스** (anomaly=`…-e2eapp…`):

| 단계(입력) | explanation(본 속성) | proposedExplanation | reviewStatus |
|---|---|---|---|
| create-anomaly (staged) | template 베이스라인 | None | None |
| **propose-explanation** (AIP 산출 제안) | **template 베이스라인** ✓(불변) | **AIP 산출문** ✓(채워짐) | `pending` ✓ |
| **approve-explanation** (target `anomaly`만) | **AIP 산출문** ✓(←proposed 복사) | AIP 산출문(유지) | `approved` ✓ |

**reject 케이스** (anomaly=`…-e2erej…`):

| 단계(입력) | explanation | proposedExplanation | reviewStatus |
|---|---|---|---|
| propose-explanation | template 베이스라인 | AIP 산출문 | `pending` |
| **reject-explanation** (target `anomaly`만) | **template 베이스라인** ✓(보존) | AIP 산출문(유지) | `rejected` ✓ |

**수정 전 §1-3 표 대비 (핵심 대조):**

| 항목 | 수정 전(§1-3) | 수정 후(§9, 실측) |
|---|---|---|
| propose가 쓰는 곳 | ⚠️ 본 `explanation`(베이스라인 덮어씀); `proposedExplanation`은 고아 | ✓ `proposedExplanation` 속성; 본 `explanation` 불변 |
| propose 후 pending 본 속성 | ⚠️ AIP 산출문(불변식 깨짐) | ✓ template 베이스라인(★불변식 성립) |
| approve(target만) | ⚠️ 본 explanation을 null로 덮어써 **지워짐** | ✓ 저장된 `proposedExplanation`을 본 explanation으로 **복사** |
| reject | ✓ 정상(보존) | ✓ 정상(보존) — 무변경 |

### 9-3. ★핵심 불변식 판정
- **pending 중 본 explanation 미변경** ✓ — create 시 template 베이스라인, propose 후에도 동일(둘 다 직독 일치).
- **proposedExplanation 채워짐** ✓ — AIP 산출문(로컬 권위본 `proposed_explanation`과 Foundry 직독값 일치 = dual-write 정합).
- **approve: explanation ← proposedExplanation 복사 + reviewStatus=approved** ✓ (target만 전달로 성립).
- **reject: explanation 보존 + reviewStatus=rejected** ✓.
→ **Foundry Object Explorer 레벨에서 스테이징 불변식 성립.** 로컬 권위본(앱 read 경로)과 플랫폼
  스파인이 이제 일치한다. §6의 "규칙 수정 후에야 플랫폼 레벨 성립" 조건이 충족됨.

### 9-4. 게이트 off 불변 + 정리
- 게이트 off(SKAI_REVIEW 미설정) 기본 동작: 앱 테스트 스위트 `.venv`(3.14) **325 passed, 4 skipped**
  (§5와 동일, 회귀 0). staged 배선이 기본 즉시-적용 경로를 건드리지 않음을 재확인.
- 데모 자산 정리: 생성 PK 역순 delete-anomaly/observation/aircraft — **Anomaly 2 · Observation 2 ·
  Aircraft 2 삭제, Foundry 잔여 0** 직독 확인(테스트 잔여물 없음).
