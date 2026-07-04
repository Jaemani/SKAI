# DB 레짐 3분리 확립 + 혼재 정리 + disjoint 해소 (A6)

- 날짜: 2026-07-04
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 근거: 초기 계획 잔여 A6 · DR-0012 병행 사항("누적 skai.db 정리") ·
  region-summary-wire.md §4-2(disjoint 문제) · live-multisource.md §2(순수 라이브/데모 분리).
- 목표: (1) DB 레짐 3분리를 **명문화 + 집행**, (2) 라이브 db(skai.db)의 옛 합성 오염 제거,
  (3) 합성 재오염 방지 가드, (4) 데모 Foundry 자산의 로컬↔Foundry disjoint 해소.

## 1. DB 레짐 3분리 (SSOT)

| db 경로 | 역할 | 내용 | 수명 | 쓰는 주체 |
|---|---|---|---|---|
| `data/skai.db` | **라이브 런타임** | 실데이터 **전용**(opensky·gdelt·metar·celestrak). 합성 금지 | 누적(재인제스트 가능) | live 폴러(`connectors.opensky`), 서버 |
| `data/demo/skai_demo.db` | **replay 합성** | 결정적 선언 시나리오 전량(P5). 같은 앵커=같은 산출 | 매 `demo.sh replay` 재생성 | `demo.sh replay` → `inject_synthetic --scenario all` |
| `data/demo/skai_foundry_local.db` | **Foundry 데모 로컬 미러** | skaidemo 합성 1건(anomaly+region). obs·aircraft는 Foundry 소재 | 매 `demo_foundry` 재생성 | `demo_foundry.py`(HybridStore dual-write 로컬측) |

- **경로 상수 SSOT**: `store_local.DEFAULT_DB` = `data/skai.db`(라이브). `demo.sh $DEMO_DB` =
  `skai_demo.db`. `demo_foundry.py LOCAL_DB` = `skai_foundry_local.db`. HybridStore 기본
  `db_path=DEFAULT_DB`지만 demo_foundry는 `make_store(LOCAL_DB)`로 데모 미러를 명시 지정한다.
- **정리 대상이던 잔재 db**(레짐 밖, 방치): `data/skai_p5_demo.db`·`data/p7_hybrid.db`는 옛 P5/P7
  검증 산출물. gitignore(`data/`·`*.db`)라 커밋 안 되며 런타임에 미참조 → 이번엔 건드리지 않음
  (백로그: 원하면 삭제 가능, 재생성 스크립트 존재).

## 2. skai.db 혼재 정리 — 백업 후 리셋 (판단 기록)

### 정리 전 상태 (백업 인벤토리: `data/backup/skai-cleanup-inventory.20260704-223910.txt`)
- observation 1406 = opensky(실) **1385** + synthetic **21**.
- 합성 21건 내역: `nvshadow-*`(narrative_hidden 시나리오) 16 · `p4dem1/p4dem2`(inject) 2 ·
  `synth01/02/03`(inject) 3.
- anomaly 21 = 합성 파생(emergency_squawk synth/p4dem 5 · adsb_dropout nvshadow 2 ·
  loitering nvshadow 1) + satellite_proximity 13(실 위성 NORAD ref지만, 합성 시나리오 실행 시각
  버킷과 상관돼 생성 — 순수 실/합성 경계가 애매).

### 판단: **surgical 삭제 대신 백업 후 리셋(재생성)**
- 근거 ①: 합성 파생 링크가 link 3613행에 얽혀 있고, satellite_proximity 13건은 실 위성 +
  (합성/실) 항적 상관이라 "무엇이 합성 파생인가"가 애매 → surgical cascade는 오삭제/누락 위험.
- 근거 ②: `live-multisource.md §5`가 skai.db를 "gitignore된 런타임 산출물, 재인제스트 가능"으로
  규정. 팀리드도 "백업 후 리셋 OK(실데이터 재인제스트 가능)"로 명시 승인.
- 근거 ③: 리셋은 "합성 잔존 0"을 **모호성 없이** 보장(레포트 가치). surgical은 잔존 검증이 복잡.

### 집행
1. 백업(SHA 대조 일치 확인): `data/backup/skai.db.20260704-223910.pre-regime-cleanup`
   (+ `-shm`, `.live.json` 사이드카). 인벤토리 텍스트 동반 보존.
2. 리셋: `skai.db`(+`-wal`/`-shm`/`.live.json`) 삭제 → `LocalOntologyStore(DEFAULT_DB)` init으로
   **빈 스키마 재생성**(12테이블, 0행).
3. 검증: 전 테이블 0행 · `source='synthetic' OR source_url LIKE 'synthetic://%'` = **0**.
- 되돌리기: `cp data/backup/skai.db.20260704-223910.pre-regime-cleanup data/skai.db`.
- 재populate: `scripts/demo.sh live`(순수 라이브 4소스 폴러)가 다음 실행에 실데이터로 채운다.

## 3. 레짐 가드 (합성 재오염 방지)

`scripts/inject_synthetic.py`에 `_guard_live_db()` 추가 — 주입 대상 db가 라이브 런타임
`DEFAULT_DB`(realpath 정규화 비교)이면 **`--allow-live-db` 없이는 거부(exit 2)**하고, 순수 합성은
`--db data/demo/...`로 분리하도록 안내한다.
- **함수 레이어(`inject_scenario`·`inject`)는 무변경** — 가드는 CLI `main()`에만. 프로그래매틱
  호출(테스트 `test_p6.py`)·replay 경로 불변.
- `replay`는 `--db skai_demo.db`(데모 db)라 가드 통과. `live --inject`만 `skai.db`에 데모 서사
  1건을 의도적으로 얹으므로 `demo.sh`가 그 호출에 `--allow-live-db`를 넘겨 명시 승인한다.
- 검증: `--db` 미지정(→skai.db) 거부 · 명시 skai.db 거부 · 데모 db 통과 · `--allow-live-db` 통과 ·
  경로 정규화(`data/skai.db`=live, `skai_demo.db`≠live) 전부 확인.

## 4. disjoint 해소 (region-summary-wire §4-2 종결)

### 문제(§4-2)
foundry 모드 assess는 **anomaly·region을 로컬에서** 읽는다(HybridStore 설계 — obs·aircraft만
Foundry). 데모 skaidemo가 Foundry엔 있고 로컬 skai.db엔 없어 disjoint → 자연 assess가 skaidemo를
못 집어 §4-2 E2E는 **임시 로컬 미러**로 우회했다.

### 진짜 원인 = "assess가 어느 로컬 db를 읽나"의 불일치
demo_foundry는 이미 HybridStore로 skaidemo anomaly를 **로컬(skai_foundry_local.db)+Foundry에
dual-write**한다(dual-write 부재가 아님). §4-2가 assess를 기본 skai.db에 물려 disjoint가 난 것.
→ 해소 = assess를 demo_foundry가 쓴 **같은 로컬 db(skai_foundry_local.db)**에 물리고, 그 미러가
자연 assess에 **자립**하도록 만든다.
- **`skai.db`로 통일은 기각**: demo_foundry는 로컬 미러를 매 실행 재생성(throwaway)하고 합성이므로,
  skai.db로 지정하면 (a) 실데이터 wipe (b) skai.db 재오염 — 레짐 §1·§2 위반. 팀리드 가설
  "HybridStore 로컬 경로=skai.db?"의 답은 **아니오**(foundry-데모 전용 미러 db가 정답).

### 코드 변경
`scripts/demo_foundry.py run()`: 로컬 미러에 `store.write_region(KADIZ_REGION)` 추가
(HybridStore write_region=로컬 전용). region이 없으면 `_region_name`이 id('KADIZ')로 폴백 →
미러 자립성 확보(anomaly+region 로컬, obs·aircraft는 Foundry). 다른 경로 불변.

### E2E 검증 (임시 미러 없음)
1. `demo_foundry.sh run` **1회**(허용된 Foundry 쓰기 1배치): 직전 데모 자산 dedup 정리 →
   fresh `anomaly-emergency_squawk-skaidemo1783172688-2971954`(conf 0.95, confirmed)를
   로컬 미러 + Foundry에 dual-write. 로컬 미러 확인: anomaly 1 · region KADIZ(친화명) · obs 0(Foundry 소재).
2. 자연 assess: `SKAI_STORE=foundry` + `SKAI_COPILOT_LLM=aip`, db=`skai_foundry_local.db`,
   "지금 KADIZ 근방 이상한 거 있어?" → 결과:
   - `intent=situation_summary` · **`produced_by=aip_logic`** ✓ (AIP Logic이 헤드라인 생성)
   - `region={id:KADIZ, name:한국 방공식별구역 (KADIZ)}` ✓ (미러 region 자립)
   - `counts={flights:4, anomalies:1}` — flights 4는 **Foundry**, anomaly 1(skaidemo)은 **로컬 미러**
     에서 자연히 합류(reads 분할 정상)
   - headline: AIP가 skaidemo Anomaly 객체 실제 속성(스쿽 7500·위치 36.8/124.2·confirmed) 종합
   - cites: anomaly + 근거 obs + 실 항적 표본 → provenance 보존
   - **VERDICT: AIP-NATURAL-OK** — §4-2의 임시 미러 없이 자연 흐름으로 AIP 요약이 뜸.
- **Foundry 부가쓰기 억제**: assess는 SituationAssessment를 Foundry에 dual-write(best-effort)한다.
  감사 제약("Foundry 쓰기=demo_foundry 1회분만") 준수 위해 **검증 하네스에서만** `foundry.write_assessment`를
  no-op 처리(read/요약 경로와 무관, 실데모에선 정상 dual-write). Foundry 실쓰기는 demo_foundry 1배치뿐.

### 데모 재현 배선(문서화)
foundry-데모 assess를 서버로 돌리려면 서버를 `SKAI_STORE=foundry SKAI_COPILOT_LLM=aip
SKAI_DB=data/demo/skai_foundry_local.db`로 기동(= demo_foundry가 쓴 로컬 미러를 read). demo.sh에
foundry-데모 서버 모드 추가는 이번 범위 밖(직접 assess로 disjoint 해소를 입증).

## 5. 검증 종합
- pytest(.venv): **280 passed, 4 skipped** — 회귀 0.
- replay 결정성: 같은 앵커 2회 빌드 내용 **IDENTICAL**(anomaly 9·obs 83·link 244 = 기존 skai_demo.db와 동일). 회귀 0.
- 라이브 db 합성 잔존: **0**(obs 0·anomaly 0, 전 테이블 0행).
- disjoint 해소 E2E: **1회** 통과(produced_by=aip_logic, 임시 미러 없음).
- 스크립트 구문: demo.sh·demo_foundry.sh(`bash -n`) · inject_synthetic.py·demo_foundry.py(ast) OK.

## 6. 되돌리기
- skai.db 복원: 백업본 cp(§2).
- 가드 제거: `inject_synthetic.py`의 `_guard_live_db`·`_is_live_runtime_db`·`--allow-live-db` arg +
  `main()` 호출 역편집. `demo.sh`의 `--allow-live-db` 인자 제거.
- demo_foundry region 쓰기 제거: `run()`의 `store.write_region(KADIZ_REGION)` + import 역편집
  (제거해도 disjoint 재발 아님 — region_name이 id로 폴백될 뿐, anomaly 합류는 유지).
- demo.sh 헤더 레짐 주석은 문서라 무해.
