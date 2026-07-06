# dropout-semantics.md — ADS-B dropout 의미 재정의 (오탐 폭주 긴급 수정)

- 날짜: 2026-07-06
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 근거: 팀리드 긴급 지시 — 라이브에서 dropout 오탐 폭주(408건 중 400건, 비행 중 민항기 전부 포함, 계속 증가). 운영 조건 adsb.fi 항적 · `SKAI_POLL_INTERVAL=60`(관측 간격 ~60-75s).
- 판정: **FIXED** (의미 재정의 + 폴 간격 인지 임계 + 침묵-이벤트 dedup). 전체 스위트 회귀 0.

---

## 0. 결론 (한 줄)

dropout을 "과거 언젠가 gap이 있었음(sticky)"이 아니라 **"지금 끊겨 있음"**(now − 마지막 관측 > 침묵 임계)으로 재정의했다. 정상 송신 중(매 사이클 새 관측) 기체는 이제 절대 후보가 아니며, 임계는 실제 폴 간격을 인지하고(60s 폴 → 180s), 같은 침묵은 1건으로 dedup된다.

---

## 1. 원인 (실측 확정)

두 겹의 결함이 곱해져 폭주했다.

1. **Track gap 임계가 폴 간격 미인지** — `ontology/custody.py`의 `has_gap`이 `GAP_THRESHOLD_SECONDS=90` 상수를 썼다. 60-75s 간격 폴에서 한 사이클만 밀리거나 한 기체가 한 사이클 빠지면 >90s gap이 생기고, `build_track`은 **전체 이력**을 보므로 이력 어딘가에 gap이 한 번이라도 있으면 `has_gap=True`가 **영구 고착(sticky)** 된다.
2. **`detect_adsb_dropout`의 의미 결함** — 후보 조건이 `track.has_gap`(과거 이력) AND 마지막 관측이 민감구역 내였다. 그래서 **지금 정상 송신 중인 기체**(매 사이클 새 관측)도 has_gap 고착 때문에 발화했다. 게다가 dedup 앵커가 `last.ts // 600`(10분 버킷)인데, 송신 중이면 `last.ts`가 매 사이클 전진 → 10분마다 새 버킷 → **같은 기체가 10분마다 새 Anomaly 재생성** = 무한 증가. KADIZ가 민감구역이라 사실상 모든 비행 중 기체가 이 경로를 탔다(→ 400건).

---

## 2. 수정

### 2.1 폴 간격 인지 임계 (`ontology/model.py`)
- `poll_interval_seconds()` — `SKAI_POLL_INTERVAL`(하위호환 `POLL_INTERVAL`) 환경변수를 읽는다. poller(`connectors.opensky.main`)가 읽는 것과 **같은 소스**(SSOT). 미상이면 None.
- `gap_threshold_seconds()` = `max(GAP_THRESHOLD_SECONDS, k×폴간격)`, k=`DROPOUT_POLL_MULTIPLIER`=3. 미상이면 base 90(테스트·단발 실행 불변). 60s 폴 → **180s**.
- 임계를 poller 인자로 스레딩하지 않고 **환경변수 읽기**를 택한 이유: `run_poller→ingest_cycle→scan_and_create_all→detect_adsb_dropout` 4개 시그니처를 건드리지 않고, poller가 쓰는 것과 동일한 SSOT를 재사용해 항상 일치. (팀리드가 "네 판단"으로 위임한 지점.)

### 2.2 custody `has_gap` (`ontology/custody.py`)
- `build_track`이 상수 대신 `gap_threshold_seconds()`를 쓴다. 경로 재구성용 gap 표식의 **의미는 유지**하되(트랙 시각화·경로), 폴 간격을 인지하도록만 바꿨다. dropout 발화는 더 이상 이 값에 의존하지 않는다(아래).

### 2.3 dropout 의미 재정의 (`anomaly/rules.py::detect_adsb_dropout`)
- 게이트를 `track.has_gap` → **`now − last.ts > gap_threshold_seconds()`**(지금 침묵 중)로 교체. **현재 관측이 신선하면(침묵 임계 미만) 과거 gap 이력과 무관하게 후보가 아니다.** ← 폭주의 직접 차단.
- **활성 창 상한** `DROPOUT_ACTIVE_WINDOW_SECONDS=30분`: 침묵이 이 안에서 시작한 것만 후보. 오래 전 침묵(콜드스타트·stale DB의 옛 기체)이 재수집 시 한꺼번에 발화하는 2차 폭주를 방어.
- **침묵-이벤트 dedup**: draft.ts = `last.ts`(침묵 시작), `dedup_window=1`로 앵커를 **정확 침묵 시작 시각**에 고정 → `anomaly_id = anomaly-adsb_dropout-{icao}-{last.ts}`. 같은 침묵은 (침묵 중 last.ts 고정이므로) 항상 같은 id = 1건. 기체가 돌아오면 새 관측으로 last.ts가 바뀌고, 다음 침묵은 다른 last.ts = **새 이벤트로 정당하게 발화**(과거 버킷 전진 재발화 제거).
- **착륙 억제(보너스)**: 마지막 관측 `on_ground=True`면 전면 제외(착륙·택싱), 저고도(`alt<1000m`)면 착륙 추정으로 신뢰도 상한을 0.3으로 하향(단정 금지 강화).
- 교차소스(crosscheck) 판정 로직은 불변(미확인 0.42 / 부재확인 0.72 / 여전히 관측 → 생성 안 함).

---

## 3. 검증 (수치)

- **전체 스위트**: `.venv/bin/python -m pytest -q` → **409 passed, 4 skipped**(기준 403 passed/4 skipped + 신규 6). 회귀 0.
- **신규 테스트**:
  - `test_dropout_negative_fresh_transmitting` — 과거 gap 있어도 신선 관측이면 비발화(핵심 폭주 교정).
  - `test_dropout_negative_stale_silence` — 활성 창(30분) 밖 침묵 비발화.
  - `test_dropout_return_then_resilence_new_event` — 같은 침묵 1건 / 복귀 후 재침묵 새 id.
  - `test_dropout_poll_interval_scales_threshold` — `SKAI_POLL_INTERVAL=60` → 150s 침묵 비발화, 200s 발화.
  - `test_dropout_landing_suppressed_on_ground` / `test_dropout_low_altitude_downgrades_confidence` — 착륙 억제·하향.
  - `test_track_gap_threshold_poll_interval_aware`(P1) — 120s 간격이 폴 60s면 gap 아님, 200s면 gap.
- **기존 테스트 교정 1건**: `test_dropout_negative_no_gap`(전제 "gap 없으면 비발화" = 구 의미)를 `test_dropout_negative_fresh_transmitting`(신 의미 "신선하면 비발화")로 교체. 나머지 dropout 단위·crosscheck-live 테스트(침묵 1000s)는 활성 창(1800s) 안이라 무수정 통과.
- **replay 자산**(임시 DB, :8000·data/skai.db 미접근): 양성 dropout 시나리오 **3/3 발화** — SHADOW1(dropout_confirmed, conf 0.72, 침묵 300s) · GHOST2(dropout_unconfirmed, 0.42, 250s) · SHADOW7(narrative_hidden, 0.72, 300s). 음성 RELAY3(dropout_present_mirror, 2차 소스 관측) 정상 억제. 합성 시나리오가 이미 trailing-silence(dt_end<0) 구조라 새 의미로도 그대로 발화.
- **폭주 회귀 재현 검증**(임시 DB): (A) 매 사이클 fresh 관측을 받는 송신 중 기체(과거 gap 보유)를 5사이클 스캔 → dropout **0**. (B) 침묵 기체를 10분 버킷 경계(1120//600 vs 1700//600)를 넘겨 4회 재스캔 → dropout **정확히 1건**(`anomaly-adsb_dropout-gone2-1120`, 재발화 없음).

---

## 4. 한계 · 미사용 · 후속

- **민감구역 통과 후 이탈 = 저신뢰 dropout 1건**: KADIZ가 민감구역이라, 실제로 구역을 정상 통과 후 bbox를 벗어나 침묵에 든 기체도 활성 창(30분) 동안 저신뢰(0.42) dropout 1건을 낸다. bbox 한계의 본질적 부분이며, crosscheck(2차 소스가 여전히 보면 억제)·착륙 억제·활성 창으로 완화한다. **사이클당 폭주는 아니다**(기체·침묵당 1건, dedup 고정).
- **착륙 억제는 부분 구현**: `on_ground`(전면 억제)·저고도(신뢰도 하향)만 구현. 팀리드가 언급한 "저고도 + **강하 중**"의 강하 추세 판정은 `detect_adsb_dropout`이 고도 시퀀스를 받지 않아 시그니처 변경 없이는 불가 → 후속(관측 시퀀스 주입 또는 Track에 alt 계열 추가 시).
- **k=3·활성 창 30분은 휴리스틱**: 라이브 관측 후 튜닝 여지. 상수로 분리해 둠(`DROPOUT_POLL_MULTIPLIER`, `DROPOUT_ACTIVE_WINDOW_SECONDS`).
- **라이브 DB 정리는 범위 밖**: 이미 쌓인 400건 오탐 Anomaly 정리는 오케스트레이터 몫(지시대로 `data/skai.db` 미접근). 이 수정은 **신규 발생을 멈추는** 것이지 기존 레코드를 지우지 않는다.
- **git commit은 하지 않음**(지시).
