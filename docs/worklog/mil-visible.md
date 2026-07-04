# mil-visible.md — 군용기 지도 가시화 배선 (실행 로그)

- 날짜: 2026-07-05
- 담당: sonnet 실행 에이전트 (팀리드 지시 — 군용기 지도 미구분 갭 closure)
- 근거: `mil-enrich.md`(DR-0013 결정 5)가 이미 만든 mil_enrich(adsb.fi dbFlags)·military_db(콜사인·대역 휴리스틱) 신호가 **이상탐지(detect_military_approach)에만** 배선돼 있고 `Aircraft.is_military`는 라이브 경로에서 항상 False(`ontology/mapping.py`)였던 갭
- 판정: **WIRED** (라이브 판정 영속 + API 노출 + 지도 시각 구분 완료)

---

## 0. 결론 (정직 문구)

- 라이브 폴러(`connectors/opensky.py`·`connectors/adsbfi_tracks.py`)의 `ingest_cycle`이 매 사이클
  `anomaly/military_db.resolve_is_military()`로 mil_enrich DB 플래그(라이브 게이트 on 시) 또는
  콜사인·대역 휴리스틱 히트를 종합해 **Aircraft.is_military를 write에 실어 영속**시킨다. 한 번
  True가 되면 이후 사이클에서 신호가 사라져도 False로 되돌아가지 않는다(단조 판정).
- `/api/observations`·`/api/tracks`가 `aircraft_map`에서 `is_military`를 그대로 노출한다.
- 지도(`web/index.html`)는 기존 항공기 표시 토글(`tog-ac`, 커밋 8459e16) 구조를 건드리지 않고,
  마커 색(올리브 `#9aa63c`, 민간 amber·비상 red와 구분)과 툴팁 문구("🪖 군용 추정(저신뢰)")로
  군용 추정 항적을 시각 구분한다. 범례에 별도 행을 추가했다. 비상 스쿽(emerg)과 겹치면 emerg
  색이 우선한다(안전신호가 더 급함) — CSS 선언 순서로 구현.
- **단정 아님이 문구에 명시된다** — "군용 추정(저신뢰)"이지 "군용"이 아니다. Aircraft.is_military
  자체가 애초에 저신뢰 휴리스틱·커뮤니티 DB 기반이므로(military_db.py 상단 주석), 지도 표기도
  그 신뢰 수준을 과장하지 않는다.

---

## 1. 판정 영속 — `anomaly/military_db.py::resolve_is_military`

```python
def resolve_is_military(existing_is_military, icao24, callsign, mil_enrich=None) -> bool:
    # 우선순위: 기존 True(합성·이전 판정) 불변 > mil_enrich DB 플래그 > 콜사인·대역 휴리스틱.
```

- `write_aircraft`가 `INSERT OR REPLACE`라서(`ontology/store_local.py:235`), 이 함수가 계산한 값을
  매 write에 싣지 않으면 판정이 그대로 소실된다. 그래서 호출측(ingest_cycle)이 사이클 시작 시점에
  `store.aircraft_map()` 스냅샷을 1회 떠서(사이클당 추가 쿼리 1회 — KADIZ 규모 aircraft 테이블
  전체 조회라 비용은 작지만 없던 쿼리가 새로 생긴 것은 사실) 기존 판정을 읽고, 새 신호와 OR
  결합해 다시 write한다.
- **단조(monotonic)**: 기존 True는 이번 사이클 신호 유무와 무관하게 유지 — 합성 주입(`is_military=True`
  명시)도 이 규율로 불변(팀리드 지시 그대로).
- 근거 문구(mil_reason)는 이 함수가 다루지 않는다 — 이상탐지 경로(`detect_military_approach`)가
  `signal.mil_reason`으로 이미 서술하므로 boolean만 영속시킨다(팀리드 지시).

배선 지점: `connectors/opensky.py::ingest_cycle`(OpenSky 브랜치) + `connectors/adsbfi_tracks.py::ingest_cycle`
(adsbfi 브랜치) — 둘 다 사이클 시작 시 `ac_map_before = store.aircraft_map()`을 뜨고, 각 관측 write
직전에 `aircraft.is_military = resolve_is_military(...)`로 덮어쓴다.

---

## 2. API 노출

- `/api/observations`(`server/app.py`): `ac_map`에서 `is_military` 필드 추가(ac 없으면 False).
- `/api/tracks`: 동일 규율로 `is_military` 필드 추가(트랙 폴리라인 툴팁용).

---

## 3. 지도 시각 구분 (`web/index.html`)

- `acIcon(o, emerg)` — `is_military`면 클래스에 `mil` 추가 → CSS `.ac-ico.mil .ac-tri/.ac-dot`가
  올리브색(`#9aa63c`)으로 렌더. `.ac-ico.emerg` 규칙이 스타일시트에서 더 뒤에 선언돼 있어, 비상+군용이
  겹치면 emerg(적색·점멸)가 이긴다(의도적 — 안전신호 우선).
- `acTip(o)` — 군용이면 "🪖 군용 추정(저신뢰)" 문구를 콜사인 옆에 추가.
- `drawAircraft`의 `iconKey`에 `is_military` 비트를 포함시켜, 사이클 중 판정이 새로 True가 될 때
  (heading/emerg 불변이어도) 아이콘이 재교체되도록 했다(안 하면 마커가 갱신 안 됨).
- `drawTracks` 폴리라인 툴팁에도 동일 문구 추가.
- 범례(`#legend`)에 "🪖 군용 추정 항적(저신뢰)" 행 + 스와치 색 추가.
- **기존 민간/전체 토글(`tog-ac`, 8459e16)은 건드리지 않음** — 그 토글은 "항공기 레이어 전체
  표시/숨김"이지 "군용만 필터"가 아니어서(재확인함), 이번 작업은 그 구조를 그대로 두고 색상
  구분만 추가했다. 별도의 "군용만 보기" 필터는 이번 스코프 밖(팀리드 지시엔 조건부 — "민간 토글이
  군용만 남기기 로직이면"이었는데 실제로는 아니었음).

---

## 4. 테스트

신규 `tests/test_mil_visible.py` (12건):
- `resolve_is_military` 4건 — 기존 True 불변·DB 플래그 히트·휴리스틱 히트·무히트 False.
- `connectors/opensky.py::ingest_cycle` 4건 — 휴리스틱 영속·DB 플래그 영속·기존 True 보존(단조)·
  민간기 False 유지(회귀 방어).
- `connectors/adsbfi_tracks.py::ingest_cycle` 2건 — 휴리스틱 영속·기존 True 보존.
- `server/app.py` API 노출 2건 — `/api/observations`·`/api/tracks`.

회귀: `.venv/bin/python -m pytest -q` → **403 passed, 4 skipped**(기존 391 passed + 신규 12). 회귀 0.

JS 문법: `node --check`로 `<script>` 블록 추출 검증 통과.

**실검**: 임시 DB(`scripts.inject_synthetic.inject_scenario(..., "all")`) + 임시 포트(8099)로 서버
기동 → `/api/observations`·`/api/tracks`에서 합성 군용기 2대(`m2falcon`/FALCON9, `780a1c`/PLAAF01)가
`is_military: true`로 노출됨을 확인, 루트 페이지 HTTP 200 확인 후 해당 임시 프로세스만 종료
(라이브 `:8000` 서버는 건드리지 않음).

---

## 5. 한계·후속 (정직)

- **회고 소급 없음** — 이 배선은 라이브 폴러의 `ingest_cycle`에서만 동작한다. 이미 DB에 쌓인 과거
  Aircraft 레코드는 poller가 그 icao24를 다시 관측해야 재판정된다(다음 폴 사이클에 자연 반영,
  별도 백필 스크립트는 만들지 않음 — 스코프 밖).
- **합성 시나리오 경로는 그대로** — `scripts/scenarios.py`/`scripts/inject_synthetic.py`는 여전히
  `is_military`를 명시값으로 직접 쓴다(예: `military_incursion` 시나리오의 "우방 요격기" 항적
  "ROKAF31"은 콜사인 휴리스틱만으로 탐지되도록 의도적으로 `is_military=False`로 주입돼 있음 —
  이번 작업이 만든 회귀 아님). 이 배선은 라이브 관측에만 적용되고 합성 주입 로직은 건드리지
  않았다(팀리드 지시: 합성 불변).
- **mil_enrich DB 플래그는 기본 off** — `SKAI_MIL_ENRICH=live` 게이트를 켜야 DB 플래그 신호가
  들어온다(mil-enrich.md와 동일 게이트). 기본 상태에서는 콜사인·대역 휴리스틱만으로 영속된다.
- **copilot 질의 경로 부수 효과(테스트는 안 함)** — `copilot/tools.py::_effective_military`는
  `ac.is_military`도 참조하므로, 이번에 라이브에서 True가 영속되면 "군용기만 보여줘" 같은 코파일럿
  질의도 간접적으로 더 정확해진다. 다만 이 부수 효과에 대한 전용 테스트는 추가하지 않았다(스코프
  밖 — 기존 copilot 테스트가 회귀 없음만 확인).
- **지도에 "군용만 보기" 필터는 없음** — 색상 구분만 추가했다. 필터 토글은 요청받지 않았고, 필요
  시 기존 `tog-ac` 패턴을 복제해 추가 가능(다음 작업 후보).
