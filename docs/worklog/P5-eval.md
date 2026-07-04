# P5-eval.md — 이상탐지 확장 + 교차소스 내러티브 + 평가 (실행 로그)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P5)
- 근거: PROMPTS.md P5 · **DR-0007**(전체 결정 — 그대로 집행) · ontology.md §2(correlated_with)·§4 · architecture.md §3·§7 · CLAUDE.md 기술기준(dropout 교차검증) · 이월 P4 §8·P3 §6·P2 §6
- 상태: **완료** — 테스트 **98/98**(기존 74 + P5 24) · 탐지 P/R 1.00(전 유형) · 은닉 정황 내러티브 end-to-end · 맨몸 LLM 비교 실호출 · claude 서술 경로 실호출

---

## 1. 무엇을 만들었나

이상탐지를 1종(비상 스쿽)에서 **5종**으로 확장하고, 교차소스 상관을 **온톨로지 링크로 영속**했으며, 라벨된 합성 세트로 **precision/recall을 수치화**했다.

```
anomaly/military_db.py     군 콜사인 프리픽스·군용 예약 icao24 대역 사전(공개 지식·저신뢰 판정)
anomaly/crosscheck.py      CrossCheckSource 인터페이스 + Null(미확인)·SyntheticMirror(데모) 백엔드
anomaly/rules.py           +AnomalyDraft(유형-무관) +dropout·로이터링·군용기·위성근접 탐지 + 임계 상수
anomaly/correlation.py     시공간 버킷 → Anomaly—correlated_with→Anomaly/OrbitPass/NewsEvent 영속(상관 SSOT)
anomaly/actions.py         +create_from_draft(타입드 evidence/involves) +scan_and_create_all(전 유형+상관)
anomaly/explainer.py       +explain_draft(유형별 템플릿 서술 — dropout '단정 금지'·군용 '저신뢰' 명시)
ontology/geo.py            공용 지오 헬퍼(haversine·경로길이·point_in_region·region_of_point)
ontology/model.py          +OPAREA_WEST_REGION(데모 OpArea 소구역) +SENSITIVE_CLASSIFICATIONS
ontology/store.py/_local/_foundry  write_anomaly 타입드 근거 정규화 + query_evidence/involves/correlations
copilot/assessment.py      상관 문장을 persisted correlated_with에서 읽도록 리팩토링(중복 계산 제거)
                           + 유형별 이상징후 서술(_anomaly_sentence_text) + build_subgraph correlated_with 엣지
copilot/tools.py           query_anomalies에 attrs 전달(유형별 문장 조립용)
server/app.py              이상징후 dict에 evidence_objects(타입드)·correlations 추가
web/index.html             유형별 아이콘/색 + OpArea 폴리곤 + correlated_with 서브그래프 엣지 + 상세 상관 카드
scripts/scenarios.py       선언적 시나리오 12건(dropout·로이터링·군용·위성·스쿽·정상·은닉정황) + apply_scenario
scripts/inject_synthetic.py  +--scenario(narrative_hidden / all) 데모 주입 경로
eval/run_eval.py           탐지 P/R + 맨몸 LLM 비교 + claude 서술 실호출 → 표·JSON
tests/test_p5.py           24 케이스(룰 4종 양성·음성 / dropout 교차 / correlated_with / 시공간 버킷 경계)
```

**설계 원칙 유지**: 사실·신뢰도는 룰이 확정하고 LLM은 서술만(explainer 규율). 근거(evidence) 없는 Anomaly·cites 없는 문장은 store 레벨에서 거부(provenance 강제). 상관 로직은 correlation.py 한 곳(SSOT), copilot은 읽기만.

---

## 2. 룰별 로직 · 임계 (anomaly/rules.py)

유형-무관 `AnomalyDraft`(evidence/involves를 `(dst_type, dst_id)` 튜플로 보유)를 도입해 근거 타입이 유형마다 다른 문제를 풀었다(dropout=Observation, 위성 근접=OrbitPass+Satellite). 비상 스쿽 경로(AnomalyCandidate)는 무변경(기존 테스트 보존).

| 유형 | 신호(그래프 패턴) | 근거/주체 | 신뢰도 | 핵심 임계 |
|---|---|---|---|---|
| ADS-B dropout | Track.has_gap AND 마지막 위치가 민감 Region 내 + 교차 판정 | Observation / Aircraft | 미확인 **0.42**·확인 **0.72** | `GAP_THRESHOLD=90s`, 교차소스로 이분 |
| 로이터링 | Track 지속 ≥ 임계 AND 변위/경로길이 비율 낮음 | Observation / Aircraft | **0.60** | `MIN=10분`, `MAX_RATIO=0.35`, `MIN_PATH=15km` |
| 군용기 접근 | is_military(저신뢰 판정) AND Observation within OpArea | Observation / Aircraft | **0.5~0.65** | military_db 휴리스틱 / is_military 플래그 |
| 위성 근접 | OrbitPass over 민감 Region during now±창 AND 최대앙각 임계 | OrbitPass / Satellite | **0.40** | `WINDOW=±60분`, `MIN_ELEV=70°`(near-overhead) |
| 비상 스쿽(P2) | squawk ∈ {7500,7600,7700} | Observation / Aircraft | 0.90~0.95 | (기존) |

### dropout 교차소스 판정 (CLAUDE.md 기술기준 = "단정 금지"를 코드로)
`CrossCheckSource.confirm_absence(icao24, window) -> bool|None`:
- **None(미확인)** = 2차 소스 없음 → 저신뢰 후보(0.42), 설명문에 "**단정하지 않습니다**" 명시.
- **True(부재 교차 확인)** = 미러도 관측 못 함 → 상향(0.72), "의도적 트랜스폰더 차단 가능성".
- **False(여전히 관측)** = 우리 쪽 결측은 센서 아티팩트 → **dropout 생성 안 함**(오탐 차단).

데모 기본 = `SyntheticMirrorSource`(주입 시나리오가 absent/present 집합 제공). 라이브 2차 소스(adsb.fi 등)는 **인터페이스만**(ToS 검토 별도 — DR-0007). 기본값 `NullCrossCheckSource`는 항상 None → 라이브에서도 저신뢰 후보는 낸다.

### 군용기 저신뢰 판정 (anomaly/military_db.py)
공개 지식 기반 사전 — 군 콜사인 프리픽스(RCH=Reach/미 공수, CNV=미 해군 등)·군용 예약 icao24 대역(0xAE0000–0xAFFFFF = 미 정부/군용, 널리 문서화). **국가 블록 = 군용 표식 아님**이므로 신뢰도 상한 ≤0.65(단정 금지). 데모 기본은 합성 군용기(is_military 명시). **OpArea 데모 소구역**(KADIZ 내 서해, classification="OpArea") 1개를 Region으로 추가 — 스키마 변경 아님, 데이터 추가.

### 위성 근접 승격 (P4 발견 #3)
상관 문장으로만 남기지 않고 **저신뢰 Anomaly로 승격**(evidenced_by OrbitPass, involves Satellite — P2 §6-2·P3 §6-4의 generic 확장 지점 사용). near-overhead(최대앙각 ≥70°)만 승격해 전역 통과 나열을 막는다.

**라이브 배선 범위**: P5 전 유형 스캔(`scan_and_create_all`)은 주입기·평가·데모가 호출한다. OpenSky 폴러는 기존대로 비상 스쿽 1종만(`scan_and_create`) 유지 — 라이브 짧은 트랙에서 dropout/로이터링 오탐을 피하고 기존 P1~P4 동작을 무변경 보존(전 유형 라이브 배선은 P6 토글로).

---

## 3. correlated_with 설계 (anomaly/correlation.py)

시공간 버킷으로 이상징후를 다른 이상징후·뉴스·위성통과와 잇고 **온톨로지 링크로 영속**한다. 이것이 "은닉 정황" 내러티브(ontology.md §2 깊이 증명 질의)의 그래프 백본이다.

| 상관 | 시간 술어 | 공간 술어 | 링크 |
|---|---|---|---|
| Anomaly ↔ OrbitPass | 통과창 ∩ [a.ts±60분] | 이상징후 점이 통과 Region bbox 내 | Anomaly→OrbitPass |
| Anomaly ↔ NewsEvent | \|a.ts − news.ts\| ≤ 24h | 뉴스가 이상징후 Region **언급**(콜사인 비의존) | Anomaly→NewsEvent |
| Anomaly ↔ Anomaly | \|Δt\| ≤ 60분 | haversine ≤ 300km | Anomaly→Anomaly(정준방향 1개) |

- **콜사인 비의존**(DR-0007 결정 4): 뉴스↔이상징후는 "같은 Region 언급 + 시간 근접"으로 잇는다(군용 인시던트엔 상용 콜사인이 없음 — P3 발견 #2).
- correlated_with는 **정황(확증 아님)** — 신뢰도를 올리지 않고 관계만 남긴다.
- **copilot 리팩토링**: `_assemble_satellite`의 ±버킷 인라인 계산을 제거하고, `correlate()`가 영속한 링크를 **읽어** 상관 문장을 만든다(로직 중복 제거·SSOT). assess()가 질의 범위 이상징후에 대해 correlate를 호출해 링크를 보장한 뒤 조립한다.
- **서브그래프**: build_subgraph에 correlated_with 3홉을 추가 — dropout Anomaly ─correlated_with→ OrbitPass·NewsEvent 서브그래프가 온톨로지 그래프에 남는다.

> 데모 참고: 공간 임계 300km는 관심지역(KADIZ ~800km) 규모의 관대한 값이라 같은 now·같은 지역 이상징후를 폭넓게 묶는다(`all` 주입 시 풍부한 클러스터). 실 운용은 이 임계를 좁혀 정밀도를 높인다.

---

## 4. 평가표 (precision / recall) — architecture.md §7 "숫자로 증명"

라벨된 합성 시나리오 12건(각 격리 임시 DB, 고정 now 앵커 = 결정적)을 주입·탐지해 유형별·전체 P/R을 산출(`eval/run_eval.py`, JSON = `p5_eval.json`).

### 유형별
| 유형 | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| 비상 스쿽 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| ADS-B dropout | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| 로이터링 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| 군용기 접근 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| 위성 근접 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| **전체(micro)** | **9** | **0** | **0** | **1.00** | **1.00** | — |

### 시나리오별 (신뢰도 = 탐지 시 산출값)
| 시나리오 | 라벨 | 탐지 결과 | 판정 |
|---|---|---|---|
| dropout_confirmed | adsb_dropout | adsb_dropout **0.72**(교차 확인) | OK |
| dropout_unconfirmed | adsb_dropout | adsb_dropout **0.42**(미확인·단정 금지) | OK |
| dropout_present_mirror | 정상(음성) | — (미러가 관측 → dropout 아님) | OK |
| emergency_hijack | emergency_squawk | emergency_squawk 0.95 | OK |
| loitering_orbit | loitering | loitering 0.60 | OK |
| military_callsign | military_approach | military_approach 0.55(콜사인 RCH) | OK |
| military_flag | military_approach | military_approach 0.55(is_military) | OK |
| satellite_overhead | satellite_proximity | satellite_proximity 0.40 | OK |
| narrative_hidden | adsb_dropout·satellite_proximity | 둘 다 탐지 | OK |
| normal_transit_a/b/c | 정상(음성) | — (이상징후 0) | OK |

- **완벽한 P/R의 의미와 한계**: 결정적 합성 세트라 1.00은 예상값 — 룰이 라벨과 정확히 일치하고 정상 트래픽·미러-관측 케이스에서 **오탐 0**임을 보인다. 특히 `dropout_present_mirror`(교차소스가 오탐을 막음)와 3건의 정상 통과가 FP=0을 지지한다. 라이브 노이즈에 대한 P/R은 P6/이후 라이브 라벨링으로 별도 측정 필요.

---

## 5. 맨몸 LLM vs 온톨로지+AIP 파이프라인

같은 질의를 (a) 파이프라인(`/api/assess`) (b) `claude -p` 단독(온톨로지 없이 **같은 원시 관측 요약 텍스트**만, 타임아웃 120s)에 던졌다. **맨몸 호출은 실제로 성공**(폴백 아님).

질의: "지금 KADIZ에서 ADS-B가 끊긴 기체가 위성 통과·뉴스와 겹치나? 근거와 함께 답하라."

| 항목 | 온톨로지+파이프라인 | 맨몸 LLM(claude -p) |
|---|---|---|
| 출처(provenance) | 문장별 cites → 객체 id로 역추적 | **기계검증 가능한 출처 없음** |
| 인용 문장 비율 | **11/11 (100%)** | 구조적 인용 없음(자유 텍스트) |
| 무근거 주장 | **0건**(cites 없는 문장은 write에서 거부) | 검증 불가(사실 그라운딩 소스 없음) |
| 종합 신뢰도 | 0.52(문장 평균, 저신뢰 뉴스 반영) | 표기 없음 |
| 사실 처리 | 룰이 확정한 값만 문장화 | raw ts로 추론(예: "240초 공백" 식별) — 유능하나 검증 불가 |

맨몸 LLM은 원시 관측을 받아 gap·상관을 **추론은 잘 하지만**(240초 공백 식별, 위성/뉴스 시공간 판단), 그 주장을 **원 객체로 역추적할 수 없다**. 파이프라인은 모든 문장이 온톨로지 객체 id를 인용해 환각을 구조적으로 차단한다. 비교의 본질 = **provenance 유무**이며, 맨몸 호출 성공/실패와 무관하게 이 구조 차이가 핵심 우위다(맨몸이 실패하면 정성 구조 비교표로 대체하도록 하네스가 설계됨).

---

## 6. claude 서술 경로 실호출 (P4 이월 #4)

`SKAI_EXPLAINER=claude`로 assess 1회 실행 → 서술 백엔드·cites 불변 확인. 실행마다 결과가 갈렸다(중첩 세션 특성 — P2/P4가 기록한 대로):
- **1차 실행**: `produced_by=claude`(실호출 성공) · cites 불변 True.
- **2차 실행**: `claude -p` TimeoutExpired → `template(claude 폴백)` · cites 불변 True.

즉 실 `claude -p` 경로를 실호출로 검증했고, **성공하든 타임아웃으로 폴백하든 cites·신뢰도는 불변**(DR-0004)임을 양쪽에서 확인했다. 데모 기본은 template(재현성).

---

## 7. 은닉 정황 내러티브 (재현법 + 스크린샷)

ontology.md §2 예시 그대로 — "ADS-B 끊긴 기체 + 위성 통과 + 뉴스 언급"이 correlated_with로 묶인 서브그래프.

```bash
# 1) 데모 DB에 은닉 정황 시나리오 주입(지금 창에 맞춰 now=현재)
DEMO=data/skai_p5_demo.db
.venv/bin/python -m scripts.inject_synthetic --scenario narrative_hidden --now $(date +%s) --db "$DEMO"
# → dropout Anomaly(SHADOW7, 0.72 교차확인) + satellite_proximity Anomaly(SYN-RECON-7, 0.40)
#   correlated_with 5링크: dropout↔pass, dropout↔news, dropout↔sat, sat↔pass, sat↔news
# 2) 서버 + 딥링크(질의 자동 실행 + 서브그래프 자동 오픈)
SKAI_DB="$DEMO" .venv/bin/python -m server.app
#   http://localhost:8000/?q=<질의>&sg=1
```

- **assessment 문장**으로 확인: dropout(SHADOW7)·위성 근접이 각각 "correlated_with로 위성 통과 1건과 시공간 상관", "OSINT 뉴스 1건과 correlated_with로 연결(은닉 정황)"을 인용(이상징후+통과+뉴스 id를 함께 cites = 교차소스 provenance).
- **서브그래프**로 확인: `docs/worklog/p5_narrative.png` — dropout Anomaly ─correlated_with(청록 점선)→ OrbitPass·NewsEvent·satellite_proximity Anomaly 클러스터(노드 8·correlated_with 엣지 6). 좌측 상세 카드에 CORRELATED_WITH(NewsEvent·OrbitPass·Anomaly) 노출.
- **전 유형 보드**: `docs/worklog/p5_board.png`(`--scenario all`) — 5종 이상징후가 유형별 색/아이콘으로 지도·타임라인에 구분, OpArea 폴리곤(주황 점선), 유형 범례.

---

## 8. 검증 결과 (성공기준 4항목 — 실행으로 증명)

| # | 기준 | 결과 |
|---|---|---|
| 1 | tests/test_p5.py(룰 4종·dropout 교차·correlated_with·버킷 경계) + 기존 74 유지 | **OK** — **98/98**(74+24) |
| 2 | 은닉 정황 내러티브 end-to-end(3객체 상관 + 질의 문장 + 서브그래프 엣지 + 스크린샷) | **OK** — §7, `p5_narrative.png` |
| 3 | 유형별 precision/recall 표 + 맨몸 LLM 비교표 | **OK** — §4(전 유형 1.00)·§5 |
| 4 | 검증 후 프로세스 정리 · OpenSky 신규 호출 최소화 | **OK** — 서버 종료·잔여 0, 합성 중심(OpenSky 신규 호출 0) |

**제약 준수**: dropout 단일소스 결측 단정 금지(문장·신뢰도·설명 모두 반영) · 근거 없는 Anomaly/문장 금지(강제 유지) · git commit 없음 · 루트 기획문서 무수정 · 라이브 2차 ADS-B 소스 미연결(인터페이스만).

---

## 9. 루트 기획문서와의 정합 (어긋남 아님 — 기록만)

- **correlated_with 링크 구현**: ontology.md §2에 정의됐으나 P4까지 미구현(문장 cites로만). P5에서 온톨로지 링크로 영속 → 링크타입 11종이 전부 사용 상태에 근접. 루트 문서 무수정.
- **OpArea Region 추가**: ontology.md Region.classification에 이미 "OpArea" 정의 → **데이터 추가**(스키마 변경 아님). 데모 소구역 1개(서해).
- **evidence 타입 일반화**: store.write_anomaly가 evidence/involves를 문자열(기본 타입)뿐 아니라 `(타입,id)` 튜플도 받도록 정규화 — P2 하위호환 유지(str=Observation/Aircraft). ontology.md §2 N:M generic 링크 정의 내.
- **위성 근접 신뢰도**: 저신뢰(0.40, 정황). 상관은 신뢰도를 올리지 않는다(정황 유지).
- **평가 = 시나리오 레벨 P/R**: 각 시나리오 1표본(유형별 양성/음성). architecture.md §7 "합성 시나리오 주입으로 precision/recall"의 직접 구현.

---

## 10. P6에 넘길 이슈 / 발견사항

1. **스냅샷 재생 모드**(P6 핵심): `scripts/scenarios.py`가 선언적(now 앵커)이라 P6 스냅샷 재생이 그대로 재사용 가능. `--scenario all --now <스냅샷시각>`으로 라이브 부재 시에도 5종+내러티브 재현. 데모 대본에 넣을 것.
2. **라이브 전 유형 배선**: OpenSky 폴러는 비상 스쿽만 스캔(오탐 회피). 라이브에서 dropout/군용/로이터링을 켜려면 `scan_and_create_all` 배선 + 임계 재튜닝(라이브 트랙은 짧음) + dropout 라이브 2차 소스(ToS 검토 후) 필요.
3. **상관 공간 임계**: 데모용 300km는 관대함(같은 지역 광폭 클러스터). 라이브 정밀도엔 좁혀야 함. 위성↔이상징후는 통과 Region bbox 포함이라 OpArea 세분화 시 자동 정밀화.
4. **위성 근접이 자기 근거 통과와도 correlated_with**: 승격된 위성 근접 Anomaly가 자신의 evidence OrbitPass와도 상관됨(같은 시공간 = 참이나 다소 중복). 무해하나 P6에서 자기 근거 제외 여부 결정 가능.
5. **군용 판정 사전 확장**: military_db는 소수 공개 프리픽스·1개 대역만. 라이브 정밀도엔 ROKAF/USFK 콜사인·추가 대역 보강 여지(단 저신뢰 상한 유지).
6. **claude 서술 중첩 지연**: assess의 claude 폴리시 호출이 중첩 세션에서 타임아웃 가능(관찰됨). 데모는 template 기본. P6 데모 전 비중첩 환경 1회 확인 권장(1차 실행은 성공).
7. **evidence_objects/correlations UI**: 서버는 노출, 프론트 상세 카드는 렌더. 서브그래프 노드 수가 `all`에서 늘면 방사형 레이아웃 겹침 우려(P4 §8-6) — 필요 시 타입 필터/상한.

---

## 11. 되돌리기

- 신규: `anomaly/military_db.py`·`crosscheck.py`·`correlation.py` · `ontology/geo.py` · `scripts/scenarios.py` · `eval/`(패키지) · `tests/test_p5.py` · `docs/worklog/p5_*.png`·`p5_eval.json`·`p5_eval_output.txt` 삭제.
- 기존 파일 역편집: `anomaly/rules.py`(AnomalyDraft·4 탐지·임계) · `anomaly/actions.py`(create_from_draft·scan_and_create_all) · `anomaly/explainer.py`(explain_draft) · `ontology/model.py`(OpArea·SENSITIVE_CLASSIFICATIONS) · `ontology/store*.py`(타입드 근거 정규화·query_evidence/involves/correlations) · `copilot/assessment.py`(상관 리팩토링·유형별 서술·서브그래프 correlated_with) · `copilot/tools.py`(attrs) · `server/app.py`(evidence_objects·correlations) · `web/index.html`(유형 색·OpArea·correlated_with 엣지) · `scripts/inject_synthetic.py`(--scenario).
- 온톨로지 스키마 v0.1 유지(신규 테이블 없음 — correlated_with는 기존 link 테이블 사용). 런타임 산출물(data/·*.db)은 gitignore. 데모 DB(`data/skai_p5_demo.db`)는 재생성 가능.
- 루트 기획문서 무변경(어긋남은 §9에 기록만).
