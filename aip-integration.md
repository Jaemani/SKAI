# aip-integration.md — Palantir AIP + OSDK 실사용 (Air ISR)

AIP는 옵션이 아니라 **척추**. 이 문서 = 온톨로지(`ontology.md`)를 Foundry에 올리고 OSDK/AIP Logic로 앱을 짓는 구체 경로. 검증된 사실 기반(2026-07 조사).

> 사전조건: build.palantir.com Developer Tier 가입됨(무료). Morph Systems 현장 세션/멘토링 활용.

---

## 0. 검증된 사실 (조사 결과)
- **OSDK (Ontology SDK)**: Foundry enrollment마다 **생성되는** 타입드 SDK. Python(>=3.9,<3.13) pip 설치. Ontology의 Object Type이 클래스가 되고, Action/Query에 직접 접근. Developer Console에서 내 온톨로지용 문서 생성됨. TS/Java/Python 지원.
- **Foundry Platform SDK** (`foundry-platform-sdk`, PyPI): 저수준 플랫폼 API. OSDK 2.x는 이 클라이언트와 통합(단일 client).
- **AIP Logic**: **노코드**로 LLM 함수 만드는 환경. 온톨로지에 built-in 접근. 프롬프트 엔지니어링·테스트·평가·automation. 함수 출력이 **staged human review** 후 온톨로지 edit로 적용 가능.
- **OSDK ↔ AIP Logic 연결**: OSDK로 만든 Python/TS/Java 앱이 AIP Logic 함수를 built-in 호출. 즉 커스텀 앱에서 AIP 추론을 부른다.
- 참고 문서: palantir.com/docs/foundry/ontology-sdk/python-osdk, /docs/foundry/logic/overview, build.palantir.com(튜토리얼: OSDK로 앱 end-to-end).

### §0-보강: P0-B 검증 결과 (2026-07-04, 상세·출처: `docs/worklog/P0B-foundry.md`)
- **스키마 생성은 UI 전용(API 불가)**: Object/Link/Action Type 생성 API는 미출시(2026-01 기준 open request, foundry-platform-python #318). **Ontology Manager 브라우저 작업이 유일 경로** → 이 단계는 헤드리스 자동화 불가.
- **생성 OSDK 설치엔 private index + 토큰 필요**: `pip install <PKG> --upgrade --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"` (PKG·INDEX-URL은 Developer Console 앱 Overview).
- **Python 버전 함정**: 생성 OSDK는 >=3.9,<3.13인데 이 머신은 3.14.5 → **3.12 전용 venv 필요**. 저수준 `foundry-platform-sdk`(1.97.0, import명 `foundry_sdk`)는 3.10~3.14 지원 → 3.14 대체 경로 후보.
- **토큰 발급 경로**: build.palantir.com → Account(사이드바 하단) → Settings → Tokens → Generate(**1회만 표시**). env `FOUNDRY_TOKEN`/`FOUNDRY_HOSTNAME` 자동 감지.

## 1. 아키텍처: AIP를 spine으로
```
[Public feeds]  OpenSky / Celestrak / METAR / GDELT
       │  (1) ingest
       ▼
[Foundry Datasets]  raw → clean (파이프라인/스케줄)
       │  (2) map to objects
       ▼
[Ontology]  Aircraft·Observation·Track·Satellite·OrbitPass·Region·NewsEvent·Anomaly·Assessment
       │                         ▲ (4) Actions write back (human review)
       │  (3) OSDK 타입드 접근      │
       ▼                         │
[App / Agent]  OSDK(Python/TS) ── AIP Logic 함수 호출 ──> 이상탐지·요약 추론
       │
       ▼
[Frontend]  지도+타임라인+채팅 (OSDK로 객체 read, Action 호출)
```

## 2. 단계별 구현
### (1) Ingest → Foundry
- 커넥터(Python)가 공개 API를 폴링 → Foundry Dataset에 적재. Dev Tier에서 코드 워크스페이스/파이프라인 사용.
- 대안(초기 속도용): 로컬에서 정규화한 뒤 Foundry로 push. 단 **온톨로지는 Foundry에 두는 게 핵심**.

### (2) 온톨로지 정의
- Ontology Manager에서 `ontology.md`의 Object/Link/Action Type을 생성. (**UI 전용** — 생성 API 없음, §0-보강)
- Observation→Aircraft(observed_as), Anomaly→evidence(evidenced_by) 등 링크 정의. Action Type에 evidence 필수 파라미터.

### (3) OSDK 생성·사용
```bash
# Developer Console에서 내 Ontology용 OSDK 패키지 발행 후 (PKG·INDEX-URL은 앱 Overview에서):
pip install <PKG> --upgrade --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"  # Python 3.12 venv에서
# 저수준 대체(3.14 OK): pip install foundry-platform-sdk
```
```python
# ✅ 실행 검증 완료(2026-07-04, ROUNDTRIP-OK) — 상세·전체 스니펫: docs/worklog/P0B-foundry.md §8-4
# 하이브리드: read = 생성 OSDK(skai_osdk_sdk), write = 저수준 foundry_sdk의 액션 apply
# (발행 OSDK에 Action 미포함 시 client.ontology.actions가 비므로 저수준 우회 — 재발행 시 Action 포함 권장)
import os
from skai_osdk_sdk import FoundryClient, UserTokenAuth   # 생성 OSDK (read)
import foundry_sdk                                        # 저수준 (write=action)
osdk = FoundryClient(auth=UserTokenAuth(os.environ["FOUNDRY_TOKEN"]), hostname=os.environ["FOUNDRY_HOSTNAME"])
ac = osdk.ontology.objects.Aircraft.get("<icao24>")       # read-back 검증됨
# 객체 read
for ac in client.ontology.objects.Aircraft.where(...):
    ...
# Action 호출 (human-on-the-loop: 제안→검토)
client.ontology.actions.create_anomaly(type="emergency_squawk", evidence=[obs_id], ...)
```

### (4) AIP Logic 함수
- **AnomalyExplainer**: Anomaly 후보 + 근거 Observation을 받아 자연어 설명·신뢰도 산출. 온톨로지 read 권한만 부여(보안모델).
- **RegionSituationSummary**: Region+window의 Anomaly/이벤트를 aggregate → SituationAssessment 텍스트(문장별 cites). 출력은 staged review 후 Assessment 객체로.
- Logic 함수는 OSDK 앱에서 호출하거나 automation으로 트리거.

## 3. LLM 선택
- **온톨로지 내부 추론**: AIP Logic의 플랫폼 제공 모델 사용(거버넌스·권한 통합).
- **커스텀 에이전트 코드**(OSDK 앱에서 직접): Claude API 가능 — `claude-opus-4-8`(고난도 추론/요약), `claude-sonnet-5`(균형), `claude-haiku-4-5-20251001`(경량 분류). 키는 환경변수.
- 원칙: 사실추출·citation 매핑은 룰+온톨로지로 하드하게, 서술·설명만 LLM.

## 4. 데모에서 AIP가 "얕지 않게" 보이는 법
- 화면에서 **Action 호출 → 객체 상태 전이 → provenance 링크**가 실제로 도는 걸 보여준다(단순 챗 답변 X).
- "이 이상징후를 confirm" 클릭 → Anomaly.status 전이 → Assessment 재생성 → 근거 링크가 원 Observation으로.
- 온톨로지 그래프 뷰로 Aircraft-Observation-Anomaly-NewsEvent 서브그래프 시각화.

## 5. 리스크 & 폴백
- **AIP 러닝커브**: P0에서 온톨로지 1객체(Aircraft)+1링크+1액션+OSDK read를 먼저 뚫어 파이프 검증. 막히면 Morph 멘토.
- **폴백(보험, plan A 아님)**: AIP가 시간 내 안 되면 로컬 SQLite로 같은 온톨로지 스키마를 구현하고 화면은 유지 → 이후 Foundry로 이관. 온톨로지 설계는 어느 쪽이든 재사용.
