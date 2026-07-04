# 뉴스 레이어 정직화 (2026-07-05)

근거: `docs/decisions/DR-0013-signal-honesty-round.md` #10~#13 (news-audit 조사 결과, 확정 진단).

## 변경 요약

| # | 변경 | 파일 |
|---|---|---|
| 1 | `/api/news` 48h 나이 상한 적용(copilot과 동일 `NEWS_MAX_AGE_SECONDS` 재사용, replay는 `SKAI_NOW_ANCHOR` 기준) | `server/app.py` |
| 2 | 뉴스 카드 시각 표기를 `MM-DD HH:MM`로(기존 `fmtClock`은 HH:MM만 — 다른 사용처는 불변, 뉴스 전용 `fmtNewsTime` 신설) | `web/index.html` |
| 3 | 엔티티 링킹: `Korean Air`가 `Korean Air Force`/`South Korean Air Force`에 매칭되던 정규식 오탐 수정(부정 전방탐색 `(?!\s+Force\b)`) | `ontology/entity_linking.py` |
| 4 | 뉴스 카드 관련성 배지: mentions 0건 기사는 흐리게(opacity) + "링크 없음" 배지 | `web/index.html` |

## 상세

### 1. `/api/news` 나이 상한 (DR-0013 #10)
- `copilot/tools.py`의 `NEWS_MAX_AGE_SECONDS`(48h)를 import해 재사용 — 값 재정의 없음(단일 진실 공급원).
- `server/app.py`의 `_now_anchor()`를 그대로 사용해 replay 모드(`SKAI_NOW_ANCHOR` 설정)는 벽시계가 아니라 앵커 시각 기준으로 나이를 계산. 미설정(라이브)이면 `time.time()`.
- 확인: `scripts/inject_synthetic --scenario all`로 만든 replay 데모 DB에는 뉴스가 `narrative_hidden` 시나리오 1건뿐이고 앵커 기준 30분 전(`dt=-1800`)이라 48h 상한에 전혀 걸리지 않는다 — synthetic 소스 우회 로직은 필요 없다고 판단, 추가하지 않음(불필요한 분기 회피). 실측: `api_news()` 호출 결과 1건 그대로 통과 확인.

### 2. 뉴스 시각 날짜 표기 (DR-0013 #10)
- 기존 `fmtClock`(HH:MM만)을 뉴스 외 다른 사용처(OrbitPass 통과창)는 그대로 두고, 뉴스 카드 전용 `fmtNewsTime(ts)`를 신설해 `MM-DD HH:MM`로 표기.
- `fmtNewsTime`은 `fmtClock`과 동일한 `Date` 로컬 시각 해석을 쓴다(getMonth/getDate/getHours/getMinutes) — 시각대 해석 자체는 건드리지 않고 날짜만 추가.

### 3. 엔티티 링킹 오탐 수정 (DR-0013 #11)
- 원인: `OPERATOR_ALIASES["op-kal"]`의 `"Korean Air"`를 `\bKorean Air\b`로 컴파일하면, `"Korean Air Force"`에서도 `"Air"` 뒤 공백이 단어경계를 만족해 매칭됨(재현 확인) → 공군 기사가 민항사(op-kal)로 오링크 + `link_newsevent`의 confidence가 Operator 매칭 시 +0.05 오상향.
- 수정: `_compile_operator_matchers()`에서 alias가 `"korean air"`(소문자 비교)일 때만 뒤에 `(?!\s+Force\b)` 부정 전방탐색을 추가. 다른 별칭(예: `"US Air Force"`, `"Chinese Air Force"`)은 별칭 자체가 이미 "Force"까지 포함하므로 영향 없음.
- 회귀 테스트(`tests/test_rss.py`):
  - `test_match_operators_korean_air_force_not_linked_to_airline` — `"South Korean Air Force jets entered the zone"` → `op-rokaf`만 걸리고 `op-kal`은 안 걸림.
  - `test_match_operators_korean_air_normal_match_preserved` — `"Korean Air flight KE123 diverted"` → `op-kal` 정상 매칭 유지(회귀 없음 확인).

### 4. 관련성 상태 배지 (DR-0013 #12)
- 서버 응답(`/api/news`)에는 `mentions`가 이미 내려가고 있었다(기존 코드 확인 — 추가 배선 불필요).
- 프론트: `mentions.length === 0`인 기사는 `.news-item.unlinked`(opacity 0.55 + border 회색) + `<span class="badge b-nolink">링크 없음</span>` 배지 표시. 기존 배지 스타일(`.badge` 베이스 클래스)을 재사용.

## 테스트
- 신규: `tests/test_rss.py`(엔티티 링킹 회귀 2건), `tests/test_p4.py`(`/api/news` 나이 필터·앵커 기준 2건).
- 전체 스위트: `.venv/bin/python -m pytest -q` → **346 passed, 4 skipped**(작업 시작 시점 330 passed 대비 회귀 0, 순증가는 병행 작업 중인 다른 에이전트의 위성 허용목록 테스트 포함).

## 한계 (정직하게 명시)
- 엔티티 링킹은 여전히 사전+정규식+실존대조 조합이지 문맥이해 NER이 아니다(`ontology/entity_linking.py` 모듈독스트링에 이미 명시된 기존 한계, 이번 수정으로도 안 바뀜). 이번 수정은 "Korean Air" 접두 오탐 1건만 좁게 고쳤을 뿐, 유사한 접두-포함 관계를 가진 별칭 쌍이 향후 추가되면 같은 클래스의 오탐이 재발할 수 있다(케이스별 부정 전방탐색이라 일반 해법 아님).
- RSS·StealthMole 폴링은 이번 라운드에서도 **기본 off, opt-in 유지**(DR-0013 #13 그대로 — `SKAI_POLL_SOURCES`에 명시해야 켜짐). 뉴스 나이 필터·날짜 표기·링크배지는 opensky,gdelt,metar,celestrak 기본 경로에서만 검증했고 RSS/StealthMole 활성 시의 별도 검증은 하지 않음.
- GDELT 쿼리 자체(수집 스코프·키워드)는 이번 스코프 밖 — 관련성 배지는 사후 표시일 뿐 수집 단계에서 무관 기사를 걸러내지 않는다.
