# SKAI — Air ISR Fusion Copilot

**공중·우주 상황인식 융합 코파일럿**

**Ontology-Based Air/Space Situational Awareness & Anomaly Detection**

Last updated: 2026-07-08 KST

SKAI는 공개 항적, 위성 궤도, 기상, 뉴스/OSINT를 온톨로지로 연결해 **"이 지역, 지금 하늘에서 뭐가 이상한가"** 에 출처가 달린 답을 주는 시스템이다. 모든 문장은 근거 객체(cites)를 물고 태어나고, 이상징후는 분석가의 승인(confirm/dismiss)으로만 확정되는 결정 루프로 이어진다.

중요한 경계: SKAI는 교전·표적 결심 시스템이 아니다. 산출은 상황인식·이상탐지·요약까지이며, 이상징후는 침해·적대행위의 확정이 아니라 **분석가가 확인해야 할 후보**다(신뢰도 0~1 병기, 단일 소스 결측은 단정하지 않음).

## 1. Competition Context

| 항목 | 내용 |
|---|---|
| 대회 | D4D \| Deploy for Defense Hackathon APAC - SEOUL |
| 일정 | 2026-07-03 ~ 2026-07-06. 공식 이벤트 페이지 기준 본 일정은 2026-07-04 ~ 2026-07-05, 발표 2026-07-06 |
| 성격 | APAC Defense Tech Builders Network, 24H defense-tech hackathon |
| 선택 트랙 | **T2 · OSINT & 국방인텔** (공중 유형) |
| 제출 문제 | 공개 항적·위성궤도·기상·뉴스/OSINT를 융합해 특정 지역의 공중·우주 상황을 파악하고 이상징후를 탐지하는 AI 기반 시스템 (배경: Project Maven형 다중소스 융합 코파일럿 = force multiplier) |
| 심사 기준 | Problem Fit 25 · Military Deployability 30 · Technical Execution 25 · Creativity 20 |
| 제공 지원 | Palantir Foundry Developer Tier · Morph Systems 멘토링 · StealthMole API(해커톤 한정, NDA) |

대회 정보 출처: [D4D Luma event page](https://luma.com/2ew4xn7b). 트랙명과 평가 기준은 D4D 제출 화면과 현장 안내 기준.

같은 대회 자매 프로젝트: [Project-Omija](https://github.com/Jaemani/Project-Omija) (방산 공급망 자격증명 조기경보 — 동일 T2 트랙).

## 2. Problem Statement

공중·우주 상황 인식에 필요한 데이터(항적·위성 궤도·기상·뉴스)는 이미 공개돼 있다. 병목은 데이터 양이 아니라 **연결과 의미 도출**이다. 분석가가 창 5개를 띄워놓고 눈으로 교차하는 동안 다음 질문들은 답을 얻지 못한다.

- 이 기체는 누구 것이고 군용인가 민간인가?
- 신호가 끊긴 것은 고의 소등인가, 수신 커버리지 문제인가?
- 그 침묵이 위성 통과 창, 같은 지역을 언급한 뉴스와 시공간적으로 겹치는가?
- AI가 요약해 준다면, 그 문장 하나하나를 어떤 관측으로 검증할 수 있는가?
- 사람이 지금 확인해야 할 것은 무엇인가?

SKAI의 문제의식은 **데이터 수집**이 아니라 **근거 있는 공중 상황 판단**이다. AI가 요약을 대신하더라도 근거 없는 주장(환각)은 군·정보 현장에서 쓸 수 없다.

## 3. Approach

### Core Hypotheses

1. 교차소스 이상징후는 flat table이 아니라 그래프여야 잡힌다.
2. 문장을 LLM이 생성하지 않고 온톨로지 사실에서 조립하면 환각이 구조적으로 차단된다.
3. 규칙(근거 강제)이 앱이 아니라 데이터 계층에 살아야 군 배포가 가능하다.
4. 군용기는 이중 경로로 봐야 정직하다 — 트랜스폰더를 켠 기체는 저신뢰 식별, 끈 기체는 부재(dropout) 탐지.
5. 공개 데이터만으로 무인 라이브 운영이 가능하다.

검증 결과: 1~4 검증 완료, 5는 조건부(대회 후 실운영에서 오탐 폭주·쿼터 소진을 실제로 겪고 탐지 의미 재정의·대체 소스·자동 해소 라이프사이클로 해소 — [§5](#5-current-implementation-level) 참조).

### Ontology Design

객체 11종 · 링크 11종 · 액션. 억지 모델링을 막기 위해 **스멜테스트**(다중홉 질의 / 엔티티 해소 / 상태 전이 액션 / provenance 그래프 중 2개 이상 통과 못 하면 폐기)를 설계 단계에서 먼저 걸었다. 전체 정의와 정당화는 [`ontology.md`](ontology.md).

핵심 객체:

| Object Type | 역할 |
|---|---|
| `Aircraft`, `Observation`, `Track` | 항공기 신원·ADS-B 관측(증거 객체)·시계열 경로(custody) |
| `Satellite`, `OrbitPass` | 위성과 관심지역 상공 통과 창 |
| `Region`, `WeatherState` | 지오펜스(KADIZ/OpArea)·지역 기상 |
| `NewsEvent` | OSINT/뉴스 증거 객체 (저신뢰 고정, 신뢰도 ≤0.4) |
| `Anomaly` | 파생 이상징후. status: candidate/confirmed/dismissed/resolved |
| `SituationAssessment` | 질의가 만드는 산출 인텔 객체 (문장별 cites) |

핵심 링크:

| Link Type | 의미 |
|---|---|
| `Anomaly.evidenced_by -> Observation/NewsEvent/OrbitPass` | provenance 백본 — 근거 없는 생성은 store가 거부 |
| `Anomaly.correlated_with -> Anomaly/OrbitPass/NewsEvent` | 교차소스 "은닉 정황" — 사유(시간차·거리) 영속 |
| `Observation.within -> Region` | 지오펜스 공간 조인 — 군용 접근·dropout의 발화 게이트 |
| `SituationAssessment.cites -> 객체` | 답변 문장 하나하나의 근거 |

깊이 증명 질의: *"지난 30분 KADIZ에서 ADS-B가 끊긴 기체가, 위성이 머리 위를 지나갈 때, 뉴스가 언급한 지역과 겹치나?"* — 4개 객체 타입의 시공간 교차는 한 행으로 표현할 수 없고, 이것이 온톨로지를 쓰는 이유다.

### Detection & Honesty Logic

신뢰도가 점수 이전에 **단정 금지 원칙**을 따른다.

| 유형 | 판정 | 신뢰도 |
|---|---|---|
| 비상 스쿽 7500/7600/7700 | 단일 관측으로 확정적 | 0.95 |
| ADS-B dropout | 민감구역 내 "지금 침묵"만 발화. 2차 소스 교차 확인 시에만 상향, 기체 복귀 관측 시 자동 해소(resolved) | 0.42 → 0.72 |
| 군용기 접근 | 군용 판정(공개 DB플래그 > 콜사인·대역 휴리스틱) AND 작전구역 진입 — 존재만으로는 경고 아님 | 0.50~0.65 |
| 위성 근접 | 공개 문서화된 ISR-관련 위성 허용목록 48기만 승격 — ISS 통과 같은 정상 사건은 경고로 올리지 않음 | 0.40 |
| 로이터링·급기동 | 물리 임계 기반(글리치 방어), 정황 표기 | 0.50~0.62 |

근거·경로 없이 파생 판단 객체(`Anomaly`, `SituationAssessment`)를 만들지 않는다 — store 레벨 불변식(EvidenceError/SentenceEvidenceError)으로 집행된다.

## 4. Data Used

### External / Public Data

| 출처 | 사용 방식 | 인증/리밋 |
|---|---|---|
| [OpenSky Network](https://opensky-network.org) | ADS-B 항적 (bbox 상태벡터) | 익명 ~400 크레딧/일 |
| [adsb.fi opendata](https://adsb.fi) | 2차 항적(반경 질의)·군용 DB플래그(`/v2/mil`)·dropout 교차확인 | 무인증, 1 req/s |
| [Celestrak](https://celestrak.org) | 위성 TLE → sgp4 로컬 계산으로 통과 창 산출 | 무인증, 12h 캐시 |
| [aviationweather.gov](https://aviationweather.gov) | METAR 5개 공항(인천·무안·제주·김해·청주) 한 호출 | 무인증 |
| [GDELT](https://www.gdeltproject.org) | KADIZ 관련 키워드 뉴스, 48h 상한 | 무인증, 5s 간격 |
| StealthMole (해커톤 API) | 위협 OSINT를 저신뢰 NewsEvent로 편입 (GM/RM/LM/TT 모듈만) | NDA·키 필요, 기본 off |

### Internal / Project Data

| 데이터 | 위치 | 용도 |
|---|---|---|
| 합성 시나리오 주입기 | `scripts/scenarios.py`, `data/demo/` | 라이브에 없는 이상징후의 결정적 재현 (replay 모드, `[합성]` 배지 명시) |
| 로컬 온톨로지 store | `data/skai.db` (SQLite, gitignore) | 런타임 권위 본 — 클론 시 각자 생성 |
| Foundry 온톨로지 | 사용자 enrollment (11객체·36액션) | 스키마 레벨 근거 게이트·staged review·Automation·AIP Logic |

금지 데이터·행위: 개인 크리덴셜류 StealthMole 모듈(CL/CDS/CB/CDF) 사용 금지, 비공개·유료 군용 피드 금지, 레이트리밋 우회 금지, 실시간 무장교전·표적 결심 자동화 범위 밖.

## 5. Current Implementation Level

### Implemented

| 영역 | 현재 수준 |
|---|---|
| 융합 파이프라인 | 5소스 연속 폴링(소스별 주기·오류 격리), 소스별 단위 정규화(ft/kt → SI), provenance 3필드 필수 |
| 이상탐지 | 6유형(비상 스쿽·dropout·로이터링·군용 접근·위성 근접·급기동) + 교차소스 상관(사유 영속) + resolved 라이프사이클(반증 증거 기반 자동 해소) |
| 코파일럿 | 의도 분류(요약/카운트/필터/왜/상관/기상/뉴스) → 병렬 read → 문장 조립(문장별 cites 강제, 무근거 시 거부) |
| 화면 | 지도(군용 구분 마커·선택 기체 트랙·지오펜스·위성 궤적)·타임라인(합성 배지·해소 그룹·시간창)·서브그래프·소스 신선도 |
| 결정 루프 | confirm/dismiss 상태 전이 영속, 질의마다 SituationAssessment 인텔 객체 생성 |
| 재현성 | replay 모드 = 소켓 차단으로 네트워크 0 증명 + 재기동 후 바이트 동일 |
| Tests | `pytest -q` 기준 **422 passed, 4 skipped** |

### Palantir Foundry Measurement

| 항목 | 결과 |
|---|---|
| Ontology | Object Type 11종 · Action 36종, 라이브 왕복 검증(read/write/traverse) |
| Evidence 게이트 | 무근거 `create-anomaly`를 **플랫폼 액션 검증이 거부** — 앱 우회 접근 포함 |
| OSDK | 0.12.0, 타입드 read + 액션 apply |
| AIP Logic | 2함수 실배선 — `explain-anomaly`(설명·신뢰도), `region-situation-summary`(상황 헤드라인). 온톨로지 객체 참조를 받아 서술 생성, 라이브 E2E 검증 |
| Staged Human Review | AI 산출 pending → 분석가 approve 시에만 온톨로지 반영 (플랫폼 레벨 E2E 검증) |
| Automation | 신규 비상 스쿽 감시 → 플랫폼 알림 실수신 검증 |
| **Pending** | read 권위 기본값은 로컬(재현성 우선, Foundry read는 opt-in) · `resolved` 상태는 Foundry에 액션이 없어 미러 안 됨 · 탐지·상관·citation 조립은 자체 엔진(AIP는 서술 생성까지) |

중요: "AIP가 탐지한다"는 사실이 아니다. 탐지는 설명 가능한 룰, 서술·요약은 AIP Logic, 근거 게이트·승인·자동화는 Foundry — 이 역할 분담을 과장 없이 말하는 것이 이 프로젝트의 태도다. 자체 냉정평가는 [`docs/EVALUATION.md`](docs/EVALUATION.md).

## 6. Screenshots

아래 대표 컷은 **2026-07-08 라이브 실데이터 캡처**다(실항적 258기·실 dropout 26건·위성 통과 77건 — 재현 불가능한 실세계 스냅샷). 전체 인벤토리(12장, 장면별 온톨로지 요소 설명·라이브/replay 구분): [`docs/SCREENS.md`](docs/SCREENS.md)

| 전체 화면: 지도·타임라인·소스 3열 | 코파일럿: 문장별 cites 배지 |
|---|---|
| <img src="docs/screens/01_overview.png" width="420" alt="Overview: map, timeline, sources"> | <img src="docs/screens/06_copilot_cites.png" width="420" alt="Copilot sentence-level citations"> |

| 근거(provenance) 카드 | 교차소스 상관 사유 |
|---|---|
| <img src="docs/screens/03_anomaly_evidence.png" width="420" alt="Anomaly evidence cards"> | <img src="docs/screens/04_correlation_reason.png" width="420" alt="Correlation with reason"> |

| 서브그래프: correlated_with | 결정 루프: confirm 상태 전이 |
|---|---|
| <img src="docs/screens/08_subgraph.png" width="420" alt="Ontology subgraph modal"> | <img src="docs/screens/10_confirm_loop.png" width="420" alt="Confirm state transition"> |

| 라이프사이클: 복귀 관측으로 자동 해소된 dropout | 소스 신선도(LIVE)·뉴스 |
|---|---|
| <img src="docs/screens/11_timeline_resolved.png" width="420" alt="Resolved anomalies group (live)"> | <img src="docs/screens/09_sources_news.png" width="420" alt="Source freshness and news panel"> |

## 7. Artifact Versions

| Version | 시기 | 산출물 | Status |
|---|---|---|---|
| P0 | 07-03~04 | 4소스 실응답 검증 + Foundry OSDK write/read 왕복(ROUNDTRIP-OK) | 정찰 — 가정 검증 |
| P1~P6 | 07-04 | 로컬 수직관통: 융합 → 탐지 5종 → cites 강제 코파일럿 → 지도/타임라인/서브그래프 → replay/live 이중 데모 | 테스트 106 |
| P7 | 07-04~05 | Foundry 이관: 11객체·36액션(UI 구축+실측 재검증 반복), evidence 게이트, dual-write, OSDK 0.8.0 | 라이브 왕복 14검증 |
| DR-0011 | 07-04 | 대화형 코파일럿(의도 분류)·실시간 폴링·프론트 재설계 | UX 라운드 |
| DR-0012 | 07-05 | 과장→사실 전환: AIP Logic 2함수 실배선, OSDK 타입드 read, Foundry read 모드, staged review·Automation 검증 | AIP 전 구성요소 실사용 |
| DR-0013 | 07-05 | 신호 정직화: 위성 ISR 허용목록, 상관 사유 영속, 합성 라벨 전면 표기, 뉴스 48h·오링크 수정, 설명 레이어 | 테스트 366 |
| 제출판 | 07-05 | 라이브 데모 URL(터널)·스크린샷·설명문 제출 (제출물 원본은 git 이력에 보존) | 제출 완료 |
| DR-0014 | 07-05~08 | 라이브 운영: adsb.fi 항적 대체·군용기 지도 가시화·dropout 의미 재정의(오탐 폭주 근절)·resolved 라이프사이클 | **테스트 422** — 순수 실데이터 운영 |

## 8. How To Run

Prerequisite: Python 3.12+. 계정·API 키·`.env` 불필요.

```bash
git clone https://github.com/Jaemani/SKAI.git && cd SKAI
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

scripts/demo.sh replay    # 오프라인 결정적 데모 (합성 시나리오, 네트워크 0) → http://localhost:8000
scripts/demo.sh live      # 실데이터 (KADIZ 실항적·기상·뉴스·위성 폴링)
scripts/demo.sh stop
.venv/bin/python -m pytest tests/ -q    # 422 passed
```

Foundry 모드(개별 토큰 + Python 3.12 전용 venv)와 팀 온보딩은 [`TEAM.md`](TEAM.md).

## 9. Repository Map

| 경로 | 역할 |
|---|---|
| `ontology/` | 온톨로지 모델·store(로컬 SQLite/Foundry 하이브리드)·custody·지오·엔티티링킹 |
| `connectors/` | OpenSky·adsb.fi(항적/군용/교차확인)·Celestrak·METAR·GDELT·RSS·StealthMole 폴러 |
| `anomaly/` | 탐지 룰 6유형·상관 엔진·설명 생성(template/claude/AIP)·라이프사이클·ISR 위성 허용목록 |
| `copilot/` | 의도 분류·병렬 read·문장 조립(cites 불변식)·서브그래프 빌더 |
| `server/` | FastAPI API + 오프라인 소켓 가드 |
| `web/` | 단일 페이지 프론트(지도·타임라인·코파일럿) |
| `scripts/` | demo.sh(replay/live)·demo_foundry.sh(실 Foundry 실연)·시나리오 주입기·P7 검증 스크립트 |
| `eval/`, `tests/` | 평가 하네스(합성 회귀·라이브 eval)·테스트 422 |
| `docs/` | 결정 기록·변경 이력·워크로그·평가·가이드 (아래 §10) |

## 10. Detailed Documents

| 문서 | 읽을 때 |
|---|---|
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | 무엇이 진짜고 무엇이 한계인지 — 자체 냉정평가 (여기부터) |
| [`docs/decisions/`](docs/decisions/) | DR-0001~0014 — 왜 이렇게 결정했나 |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | 시간순 변경 이력 (아키텍처·온톨로지) |
| [`docs/worklog/`](docs/worklog/) | 단계별 실행 로그 — P0~P7 + 각 라운드 상세·실측 |
| [`docs/SCREENS.md`](docs/SCREENS.md) | 기능별 화면 캡처 11장 + 온톨로지 요소 매핑 |
| [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) | 사용법·화면 구성 |
| [`docs/foundry-build-guide.md`](docs/foundry-build-guide.md) 외 `foundry-*.md` | Foundry 온톨로지·AIP Logic·staged review·Automation 재현 절차 |
| [`ontology.md`](ontology.md) · [`direction.md`](direction.md) · [`architecture.md`](architecture.md) · [`data-sources.md`](data-sources.md) · [`aip-integration.md`](aip-integration.md) · [`PROMPTS.md`](PROMPTS.md) | 개발 전 작성한 기획 SSOT 8문서 중 6종 |
| [`TEAM.md`](TEAM.md) | 팀 온보딩 — 실행·Foundry 접근·정직 원칙 |

## Future Direction

1. **AIP triage** — 남은 저신뢰 후보(dropout)의 신호/노이즈 분류를 AIP Agent가 온톨로지 서브그래프 traverse로 수행. "AIP여야 풀리는 문제"의 다음 지점
2. **Foundry 갭 종결** — `resolve-anomaly` 액션 추가로 라이프사이클 미러, 온톨로지 스키마 스냅샷 레포 박제
3. **지속 운영** — 상시 머신 dual-write → Workshop 대시보드 → 스트리밍 인제스트(엔터프라이즈 영역)
4. **탐지 고도화** — 진짜 교차소스 엔티티 해소, 착륙 추세 억제, 2소스 독립 교차확인 자동 운용
