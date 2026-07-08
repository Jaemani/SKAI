# SKAI — Air ISR Fusion Copilot

> **"이 지역, 지금 하늘에서 뭐가 이상한가?"** — 공개 항적·위성궤도·기상·뉴스/OSINT를 **온톨로지**로 융합해, 모든 문장에 출처가 달린 상황 요약과 이상징후를 지도·타임라인으로 답하고, 분석가의 승인(Action)으로 결정을 잇는 공중 ISR 코파일럿.

빠른 실행은 [§8 실행](#8-실행), 문서 전체 지도는 [§9 문서 지도](#9-문서-지도-상세는-여기로), 팀원 온보딩은 [`TEAM.md`](TEAM.md).

---

## 1. 대회 정보

| 항목 | 내용 |
|---|---|
| 대회 | **D4D \| Deploy for Defense Hackathon APAC — SEOUL** |
| 트랙 | **T2 · OSINT & 국방인텔** (공중 유형) |
| 과제 | 공개 항적·위성궤도·기상·뉴스/OSINT를 융합해 특정 지역의 공중·우주 상황을 파악하고 이상징후를 탐지하는 AI 기반 시스템 (배경: 미국 Project Maven형 다중소스 융합 코파일럿 = force multiplier) |
| 일정 | 개발 시작 2026-07-03 · 제출 마감 2026-07-05 · 발표 2026-07-06 |
| 심사 기준 | Problem Fit 25 · **Military Deployability 30** · Technical Execution 25 · Creativity 20 |
| 제공 지원 | Palantir Foundry Developer Tier · Morph Systems 멘토링 · StealthMole API(해커톤 한정, NDA) |

## 2. 문제의식

공중·우주 상황 인식에 필요한 데이터(항공기 항적·위성 궤도·기상·뉴스/OSINT)는 이미 공개돼 있다. 병목은 데이터 양이 아니라 **연결과 의미 도출**이다 — 분석가가 창 5개를 띄워놓고 눈으로 교차하는 동안 이상징후(비정상 항적, ADS-B 신호 소실, 위성 통과와의 시공간 중첩)를 실시간으로 따라가지 못한다. 그리고 AI가 요약을 대신하더라도 **근거 없는 주장(환각)** 은 군·정보 현장에서 쓸 수 없다. 이 두 문제를 동시에 겨냥했다.

## 3. 문제 접근 방식

### 설계 원칙 (개발 전 확정 — 초기 커밋이 코드 0줄, 기획문서 8개)

1. **온톨로지가 척추** — 앱을 먼저 짓지 않고 도메인을 객체 11종·링크 11종·액션으로 모델링. 억지 모델링을 막기 위해 **스멜테스트**(다중홉 질의·엔티티 해소·상태 전이 액션·provenance 그래프 중 2개 이상 통과 못 하면 폐기)를 스스로에게 먼저 걸었다 → [`ontology.md`](ontology.md)
2. **provenance 강제** — 모든 주장은 근거 객체로 역추적. 근거 없는 이상징후·문장은 저장 단계에서 거부(환각의 구조적 차단).
3. **결정 루프, Q&A 아님** — AI가 근거를 인용해 제안하고, 사람이 승인(confirm/dismiss)해야 상태가 바뀐다(human-on-the-loop).
4. **합법 공개소스만** — ToS·레이트리밋 준수, 무단 스크래핑 금지, 표적타격 자동화는 범위 밖 → [`CLAUDE.md`](CLAUDE.md) 가드레일
5. **정직 원칙** — 과장 금지. 자체 냉정평가([`docs/EVALUATION.md`](docs/EVALUATION.md))로 과장을 색출하고, 지우는 대신 **사실로 전환**하는 라운드를 반복했다.

### 참조 자료

- **내부(기획 SSOT, 개발 전 작성)**: [`direction.md`](direction.md)(백본 5요소) · [`ontology.md`](ontology.md)(도메인 모델·정당화) · [`aip-integration.md`](aip-integration.md)(Foundry/OSDK/AIP 실사용 경로) · [`data-sources.md`](data-sources.md)(소스 카탈로그·실응답 검증) · [`architecture.md`](architecture.md)(파이프라인 설계) · [`PROMPTS.md`](PROMPTS.md)(P0~P6 실행 계획)
- **외부 데이터**: [OpenSky Network](https://opensky-network.org)(ADS-B 항적) · [adsb.fi opendata](https://adsb.fi)(2차 항적·군용 DB플래그·교차확인) · [Celestrak](https://celestrak.org)(위성 TLE→sgp4 통과 계산) · [aviationweather.gov](https://aviationweather.gov)(METAR) · [GDELT](https://www.gdeltproject.org)(뉴스 인덱스) · StealthMole(위협 OSINT, 해커톤 제공·저신뢰 편입)
- **외부 기술**: Palantir Foundry Ontology/Actions · OSDK(타입드 SDK) · AIP Logic(노코드 LLM 함수) · Automation · sgp4 · FastAPI · Leaflet

## 4. 문제 해결 방식 — 가설과 검증

| 가설 | 검증 결과 |
|---|---|
| **H1. 교차소스 이상징후는 flat table이 아니라 그래프여야 잡힌다** | ✅ "ADS-B가 끊긴 기체 × 같은 시공간의 위성 통과 × 같은 지역 언급 뉴스"를 `correlated_with` 링크(+시간차·거리 사유 영속)로 묶는 은닉 정황 내러티브 구현 — 4객체 교차는 1행으로 표현 불가 |
| **H2. 문장을 LLM이 생성하지 않으면 환각이 구조적으로 차단된다** | ✅ 문장은 온톨로지 사실에서 조립되고 cites 없는 문장은 저장 거부. 라이브 실측: 산출 문장 24/24 출처 인용(100%), 맨몸 LLM 대비 기계검증 가능 출처 10:0 |
| **H3. 규칙이 앱이 아니라 데이터 계층에 살아야 배포 가능하다** | ✅ 같은 온톨로지를 실제 Foundry에 구축(11객체·36액션) — **앱을 우회해도 무근거 이상징후 생성을 플랫폼이 거부**, AI 제안→사람 승인(staged review)·플랫폼 자동 알림(Automation) 라이브 검증 |
| **H4. 군용기는 이중 경로로 봐야 정직하다** | ✅ 트랜스폰더 ON = 콜사인·공개 DB플래그로 저신뢰 식별(실군용기 라이브 포착), OFF = 민감구역 내 **부재(dropout)를 교차소스로 탐지** — "군용기를 다 잡는다"는 주장 없이 성립 |
| **H5. 공개 데이터만으로 무인 라이브 운영이 가능하다** | ⚠️ **조건부** — 대회 후 실운영에서 오탐 폭주(정상기 전수 dropout)·API 쿼터 소진·프로세스 고아화를 실제로 겪고, 탐지 의미 재정의·대체 소스·라이프사이클(자동 해소)로 해소함. 이 과정이 §5 후반 라운드다 |

### 진행 흐름

**기획(07-03)** 8문서 → **P0 이중 정찰** 4소스 실응답 + Foundry OSDK 왕복 검증 → **P1~P6 수직관통**(융합→탐지→citation 코파일럿→지도/타임라인/서브그래프→오프라인 재현 데모) → **P7 Foundry 이관**(스키마는 UI 전용이라 사용자 수작업 + 코드 병렬, 하이브리드 스토어) → **자체 냉정평가**(EVALUATION: "AIP-spine은 과장" 판정) → **DR-0012 갭 종결**(AIP Logic 실배선·OSDK 타입드 read·Foundry read 모드) → **DR-0013 신호·화면 정직화**(위성 ISR 허용목록·상관 사유·합성 라벨 전면 표기·설명 레이어) → **DR-0014 라이브 운영 라운드**(adsb.fi 항적 대체·군용 가시화·dropout 의미 재정의·resolved 라이프사이클).

## 5. 산출물 — 버전별 설명

| 단계 (시기) | 산출물 | 수준 |
|---|---|---|
| **P0** (07-03~04) | 4소스 실응답 스키마 검증 + Foundry OSDK write/read 왕복(ROUNDTRIP-OK) | 정찰 완료 — 가정 검증 |
| **P1~P6** (07-04) | 로컬 수직관통: 4소스 융합 → 이상탐지 5종 → 문장별 cites 강제 코파일럿 → 지도·타임라인·서브그래프 → 이중 모드 데모(`replay`=네트워크 0·바이트 동일 재현 / `live`) | 테스트 106, 데모 가능 |
| **P7 + 스키마 라운드** (07-04~05) | Foundry 온톨로지 11객체·36액션(사용자 UI 구축 + 실측 재검증 반복), evidence 강제 액션 게이트, dual-write, OSDK 0.8.0 | 라이브 왕복 14검증 |
| **DR-0011** (07-04) | 대화형 코파일럿(의도 분류)·실시간 폴링·프론트 재설계 | UX 라운드 |
| **DR-0012** (07-05) | 과장→사실 전환: **AIP Logic 2함수 실배선**(explain-anomaly·region-situation-summary — 온톨로지 객체 참조로 서술 생성), OSDK 타입드 read, Foundry-primary read 모드, staged review·Automation 검증, OSDK 0.12.0 | AIP 전 구성요소 실사용 도달 |
| **DR-0013** (07-05) | 신호 정직화: 위성 근접을 ISR 허용목록 48기로 게이트(스팸 차단), 상관 사유(시간차·거리) 영속·표시, 합성 라벨 전면 전파, 뉴스 48h·오링크 수정, 설명 레이어(서브그래프 안내·소스 신선도) | 테스트 366 |
| **제출판** (07-05) | 라이브 데모 URL(터널)·스크린샷 8장·설명문 제출 (제출물 원본은 저장소에서 정리 — git 이력 `4dab580` 이전에 보존) | 제출 완료 |
| **DR-0014** (07-05~08) | 라이브 운영: adsb.fi 항적 대체(쿼터 무관)·군용기 지도 가시화(실기체 포착)·**dropout 의미 재정의**(오탐 폭주 근절)·**resolved 라이프사이클**(반증 증거 기반 자동 해소)·트랙 표시 정리 | **테스트 422** — 순수 실데이터로 탐지→해소 루프 성립 |

## 6. 최종 결론 (객관·솔직)

**실측으로 방어되는 것:**
- 온톨로지는 장식이 아니라 데이터의 존재 방식이다 — 화면의 모든 요소(마커·근거 패널·상관 사유·서브그래프·cites 배지)가 객체·링크의 렌더이고, provenance 강제가 store 불변식으로 집행된다(스멜테스트 3강 통과, [`EVALUATION.md`](docs/EVALUATION.md) §3).
- 환각 차단은 프롬프트가 아니라 구조다 — 문장 조립·cites 강제로 라이브 인용 100% vs 맨몸 LLM 0.
- Palantir는 기능이 아니라 배포 축이다 — 기능은 로컬로 완결되지만(정직하게 인정), **규칙의 집행 지점**(앱 우회 불가 액션 게이트)·거버넌스(staged review·Automation)·조직 스케일은 플랫폼에서만 성립. 이 역할 분담을 과장 없이 말하는 것이 이 프로젝트의 태도다.
- 재현성 — replay는 소켓 차단으로 네트워크 0을 증명하고 재기동 후 바이트 동일. 테스트 422 passed.

**정직한 한계:**
- **AIP는 서술 생성까지만** — 탐지·상관·평가·근거 강제는 자체 엔진이다. "AIP가 탐지한다"는 사실이 아니다.
- read 권위 기본값은 로컬 SQLite(재현성 우선), Foundry read는 opt-in. `resolved` 상태는 Foundry에 액션이 없어 미러되지 않는다(갭 기록).
- 엔티티 해소는 키워드 수준(뉴스↔실체의 진짜 ER 아님). dropout은 "고의 소등 vs 수신 커버리지"를 원리적으로 단정할 수 없어 저신뢰(0.42)로만 표기하며, 교차확인은 항적 소스와 독립일 때만 켠다.
- 라이브 P/R은 ground truth가 없어 산출 불가 — 합성 회귀는 결정성 검증이지 실세계 정확도가 아니다.
- 공개 ADS-B는 수신기 밀도에 의존(서해 외곽 희소), Developer Tier는 상시 운영 환경이 아니다.

## 7. 나아가야 할 방향

1. **AIP triage** — 남은 저신뢰 후보(dropout 등)의 신호/노이즈 분류를 AIP Agent가 온톨로지 서브그래프를 traverse하며 수행 — "AIP여야 풀리는 문제에 AIP를 쓰는" 다음 지점 (팀 결정 대기, [`TEAM.md`](TEAM.md) 논의 방향)
2. **Foundry 갭 종결** — `resolve-anomaly` 액션 추가로 라이프사이클 미러, 온톨로지 스키마 스냅샷을 레포에 박제(읽기 API로 덤프)
3. **지속 운영** — 상시 머신에서 Foundry dual-write 상주화 → Workshop 대시보드로 분석가 화면을 플랫폼 안으로 → 스트리밍 인제스트(엔터프라이즈 영역)
4. **탐지 고도화** — 진짜 교차소스 엔티티 해소, 착륙 추세 기반 dropout 억제, 2소스 독립 교차확인 운용 원칙 자동화

## 8. 실행

```bash
git clone https://github.com/Jaemani/SKAI.git && cd SKAI
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 계정·키·.env 불필요

scripts/demo.sh replay    # 오프라인 결정적 데모 (합성 시나리오, 네트워크 0) → http://localhost:8000
scripts/demo.sh live      # 실데이터 (KADIZ 실항적·기상·뉴스·위성 폴링)
scripts/demo.sh stop
.venv/bin/python -m pytest tests/ -q    # 422 passed
```

Foundry 모드(개별 토큰 필요)와 팀 온보딩 상세는 [`TEAM.md`](TEAM.md).

## 9. 문서 지도 (상세는 여기로)

| 무엇 | 어디 |
|---|---|
| 뭐가 진짜고 뭐가 한계인지 (자체 냉정평가) | [`docs/EVALUATION.md`](docs/EVALUATION.md) |
| 의사결정 기록 (왜 이렇게 했나) | [`docs/decisions/DR-0001~0014`](docs/decisions/) |
| 시간순 변경 이력 | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |
| 단계별 실행 로그 (P0~P7 + 각 라운드 상세) | [`docs/worklog/`](docs/worklog/) |
| Foundry 구축·AIP Logic·staged review·Automation 재현 가이드 | [`docs/foundry-*.md`](docs/) |
| **화면 캡처 — 기능별 시각 문서** | [`docs/SCREENS.md`](docs/SCREENS.md) |
| 사용법·화면 설명 | [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md) |
| 팀 온보딩 | [`TEAM.md`](TEAM.md) |
