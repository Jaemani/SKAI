# Air ISR Fusion Copilot (T2 · 공중)

**D4D Hackathon · Track T2 · 공중 유형**
Maven형 공중 ISR 융합 코파일럿 — 공개 항적·위성궤도·기상·뉴스/OSINT를 **Palantir 온톨로지**로 융합해 특정 지역의 공중·우주 상황을 요약하고 이상징후를 탐지하는 AI 코파일럿.

## 한 줄
> "이 지역, 지금 하늘에서 뭐가 이상한가?" — 자연어로 물으면, 여러 공개 소스를 온톨로지로 융합해 **출처가 달린** 상황 요약 + 이상징후를 지도/타임라인으로 답하고, 분석가가 승인하는 **Action**으로 결정을 잇는다.

## 콜드스타트 (context 없이 시작하는 에이전트용)
`CLAUDE.md`가 자동 로드된다. 없으면 아래 순서로 읽어라:
1. `CLAUDE.md` — 규칙·가드레일 2. `direction.md` — 백본 5요소
3. `ontology.md` — **핵심**: 도메인 온톨로지 + 스멜테스트 4. `aip-integration.md` — AIP+OSDK 실사용
5. `data-sources.md` — 공개 소스 6. `architecture.md` — 파이프라인
→ 그 다음 `PROMPTS.md`의 **P0** 실행.

## 왜 이 방향 (전략)
- **온톨로지 = 척추(AIP-spine)**: 앱이 아니라 도메인 모델. GPT-wrapper와의 해자. 심사 "군 적용성 30%"는 Palantir 배포 패러다임(온톨로지→결정→액션) 적합성.
- **공개 데이터만으로 완결** → 라이브 데모가 24H 안에. 실데이터가 화면에서 움직이면 임팩트 최상.
- **Project Maven 서사** = 심사위원(Palantir/Morph/공군 체계단)이 즉시 이해.
- **에이전트 자율 반복**: 라이브 API에 스스로 붙어 테스트→수정.

## 지표 (심사 기준 매핑)
| 심사 항목 | 대응 |
|---|---|
| Problem Fit 25% | 파편화된 공중 상황인식 병목 정조준 |
| Military Deployability 30% | 온톨로지→결정→Action, KADIZ 라이브, 출처·신뢰도, 분석가 워크플로 |
| Technical Execution 25% | 온톨로지 융합 + AIP Logic 이상탐지 + citation 강제 + 지도/타임라인 |
| Creativity 20% | ADS-B dropout·위성 conjunction·교차소스 "은닉 정황" 내러티브 |

## MVP 우선순위 (24H, 깊이 타협 없이)
관심지역 1곳 고정(KADIZ) → OpenSky 라이브 → 온톨로지 write → 이상탐지 3종(비상 스쿽·ADS-B gap·군용기 접근) → AIP Assessment(citation) → 지도. 이게 되면 확장. **온톨로지·AIP 깊이는 타협 금지.**

## 파일
`CLAUDE.md`(규칙) · `direction.md`(백본) · `ontology.md`(척추) · `aip-integration.md`(AIP) · `data-sources.md`(소스) · `architecture.md`(설계) · `PROMPTS.md`(실행)
