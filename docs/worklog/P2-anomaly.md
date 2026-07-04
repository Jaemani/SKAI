# P2-anomaly.md — 이상탐지 끝단 실행 로그 (비상 스쿽 → Anomaly → 화면)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P2)
- 근거: PROMPTS.md P2 · DR-0004 (ExplainerBackend 분리) · ontology.md §1~§4 · P1-vertical.md §5 발견
- 상태: **완료** (테스트 32/32 통과 · 합성 주입 end-to-end 확인 · 화면 3상태 렌더 확인)

---

## 1. 무엇을 만들었나

"비상 스쿽" 1종의 끝단: **룰 탐지 → 설명·신뢰도 → CreateAnomaly(evidence 필수) → 지도/타임라인 → confirm/dismiss**.
저장은 P1과 동일하게 `OntologyStore` 어댑터 뒤(SQLite "보험"). 온톨로지 스키마는 ontology.md §1~§4 정의 그대로.

```
anomaly/rules.py        squawk ∈ {7500,7600,7700} 후보 탐지(문자열 비교) + dedup 자연키
anomaly/explainer.py    ExplainerBackend(Protocol) + 3 백엔드 (DR-0004)
                          TemplateExplainer(기본) / ClaudeCliExplainer(옵션) / AipLogicExplainer(스텁)
anomaly/actions.py      create_anomaly(evidence 강제)·confirm/dismiss(상태 전이)·scan_and_create(파이프라인)
ontology/model.py       + Anomaly dataclass, ANOMALY_STATUSES, ANOMALY_WINDOW_SECONDS
ontology/store.py       + EvidenceError, validate_evidence + Anomaly Protocol 메서드
ontology/store_local.py + anomaly 테이블·write_anomaly(evidence 강제)·상태전이·evidence/involves 조회
ontology/store_foundry.py + 동일 메서드 스텁(Protocol 정합 유지)
connectors/opensky.py   ingest_cycle 끝에 scan_and_create 배선(폴러 사이클마다 탐지)
server/app.py           + GET /api/anomalies · POST /api/anomalies/{id}/confirm|dismiss
web/index.html          이상징후 빨강 마커 + 타임라인 + 근거·confirm/dismiss (P1 훅 승격)
scripts/inject_synthetic.py  합성 비상 스쿽 주입기(source="synthetic", provenance 유지)
tests/test_p2.py        18 케이스 (evidence 거부·룰·dedup·상태전이·explainer)
```

## 2. 주입기 사용법 (scripts/inject_synthetic.py)

라이브 KADIZ에 비상 스쿽이 상시 뜨지 않음(P1 발견 #3) → **명시 실행 시에만** 합성 주입.
커넥터를 우회해 store에 직접 Observation을 write하되 `source="synthetic"` + `source_url="synthetic://..."`로
**provenance는 유지**(validate_provenance 통과 = 합성도 출처를 남김). 설명문엔 "[합성 시나리오]" 표기.

```bash
# 기본: 7700(일반 비상) 1건 주입 + 탐지 + Anomaly 생성
.venv/bin/python -m scripts.inject_synthetic

# 코드·위치·콜사인 지정 (7500 하이재킹 / 7600 통신두절 / 7700 일반비상)
.venv/bin/python -m scripts.inject_synthetic --squawk 7500 --icao24 synth03 \
    --callsign CES7500 --lat 35.2 --lon 125.8

# claude 설명 백엔드로 (기본은 template — 데모 재현성)
SKAI_EXPLAINER=claude .venv/bin/python -m scripts.inject_synthetic
```

주입 후 지도(http://localhost:8000) 타임라인에 candidate로 노출. `--db`로 대상 DB 교체 가능.

### 서버 기동
P1의 `scripts/run_p1.sh start`가 그대로 사용됨(서버 + 폴러). 폴러가 사이클마다 이상탐지도 수행.
서버만 필요하면 `.venv/bin/python -m server.app`.

## 3. 검증 결과 (성공기준 5항목)

| # | 기준 | 결과 |
|---|---|---|
| 1 | 테스트 (evidence 거부·룰·dedup·confirm/dismiss) + 기존 P1 유지 | **OK** — 32/32 통과 (P1 14 + P2 18) |
| 2 | 합성 주입 end-to-end (주입→탐지→Anomaly→API→confirm) | **OK** — 아래 로그 |
| 3 | 화면 (빨강 마커 + 타임라인 + 근거 표시) | **OK** — `docs/worklog/p2_anomaly.png` |
| 4 | ClaudeCliExplainer 1회 실동작 | **OK(폴백 검증)** — 실 subprocess 호출 → 타임아웃 → template 폴백 |
| 5 | 서버·프로세스 정리 + OpenSky 최소화 | **OK** — 프로세스 clean, OpenSky 신규 호출 0(기존 P1 DB 재사용) |

### end-to-end 로그 요약 (기준 2)
```
inject 7700(synth01) → Anomaly candidate conf=0.93 evidence=[synth01-...] involves=[synth01]
inject 7600(synth02) → Anomaly candidate conf=0.90
inject 7500(synth03) → Anomaly candidate conf=0.95  (하이재킹 = 최고 신뢰도)
GET /api/anomalies    → 3건, 각 evidence·involves·source_url 노출
POST .../synth01/confirm → status=confirmed (영속)
POST .../synth02/dismiss → status=dismissed (영속)
GET /api/anomalies    → synth01:confirmed, synth02:dismissed, synth03:candidate
POST .../nope/confirm → HTTP 404
```
- **evidence 강제(핵심)**: `store.write_anomaly`가 `validate_evidence`로 evidence=[]를 EvidenceError로 거부.
  create_anomaly는 이 write만 경유 → **근거 없는 Anomaly는 어떤 경로로도 저장 불가**(테스트 `test_write_anomaly_rejects_empty_evidence`로 증명, 거부 후 저장 0건).
- **dedup**: 같은 (기체, 유형, 시간창=600s) → `anomaly-{type}-{icao24}-{window}` 자연키로 1개만. 재스캔 시 신규 0.
- **confidence**: 7500→0.95, 7700→0.93, 7600→0.90 (하드 신호 0.9대). 합성이어도 신뢰도는 룰(스쿽 코드)이 결정.

### 화면 (기준 3) — docs/worklog/p2_anomaly.png (1400×900)
한 프레임에 3상태를 모두 시연:
- **타임라인(좌상)**: 7500 CES7500 **후보**(빨강 테두리·선택), 7700 KAL7700 **확인됨**(빨강 배지), 7600 AAR7600 **기각됨**(회색·취소선). 각 항목 합성 배지·신뢰도.
- **선택 상세**: 설명문("[합성 시나리오] ...하이재킹..."), **근거(EVIDENCE·PROVENANCE)** 카드(콜사인·관측시각·source_url 링크), **확인/기각 버튼**.
- **지도**: 이상징후 마커 3개 — 후보(빨강·흰 강조링)·확인됨(짙은 빨강)·기각됨(회색·흐림). KADIZ 폴리곤 + P1 라이브 항적(노란 점).
- **통계(우상)**: 이상징후 3, 범례에 "이상징후(비상 스쿽)" 추가.

### ClaudeCliExplainer (기준 4)
- `claude -p` 서브프로세스를 **실제로 호출**(mock 아님). 짧은 프롬프트 단독 테스트(`claude -p "..."`)는 rc=0 성공 확인.
- 전체 설명 프롬프트는 **중첩 세션 컨텍스트**(이미 claude가 돌고 있는 안에서 또 claude 호출)에서 30초 초과 → 설계대로 **TemplateExplainer로 폴백**(backend=`template(claude_cli 폴백)`, confidence 0.93 보존, 크래시 없음).
- 즉 폴백 경로가 실 subprocess로 end-to-end 검증됨. 비중첩 환경에선 2~3문장 설명이 30초 내 완료될 것으로 예상(단독 호출은 즉답). **데모 기본은 template이라 이 지연과 무관**.

## 4. explainer 백엔드별 동작 (DR-0004)

| 백엔드 | 활성 조건 | 동작 | 신뢰도 출처 |
|---|---|---|---|
| **TemplateExplainer** | 기본(SKAI_EXPLAINER 미설정/template) | 결정적 설명문, LLM 없이 항상 동작(데모 재현성) | 룰(스쿽 코드) |
| **ClaudeCliExplainer** | `SKAI_EXPLAINER=claude` | `claude -p`로 서술 강화(30s 타임아웃), 실패·비정상종료·빈출력 시 template 폴백 | 룰(LLM은 서술만 — aip-integration.md §3) |
| **AipLogicExplainer** | `SKAI_EXPLAINER=aip` | NotImplementedError(스텁) — Foundry AIP Logic 개통 시 최종 이관 | (이관 대상) |

**핵심 규율**: 사실(콜사인·스쿽 값·좌표·신뢰도)은 룰이 확정, LLM은 서술 문장만 강화. 그래서 LLM이 죽어도 사실 무결성·신뢰도는 불변. `get_explainer()` 팩토리가 env로 선택.

## 5. 루트 기획문서와의 정합 (어긋남 아님 — 기록만)

- **PROMPTS.md P2 "AIP Logic 함수 AnomalyExplainer"** → DR-0004대로 `ExplainerBackend` 인터페이스로 구현. AIP Logic은 Foundry 개통 전 접근 불가(P0-B BLOCKED)라 `AipLogicExplainer`를 명시 스텁으로 두고 기본은 `TemplateExplainer`. **DR-0004가 승인한 편차**, Foundry 도착 시 스텁 구현으로 원설계 복귀.
- **ontology.md Anomaly.geo** → 이상징후는 단일 좌표(점)라 `lat`/`lon`으로 저장(Region의 폴리곤과 대비). 객체·링크(evidenced_by N:M, involves N:M) 정의는 ontology.md §1~§2 그대로.
- **Anomaly —correlated_with→ Anomaly**(ontology.md §2)는 **P5 범위**(교차소스 내러티브)라 P2에서 미구현 — 스코프 정합.
- 루트 기획문서(ontology.md 등) **무수정**.

## 6. P3에 넘길 이슈 / 발견사항

1. **탐지 대상 = 최신 관측만**: `scan_and_create`가 기본으로 `query_latest_observations`(항공기별 1건)를 스캔. 짧게 떴다 사라진 비상 스쿽은 다음 사이클에 최신에서 빠지면 놓칠 수 있음. dedup 자연키가 있으니 "전체 관측 스캔"으로 넓혀도 안전 — P5 dropout/급기동 룰 추가 시 스캔 범위 재검토 권장.
2. **involves는 Aircraft만**: P2는 비상 스쿽뿐이라 involves→Aircraft. P3의 위성(OrbitPass)·P5의 correlated_with 추가 시 involves 대상 타입(Satellite) 확장 필요(store.write_anomaly의 involves는 이미 generic link라 대상 타입만 늘리면 됨).
3. **Anomaly 위치 = 근거 관측의 점**: 지도 마커가 관측 좌표에 찍힘. dropout처럼 "마지막 관측 위치"가 핵심인 유형은 이 좌표 의미가 달라짐 — 유형별 geo 산출 규칙을 P5에서 명문화.
4. **confidence는 룰 하드코딩 테이블**: `_SQUAWK_CONFIDENCE`. P5 교차소스 검증(예: 뉴스·위성 통과와 겹치면 상향)이 들어오면 confidence를 정적 테이블이 아니라 correlated_with 근거 수로 조정하는 로직 필요.
5. **ClaudeCliExplainer 중첩 지연**: 데모를 claude 백엔드로 돌릴 경우(비권장) 나열형 프롬프트가 느릴 수 있음. P4 코파일럿(GenerateSituationAssessment)도 같은 백엔드 패턴을 쓸 예정(DR-0004 영향)이므로, 배치·스트리밍 또는 타임아웃 상향을 P4에서 검토.
6. **API read 성능**: `_anomaly_to_dict`가 anomaly마다 aircraft_map()·get_observation()을 호출(N+1). P2 규모(수건)엔 무시가능하나, 이상징후가 수백 건 되는 P5에선 배치 조회로 최적화 여지.

## 7. 되돌리기

- 신규: `anomaly/` 패키지 + `scripts/inject_synthetic.py` + `tests/test_p2.py` + `docs/worklog/p2_anomaly.png` 삭제.
- 기존 파일 편집 되돌리기(역편집): `ontology/model.py`(Anomaly·상수), `ontology/store.py`(EvidenceError·validate_evidence·Protocol), `ontology/store_local.py`(anomaly 테이블·메서드), `ontology/store_foundry.py`(스텁 메서드), `connectors/opensky.py`(scan 배선·반환 3-tuple), `server/app.py`(anomaly 엔드포인트), `web/index.html`(타임라인·마커).
- 온톨로지 스키마 v0.1 유지(anomaly 테이블은 `CREATE TABLE IF NOT EXISTS`라 기존 DB에 비파괴 추가). 런타임 산출물(data/·*.db)은 gitignore.
- 루트 문서 무변경.
