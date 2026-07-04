# P4-copilot.md — 코파일럿 실행 로그 (자연어 질의 → citation Assessment)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P4)
- 근거: PROMPTS.md P4 · **DR-0006**(citation 강제 = 조립) · ontology.md §1~§3 · architecture.md §4~§5 · P3-fusion.md §6(이월 이슈)
- 상태: **완료** (테스트 74/74 통과 · 질의 3개 end-to-end · 채팅+서브그래프 화면 렌더 · 이월 #1 수정)

---

## 1. 무엇을 만들었나

P3(4종 소스 온톨로지)에 더해 **자연어 질의 → 툴화된 병렬 read → 문장별 cites가 강제된 SituationAssessment → 채팅 UI + 서브그래프 뷰**. Q&A 챗봇이 아니라 "산출 인텔 객체 생성"(GenerateSituationAssessment 액션)임을 구조로 보인다.

```
copilot/parser.py       결정적 질의 파서 — 지역(KADIZ+별칭)·시간창("지금"=30분/"최근 N분·시간"). 결과를 응답에 노출(투명성).
copilot/tools.py        툴화된 온톨로지 read — query_flights/anomalies/sat_passes/weather/news. 각 Fact가 근거 객체 id(cites) 보유. OSDK 치환 지점 주석.
copilot/assessment.py   핵심 — 병렬 read(ThreadPool) → 사실→문장 조립(cites 강제) → (옵션)LLM 서술만 다듬기 → write_assessment 영속. build_subgraph 포함.
ontology/model.py       + SituationAssessment·AssessmentSentence dataclass, cite_object_type(id 접두어→Object Type).
ontology/store.py       + SentenceEvidenceError·validate_sentence_cites + Protocol(write_assessment/query_assessments/get_assessment/query_assessment_links).
ontology/store_local.py + assessment 테이블·write(문장별 cites 강제+aggregates/cites 링크·upsert 시 stale 링크 제거)·query·서브그래프 링크. + delete_future_orbitpasses_for(이월 #1).
ontology/store_foundry.py + 동일 메서드 스텁(Protocol 정합).
connectors/celestrak.py  이월 #1 수정 — 재계산 전 위성별 미래 OrbitPass 선삭제(과거 보존).
server/app.py           + POST /api/assess · GET /api/assessments · GET /api/subgraph.
web/index.html          + 채팅 패널(문장별 cites 배지·클릭 하이라이트) + 서브그래프 자체 SVG 모달(외부 그래프 라이브러리 없음) + ?q=&sg= 딥링크.
tests/test_p4.py        16 케이스(파서·cites 거부·조립·영속·링크·서브그래프·stale 수정).
```

### citation을 "생성"이 아니라 "조립"으로 강제 (DR-0006 핵심)

이 프로젝트의 존재 이유 = **근거 없는 주장 출력 금지**(CLAUDE.md 원칙 4). LLM이 문장을 생성하면 citation이 사후 장식이 되어 환각 방지가 무너진다. 그래서:

1. 툴이 store를 읽어 **사실(Fact)** 을 확정 — 각 Fact가 근거 객체 id를 들고 태어난다.
2. 문장은 사실 단위로 **조립**된다 — `{text, cites:[객체id], confidence, kind}`. cites는 조립의 부산물이지 사후 매칭이 아니다.
3. `write_assessment`가 **문장 단위로 다시 검증** — cites 빈 문장이 하나라도 있으면 `SentenceEvidenceError`로 전체 거부(P2 EvidenceError 패턴 계승).
4. LLM(`SKAI_EXPLAINER=claude`)은 조립된 문장의 **서술만** 다듬고 cites 매핑은 룰 측에 남는다. 실패/문장 수 불일치 시 원문 유지(DR-0004 폴백).

---

## 2. 질의/답변 예시 3개 (문장별 cites)

라이브 서버(`/api/assess`)에 POST한 실제 응답. **모든 문장이 cites를 갖는다**(전부 근거 보유=True). 종합 신뢰도 0.71(저신뢰 뉴스가 평균을 끌어내림 — 투명).

### 질의 1 — "지금 KADIZ 근방 이상한 거 있어?"
- 파싱: 지역 **KADIZ** · 창 **최근 30분(지금)** · defaulted=[] · 소스히트 {항적 2·이상 2·통과 11·기상 1·뉴스 4}
- 문장 8개(요약·이상징후×2·상관×2·위성·기상·뉴스):

| kind | conf | cites | 문장(요약) |
|---|---|---|---|
| 요약 | 0.95 | 4 | 최근 30분(지금) KADIZ에서 이상징후 2건·항적 2대 확인 |
| 이상징후 | 0.95 | 2 | [합성] DEMO75 비상 스쿽 7500(하이재킹) — 미검토, 근거 관측 1건 |
| 이상징후 | 0.93 | 2 | [합성] DEMO77 비상 스쿽 7700(일반 비상) — 미검토, 근거 관측 1건 |
| 상관 | 0.50 | 3 | 이상징후 DEMO75(16:56)는 ±60분 안 위성 통과 2건과 겹침: SL-14 R/B·COSMOS 2219 (확증 아님) |
| 상관 | 0.50 | 3 | 이상징후 DEMO77(16:56)는 ±60분 안 위성 통과 2건과 겹침 (확증 아님) |
| 위성 | 0.85 | 9 | 질의 시각창 근방 위성 통과 9건 |
| 기상 | 0.90 | 1 | RKSI MVFR·실링 1000ft·시정 3.73sm (관측 57분 전, 질의창 밖) |
| 뉴스 | 0.35 | 4 | OSINT 4건이 KADIZ 언급 — 확증 아님, 교차검증 요망 |

- cites 예: 상관 문장 = `['anomaly-…-p4dem2-…', 'pass-19574-…', 'pass-22219-…']`(이상징후+통과 함께 인용 = 교차소스 provenance).

### 질의 2 — "최근 1시간 위성 통과랑 겹치는 이상징후는?"
- 파싱: 지역 **KADIZ**(defaulted — 질의에 지역어 없음) · 창 **최근 1시간**
- 상관 문장이 핵심 답: 이상징후 DEMO75/DEMO77이 각각 ±60분 안 KADIZ 상공 위성 통과(SL-14 R/B NORAD 19574 16:48~16:50·COSMOS 2219 NORAD 22219 16:47~16:49)와 **같은 시공간 창에 공존** → 상관 후보(확증 아님).
- 기상 문장은 이 창(1시간)에선 "질의창 밖" 표시가 사라짐(관측 57분 전 < 1시간). = 시간창 의미가 실제로 작동.

### 질의 3 — "서해 쪽 기상이랑 뉴스 맥락 요약해줘"
- 파싱: 지역 **KADIZ**(별칭 "**서해**" 매칭) · 창 **최근 30분(기본)**(시간 표현 없음 → defaulted=['window'])
- 기상 문장(RKSI MVFR)·뉴스 문장(GDELT 저신뢰 4건, "Why did Russian and Chinese aircraft enter South Korea air defense zone?" 등 KADIZ 언급)이 맥락으로 조립. 각각 WeatherState·NewsEvent id를 cites.

> 세 질의 모두 같은 초에 POST됐지만 **질의 해시로 id를 분리**해 3건이 각각 영속(`/api/assessments` = 3건). 같은 질의·같은 시각은 같은 id(멱등 upsert).

---

## 3. citation 강제 증명 (거부 사례)

cites 없는 문장은 어떤 편의로도 Assessment에 못 들어간다. `write_assessment`에 근거 있는 문장 1개 + 근거 없는 문장 1개를 섞어 넣으면 **전체가 거부**된다:

```
입력 문장[0] = {text:'항공기 X가 위협적이다', cites:['obs-real']}   ← 근거 있음
입력 문장[1] = {text:'적이 곧 공격할 것이다', cites:[]}             ← 근거 없음(무근거 주장)

→ SentenceEvidenceError: cites 없는 문장 거부 — 근거 객체 없는 주장은 Assessment에
   못 들어간다 (DR-0006: citation은 조립의 부산물). 문장[1]='적이 곧 공격할 것이다'
→ 저장된 assessment: 0 건 (부분 저장 없음 — 검증이 write 이전)
```

추가로 **근거 사실이 하나도 없는 질의**는 무근거 요약을 지어내지 않고 `no_evidence` 응답을 낸다(assessment_id=None, Assessment 미생성). "해당 없음"을 정직하게 보고 = 환각 대신 침묵.

---

## 4. 서브그래프 뷰 (온톨로지 깊이 시연)

`GET /api/subgraph?assessment_id=`가 Assessment 중심의 노드·엣지 JSON을 내고, 프론트가 **자체 SVG 방사형 레이아웃**으로 렌더한다(외부 그래프 라이브러리 없음 — DR-0006). 질의 1의 서브그래프(`docs/worklog/p4_subgraph.png`): 24 노드·25 엣지.

**다중홉 provenance 그래프**(ontology.md §0 스멜테스트 1·4 통과):
```
SituationAssessment(상황평가)
  ─aggregates→ Anomaly(이상징후 DEMO75/DEMO77)
                 ─evidenced_by→ Observation(관측 sq7500/sq7700)   ← 2홉 깊이
                 ─involves→ Aircraft(✈ DEMO75/DEMO77)             ← 2홉 깊이
  ─cites→ OrbitPass(위성통과 12) / WeatherState(기상 RKSI) / NewsEvent(뉴스 4)
```
= flat table로 표현 불가한 서브그래프(한 행이 아님). 노드 색=Object Type, 엣지=Link Type(aggregates 빨강 점선·cites 파랑·evidenced_by/involves 깊이). 노드 클릭 → 상세(id·원 소스·좌표) + "지도에서 보기".

채팅에서도 문장의 **cites 배지 클릭 → 지도 하이라이트**(관측·통과·기상은 좌표로 팬+펄스, 뉴스는 좌표 없어 원문 링크로). 근거가 화면에서 원 객체까지 역추적된다.

---

## 5. 검증 결과 (성공기준 4항목 — 실행으로 증명)

| # | 기준 | 결과 |
|---|---|---|
| 1 | tests/test_p4.py(cites 거부·파서·조립·영속·stale) + 기존 58 유지 | **OK** — **74/74 통과**(P1 14 + P2 18 + P3 26 + P4 16) |
| 2 | 질의 3개 end-to-end, 문장별 cites | **OK** — §2. 3질의 모두 8문장 전부 cites 보유, 3 Assessment 영속 |
| 3 | 채팅 질의→답변→cites 배지→지도 하이라이트 + 서브그래프 렌더 | **OK** — `p4_copilot.png`·`p4_subgraph.png`(1500×950) |
| 4 | 검증 후 프로세스 정리 + OpenSky 신규 호출 최소화 | **OK** — 서버 종료·잔여 0, 기존 P1~P3 DB 재사용, OpenSky 신규 호출 0(합성 스쿽 2건만 주입) |

### citation 정확도(architecture.md §7 평가지표)
- **유효 cites 비율 = 100%**: 세 Assessment의 모든 문장이 비어있지 않은 cites를 갖고, 모든 cite id가 store의 실 객체로 해상됨(`cited_objects` 미해상 0 — 테스트 `test_cited_objects_all_resolved`).
- **거부율 증명**: 무근거 문장 주입 시 100% 거부(§3), 무근거 질의는 Assessment 미생성.

### 시간창 의미 작동 확인
- 이상징후는 "지금"(30분) 창에 신선 주입분만 진입(기존 46분+ 이상징후는 자동 제외).
- 기상 관측(57분 전)이 30분 창에선 "질의창 밖" 표시, 1시간 창에선 정상 — 창 필터가 실제로 다르게 동작.
- **위성 통과 상관**: OrbitPass는 미래 예측 객체라 "겹침"을 구간 포함이 아니라 이상징후 발생 시각 ±60분(`CORRELATION_WINDOW_SECONDS`)의 시공간 버킷으로 정의(ontology.md §2 correlated_with). now±1h에 11건 통과 → 신선 이상징후와 상관 성립.

---

## 6. P3 이월 #1 수정 (OrbitPass stale 누적)

P3 §6-1: 통과창은 "now 이후 12h"를 계산 → 폴러 반복마다 신규 id가 쌓여 과거 계산의 미래 통과가 stale로 잔존(2회 실행 시 99→196 배증). **수정**: `store.delete_future_orbitpasses_for(satellite_ref, now_ts)` 신설 + `celestrak.ingest`가 재계산 직전 위성별 **미래**(start_ts ≥ now) 통과를 삭제(과거는 관측 이력으로 보존)하고 새 계산으로 대체. 만료/무통과 위성도 정리된다. of/over 링크도 함께 제거. 테스트 `test_delete_future_orbitpasses_preserves_past`(과거 1건 보존·미래 2건 삭제·링크 정합) 통과.

---

## 7. 루트 기획문서와의 정합 (어긋남 아님 — 기록만)

- **SituationAssessment가 코드에 없었음**: ontology.md §1엔 정의됐으나 model.py엔 미구현(P3까지 미사용). P4에서 dataclass·테이블·액션을 추가 = 온톨로지 v0.1 액션 4종 중 **3종째 구현**(CreateAnomaly·Confirm/Dismiss·GenerateSituationAssessment). SetRegionAlertLevel만 잔여. 루트 문서 무수정.
- **시간창의 위성 상관 정의**: PROMPTS P4는 "이상탐지 병합"만 명시. OrbitPass가 미래 예측 창이라 "겹침"을 시공간 버킷(±60분)으로 구현 — ontology.md §2 correlated_with·§0 예시("위성이 머리 위 지나갈 때")의 직접 구현이지 스코프 이탈 아님. 상관은 정황(confidence 0.5)이며 확증 아님을 문장에 명시.
- **뉴스는 질의 시간창으로 안 자름**: NewsEvent는 OSINT 회고(7d 창)라 30분/1시간 창으로 자르면 대개 0건. region 언급 뉴스를 최신순으로 반환하되 저신뢰(≤0.4)·확증 아님을 문장에 명시(DR-0005 준수).
- **요약 문장 cites 표본화**: "항적 N대"의 전수 근거는 counts(같은 tool read 산출), 배지·서브그래프 가독을 위해 대표 관측 6건만 인용. 무근거 아님.
- **딥링크(?q=&sg=)**: 공유 가능한 질의 링크 + 헤드리스 스크린샷 재현용 실 기능(데모 전용 훅 아님).
- **결정적 파서 유지**: LLM 파싱 비목표(DR-0006 결정 2). 지역 별칭은 한국어 질의 중심 사전(parser.REGION_ALIASES), gdelt의 영문 뉴스 매칭 사전과 목적 분리.

---

## 8. P5/P6에 넘길 이슈 / 발견사항

1. **correlated_with 온톨로지 링크 미저장(P5 핵심)**: 현재 이상징후↔통과 상관은 assessment 문장의 cites로만 표현(교차소스 provenance). P5는 이를 `Anomaly —correlated_with→ Anomaly/OrbitPass/NewsEvent` **링크로 영속**해 "은닉 정황" 내러티브(ontology.md §2 예시)를 온톨로지 그래프에 남겨야 한다. 시공간 버킷 로직(`CORRELATION_WINDOW_SECONDS`)은 재사용 가능.
2. **뉴스↔항적 시공간 상관 미구현**: P3 §6-2대로 콜사인 exact match는 군용 인시던트에 안 걸림. P5 correlated_with는 콜사인 대신 시공간 버킷으로 뉴스↔이상징후를 잇는 게 목표. 현재 news 툴은 region 언급만 필터(시간 상관 없음).
3. **이상탐지 1종(비상 스쿽)만 병합**: assessment는 기존 Anomaly를 읽을 뿐 새 탐지를 안 돈다. P5에서 dropout·로이터링·군용기 접근·위성 근접이 추가되면 assessment는 자동으로 그 Anomaly들을 문장화(툴 무변경) — 단 위성 근접(OrbitPass over Region during window)은 **Anomaly로 승격**돼야 상관이 아닌 이상징후로 잡힘(현재는 상관 문장으로만).
4. **LLM 서술 다듬기 미검증(라이브)**: `SKAI_EXPLAINER=claude` 경로는 구현·폴백 테스트했으나 실제 `claude -p` 호출로 문장 다듬기는 데모 재현성 위해 기본 template 사용. P6 데모 전 1회 실호출로 서술 품질·cites 불변 확인 권장(문장 수 불일치 시 원문 폴백은 검증됨).
5. **now 앵커링**: 파서는 wall-clock now 기준(정직한 "지금"). 데모는 신선 합성 주입으로 창을 맞춤. P6 스냅샷 재생 모드에선 now를 스냅샷 시각에 앵커(assess의 now 파라미터 이미 주입 가능)해야 라이브 부재 시에도 질의가 결과를 냄. NewsEvent/WeatherState 주입기는 여전히 미구현(P3 §6-5) — 스냅샷 재생에 필요.
6. **서브그래프 규모**: 통과 12·뉴스 4로 24노드까지는 방사형 SVG가 읽힘. P5에서 correlated_with·다이상징후로 노드가 늘면 레이아웃(force-directed 없이) 겹침 우려 — 필요 시 노드 수 상한 또는 타입 필터 토글. 현 규모는 충분.
7. **assessment 목록 UI 미노출**: `/api/assessments`(영속 인텔 목록)는 API만 있고 프론트 히스토리 패널 없음. P6 데모에서 "질의마다 인텔 객체가 쌓임"을 보이려면 목록 뷰 추가 고려(선택).

---

## 9. 되돌리기

- 신규: `copilot/`(4파일)·`tests/test_p4.py`·`docs/worklog/p4_copilot.png`·`p4_subgraph.png`·`docs/decisions/DR-0006-*.md`(기존) 삭제.
- 기존 파일 역편집: `ontology/model.py`(SituationAssessment·AssessmentSentence·cite_object_type), `ontology/store.py`(SentenceEvidenceError·validate_sentence_cites·Protocol), `ontology/store_local.py`(assessment 테이블·write/query·delete_future_orbitpasses_for·counts), `ontology/store_foundry.py`(스텁), `connectors/celestrak.py`(이월 #1 미래 pass 선삭제·return 시그니처), `server/app.py`(3 엔드포인트·title), `web/index.html`(채팅·서브그래프·딥링크).
- 온톨로지 스키마 v0.1 유지(assessment 테이블은 `CREATE TABLE IF NOT EXISTS`라 기존 DB 비파괴). 런타임 산출물(data/·*.db)은 gitignore. 데모용 합성 스쿽 2건·Assessment 3건은 DB에 남음(재주입/삭제 가능).
- 루트 기획문서 무변경(어긋남은 §7에 기록만).
