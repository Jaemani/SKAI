# P0-B — Foundry/OSDK 파이프 정찰 (실행 로그)

- 날짜: 2026-07-04
- 담당: opus 서브에이전트 (P0-B)
- 목표: build.palantir.com Developer Tier + OSDK 파이프가 **이 머신에서 지금** 뚫리는지 판정. 뚫리면 온톨로지 객체 read/write 왕복까지, 막히면 정확한 블로커 + 사용자 액션 목록.
- 제약 준수: 시크릿 값 미출력 · 새 가입/결제 없음 · Foundry 쓰기 없음(인증 확인까지만) · 브라우저 자동화 없음 · git commit 없음.

---

## 1. 로컬 정찰 결과 (시크릿 값 제외, 존재 여부만)

| 항목 | 결과 |
|---|---|
| 환경변수 (`FOUNDRY_*`, `PALANTIR_*`, `OSDK_*`) | **없음** (PATH 외 매칭 0건) |
| `~/.foundry`, `~/.palantir`, `~/.config/foundry`, `~/.config/palantir`, `~/.osdk` | **모두 없음** |
| `~/SKAI/.env`, `~/SKAI/.env.local` | **없음** |
| 셸 rc (`.zshrc`/`.zshenv`/`.zprofile`/`.bashrc`) export | Foundry/Palantir/OSDK 관련 **없음** (PATH만) |
| pip 패키지 (`foundry-platform-sdk`, `osdk`, `palantir`) | 시스템 python3 · `~/SKAI/.venv` **모두 미설치** |
| CLI (`foundry`/`osdk`/`palantir`) | PATH에 **없음** |
| **Python 버전** | **3.14.5** (homebrew 시스템 기본, `~/SKAI/.venv`도 3.14) |

**결론(로컬)**: Foundry 크리덴셜·SDK·CLI가 이 머신에 **하나도 없음**. Developer Tier enrollment 존재 여부조차 로컬 증거로는 확인 불가(aip-integration.md는 "가입됨"을 전제로 두지만 로컬엔 흔적 0).

---

## 2. 검증된 사실 (2026-07 조사, 출처 URL 포함)

### 2-1. Developer Tier 가입 · 토큰 발급
- Developer Tier는 **무료** 가입. passwordless(FIDO2 passkey) 자체 IdP 사용. 진입점 `build.palantir.com`.
- **user token 발급 경로**: 사이드바 하단 **Account → Settings → Tokens** → 이름/설명/만료일 입력 → **Generate** → 토큰은 **단 한 번만** 표시됨. 개인 Foundry 계정에 귀속되는 토큰이라 프로덕션·공개 레포 사용 금지, 환경변수로 보관 권장.
- 출처: [Getting started 인증](https://www.palantir.com/docs/foundry/getting-started/authentication) · [User-generated tokens](https://www.palantir.com/docs/foundry/platform-security-third-party/user-generated-tokens) · [Palantir for Developers](https://www.palantir.com/developers/) · [build.palantir.com](https://build.palantir.com/)

### 2-2. 온톨로지 스키마 생성(Object/Link/Action Type) — **UI 전용, API 불가**
- **핵심 블로커**: Foundry Platform SDK는 온톨로지 스키마(Object/Action/Link/Query Type)에 대해 **read만** 지원. Object/Link/Action Type을 **생성하는 API 엔드포인트는 존재하지 않음**. 스키마 정의는 **Ontology Manager UI**가 유일한 길.
- Palantir 엔지니어가 2025-08에 "ontology 수정용 엔드포인트 개발 중"이라 언급했으나, **2026-01 기준 여전히 open feature request**(미출시).
- 즉 P0-B의 "Aircraft Object Type + observed_as Link + CreateAnomaly Action 생성"은 **사용자가 브라우저에서 직접** 해야 함. 스크립트/헤드리스 자동화 불가.
- 출처: [GitHub issue #318 — API endpoints to create Object/Action/Link Types](https://github.com/palantir/foundry-platform-python/issues/318) · [Ontology 개요](https://www.palantir.com/docs/foundry/ontology/overview) · [Create an object type](https://www.palantir.com/docs/foundry/object-link-types/create-object-type) · [Programmatic Ontology Management 커뮤니티 스레드](https://community.palantir.com/t/programmatic-ontology-management-in-palantir-foundry-alternatives-to-gui-based-approach/979)

### 2-3. OSDK 발행 — **Developer Console UI**
- Developer Console **Resources 페이지 → "Yes, generate an Ontology SDK" → Ontology 선택 → 포함할 Object Type / Action Type 선택 → 발행**. TS는 NPM, Python은 Pip 또는 Conda 패키지로 생성됨.
- 사전조건: **Developer Console application을 먼저 생성**해야 함. 온톨로지에 Object Type이 이미 존재해야 SDK에 담을 수 있음(= 2-2 UI 작업이 선행).
- 출처: [OSDK 개요](https://www.palantir.com/docs/foundry/ontology-sdk/overview) · [Generate OSDK for other languages](https://www.palantir.com/docs/foundry/ontology-sdk/generate-osdk-for-other-languages) · [Bootstrap a Python application](https://www.palantir.com/docs/foundry/developer-console/how-to-bootstrapping-python)

### 2-4. OSDK Python 설치 · 인증
- **생성된 OSDK 패키지 Python 요구사항**: **>=3.9, <3.13** (2026-07 문서 기준 여전히 유효).
- 설치 명령(개인 토큰이 private index에 박힘 — 값은 앱 Overview 페이지에서):
  ```bash
  pip install <YOUR-PACKAGE-NAME> --upgrade \
    --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"
  ```
- 인증(생성된 OSDK):
  ```python
  from foundry_sdk import UserTokenAuth  # 또는 생성 패키지의 FoundryClient
  auth = UserTokenAuth(hostname="<YOUR-FOUNDRY-URL>", token=os.environ["FOUNDRY_TOKEN"])
  client = FoundryClient(auth=auth, hostname="<YOUR-FOUNDRY-URL>")
  result = client.ontology.objects.Aircraft.get("primaryKey")  # read 예시
  ```
- 출처: [Bootstrap a Python application](https://www.palantir.com/docs/foundry/developer-console/how-to-bootstrapping-python) · [Python OSDK](https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk)

### 2-5. 저수준 `foundry-platform-sdk` (PyPI)
- **최신 1.97.0** (2026-06-30 릴리스). import 이름 **`foundry_sdk`**. **Python <4.0, >=3.10** → **3.10~3.14 지원**(생성 OSDK와 달리 3.14 OK).
- 인증: `UserTokenAuth(token)` + `hostname="xxx.palantirfoundry.com"`, 또는 env `FOUNDRY_TOKEN` / `FOUNDRY_HOSTNAME` 자동 감지. OAuth2 client-credentials(ConfidentialClientAuth)는 프로덕션 권장.
  ```python
  import foundry_sdk, os
  client = foundry_sdk.FoundryClient(
      auth=foundry_sdk.UserTokenAuth(os.environ["FOUNDRY_TOKEN"]),
      hostname="example.palantirfoundry.com")
  ```
- 출처: [foundry-platform-sdk · PyPI](https://pypi.org/project/foundry-platform-sdk/) · [foundry-platform-python (GitHub)](https://github.com/palantir/foundry-platform-python)

---

## 3. 판정

### 판정: **BLOCKED** (이 머신에서 지금 왕복 불가)

지금 자동으로 가능한 것과 블로커를 분리:

**지금 가능(자동/스크립트):**
- 저수준 `foundry_sdk`(1.97.0) 설치 및 인증 확인 코드 작성 — 단 **유효 토큰 + hostname이 있을 때만**. Python 3.14에서도 이 SDK는 돌아감.

**블로커 (모두 사용자/브라우저 필요):**
1. **크리덴셜 부재** — 토큰·hostname·enrollment 로컬 증거 0. 토큰 없이는 인증 확인조차 불가.
2. **온톨로지 스키마 생성이 UI 전용** — Aircraft Object Type / observed_as Link / CreateAnomaly Action 생성은 Ontology Manager 브라우저 작업. API 없음 → 헤드리스 자동화 불가(2-2).
3. **OSDK 발행이 UI 전용** — Developer Console에서 앱 생성 + SDK 발행 후에야 pip 설치 가능(2-3).
4. **Python 버전 충돌** — 생성 OSDK는 **<3.13** 요구인데 이 머신은 **3.14.5**. 토큰·패키지를 받아도 현재 환경엔 **설치 실패**. Python 3.12 별도 환경(pyenv/venv) 필요.

**요약**: P0-B의 성공기준("OSDK로 온톨로지 객체 read/write 왕복")은 **선행 UI 작업(온톨로지 생성 + OSDK 발행) + 토큰 + 3.12 환경**이 모두 갖춰지기 전엔 이 머신에서 불가. 내가 브라우저 자동화 없이 완료할 수 있는 최대치는 "토큰 주어졌을 때 저수준 SDK 인증 200 확인"까지이며, 그 토큰조차 없음.

---

## 4. 사용자 액션 체크리스트

> 브라우저에서 순서대로. 각 단계 후 결과를 P0-B 담당에게 넘기면 그 다음 자동 단계(인증 확인·왕복 스크립트)를 이어감.

1. **build.palantir.com 로그인 → enrollment 확인** (~2분)
   - 가입돼 있으면 로그인. 안 돼 있으면 Developer Tier 무료 가입(passkey). enrollment hostname(`<something>.palantirfoundry.com` 또는 build 스택 URL) 확보.
2. **user token 발급** (~2분)
   - Account(사이드바 하단) → Settings → Tokens → 이름 `skai-p0` · 만료 예: 30일 → Generate → **한 번만 표시**되므로 즉시 복사.
   - 저장 위치: `~/SKAI/.env`에 `FOUNDRY_TOKEN=...` + `FOUNDRY_HOSTNAME=<hostname>` (이미 `.gitignore`에 `.env` 포함됨 — 커밋 안 됨). **토큰 값을 채팅에 붙여넣지 말 것.**
3. **Ontology Manager에서 스키마 3종 생성** (~15~25분, UI 필수)
   - Object Type `Aircraft` (primary key = `icao24`, 속성 ontology.md 참조).
   - Object Type `Observation` + Link Type `observed_as`(Observation→Aircraft).
   - Action Type `CreateAnomaly` (evidence 파라미터 필수). *P0는 최소 1객체 왕복이 목표이므로 Aircraft만 먼저여도 됨.*
   - 막히는 지점(backing dataset 요구 등) 발생 시 **§5 Morph 질문**으로.
4. **Developer Console에서 OSDK 발행** (~10분, UI 필수)
   - 앱 생성 → Resources → "generate an Ontology SDK" → 위 온톨로지 + Object/Action Type 선택 → **Python(Pip)** 발행.
   - 앱 Overview 페이지의 `<PACKAGE-NAME>`, `<INDEX-URL>`, `<FOUNDRY-URL>`을 P0-B에 전달.
5. ~~**Python 3.12 환경 준비** (~5분)~~ **완료 (자동, .venv312)** — `uv venv --python 3.12`로 `/Users/ma/SKAI/.venv312` 생성 (Python 3.12.13, uv가 이미 3.12.13 로컬 캐시 보유). `.gitignore`도 `.venv*/`로 갱신 완료.
6. (이후 자동) P0-B가 이어받아: OSDK pip 설치 → `UserTokenAuth`로 인증 확인 → Aircraft 1건 write/read 왕복 → P0.md 갱신.

**사용자 액션 개수: 5개** (1~5, UI/설정). 6번부터는 자동.

---

## 5. Morph 멘토 질문 목록 (PROMPTS.md P0 요구)

1. Developer Tier(무료)에서 **Ontology Manager로 Object/Link/Action Type을 직접 생성**하는 게 표준 경로가 맞나? 별도 프로젝트·권한·Marketplace 설치가 선행돼야 하나?
2. Object Type을 만들 때 **backing dataset이 반드시 있어야** 하나, 아니면 **합성/수기 객체만으로 1건 write/read 왕복**이 가능한가? (P0 목표는 라이브 없이도 왕복 검증)
3. **CreateAnomaly Action의 write-back + staged human review** 설정 최소 절차는? Action에서 evidence 링크를 **필수 파라미터로 강제**하는 방법은?
4. 생성 **OSDK Python 패키지의 3.13/3.14 지원 계획**이 있나? 없으면 우리는 3.12로 내려야 하는데, **저수준 `foundry_sdk`(3.14 지원)만으로 P0 객체 왕복**을 대체할 수 있나(생성 OSDK 없이 platform API로 object create/read)?
5. **Object/Action Type 생성 API**(GitHub issue #318, "개발 중")가 Dev Tier에서 이미 열려 있나? 아니면 UI가 유일한가?
6. **OSDK 앱에서 AIP Logic 함수를 호출**하는 최소 예제와 **권한 모델**(Logic 함수에 온톨로지 read-only 부여)은?
7. Dev Tier의 **제약**(Object Type 개수, Action 수, rate limit, 데이터 용량)이 P0~P2 데모 범위에서 걸림돌이 되나?

---

## 7. 인증 확인 (2026-07-04)

### 판정: **AUTH-OK**

| 항목 | 결과 |
|---|---|
| 인증 방식 | `UserTokenAuth` + bare hostname |
| hostname 형식 | bare (스킴 없음) — `https://` 제거 불필요 |
| hostname 도메인 | `*.palantirfoundry.com` |
| 사용 엔드포인트 | `client.ontologies.Ontology.list()` (read-only) |
| HTTP 상태 | 200 OK |
| SDK 버전 | foundry-platform-sdk 1.97.0 |
| 접근 가능 ontology 수 | **2개** |

**접근 가능 Ontology 목록 (RID 축약):**
1. `Ontology` — 시스템 기본 온톨로지 (rid …`000000000000`)
2. `mayh Ontology` — 사용자 계정 온톨로지 (rid …`840ccb0f2a0c`)

> "mayh Ontology"가 P0 스키마 작업 대상. 이 온톨로지에 Aircraft Object Type을 생성해야 함.

### 다음 자동 단계

사용자가 **§4 체크리스트 3번(Ontology Manager에서 Aircraft Object Type 생성)**을 완료하면 아래 단계를 이어받음:
1. `scripts/p0b_auth_check.py` 재실행으로 Aircraft 객체 존재 확인
2. `client.ontologies.OntologyObject.list()` 또는 OSDK로 Aircraft 1건 write/read 왕복
3. P0.md 완료 판정 업데이트

---

## 6. aip-integration.md §0 대비 정정 사항

> aip-integration.md는 수정하지 않음. 아래는 메인 스레드가 반영 판단할 정정/보강 후보.

1. **정정 — 온톨로지 스키마 생성은 API 불가, UI 전용**: §0/§2(2)는 "Ontology Manager에서 생성"이라 적어 UI를 암시하나, **"API로는 불가능(현재)"**을 명시할 것. Object/Action/Link Type 생성 API는 2026-01 기준 미출시(issue #318). → P0-B를 헤드리스로 완료할 수 없는 근본 이유.
2. **정정 — OSDK 설치 명령**: §3의 `pip install <생성된-osdk-패키지>`는 불완전. 실제는 **private index + 토큰** 필요:
   `pip install <PKG> --upgrade --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"`.
3. **보강 — Python 버전 함정**: §0의 "OSDK Python(>=3.9,<3.13)"은 맞지만, **이 머신은 3.14.5**라 생성 OSDK 설치 불가. Python **3.12 별도 환경**이 P0 전제. 반면 저수준 `foundry_sdk`(1.97.0)는 3.14 지원 — 대체 경로 후보로 명시할 가치.
4. **보강 — 저수준 SDK 최신 정보**: `foundry-platform-sdk` **최신 1.97.0**(2026-06-30), import 이름 **`foundry_sdk`**, `UserTokenAuth`/`ConfidentialClientAuth` 2종. §0의 "OSDK 2.x는 이 클라이언트와 통합" 진술과 일관.
5. **보강 — 토큰 발급 경로 명문화**: Account → Settings → Tokens(1회 표시). §0에 없던 운영 디테일.
