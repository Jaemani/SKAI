# 군용기 접근 합성 시나리오 신설 (military_incursion)

- 날짜: 2026-07-05
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 배경: 실제 군용기는 공개 ADS-B(OpenSky)에 거의 안 잡힌다(트랜스폰더 OFF·필터링, P1 발견 #4).
  그래서 "군용기 접근"은 라이브로 재현 불가 → **합성으로 별도 선언**하되, 라이브 실데이터와
  데이터적·시각적으로 명확히 구분되게 한다(source="synthetic" 명시, 합성 배지).
- 목표: (1) 서해 OpArea 군용 다중 접근 시나리오 신설(기존과 독립·추가만), (2) 명시 주입 경로
  (`demo.sh live --inject military`·`--scenario military_incursion`), (3) 정직 구분 유지,
  (4) replay 결정성·실데이터 불변 보장.

## 1. 시나리오 설계 (`scripts/scenarios.py` — `military_incursion`)

서해 작전구역(OPAREA-WEST, lat 35~37.5·lon 123.5~125.5)에 **군용 항적 3대**가 진입하는
인시던트(위협 접근 + 우방 요격 + 정찰). 각 항적이 `detect_military_approach`의 **탐지 3신호**를
하나씩 대표한다 — 라이브 부재를 메우면서 탐지 로직 전량을 시연.

| 항적 | 콜사인(라벨) | icao24 | 탐지 신호 | mil_reason | conf |
|---|---|---|---|---|---|
| A 미식별 접근기 | PLAAF01 | `780a1c`(중국 대역) | 명시 `is_military` 플래그(합성 선언) | `관측 소스 is_military 플래그` | 0.55 |
| B 우방 요격기 | ROKAF31 | `71ba22`(한국 대역) | 군 콜사인 프리픽스(military_db) | `군 콜사인 프리픽스 'ROKAF'(대한민국 공군)` | 0.55 |
| C 정찰기 | OLIVE21 | `ae1492`(미 군용예약) | 군용 예약 icao24 대역(military_db) | `미국 정부/군용 예약 대역` | 0.50 |

- 궤적: OpArea 내 짧은 직선(5분·gap 없음·정상 스쿽 2000·고도/속도 불변) → **군용 접근만** 트리거
  (dropout·로이터링·급기동·스쿽 오탐 0). 검증에서 `set(created.keys()) == {military_approach}` 확인.
- 전부 **저신뢰(≤0.65)** — CLAUDE.md 기술기준(대역은 국가 할당이지 군용 표식 아님 → 단정 금지).
- 전 관측 `source="synthetic"` + `synthetic://` provenance(apply_scenario가 강제) → 실항적 오도 금지.
- 기존 `military_callsign`·`military_flag`(단발)와 **독립·추가**. 이 시나리오는 다중기 서사 +
  기존 시나리오가 안 건드리던 **icao24 예약대역 탐지 경로**를 처음으로 시나리오/eval 레벨에서 커버.

### 정직 주석 (중요)
- 콜사인(PLAAF01·OLIVE21 등)은 **사람이 읽는 데모 라벨**이지 탐지 근거가 아니다. PLAAF01 항적의
  탐지 근거는 "관측 소스 is_military 플래그"(합성 명시)로 뜬다 — "PLAAF가 실제 ADS-B 프리픽스"라는
  주장이 아니다. icao24는 국가 대역만 현실적으로 부여(중국 0x78·한국 0x71·미 군용예약 0xAE).
- 러시아 접근기는 A와 동일한 명시-플래그 경로로 동형 확장 가능(신호 중복 회피 위해 3신호 각 1대만 둠).
- military_db는 **무변경**(US 군용예약 대역·기존 프리픽스만) — 외국 프리픽스를 db에 추가하면
  상용 콜사인 오탐 위험 + 라이브 탐지 동작 변경이라 "추가만" 제약 위반. 외국 군용은 명시 플래그로 선언.

## 2. 명시 주입 경로 (`scripts/demo.sh`)

`live --inject`에 **선택 시나리오 인자**를 추가(기존 기본 `narrative_hidden` 불변):

```
scripts/demo.sh live                       # 순수 라이브(실데이터만, 불변)
scripts/demo.sh live --inject              # 라이브 + narrative_hidden(기존 기본, 불변)
scripts/demo.sh live --inject military              # 라이브 + 서해 군용 접근 3대(합성)
scripts/demo.sh live --inject military_incursion    # 동일(정식 시나리오 id)
```

- `military`는 `military_incursion` 별칭. 라이브 실항적과 공존하되 합성 배지로 구분.
- 라이브 db 주입이므로 레짐 가드에 `--allow-live-db`를 명시 승인(narrative_hidden과 동일 경로,
  db-regime.md §3). 순수 `live`는 `inject="no"`로 게이팅돼 **합성 주입 자체가 없음**(실데이터 불변).
- replay(`demo.sh replay` = `--scenario all`)에도 자동 포함 → 오프라인 데모 보드에 군용 인시던트 노출.

## 3. 정직 구분 (합성 배지 — 기존 경로 그대로)

- `detect_military_approach`가 `signal.is_synthetic = (o.source == "synthetic")` 설정 →
  `create_from_draft`가 `anomaly.attrs.is_synthetic`로 영속 → `assessment._anomaly_sentence_text`가
  `[합성] ` 접두 + "군용 추정 항공기 …(근거: …, **저신뢰 휴리스틱**)"로 서술. web `b-synth` 배지도 동일 경로.
- **web/index.html 무수정**(다른 에이전트 병렬). 배지 배선은 기존 is_synthetic 필드 재사용이라 프론트 변경 불필요.

## 4. 검증

- **군용 시나리오 단독**: `military_incursion` 주입 → `military_approach` **3건만**(다른 유형 0),
  전부 OpArea 내·`is_synthetic=True`·conf {0.55, 0.55, 0.50}, 탐지 3신호(플래그·프리픽스·대역) 각 1대.
- **all 주입 총계**: 10 → **13건**(군용 접근 2→5). 유형별 {스쿽1·dropout3·로이터링1·군용5·위성2·급기동1}.
- **replay 결정성**: 같은 앵커로 all 2회 빌드 → anomaly 지문 IDENTICAL·상관 링크 수 동일.
- **실데이터 불변**: 라이브 db(`data/skai.db`) 대상 군용 주입은 `--allow-live-db` 없이 **거부(exit 2)**.
  주입 시도 후 skai.db의 `military_approach`=0·synthetic 관측=0(실데이터 1895관측·93기체 온존).
- **순수 live 불오염**: `live`(--inject 없음)는 inject 게이팅으로 합성 주입 경로 미진입.
- **테스트**: 전 스위트 **326 passed, 4 skipped**(기존 325 + 신규 `test_scan_all_military_incursion_end_to_end`).
  - `tests/test_p6.py`: all 주입 count 어서션 10→13 갱신(2곳). 결정성 테스트 불변(같은 앵커=동일).
  - `tests/test_p5.py`: 신규 시나리오 단위 테스트(3건·유형 격리·저신뢰·OpArea·합성·3신호).
- `bash -n scripts/demo.sh` OK.

## 5. 되돌리기

- 시나리오 제거: `scripts/scenarios.py`의 `military_incursion` dict 삭제 → all count 자동 13→10.
- 테스트 원복: `tests/test_p6.py` 13→10(2곳)·`tests/test_p5.py`의 신규 테스트 제거.
- demo.sh 원복: `live()`의 `scenario` 인자·별칭·`case`의 `"${3:-}"`·헤더/usage 문구 역편집
  (기존 `--scenario narrative_hidden` 하드코딩으로 복귀).
- military_db·web·실데이터 db는 무변경 → 되돌릴 것 없음.

## 6. 미해결·범위 밖 (정직)

- `demo.md` 발표 스크립트의 replay 이상징후 수(현 "9건", line 175~176)는 **이미 stale**(rapid_climb
  추가로 실제 10, 이번 변경으로 13). demo.md는 Fable 소유 발표 문서라 이번 범위 밖 — Fable가 최종
  발표 스크립트 확정 시 {군용 접근 RCH451/FALCON9/PLAAF01/ROKAF31/OLIVE21, 총 13건}으로 갱신 필요.
- `live --inject military`의 **전체 라이브 폴러 실행**(네트워크+무한루프)은 미실행 — 주입 명령 자체는
  임시 db로 검증했고, 라이브 db 경로는 narrative_hidden과 동일 배선(가드·--allow-live-db 포함).
