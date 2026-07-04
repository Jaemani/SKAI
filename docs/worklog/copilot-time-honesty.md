# worklog: 코파일럿 시간 정직성 + 데모 LLM 게이트 기본값

> 작성일 2026-07-05. 팀 진단(2026-07-05)에 따른 4건 수정 — "지금" 질의에 오래된 뉴스·창 밖
> 위성 통과가 섞이는 문제, 기상 stale 표기 재확인, 라이브 데모 기본 서술 백엔드.

---

## 1. 문제 진단 (팀 원 지시 요약)

- `copilot/tools.py` `news()` — 시간 필터 없음. "지금" 질의에도 수개월 전 회고 기사가 최신
  5건 안에 섞여 나갈 수 있었다.
- `copilot/assessment.py` `_parallel_read` — 위성 통과를 상관용으로 ±`CORRELATION_WINDOW_SECONDS`
  (1h) 확장해 읽는데, 이 확장 결과가 **서술 문장에도 그대로** 들어가 "지금"(기본 30분 창)
  질의에도 최대 2.5시간치 통과가 나열될 수 있었다.
- `copilot/tools.py` `weather()` / `assessment.py` 기상 문장 — stale 여부만 각주(불리언)로
  달리는 줄 알았으나, **실제로는 `_weather_sentences`가 이미 경과분("관측 N분 전, 질의창
  밖")을 문장에 명시하고 있었다**(아래 3번 참고 — 조사 결과 기존 코드가 이미 충족).
- `copilot/assessment.py` LLM 게이트 기본값(`SKAI_COPILOT_LLM`/`SKAI_EXPLAINER`) = `template` —
  라이브 데모에서도 문장 다듬기(`_polish_narration`)가 안 돌아 답변이 기계적 나열로 보였다.

---

## 2. 변경 내역

### 2.1 뉴스 최대 나이 필터 (`copilot/tools.py`)
- `NEWS_MAX_AGE_SECONDS = 48 * 3600`(상수, `NEWS_MAX_AGE_HOURS = 48`) 추가.
- `news(store, region_id, window, limit=5, max_age_seconds=NEWS_MAX_AGE_SECONDS)` —
  질의창 **종료 시각(window[1])** 기준 `age = end - n.ts`가 `max_age_seconds`를 초과하면
  제외. 경계는 `age > max_age_seconds`만 제외(정확히 48h는 포함, inclusive).
- `Fact.data`에 `age_seconds` 필드 추가(투명성 — API 응답에서도 나이 확인 가능).
- 시간창 자체로 자르지 않는다는 기존 설계(회고 보도 수용)는 **유지** — 나이 상한만 별도로 얹었다.

### 2.2 뉴스 문장에 경과시간 명시 (`copilot/assessment.py`)
- `_fmt_news_age(seconds)` 헬퍼 추가 — 1시간 미만은 "1시간 미만 전", 그 외 "약 N시간 전".
- `_news_sentence(pq, region_name, reads)` — 시그니처에 `pq` 추가, 대표 기사(최신 1건)
  경과시간을 문장에 삽입: `"...예: '제목'(약 N시간 전 보도). 확증 아님..."`.
- `_entity_sentences`의 news 분기(단건 엔티티 설명)에도 동일하게 경과시간 삽입.
- 호출부 2곳(`_assemble_sentences`, `_assemble_for_intent`의 `INTENT_NEWS` 분기) 시그니처 갱신.
- 기존 "저신뢰·확증 아님" 문구는 그대로 유지(불변식 유지).

### 2.3 위성 서술 창을 질의창으로 제한 (`copilot/assessment.py`)
- `_assemble_correlations_and_satellite(store, reads, pq)` — `pq` 파라미터 추가.
- **상관(KIND_CORRELATION) 문장은 그대로** — `correlation.py`가 이미 영속한
  `correlated_with` 링크를 읽을 뿐이라 로직 변경 없음(SSOT는 여전히 `anomaly/correlation.py`,
  이 파일은 다른 에이전트가 작업 중이라 손대지 않음).
- **비상관 맥락(KIND_SATELLITE) "extras" 문장만** 실제 질의창(`pq.window_start`~
  `pq.window_end`)과 겹치는 통과로 한정하는 필터 추가. `_parallel_read`의 확장 읽기
  (`reads.passes`, ±1h)는 상관 계산용으로 그대로 유지 — `counts.passes`에는 여전히 확장
  읽기 전체가 잡힌다(의도적, 상관 후보를 놓치지 않기 위함). 서술 문장만 좁혔다.
- 호출부 2곳(`_assemble_sentences`, `_assemble_for_intent`의 `INTENT_CORRELATION` 분기)
  시그니처 갱신.

### 2.4 기상 stale 표기 — 조사 결과 이미 충족(변경 없음)
- `_weather_sentences`(assessment.py)를 확인한 결과, stale 관측이면 이미
  `"(관측 N분 전/후, 질의창 밖)"` 형태로 경과시간을 문장에 명시하고 있었다. `tools.py`
  `weather()`의 `stale` 불리언은 이 문장 조립의 입력일 뿐, 팀 진단의 "각주만 있음"은
  `tools.py` 레이어만 보고 내린 판단으로 보인다. **추가 수정 없음** — 회귀 방지용 테스트만
  없어 별도 테스트는 추가하지 않았다(범위 밖 재확인, 필요 시 후속 요청).

### 2.5 데모 LLM 게이트 기본값 (`scripts/demo.sh`)
- `live()` 함수에 `export SKAI_COPILOT_LLM="${SKAI_COPILOT_LLM:-claude}"` 추가 —
  미설정 시에만 `claude`로 켜서 라이브 데모 문장이 `_polish_narration`을 거치게 한다
  (cites·사실은 불변, 실패 시 원문 폴백 — DR-0004 그대로 적용됨).
  이미 값이 있으면 사용자 지정을 그대로 존중(덮어쓰지 않음).
- `replay()`는 **전혀 건드리지 않음** — `SKAI_COPILOT_LLM`을 export하는 코드가 `live()`
  안에만 있어 replay 서버 프로세스는 이 값을 상속하지 않고, `assessment.py`의 기본값
  체인(`explainer or SKAI_COPILOT_LLM or SKAI_EXPLAINER or "template"`)에 따라 여전히
  `template`로 결정적으로 동작한다. 파일 상단 환경변수 표와 `live()` 내부에 이유를 주석으로 기록.

---

## 3. 테스트

- 신규(`tests/test_p4.py`, 섹션 "6. 시간 정직성"):
  - `test_tools_news_excludes_stale_articles` — 48h 초과 기사 제외.
  - `test_tools_news_age_boundary_inclusive` — 경계값(정확히 48h 포함, +1초 제외).
  - `test_assess_news_sentence_excludes_stale_and_states_age` — assess() 응답 수준에서
    오래된 기사 배제 + 문장에 "전 보도" 경과시간 텍스트 확인 + cites에서도 제외 확인.
  - `test_satellite_context_sentence_limited_to_query_window` — `counts.passes`는 확장
    읽기 전체(2건)를 유지하되, KIND_SATELLITE 서술 문장의 cites는 질의창과 겹치는 통과
    1건만 포함함을 확인.
- 기존 `test_correlation_cites_anomaly_and_pass`(상관 문장이 질의창 밖 통과도 인용) —
  로직 변경 없음을 재확인, 그대로 통과.
- 전체 스위트: **330 passed, 4 skipped**(변경 전 326 passed, 4 skipped에서 신규 4건 추가,
  회귀 0). `.venv/bin/python -m pytest -q`.
- `scripts/demo.sh` — `bash -n`으로 문법 검증만 수행(실제 라이브 기동은 API 크레딧·프로세스
  기동을 수반해 이번 세션에서 실행하지 않음 — 아래 한계 참고).

---

## 4. 변경 파일
- `copilot/tools.py` — `NEWS_MAX_AGE_HOURS`/`NEWS_MAX_AGE_SECONDS` 추가, `news()` 나이 필터.
- `copilot/assessment.py` — `_fmt_news_age` 신설, `_news_sentence`/`_entity_sentences`(news
  분기) 경과시간 삽입, `_assemble_correlations_and_satellite` 질의창 필터 + 시그니처 변경,
  호출부 4곳 갱신.
- `scripts/demo.sh` — `live()`에 `SKAI_COPILOT_LLM` 기본값 게이트 추가 + 헤더 환경변수 주석.
- `tests/test_p4.py` — 신규 테스트 4건 + import 3건(`copilot.tools`, `NEWS_MAX_AGE_SECONDS`).

## 5. 한계 · 미검증
- **`scripts/demo.sh live` 실기동 미검증**: `SKAI_COPILOT_LLM=claude` 게이트가 실제로
  `claude -p` 서브프로세스를 불러 문장을 다듬는지는 코드 경로(`_polish_narration`)상으로만
  확인했고, 이번 세션에서 라이브 API 폴링·`claude` CLI 호출을 동반한 실기동 검증은 하지
  않았다(크레딧·네트워크 부담, 범위상 단위 테스트로 대체).
- **뉴스 48h 상한은 팀 지시값 그대로 채택** — 도메인상 "회고 보도 몇 시간까지가 유효한가"에
  대한 별도 근거 조사는 하지 않았다(지시된 기본값을 상수로 노출해 필요 시 조정 용이하게만 함).
- **기상 stale 표기(2.4)는 조사만 하고 코드는 그대로 둠** — 이미 요구사항을 충족하는 것으로
  판단했으나, 혹시 팀 원이 다른 표기(예: "질의창 밖" 위치·구두점)를 기대했다면 재조정 필요.
- **anomaly/ 디렉터리 미접근** — 다른 에이전트가 작업 중이라는 지시에 따라
  `anomaly/correlation.py`는 상수 참조(`CORRELATION_WINDOW_SECONDS`)만 하고 수정하지 않았다.
- **Foundry 경로 미접근** — `store_foundry.py`, `region_summary.py`의 AIP 호출부는 건드리지
  않았다(지시된 제약 그대로).
