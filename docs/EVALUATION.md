# EVALUATION.md — SKAI 계획 대비 실작동 냉정 평가

> 목적: 초기 기획문서(README·direction·ontology·architecture·aip-integration·PROMPTS·CLAUDE)를 채점 기준으로,
> 실제 코드·커밋·워크로그 실측을 근거로 "무엇이 진짜 작동하고 무엇이 부풀려졌나"를 판정한다.
> 작성 원칙: bias-to-honest, 포장·변명 금지. 이전 "완성" 보고도 실제 수준을 재판정.
> 근거 파일: 실코드(`ontology/` `anomaly/` `copilot/` `connectors/` `server/`) + `docs/CHANGELOG.md` + `docs/worklog/P7-foundry-migration.md` §17 + `docs/worklog/p5_eval.json` + `git log`.
> 작성일: 2026-07-04. **읽기 전용 평가(코드 무수정).**

---

## 총평 (한 문단)

**SKAI는 "껍데기"가 아니다 — 로컬 수직관통(P0~P6)은 실제로 깊고 작동하며(4소스 라이브 인제스트 → 온톨로지 → 이상탐지 5종 → 문장별 citation 강제 코파일럿 → 지도/타임라인/서브그래프, 테스트 184통과), 이 부분이 계획의 핵심 가치를 대부분 실현한다.** 온톨로지 설계는 억지가 아니라 정당하고(스멜테스트 4중 3이 강하게 통과, provenance 강제가 store 레벨에서 코드로 집행됨 = GPT-wrapper와의 진짜 해자), Foundry 온톨로지도 실재한다(사용자가 Object 11·Action 36 구축, OSDK 0.8.0 발행, 라이브 왕복 검증 완료). **가장 큰 정직성 갭은 "AIP-spine" 프레이밍이었다**: AIP의 *추론* 계층(AIP Logic)은 `NotImplementedError` 스텁이고, 실제 추론(탐지·설명·평가)은 전부 로컬 엔진(룰+템플릿+선택적 로컬 `claude -p`)에서 돌며, 실제 데모 서버(`server/app.py`)는 Foundry가 아니라 로컬 SQLite(`store_local.py`)를 읽는다. Foundry는 병렬로 검증된 write/read 경로(`demo_foundry.sh`)이며 read 권위본은 로컬이다. **"AIP 위에서 추론이 돈다"는 원래 서술은 과장이었고, 이미 최신 커밋(`f06d3fc`)과 demo.md에서 "추론은 우리 엔진, Foundry엔 온톨로지·액션 게이트"로 정직하게 교정됐다.** 계획 대비 실작동은 **로컬 스택 ~85%, Foundry 이관 ~75%(달성 가능분 기준), AIP 실활용 ~40%(온톨로지·액션만, 추론 0)** 수준으로 추정한다.

**판정 개수: MET 19 · PARTIAL 5 · GAP 3 · OVERSOLD(교차) 4.**

---

## 1. direction.md 백본 5요소

| # | 요소 | 판정 | 근거(1줄) | 근거 위치 |
|---|---|---|---|---|
| 1a | **문제**(파편화된 공중 상황인식) | **MET** | 문제 정의가 시스템 전체 구조와 정합, 실제로 융합 병목을 겨냥 | direction.md §1, 전 코드 |
| 1b | **사용자**(ISR 분석가/당직 상황장교) | **MET**(프레이밍) | 결정루프·human-on-loop 워크플로가 이 사용자를 겨냥. 단 실사용자 검증은 없음(해커톤이라 정상) | direction.md §2, `server/app.py` confirm/dismiss |
| 1c | **데이터**(공개·합법 소스) | **MET** | OpenSky·Celestrak·METAR·GDELT+StealthMole 실커넥터, 라이브 3사이클 실인제스트 검증 | `connectors/*.py`, CHANGELOG P1/P3(Aircraft 32·Obs 74·위성 94 등) |
| 1d | **산출**(출처 달린 요약+이상징후+지도/타임라인) | **MET** | 3요소 전부 구현·작동, 문장별 cites 강제 | `copilot/assessment.py`, `web/index.html`, `p4_copilot.png` |
| 1e | **차별성/moat**(온톨로지 척추 = AIP-spine) | **PARTIAL / OVERSOLD** | 온톨로지·provenance 해자는 진짜. 단 "AIP-spine(AIP 추론)"은 과장 — 추론은 로컬 | ↓ §5·§7, `anomaly/explainer.py:233` 스텁 |

---

## 2. PROMPTS.md P0~P6 성공기준

| 단계 | 성공기준(요지) | 판정 | 근거 |
|---|---|---|---|
| **P0** | 4소스 200/파싱 + OSDK 온톨로지 객체 read/write 왕복 | **MET** | 소스 실응답 확인, OSDK 왕복 ROUNDTRIP-OK. 단 스키마 생성은 UI 전용(헤드리스 불가)로 판명 | `P0B-foundry.md`, CHANGELOG "ROUNDTRIP-OK" |
| **P1** | 실 항적이 온톨로지 경유로 지도에 뜨고 갱신 | **MET**(문서화된 편차) | 라이브 3사이클 실동작·지도 렌더. **단 프론트 read가 OSDK 아니라 FastAPI→store_local**(DR-0003 승인 편차) | `p1_map.png`, `server/app.py:38,68` |
| **P2** | 주입→룰→설명→CreateAnomaly(evidence)→화면, confirm/dismiss | **MET** | 비상 스쿽 끝단 작동, evidence 없으면 store 레벨 EvidenceError 차단. 설명=템플릿(AIP Logic 아님) | `anomaly/actions.py`, `p2_anomaly.png` |
| **P3** | 4종 소스 한 온톨로지 시공간 정렬 | **MET** | 4소스 공존, 소스별 카운트 산출 | `p3_fusion.png`, `/api/counts` |
| **P4** | NL질의→요약+이상징후+근거링크, **문장별 cites** | **MET** | 문장이 사실에서 조립되고 각 cites 보유, 무근거 문장 혼입 시 전체 거부(SentenceEvidenceError) | `copilot/assessment.py:1~13`, `p4_copilot.png` |
| **P5** | 이상탐지 3종+·correlated_with 내러티브·**P/R + 맨몸 LLM 비교** | **PARTIAL** | 탐지 5종·내러티브 MET. **P/R 1.00/1.00은 자작 12합성 시나리오 = 준-동어반복**(라이브 P/R 아님, 정직히 명시됨). 맨몸 비교는 "citation 유무" 우위지 정확성 우위 아님 | `p5_eval.json`(per_type P/R=1.0, `bare_llm` 대비) |
| **P6** | 네트워크 0 재현 + 심사 4항목 대응 + "AIP 얕지 않음" 시연 | **MET** | replay 소켓가드로 egress 0·SHA-256 재현. 데모 대본은 이미 AIP 사용수준 정직화됨 | `demo.md`, `server/offline_guard.py`, git `f06d3fc` |

---

## 3. ontology.md §0 스멜테스트 4기준 (억지/얕음 판정)

| 기준 | 판정 | 실제 충족 여부 |
|---|---|---|
| **1. 다중홉 질의** | **MET(강)** | 실제로 Region ← within ← Observation → Aircraft, OrbitPass → Region, NewsEvent → Region, Anomaly → evidence를 traverse. 1테이블 조인 불가. `copilot/assessment.py` 병렬 read + `store_foundry._traverse` 라이브 확인 |
| **2. 엔티티 해소** | **PARTIAL(얕음)** | icao24 자연키로 Observation→Aircraft custody 병합 = 진짜지만 **공유 PK가 있는 "쉬운" ER**. 이종 소스 간 진짜 ER(뉴스 언급↔Aircraft 매칭)은 **키워드 매칭**뿐(mentions). 4기준 중 유일하게 얕음 |
| **3. 액션이 상태를 바꾼다** | **MET(강)** | confirm/dismiss가 Anomaly.status 전이, set-region-alert-level Modify 전이. human-on-loop, 코드·Foundry 양쪽 집행 | `anomaly/actions.py`, P7 §17 |
| **4. provenance 그래프** | **MET(가장 강)** | Anomaly—evidenced_by→Observation, Assessment—cites→객체를 **store 레벨에서 강제**(근거 없으면 write 거부). 프로젝트의 진짜 척추 | `ontology/store.py` validate_evidence/provenance |

**결론: ≥2 요구를 3강+1얕음으로 충족 → 온톨로지는 정당하다(억지 아님).** 다만 "엔티티 해소"를 강점으로 내세우면 과장 — icao24 custody는 자랑거리가 아니고, 진짜 교차소스 ER은 미구현.

---

## 4. 온톨로지 링크 11종 — "flat table로 안 되는 이유" 성립 검증

| 링크 | 성립? | 코멘트 |
|---|---|---|
| observed_as (AC—Obs) | ✅ | custody 1:N, 진짜 |
| composed_of (Track—Obs) | ✅ | gap 탐지에 시퀀스 필요 |
| operated_by (AC—Operator) | △ **약** | 단순 FK, flat table도 가능. 그래프 정당화 약함(과장 아님·무해) |
| within (Obs—Region) | ✅ | 지오펜스 공간조인 |
| of / over (OrbitPass—Sat/Region) | ✅ | 시공간 상관 |
| mentions (News—Region/Op/AC) | △ **약** | N:M이나 **키워드 링킹**(진짜 엔티티링킹 아님) |
| **evidenced_by** (Anomaly—근거) | ✅✅ | provenance 백본, 핵심 |
| involves (Anomaly—AC/Sat) | ✅ | 주체 연결 |
| **correlated_with** (Anomaly—Anomaly) | ✅✅ | 교차소스 "은닉 정황" — 진짜 그래프 |
| aggregates (Assessment—Anomaly) | ✅ | 요약 묶음 |
| cites (Assessment—Obs/News) | ✅✅ | 문장별 근거 |

**과장 링크 색출 결과: 심각한 과장 없음.** operated_by(FK)·mentions(키워드)만 그래프 정당화가 약하나 유해하지 않다. 나머지 9종은 진짜 그래프 문제.

---

## 5. AIP 사용 수준 정직 판정 (구성요소별)

| 구성요소 | 판정 | 실제 상태 |
|---|---|---|
| **Ontology**(Object/Link Type) | **실사용(MET)** | 사용자가 Ontology Manager로 11객체 구축, 속성·PK 스펙 정합. 라이브 introspection 확인 | P7 §17-1 |
| **Actions**(Action Type) | **실사용(MET)** | create×11·edit×11·delete×11·confirm/dismiss·set-alert = 36액션. evidence 강제(observations required), 상태전이, 라이브 apply 검증 | P7 §17-4 |
| **OSDK** | **부분(PARTIAL)** | 0.8.0 발행·설치됨. **단 실제 read는 저수준 `foundry_sdk`(dict)** 사용(`store_foundry.py:653` OntologyObject.list). OSDK의 실역할 = 스키마 실재 증명 + write(action apply) | P7 §17-1, `store_foundry.py:269,653` |
| **AIP Logic**(LLM 추론함수) | **미사용(GAP)** | `AipLogicExplainer.explain()` = **NotImplementedError 스텁**. AnomalyExplainer·RegionSituationSummary는 Foundry에 존재하지 않음 | `anomaly/explainer.py:233-247` |
| **read 권위(authority)** | **미사용(GAP)** | 실제 데모·코파일럿 read = **로컬 SQLite**. Foundry는 dual-write의 스파인이고 read 권위본은 로컬 | `server/app.py:38,68`, P7 §17-6 잔여1·2 |
| **추론 엔진** | **미사용(GAP)** | 탐지=룰(`rules.py`), 설명=템플릿/로컬 `claude -p`, 평가=로컬. Foundry/AIP 추론 0 | `anomaly/rules.py`, `explainer.py`, `eval/run_eval.py` |

### AIP 필수성 판정
- **필수인가? → 아니다.** 시스템의 모든 기능이 로컬 SQLite로 완결적으로 재현된다(`store_local.py`가 권위 구현). Foundry 없이도 데모는 완결.
- **있으면 좋은가? → 그렇다(맥락 한정).** 진짜 이점 두 가지: ① **스키마 레벨 evidence 강제 = 클라이언트가 앱을 우회해도 무근거 Anomaly 생성이 서버(Palantir 액션)에서 거부됨**(로컬 store 강제와 달리 인프라 레벨). ② **D4D는 Palantir 심사 해커톤** — 온톨로지를 실제 Foundry에 올리고 액션 게이트를 실연하는 것 자체가 "Military Deployability" 신호. 따라서 이 맥락에서 Foundry 사용은 "불필요한데 있어 보이려 쓴 것"이 **아니다**.
- **불필요한데 쓴 것처럼 보이는가? → 원래 프레이밍은 그렇게 읽혔다.** "AIP 에이전트가 그 위에서 추론"(direction.md §5, CLAUDE.md 원칙1)은 추론이 로컬인 현실과 어긋났다. **이미 교정됨**(git `f06d3fc`, demo.md §106-109 "AIP Logic 미사용, 실사용=온톨로지·액션").

---

## 6. CLAUDE.md 최상위 원칙 5 준수도

| 원칙 | 판정 | 근거 |
|---|---|---|
| 1. **온톨로지가 척추(AIP-spine)** | **PARTIAL** | 온톨로지는 설계·코드의 중심이 맞다. 단 실행 시스템의 권위는 로컬이고 "AIP가 그 위에서 추론"은 미달성 → "spine"의 절반(스키마·액션)만 Foundry |
| 2. **억지/얕게 쓰지 말 것** | **MET** | 스멜테스트 실제 통과, 링크 과장 없음, 스코프 규율 지킴 |
| 3. **결정 루프지 Q&A 아님** | **MET** | confirm/dismiss human-on-loop·상태전이 실동작, 단순 챗 아님 |
| 4. **provenance 강제** | **MET(모범)** | store 레벨 EvidenceError/SentenceEvidenceError로 무근거 출력 원천 차단 |
| 5. **깊이 타협 금지** | **MET** | 룰 5종이 비자명·정직 보정, 184테스트, 얇게 여러 개 아닌 한 수직관통 |

---

## 7. moat 판정 — GPT-wrapper 대비 진짜 해자

**진짜 재현불가 이점(방어 가능):**
1. **Citation-강제 아키텍처** — 문장이 LLM 생성이 아니라 사실(Fact, 근거 id 보유)에서 조립되고, cites 없는 문장은 store가 거부(`assessment.py` 불변식 1~3). GPT-wrapper가 흉내 내기 어렵고 **실제 가치가 여기**. `p5_eval.json`: 파이프라인 문장 cites 100% vs 맨몸 LLM 기계검증 citation 0.
2. **provenance 그래프의 코드 집행** — 결론 객체→근거 객체 역추적이 스키마·store 불변식.
3. **Foundry 액션 게이트(맥락 한정)** — 앱 우회해도 무근거 write가 Palantir 액션에서 거부(클라이언트 무관 강제). 로컬 스택만으론 재현 불가한 유일한 지점.
4. **비자명 이상탐지 룰의 정직한 보정** — dropout 3상태(교차확인/미확인/아티팩트), "단정 금지"의 코드화.

**로컬로도 되는 것(해자 아님·포장 주의):**
- **추론/설명 자체** — 템플릿 또는 로컬 `claude -p`. GPT-wrapper도 동일하게 서술 가능. 여기엔 해자 없음.
- **"AIP 위에서 돈다"** — 추론이 AIP가 아니라 로컬이므로, 이걸 해자로 내세우면 포장.
- **교차소스 dropout 확정** — 라이브 2차 피드(adsb.fi/ADS-B Exchange) 미배선. 라이브 파이프는 항상 `NullCrossCheckSource`(미확인·저신뢰만). 교차확인 0.72는 **합성 시나리오에서만** 발동. 인터페이스는 있으나 라이브 능력은 없음(정직히 문서화됨).

---

## OVERSOLD 목록 (이전 과장 교정)

> 아래는 "이전 보고가 실제보다 부풀렸던" 지점. 다수는 **이미 2026-07-04 정직화 패스에서 교정됨**(해당 표시).

1. **"AIP-spine / AIP 에이전트가 온톨로지 위에서 추론"** → 실제: **AIP Logic 미사용(스텁), 추론은 전부 로컬 엔진.** Foundry에 있는 건 스키마+액션 게이트뿐. **[교정됨: demo.md, git f06d3fc]**
2. **"완성" / "Foundry 위에서 돈다"의 뉘앙스** → 실제: **실행 데모 서버는 로컬 SQLite read.** Foundry는 병렬 검증 경로(`demo_foundry.sh`)이고 read 권위본은 로컬. dual-write에서 correlated_with·다중근거·다중involves·mentions·문장cites는 **로컬에만** 온전(Foundry는 단수 파라미터 한계로 첫 근거만). **[부분 교정: P7 §17-6 잔여에 명시]**
3. **"OSDK로 타입드 read"**(aip-integration.md 원안) → 실제: **read는 저수준 `foundry_sdk` dict.** OSDK는 스키마 증명+action apply 용도. **[교정됨: P7 §4 설계결정, CHANGELOG]**
4. **"평가 수치 P/R 1.00/1.00"** → 실제: **자작 12합성 시나리오의 결정적 예상값(준-동어반복).** 라이브 P/R 아님. 라이브에선 교차소스 미배선이라 dropout이 저신뢰만. 발표에서 "정밀도 100%"로 말하면 과장. 게다가 그 eval 실행에서 `claude` 서술 강화는 **실패→템플릿 폴백**(`claude_succeeded:false`)이었음. **[정직 표기 있으나 숫자만 떼면 오해]**

---

## 진짜 이점 vs 포장

| 축 | 실측으로 방어되는 것 | 실측으로 방어 안 되는 것 |
|---|---|---|
| 온톨로지 | 스멜테스트 3강 통과, 링크 대부분 정당, provenance 코드강제 | "엔티티 해소"를 강점화(icao24 custody는 쉬운 케이스, 교차소스 ER은 키워드) |
| AIP/Foundry | 온톨로지·36액션 실구축·라이브 왕복, 액션 evidence 게이트 | "AIP 추론"(스텁), "OSDK 타입드 read"(저수준), "Foundry가 런타임"(로컬임) |
| 코파일럿 | 문장→cites 강제 조립·무근거 거부 = 진짜 anti-hallucination | LLM 서술(로컬 claude, GPT-wrapper도 가능·해자 아님) |
| 이상탐지 | 5종 룰 비자명·정직 보정·"단정 금지" 코드화 | 교차소스 dropout 확정(라이브 2차 피드 없음, 합성만) |
| 평가 | citation 유무 대비는 진짜 우위 | P/R 1.0을 실세계 성능처럼 제시 |
| 재현성 | replay 네트워크 0·SHA-256 바이트 재현 | (없음 — 이건 방어됨) |

---

## 발표에서 할 말 / 하면 안 될 말

**할 말(정직 어필 — 실측 방어 가능):**
- "온톨로지를 실제 Palantir Foundry에 올렸고(Object 11·Action 36), **evidence 강제를 스키마 레벨 액션 게이트로 구현**했습니다. 앱을 우회해 접근해도 무근거 이상징후 생성이 거부됩니다 — 이건 로컬 스택으로는 재현 못 하는 배포 패러다임의 최소 단위입니다."
- "저희 코파일럿은 **문장을 LLM으로 생성하지 않습니다.** 사실을 온톨로지에서 뽑아 문장을 조립하고, 근거 링크 없는 문장은 시스템이 거부합니다. 그래서 모든 문장에 클릭 가능한 출처가 붙습니다(맨몸 LLM = 기계검증 citation 0)."
- "이상탐지는 **단정하지 않습니다.** 단일 소스 결측은 저신뢰로만, 교차 확인돼야 상향합니다 — 오탐을 구조적으로 막습니다."
- "**추론은 저희 엔진, Foundry엔 온톨로지·결정 게이트.** AIP Logic 추론 이관은 다음 단계입니다." (정직하게 선을 그으면 오히려 공격이 됨)

**하면 안 될 말(과장 — 검증 시 무너짐):**
- ❌ "AIP가 추론합니다 / AI 에이전트가 온톨로지 위에서 추론합니다" (AIP Logic 스텁, 추론 로컬)
- ❌ "시스템이 Foundry 위에서 돕니다" (실행 서버는 로컬 SQLite)
- ❌ "정밀도·재현율 100%" (자작 합성셋, 라이브 아님)
- ❌ "OSDK로 타입드하게 읽습니다" (저수준 dict SDK)
- ❌ "실시간 교차소스로 dropout을 확정합니다" (라이브 2차 피드 없음)

---

## 완성도 % 추정

| 축 | 추정 | 근거 |
|---|---|---|
| **로컬 스택**(P0~P6) | **~85%** | 4소스 라이브 인제스트·이상탐지 5종·citation 코파일럿·지도/타임라인/서브그래프·184테스트 전부 실동작. 남은 15% = 해커톤 MVP 성격(단일 지역, 데모 replay 의존, eval이 합성 위주, 교차소스 미배선) |
| **Foundry 이관** | **~75%**(달성 가능분 기준) | 온톨로지·36액션·OSDK·라이브 왕복 전부 실재·검증. 미달분 = read 권위본이 로컬(dual), 그래프 부분형성(단수 파라미터 한계로 다중 링크는 로컬), read가 OSDK 아닌 저수준 SDK. "이관 준비·검증된 슬라이스"지 "Foundry가 런타임"은 아님 |
| **AIP 실활용** | **~40%** | 6구성요소 중 온톨로지·액션 실사용(2), OSDK 부분(0.5), AIP Logic·read권위·추론엔진 미사용(0). 즉 스키마+액션 게이트만 진짜 AIP, 추론 계층은 0 |

---

## 부록: 판정 근거 파일 인덱스

- 로컬 파이프라인 실동작: `ontology/store_local.py`(987L 권위구현) · `anomaly/rules.py`(5종 룰) · `copilot/assessment.py`(문장조립 불변식) · `server/app.py`(로컬 store read) · `web/index.html`(962L 프론트)
- AIP 스텁 증거: `anomaly/explainer.py:233-247`(AipLogicExplainer NotImplementedError)
- Foundry 실측: `docs/worklog/P7-foundry-migration.md` §17(0.8.0 종결, 라이브 14검증) + `ontology/store_foundry.py`(1274L, read=`foundry_sdk` dict)
- 평가 근거: `docs/worklog/p5_eval.json`(P/R 1.0=합성 12셋, bare_llm 대비, claude_succeeded:false)
- 교차소스 한계: `anomaly/crosscheck.py`(라이브=NullCrossCheckSource, 합성=SyntheticMirrorSource)
- 정직화 이력: `git log`(f06d3fc "데모 스텝 ⑥ 발화 정직화") + `docs/CHANGELOG.md`(단계별 갭 명시) + `CLAUDE.local.md`(2026-07-04 보고 정직성 규칙)
