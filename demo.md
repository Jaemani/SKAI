# demo.md — Air ISR Fusion Copilot · 3분 발표 대본 (P6)

D4D 해커톤 T2·공중. 이 문서는 **발표자용 대본 + 리허설 체크리스트**다. 실행·검증 로그는
`docs/worklog/P6-demo.md`, 결정 근거는 `docs/decisions/DR-0008-p6-demo-packaging.md` 참조.

---

## 0. 데모 모드 (기동)

| 모드 | 명령 | 성격 |
|---|---|---|
| **재생(기본)** | `scripts/demo.sh replay` | **네트워크 호출 0**. 데모 전용 DB(`data/demo/`)에 선언적 시나리오 전체 주입 + now 앵커링 + 서버만 기동. 오프라인·발표장 네트워크 불문 **언제 돌려도 동일 결과**. 발표의 안전한 백본. |
| 라이브(순수) | `scripts/demo.sh live` | **다중소스 연속 폴링**(OpenSky 항적 25s·GDELT 뉴스 5m·METAR 기상 30m·Celestrak 위성 12h) + 서버. **실데이터만, 합성 주입 없음.** 뉴스는 실 기사 URL로 클릭 가능. 네트워크·API가 살아있을 때 오프닝 임팩트용. |
| 라이브(임팩트) | `scripts/demo.sh live --inject` | 위 순수 라이브 + 내러티브 합성 1건 가미. 라이브 KADIZ엔 재현성 있는 이상징후가 상시 없으므로, 발표 서사가 필요할 때만 명시적으로 얹는다(실 항적과 공존). |
| 정지 | `scripts/demo.sh stop` | 두 모드의 모든 프로세스 종료. |
| **Foundry 실연**(스텝 ⑥) | `scripts/demo_foundry.sh` | 위 로컬 서버와 별개. 실 Palantir Foundry에 OpenSky 인제스트 + 합성 이상징후(근거 강제) + confirm을 라이브로 쓴다(§1 ⑥). 실패 안전·폴백 내장. 종료 후 `... cleanup`. |
| **Foundry read + AIP**(스텝 ⑥ 캡스톤) | 서버를 `SKAI_STORE=foundry SKAI_COPILOT_LLM=aip SKAI_DB=data/demo/skai_foundry_local.db`로 기동 | 화면·코파일럿 read가 **실 Palantir Foundry**에서 오고(`store_backend=foundry`), 상황요약 헤드라인이 **AIP Logic이 생성**(`produced_by=aip_logic`). demo_foundry가 쓴 로컬 미러+Foundry를 읽는다(db-regime.md §4). 실패 시 template로 폴백. |

- **발표 원칙**: 전체 워크스루는 **재생 모드**로 한다(결정적·안전). 라이브는 오프닝 10~15초만
  선택적으로 쓰고, 조금이라도 불안하면 처음부터 재생으로 간다. 어느 스텝에서 라이브가 깨져도
  폴백은 항상 `demo.sh stop && demo.sh replay` 한 번이다(그 사이 대본은 아래 재생 화면 그대로).
- **라이브 오프닝을 쓸 때(선택)**: `demo.sh live`는 4소스를 각자 주기로 **연속 폴링**한다(항적 25s·
  뉴스 5m·기상 30m·위성 12h). 헤더 배지가 녹색 **"LIVE · N초 전 갱신"**으로 뛰고, 우측 뉴스 카드는
  **실 기사 URL로 클릭 가능**(GDELT). `/api/live`가 소스별 신선도(`source_last_poll`)를 내므로 "뉴스
  3m 전·기상 12m 전"처럼 소스별 최신성을 말할 수 있다. 군사 항공 RSS 4피드는 옵션(`SKAI_POLL_SOURCES=
  ...,rss`)이라 발표 기본엔 넣지 않는다. 라이브가 조금이라도 불안하면 즉시 재생으로.
- 재생 모드는 `SKAI_OFFLINE=1`로 외부 egress를 소켓 레벨 차단한다 → "네트워크 0"이 말이 아니라
  런타임으로 강제된다(증명: `docs/worklog/P6-demo.md` §1).
- now 앵커 = `eval.EVAL_NOW`(1783000000 = 2026-07-02 13:46 UTC, 사실상 "오늘"). 질의의 "지금"이
  이 시각에 고정돼 데이터 창과 항상 맞는다.

기동 직후 콘솔에 **발표 딥링크 3개**가 출력된다(질의 자동 실행 + 서브그래프 자동 오픈). 리허설 땐
그 링크를 브라우저에 붙여넣기만 하면 각 스텝이 재현된다.

---

## 1. 3분 타임라인 (초 단위)

> 화면은 3열 레이아웃: **좌** 이상징후 타임라인·선택 상세, **중** 지도 + 코파일럿 채팅,
> **우** 소스별 객체 카운트·범례. 아래 좌표/클릭은 1680×1050 기준.

### ① 문제 제기 + 지도 오프닝 — 0:00~0:15 (15초)
- **화면**: `http://localhost:8000/` (스토리보드 `p6_step1_map.png`).
- **발화**: "공중 상황 인식은 항적·위성 궤도·기상·뉴스가 서로 다른 창에 흩어져 있어, 분석가가
  눈으로 교차하느라 느리고 놓칩니다. 저희는 이 네 소스를 **하나의 온톨로지로 융합**해, 출처가
  달린 답과 이상징후를 한 화면에 냅니다." (direction.md §1)
- **조작**: 지도만 보여준다(클릭 없음). KADIZ(파란 점선)·서해 작전구역 OpArea(주황 점선), 항적,
  위성 지상궤적(청록 점선), ADS-B gap 트랙(빨강 점선)이 이미 떠 있음. 우측 카운트 패널로 "OpenSky
  83관측/11기, Celestrak 2통과, METAR 1, GDELT 1 — 4소스가 지금 온톨로지에 들어와 있다"를 1초 짚음.
- **폴백**: (라이브 오프닝을 썼고 항적이 안 뜨면) 말없이 `demo.sh replay`로 전환 — 화면 동일.

### ② 질의① — 문장별 cites 강제 — 0:15~0:50 (35초)
- **화면**: 중앙 하단 채팅 입력에 프리셋 버튼 **"지금 KADIZ 근방 이상한 거 있어?"** 클릭
  (좌표 약 (563, 967)) 또는 딥링크①. 결과는 `p6_step2_cites.png`.
- **발화**: "자연어로 물으면, 온톨로지를 병렬로 읽어 **사실을 먼저 확정하고**, 그 사실에서 문장을
  조립합니다. 핵심은 — **모든 문장이 근거 객체 id를 인용(cites)하고 태어난다**는 것. 근거 없는
  문장은 저장 단계에서 거부됩니다(환각 구조적 차단)."
- **짚을 것**: 답변 상단 "해석(투명성)" 줄(지역=KADIZ, 창=최근 30분(지금))로 질의가 어떻게
  해석됐는지 노출됨을 1초. 그 아래 문장마다 붙은 **파란 cites 배지**(예: `관측 CES7500 sq7500`,
  `이상징후 SHADOW7`)를 손으로 훑으며 "이 배지 하나하나가 온톨로지 객체이고, 클릭하면 지도로
  튀고 원 출처로 갑니다"라고 말함. 종합 신뢰도(0.5대)가 저신뢰 뉴스를 반영해 정직하게 낮음을 짚음.
- **소요**: 응답 <1초. 나머지는 배지 설명.
- **폴백**: 응답이 안 오면 새로고침 1회 → 그래도 안 되면 `demo.sh stop && demo.sh replay` 후 딥링크①.

### ③ 이상징후 confirm — Action → 상태 전이 → provenance — 0:50~1:20 (30초)
- **화면**: 좌측 타임라인에서 자동 선택된 **CES7500(비상 스쿽 7500 = 하이재킹, 신뢰도 0.95)** 상세.
  하단 **[확인(confirm)]** 버튼(좌표 약 (96, 739)) 클릭. before/after = `p6_step1_map.png`(상태:후보)
  → `p6_step3_confirmed.png`(상태:확인됨).
- **발화**: "여기가 GPT 챗봇과 갈리는 지점입니다. 이건 Q&A가 아니라 **결정 루프**예요. 분석가가
  [확인]을 누르면 — 온톨로지 **Action**이 실행돼 이 이상징후의 상태가 후보→**확인됨**으로
  **영속 전이**됩니다. 근거(evidence) Observation과 provenance 링크는 그대로 붙어 있고요."
- **핵심 시연**: confirm 후 상세 하단 "상태: **확인됨**"으로 바뀌고 버튼이 비활성화됨을 보여줌.
  그리고 **같은 질의를 다시 던지면**(딥링크① 재실행) 그 문장의 상태가 "확인됨"으로 반영됨 =
  "상태가 온톨로지에 남아 다음 판단에 쓰인다 = AIP가 얕지 않다"의 실증.
- **폴백**: confirm POST 실패 시 → 재생 재기동(상태 초기화됨) 후 다시 클릭. 상태는 DB에 영속되므로
  재기동 없이 새로고침만으로도 반영됨.

### ④ 은닉 정황 서브그래프 — correlated_with — 1:20~1:50 (30초)
- **화면**: 질의③ 딥링크(`?q=...&sg=1`)로 서브그래프 자동 오픈, 또는 답변 우상단
  **[서브그래프]** 버튼. 결과 `p6_step4_subgraph.png`.
- **발화**: "'은닉 정황'입니다. ADS-B가 끊긴 기체(SHADOW7)가, 같은 시공간의 **위성 정찰 통과**와,
  같은 지역을 언급한 **OSINT 뉴스**와 `correlated_with` 링크로 묶여 있어요. 이건 flat table로는
  안 나오는 **그래프 추론**입니다 — 콜사인 매칭이 아니라 시공간 버킷으로 이었기 때문에 상용
  콜사인이 없는 군용 인시던트에도 걸립니다."
- **짚을 것**: 중앙 초록 노드 = **상황평가(SituationAssessment)** 객체 자체 — "질의 하나가 온톨로지에
  인텔 객체로 남습니다". 청록 점선(correlated_with)이 dropout 이상징후 → 위성통과·뉴스로 뻗는 것,
  범례(빨강=이상징후·청록=위성통과·보라=뉴스)를 손으로 짚음. "확증이 아니라 **정황** — 신뢰도를
  올리지 않고 관계만 남깁니다"라고 정직하게 단서.
- **폴백**: 서브그래프가 안 열리면 좌측 상세의 "교차소스 상관(correlated_with)" 리스트로 대체 설명.

### ⑤ 평가 수치 — 3수치 정직 프레임 — 1:50~2:18 (28초)
- **화면**: 슬라이드 1장(아래 부록 A의 수치) 또는 `docs/worklog/live_eval_result.json`.
- **발화(3수치)**: "말이 아니라 숫자로 — 단, 정직한 숫자로. 세 가지만 말하겠습니다. **하나**, 라벨된
  자작 합성 시나리오 12건 회귀에서 이상탐지 5종이 전부 통과 — 이건 실세계 정확도가 아니라 **탐지
  로직의 결정성 검증**입니다. **둘**, 라이브 실데이터 질의에서도 산출 문장 **24개가 전부 출처를
  인용했고(cite 해상 100%)**, 그 인용 id는 전부 실제 온톨로지 객체로 역추적됐습니다. **셋**, 같은
  라이브 데이터를 **맨몸 LLM**에 주면 추론은 유능하지만 **기계검증 가능한 출처가 0**, 저희 파이프라인은
  **10건**을 역추적 가능하게 인용합니다(라이브 **10 : 0**). 차이의 본질은 정확도 경쟁이 아니라
  **provenance**예요."
- **정직 프레임(필수)**: "정밀도/재현율 100%"라는 표현 **금지**(자작 합성셋의 예상값 = 준-동어반복,
  EVALUATION OVERSOLD#4). 합성 12건은 **회귀 검증**, 인용 100%·10:0은 **라이브 실측** — 둘을 반드시
  구분해 말한다. **"라이브 P/R은 왜 없나?"** 질문의 정답: "라이브 데이터엔 ground truth 라벨이 없어
  precision/recall을 원리적으로 낼 수 없습니다. 그래서 숫자로 안 내세우고, 라이브에서 측정 가능한
  citation 해상·탐지 결정성만 정직하게 냅니다." (근거: `docs/worklog/live-eval.md` §1·§3)
- **폴백**: 슬라이드는 정적이라 실패 없음. (라이브 수치 재계산은 발표 중 하지 않음 — 사전 산출값 인용.)

### ⑥ 배포 경로 — Foundry 라이브 실연 + AIP Logic 캡스톤 — 2:18~2:50 (32초)
- **화면**: 발표 전 미리 로그인해 둔 **Palantir Foundry Object Explorer** 탭(준비 절차 P6-demo.md §6).
- **사전 실행(권장)**: 스텝 ⑤ 내레이션 중(또는 ⑥ 진입 직전) 터미널에서 `scripts/demo_foundry.sh` 한 번.
  ~15초간 실 Foundry에 (a) OpenSky 실 항적 → Aircraft/Observation(observed_as FK), (b) 합성 비상
  스쿽(7500) → 이상징후 생성(근거 강제·§12 에러 흡수 → evidenced_by/involves 엣지), (c) confirm 전이가
  반영된다. 콘솔이 생성된 **anomalyId**와 각 단계 OK를 출력하니 그 값으로 UI에서 찾는다. (라이브로
  돌리며 내레이션해도 됨 — 콘솔 출력 자체가 "실 Foundry에 쓰는 중"의 증거.) **AIP 캡스톤을 쓸 거면**
  같은 타이밍에 코파일럿을 Foundry-read+AIP 모드로 물려 둔다(§0 마지막 행 · 아래 캡스톤).
- **발화**: "탐지와 근거 강제 엔진은 저희 코드입니다. 저희가 Palantir에 올린 건 **온톨로지와 결정
  게이트**, 그리고 **설명·요약 서술을 생성하는 AIP Logic**이에요. 방금 한 커맨드로 OpenSky 실 항적이
  Foundry Aircraft/Observation 객체로 들어가고, 비상 스쿽 이상징후가 근거 관측을 evidenced_by로 물고
  생성됩니다. 핵심은 — **근거 없는 이상징후는 Palantir 액션 검증이 거부합니다. 저희 앱을 우회해서
  접근해도요.** 분석가의 confirm은 Foundry에 영속되는 상태 전이고요. 즉 저희 앱이 사라져도 데이터·
  근거 강제 규칙·결정 기록은 플랫폼에 남습니다. 그리고 이 상황요약 문장 자체는 — **AIP Logic이
  온톨로지 객체를 읽어 서술한 겁니다.**"
- **정직 프레임(필수·강화)**: **AIP Logic은 실사용한다 — 단 "서술 생성"에 한정.** ① `explain-anomaly`
  (이상징후 설명·권고), ② `region-situation-summary`(상황요약 헤드라인) 두 함수가 OSDK 타입드 쿼리로
  배선돼, 온톨로지 Observation/Anomaly 객체를 읽어 서술을 **생성**한다(`produced_by=aip_logic`). **AIP가
  하지 않는 것**을 선제적으로 못 박는다 — **탐지·상관·평가·근거 강제·문장별 cites 조립은 전부 우리
  엔진**(rules.py·correlation.py·assessment.py). 절대 금지: ❌"AIP가 탐지합니다" ❌"전부 AIP가 합니다"
  ❌"AIP 위에서 시스템이 돕니다". 기본값은 재현성 때문에 template이고 AIP는 opt-in(`SKAI_COPILOT_LLM=aip`)
  임도 정직하게. 이 선긋기 자체가 방어가 아니라 공격이다 — "스키마 레벨 근거 강제(클라이언트 무관)"와
  "설명·요약은 AIP Logic, 판단·근거는 우리 엔진"은 로컬 스택으로 재현 못 하는 실질 이점이다.
- **Palantir UI + 캡스톤에서 보여줄 것(각 ~7초, 시간압박 시 1·생략)**:
  1. **(선택) Aircraft → observations traverse**: 방금 쓴 Aircraft(콘솔 hex)를 열고 `observations` 링크
     → observed_as FK로 연결된 실 관측. (시간 없으면 생략.)
  2. **Anomaly의 evidenced_by**: 방금 생성된 emergency_squawk 이상징후를 열어 `evidenced_by`(→ 근거
     Observation)·`involves`(→ Aircraft) 링크를 짚는다 = provenance 그래프가 flat table이 아니라
     Foundry 온톨로지 그래프로 산다.
  3. **confirm 후 status**: 같은 Anomaly의 `status = confirmed` = Action 상태전이가 Foundry에 영속.
  4. **AIP Logic 캡스톤**: Foundry-read+AIP 모드 코파일럿(`SKAI_STORE=foundry SKAI_COPILOT_LLM=aip
     SKAI_DB=data/demo/skai_foundry_local.db`)에 "지금 KADIZ 근방 이상한 거 있어?"를 던진다. 응답
     메타에 **`store_backend=foundry`**(화면 read가 실 Palantir)·헤드라인 **`produced_by=aip_logic`**
     (AIP가 skaidemo Anomaly 객체 속성을 종합해 서술)·**`overallAssessment`**(AIP 한줄판정)가 뜬다.
     **문장 cites는 template 모드와 동일** = provenance는 룰이 보장하고 서술만 AIP가 쓴 것을 함께 짚는다.
     (근거: `docs/worklog/db-regime.md` §4 VERDICT AIP-NATURAL-OK · `aip-logic-wire.md` · `region-summary-wire.md`.)
  5. **(보너스, ~5초) Foundry Automation 알림**: demo_foundry 실행 수십 초 뒤 Foundry **알림 벨**에
     "[SKAI] 신규 비상 스쿽 이상징후 탐지"가 도착한다(Automate live monitoring — emergency_squawk
     객체 추가 감시, 2026-07-05 실수신 검증). 발화: "저희 앱이 만든 게 아니라 **플랫폼이 스스로 감지해
     보낸 알림**입니다 — 온톨로지에 이상징후가 생기면 분석가 워크플로에 Foundry가 능동 참여합니다."
     타이밍이 안 맞으면 생략(알림 도착이 수초~수분 가변 — 리허설에서 지연 확인).
- **정직성 주의(필수)**: 합성 이상징후는 `source=synthetic`으로 명시된 **주입건**임을 밝힌다(실 항적을
  하이재킹으로 오도하지 않음 — 결정루프·근거강제를 보이기 위한 마킹된 합성 신호). 실 항적 인제스트와
  합성 이상징후는 분명히 구분해서 말할 것.
- **폴백(중요)**: Foundry/네트워크 불안정 시 `demo_foundry.sh`가 명확한 실패 메시지 + "replay 전환"
  안내를 출력한다(AIP 캡스톤도 실패 시 template로 자동 폴백 — 응답은 나옴). 그러면 **이 스텝만 통째로
  스킵**하고 이미 ①~④에서 보인 로컬 데모로 갈음 — 스텝 ⑦ 클로징을 10초→20초로 늘려 시간을 흡수한다
  (전체 3분 유지). Foundry가 죽어도 발표는 안 멈춘다.

### ⑦ 심사 4항목 클로징 — 2:50~3:00 (10초)
- 부록 A의 4문장을 빠르게(각 ~2.5초). "**문제**는 파편화된 공중 상황인식, **군 배포성**은
  온톨로지→결정→Action에 KADIZ 라이브·출처·분석가 워크플로 + **실 Foundry read·액션 게이트**,
  **기술**은 4소스 라이브 융합+이상탐지 5종+citation 강제(설명·요약은 AIP Logic 2종),
  **창의성**은 ADS-B dropout·위성 conjunction·교차소스 은닉 정황. 감사합니다."

**총 소요 ≈ 3:00** (재배분: ⑥ AIP 캡스톤 +4초는 ⑤ −2초[30→28] + ⑦ −2초[12→10]에서 확보 — 합계
불변 15+35+30+30+28+32+10=180. 시간 압박 시 ⑥ UI 시연을 줄임(Aircraft traverse 생략 → evidenced_by +
confirm + AIP 캡스톤만; 더 압박되면 AIP 캡스톤을 콘솔 한 줄 `produced_by=aip_logic`만 짚음). ⑥ 폴백
발동 시 ⑦을 20초로 늘려 흡수 — 전체 3분 유지.)

---

## 2. 리허설 체크리스트 — 질의 3개 · 예상 결과

재생 모드 기동 후 아래를 확인한다(모두 결정적 — 매번 동일해야 정상).

| # | 질의 | 예상 결과(재생·앵커 고정) |
|---|---|---|
| ① | 지금 KADIZ 근방 이상한 거 있어? | `해석` 창=**최근 30분(지금)**. 이상징후 **9건**·항적 11대. 문장 **38개** 전부 cites 보유. 종합 신뢰도 0.5. no_evidence=false. |
| ② | 최근 1시간 위성 통과랑 겹치는 이상징후는? | `해석` 창=**최근 1시간**. 위성통과 상관 문장(correlated_with → SYN-RECON) 노출. 동일 9건. |
| ③ | 서해 쪽 기상이랑 뉴스 맥락 요약해줘 | `해석` 창=**최근 30분(기본)**. 하단에 기상(RKSI MVFR)·OSINT 뉴스(신뢰도 0.35, KADIZ 언급) 문장. `&sg=1`이면 서브그래프 자동 오픈. |

- **탐지 5종 확인**: 비상 스쿽(CES7500 0.95)·ADS-B dropout(SHADOW1/SHADOW7 0.72 교차확인, GHOST2
  0.42 미확인)·로이터링(ORBIT3 0.60)·군용기 접근(RCH451/FALCON9 0.55)·위성 근접(SYN-RECON 0.40).
- **confirm 동작**: CES7500 [확인] → 상태 "확인됨", 버튼 비활성. (재기동하면 다시 후보로 초기화.)
- **내러티브**: SHADOW7 dropout ↔ 위성통과 ↔ 뉴스가 서브그래프에서 청록 점선으로 묶임.
- **dropout 교차확인의 출처(정직)**: 재생의 0.72 "교차확인"은 **합성 미러**(결정성)에서 나온다.
  라이브에서 dropout을 실제 2차 소스로 교차확인하려면 `SKAI_CROSSCHECK=live`(옵트인)로 **adsb.fi**를
  배선한다(`crosscheck-live.md`). **인용 요건**: adsb.fi 라이브 교차확인을 켜서 시연·언급하면 크레딧
  **"Cross-check data: adsb.fi (https://adsb.fi)"**를 화면/발화에 명시해야 한다(ToS). 발표 기본(재생)엔
  라이브 2차 호출이 없으므로 이 각주는 라이브 crosscheck를 켤 때만 해당.
- **재현성 사전점검**: `demo.sh replay`를 두 번 돌려 같은 질의 응답이 동일하면 OK(바이트 단위 고정).

> 3개 질의는 지역+시간창 파서 기반이라 **본문 결과 집합은 비슷하고, "해석(투명성)" 줄의 시간창
> 해석이 달라진다**(DR-0006 결정적 파서 — 의미 필터링은 비목표). 발표는 각 질의로 **다른 단면**을
> 조명한다: ①=cites 배지, ②=위성 상관, ③=기상·뉴스+서브그래프. 이 프레이밍을 지킬 것.

---

## 3. Foundry 실연 — 전환 완료 (2026-07-04)

크리덴셜이 도착해 **스텝 ⑥이 "준비 완료·대기" 문구에서 실 Foundry 실연으로 교체됐다**(위 §1 ⑥).
`store_foundry.py`의 스텁이 실배선으로 채워져(P7 §11~§13), `SKAI_STORE=foundry`로 11 Object Type
write/read + Anomaly dual-write(evidenced_by 강제·§12 무해 에러 흡수) + confirm/dismiss 전이가 실
Palantir에서 돈다. 커넥터·서버·API 계약은 무변경(어댑터 교체만) — 로컬↔Foundry는 환경변수 하나로 갈린다.

**여기에 2026-07-04 세 갈래가 더 닫혔다**(전부 정직 범위 한정, 근거 워크로그 병기):

- **Foundry-primary read**(`foundry-read-mode.md`): `SKAI_STORE=foundry`면 화면 API·코파일럿의
  **정보 소재 read가 실 Palantir Foundry**에서 온다(Aircraft·Observation·Track·Satellite·OrbitPass·
  Operator·WeatherState·NewsEvent 8종). `/api/stats`·`/api/live`가 **`store_backend`**(`local`|`foundry`)를
  노출. 단 **provenance 그래프(Anomaly status·evidenced_by 다건·correlated_with·문장 cites·Region)는 로컬
  권위본**(read 권위는 로컬, Foundry는 dual-write 스파인) — "Foundry 위에서 돈다"가 아니라 "정보 소재는
  Foundry read, 근거·상관·문장은 로컬"이 정직한 선.
- **AIP Logic 2종 실사용**(`aip-logic-wire.md`·`region-summary-wire.md`): `explain-anomaly`(이상징후 설명·
  권고)·`region-situation-summary`(상황요약 헤드라인)가 OSDK 0.10.0 타입드 쿼리로 배선. `SKAI_COPILOT_LLM=aip`
  +Foundry 스토어일 때 상황요약 헤드라인을 **AIP가 생성**(`produced_by=aip_logic`, `overallAssessment` 메타).
  **cites·탐지·평가는 불변(우리 엔진)**, 서술만 AIP. 기본은 template(재현성), aip는 opt-in.
- **OSDK 타입드 read**(`osdk-typed-read.md`): 외부 read API(`query_*`·`counts`·traverse)가 저수준 dict에서
  **생성 OSDK 0.9.0 타입드 클래스**로 전환 — "OSDK로 타입드하게 읽는다"가 사실이 됨(잔여 저수준은
  write-내부 read-back 전용). 이제 발표에서 이 문구를 써도 된다(구 EVALUATION "하면 안 될 말"에서 해제).

- **실연 커맨드**: `scripts/demo_foundry.sh` — (a) OpenSky 1사이클 인제스트 → Palantir Aircraft/
  Observation(observed_as FK), (b) 합성 비상 스쿽 → write_anomaly(근거 강제·에러 흡수) → Foundry
  Anomaly + evidenced_by/involves 엣지, (c) confirm 전이. 원커맨드·발표용 출력·실패 안전(폴백 안내)·
  OpenSky 1회/실행. 발표 후 정리 = `scripts/demo_foundry.sh cleanup`.
- **데모 자산**: (b)(c) 산출물은 Object Explorer에서 보여주려 **의도적으로 남긴다**(P7 §13 "순증 0"과
  구분). 합성 식별자는 실행마다 유니크(정리 접두 매칭)이고 매 실행 시작에 직전 합성 자산을 정리해
  누적을 막는다. 실 hex 인제스트분은 실데이터로 보존.
- **폴백 지점**: Foundry/네트워크 실패 시 이 스텝만 스킵하고 `demo.sh replay` 로컬 데모로 갈음(§1 ⑥
  폴백 — ⑦ 확장으로 시간 흡수). 로컬 스택(스텝 ①~④·⑦)은 Foundry 무관하게 완결적.
- 근거 문서: `docs/worklog/P7-foundry-migration.md` §11~§13(write 전량 배선·Anomaly dual-write·D-1
  에러 흡수 실측), `aip-integration.md`, `ontology/store_foundry.py`(make_store·HybridStore).
- **스텝 ③ confirm(로컬)**은 그대로 둔다 — 로컬 결정루프 서사가 이미 완결적이고, 스텝 ⑥에서 같은
  confirm이 Foundry에도 영속됨을 실연하므로 이중으로 보인다(로컬 즉응 + Foundry 영속).

---

## 부록 A — 심사 매핑 슬라이드 골자

README 지표표 4항목 × (한 문장 + 화면 증거 + 수치).

| 심사 항목 | 한 문장 | 화면 증거 | 수치 |
|---|---|---|---|
| **Problem Fit 25%** | 항적·위성·기상·뉴스가 흩어져 수작업 융합이 병목인 공중 상황인식을 정조준. | 스텝① 4소스가 한 지도·타임라인에 융합. | 소스 4종 동시 온톨로지 적재(OpenSky 83관측/Celestrak 2통과/METAR 1/GDELT 1). |
| **Military Deployability 30%** | 온톨로지→결정→Action, KADIZ 라이브, 출처·신뢰도, 분석가 human-on-the-loop, **실 Palantir Foundry read + 액션 게이트**. | 스텝③ confirm 상태전이 + 스텝① provenance 배지 + **스텝⑥ 실 Foundry 인제스트·이상징후·confirm 실연 + Foundry-read 코파일럿(`store_backend=foundry`)**. | 상태 전이 영속(후보→확인됨), 문장별 신뢰도 0~1, Foundry에 Aircraft/Observation/Anomaly + evidenced_by/observed_as 그래프 라이브 write, **화면 정보소재 8종 read=Foundry발**(근거·상관·문장 cites는 로컬 권위). |
| **Technical Execution 25%** | 온톨로지 융합 + 이상탐지 5종(룰, 단정 금지 코드화) + citation 강제 + 지도/타임라인/서브그래프. **설명·요약 서술은 AIP Logic 2종**(탐지·근거는 우리 엔진). | 스텝②/④ cites 배지·다중홉 서브그래프 + 스텝⑥ `produced_by=aip_logic`. | **라이브** 질의 cite 해상 **100%(문장 24/24)**·맨몸 LLM 대비 기계검증 출처 **10:0**, 탐지 5종 합성 회귀 전건 통과(라이브 P/R은 별도 — 과장 금지). OSDK 타입드 read + AIP Logic `explain-anomaly`·`region-situation-summary`. |
| **Creativity 20%** | ADS-B dropout(고의 소등 의심)·위성 근접·교차소스 "은닉 정황" 내러티브. | 스텝④ correlated_with 서브그래프(dropout↔위성↔뉴스). | 교차소스 상관 링크 영속, dropout 교차판정(단정/미확인 이분). 라이브 2차 피드(adsb.fi) 교차확인 배선(옵트인). |

**맨몸 LLM 대비(스텝⑤ 근거)**: **라이브 데이터**에서 파이프라인 인용 문장 24/24(cite 해상 100%)·무근거
주장 0·기계검증 출처 **10건** vs 맨몸 LLM 기계검증 출처 **0**(추론은 성공하나 역추적 불가). 차이는
정확도 경쟁이 아니라 provenance. 원천: `docs/worklog/live_eval_result.json` + `live-eval.md`.

---

## 부록 B — 스토리보드 (재생 모드 기준)

| 파일 | 스텝 | 담는 것 |
|---|---|---|
| `docs/worklog/p6_step1_map.png` | ① | 지도 오프닝 — KADIZ·OpArea·항적·위성궤적·gap 트랙 + 자동선택 상세(**상태:후보** = confirm 전) + 4소스 카운트. |
| `docs/worklog/p6_step2_cites.png` | ② | 질의① 상황평가 — "해석(투명성)" + 문장별 파란 **cites 배지** + 좌측 근거/상관 + confirm 버튼(활성). |
| `docs/worklog/p6_step3_confirmed.png` | ③ | confirm 후 — 상세 하단 "상태: **확인됨**", 버튼 비활성(= Action 상태전이 영속). |
| `docs/worklog/p6_step4_subgraph.png` | ④ | 은닉 정황 서브그래프 — 중앙 상황평가 객체 + correlated_with(청록 점선) dropout↔위성↔뉴스 + 범례. |

캡처: `data/demo/` 재생 DB(앵커 고정) 기준, headless 브라우저 `--screenshot`(1680×1050). 지도 타일은
공개 CARTO 다크(브라우저 렌더 편의 — 데이터 파이프라인의 "네트워크 0"과 무관).

---

## 부록 C — 결정 기록

- **assessment 히스토리 패널(웹) 추가 — 생략**. 사유: "질의마다 인텔 객체가 쌓임" 서사는 이미
  (1) 서브그래프 중앙의 **SituationAssessment 객체 노드**(스텝④), (2) confirm 상태전이 영속(스텝③),
  (3) `/api/assessments` 엔드포인트로 충분히 실증된다. 3열 UI가 이미 조밀해 목록 패널은 화면 잡음
  대비 한계효용이 낮고, 새 스크린샷 churn을 유발한다(과설계). DR-0008 대본도 요구하지 않음.
- **now 앵커 = eval.EVAL_NOW 재사용**(SSOT). 데모·평가가 같은 앵커를 써 시각 표기가 "오늘"로 보이며
  결정적. 자세히는 `docs/worklog/P6-demo.md`.
