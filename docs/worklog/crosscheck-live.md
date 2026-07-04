# crosscheck-live.md — 교차소스 dropout 라이브 배선 (실행 로그)

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (초기계획 잔여 A2)
- 근거: CLAUDE.md 기술기준(**교차검증**: dropout 단정은 복수 소스로) · **DR-0007** 결정 1(CrossCheckSource 인터페이스, 라이브 2차 소스는 ToS 허용 시만) · data-sources.md §1 · docs/EVALUATION.md §7("교차소스 dropout 확정 — 라이브 2차 피드 미배선" 갭)
- 판정: **LIVE-WIRED** (adsb.fi, 게이트 off 기본 유지)

---

## 0. 결론 (발표용 정직 문구)

- **설계·인터페이스는 이미 있었고**(`anomaly/crosscheck.py`의 `CrossCheckSource`), 이번에 **ToS가 명확히 허용하는 무료 공개 2차 소스(adsb.fi)를 라이브로 실제 배선**했다.
- 라이브 실 API 왕복까지 검증 완료 — 실제 비행 중 기체는 "관측 중(dropout 아님)", 존재하지 않는 hex는 "부재 교차확인"으로 정확히 판정된다.
- **기본값은 여전히 Null(미확인·저신뢰)** — `SKAI_CROSSCHECK=live` 게이트를 켤 때만 2차 소스를 호출한다(크레딧·안정성·데모 재현성). 즉 "라이브 교차확인 **능력**을 갖췄고, 기본은 보수적(저신뢰)"이 정직한 표현.
- **한계(정직)**: 무료 2차 API는 **현재 스냅샷**만 제공(임의 과거 window 조회 불가). 따라서 교차확인은 "호출 시점에 2차 소스가 이 기체를 보는가"이며, "window 전체 부재"를 단정하지 않는다. dropout 후보는 최근(gap window 내) 결측일 때만 발화하므로 now 스냅샷 교차는 타당한 근사다.

---

## 1. ToS 판정표 (2026-07-04 실검, 출처 명시)

| 소스 | 상업/해커톤 | 인증 | 리밋 | 단일 hex 엔드포인트 | 판정 | 근거 |
|---|---|---|---|---|---|---|
| **adsb.fi** | 개인·비상업/교육용 **허용**(라이선스·판매·임대 금지, 인용 필수) | 불요 | **1 req/s** | `GET /v2/hex/<hex>` ✅ | **채택(기본)** | README verbatim 확인 |
| **airplanes.live** | 비상업/교육용(검색요약) | 불요 | 1 req/s | `GET /v2/hex/<hex>` ✅ | 옵션(기본 아님) | ToS 페이지 403(봇차단) → verbatim 미확인 |
| **ADS-B Exchange** | **무료 폐지**(2025-03), RapidAPI 유료($10/mo~) | 유료 키 | — | 유료 | **탈락** | 가드레일: 무료 공개만 |

**해커톤=비상업 판단**: 프로젝트는 판매·라이선스·임대 대상이 아닌 경진/교육 데모 → adsb.fi가 명시 금지한 "license/sell/rent/lease"에 해당하지 않음 → 비상업/교육 사용으로 ToS 명확 허용. **인용 요건**(adsb.fi + 홈페이지 링크)은 UI/문서에 명시할 것.

**출처 URL**
- adsb.fi opendata README (엔드포인트·ToS·리밋 verbatim): https://github.com/adsbfi/opendata/blob/main/README.md
- adsb.fi opendata 리포: https://github.com/adsbfi/opendata
- airplanes.live API 가이드(403): https://airplanes.live/api-guide/ · 문서: https://airplanes.live/api-docs/
- ADS-B Exchange 유료 전환: https://www.adsbexchange.com/api-lite/ · RapidAPI: https://rapidapi.com/adsbx/api/adsbexchange-com1

---

## 2. 무엇을 배선했나

### 2.1 신규 — `connectors/crosscheck_live.py`
- `LiveCrossCheckSource(CrossCheckSource)` — adsb.fi `/v2/hex/<hex>` 조회로 `confirm_absence` 구현.
  - `ac` 배열에 hex가 **신선하게**(seen ≤ 60s) 있음 → **False**(관측 중 → dropout 아님).
  - `ac` 비어있음 → **True**(2차 소스도 미관측 → 부재 교차확인 → 신뢰도 상향 근거).
  - hex 있으나 stale, 또는 HTTP 오류/429/타임아웃/이상응답 → **None**(미확인 → 저신뢰 유지, **단정 금지**).
- **리밋 규율**: 실 호출 사이 `min_interval`(1.05s = 1 req/s 여유) 강제 + **TTL 캐시**(30s, 같은 hex 재질의 억제) + dropout 후보당 1회 질의 + 앱 식별 User-Agent.
- `make_crosscheck(env)` — **게이트 팩토리**: `SKAI_CROSSCHECK=live`일 때만 `LiveCrossCheckSource`, 그 외 전부 `NullCrossCheckSource`(기본). `SKAI_CROSSCHECK_SOURCE=adsbfi|airplaneslive`로 소스 선택(기본 adsbfi).

### 2.2 라이브 폴러 배선 — `connectors/opensky.py`
- `run_poller`가 `crosscheck_live.make_crosscheck()`로 **1개 인스턴스를 생성해 사이클 간 재사용**(캐시·레이트리밋 상태 보존) → `ingest_cycle(..., crosscheck=crosscheck)`로 전달.
- **⚠️ 부수 변경(반드시 인지)**: `ingest_cycle`의 이상탐지를 `scan_and_create`(비상 스쿽 **1종만**) → `scan_and_create_all`(P5 **전 유형**: 비상 스쿽+dropout+로이터링+군용기+위성 근접 + 상관 영속)로 교체했다. **이전엔 라이브 폴러가 dropout 룰을 아예 돌리지 않았다**(scan_and_create는 스쿽 전용). crosscheck를 "라이브 폴러의 dropout 룰에 배선"하려면 폴러가 dropout을 실제로 돌려야 하므로 불가피한 전환. 효과: 라이브에서도 P5 전 유형이 탐지되고, dropout은 crosscheck로 교차 판정된다. confidence 기본 동작은 불변(Null=저신뢰).

### 2.3 미변경 (경계 준수)
- `anomaly/explainer.py`·`ontology/store_foundry.py` **미수정**(병렬 작업).
- `anomaly/crosscheck.py`·`anomaly/rules.py`·`anomaly/actions.py`의 인터페이스/룰 **미수정** — 라이브 소스는 기존 `CrossCheckSource` 계약에 그대로 꽂힘(SyntheticMirror와 동일).
- **eval/run_eval.py 미변경** — 평가는 결정적 재현성 위해 합성 미러 유지가 옳음(라이브 교차는 폴러 전용).

---

## 3. 검증

### 3.1 단위 테스트 — `tests/test_crosscheck_live.py` (신규 16건)
- 게이트 팩토리(기본 Null / live만 라이브 / 대소문자 / 소스선택 / 기타값 Null).
- `confirm_absence` 의미론(관측중 False · 부재 True · stale None · 429 None · 네트워크예외 None · 빈 hex 무호출 · 대소문자 hex 매칭) — **httpx MockTransport로 네트워크 없이**.
- 리밋 규율(TTL 캐시로 실 HTTP 1회만, 엔드포인트 URL 정확성).
- **dropout 룰 통합**(라이브 소스가 SyntheticMirror와 동일 계약): 부재→상향 0.72 · 관측→생성 안 함 · 오류→저신뢰 0.42 유지.

### 3.2 라이브 실 API 왕복 (수동, 3 호출 — 리밋 존중)
```
[probe] /v3/lat/37.5/lon/127.0/dist/100 status 200, aircraft in view: 55
[real ] confirm_absence(780d59) = False   # 실제 비행 중 → 관측 중 → dropout 아님 ✅
[fake ] confirm_absence(ffffff) = True    # 존재불가 hex → 부재 교차확인 ✅
[stats] 실 HTTP 호출=2  캐시적중=0
```
→ 실 adsb.fi에서 계약대로 동작. 레이트리밋(1 req/s) 준수, 스크래핑·우회 없음.

### 3.3 전체 스위트
- **268 passed**(기존 252 + 신규 16). 회귀 0. 기존 폴러 테스트(`test_copilot_intent.py`) 불변 통과 — `_SAMPLE_STATE`(상용기 KAL77·정상 스쿽·OpArea 밖)는 `scan_and_create_all`에서도 anomaly 0.

---

## 4. 운용 방법 & 되돌리기

- **켜기**: `SKAI_CROSSCHECK=live`(옵션 `SKAI_CROSSCHECK_SOURCE=adsbfi`) 환경변수로 폴러 기동. 미설정 시 기본 Null.
- **인용 준수**: adsb.fi 사용 시 UI/크레딧에 "Cross-check data: adsb.fi (https://adsb.fi)" 표기 필요(ToS 요건). — *후속 UI 반영 항목.*
- **되돌리기**: `git` 역편집. 핵심은 opensky.py의 `scan_and_create_all`→`scan_and_create` 원복 + crosscheck 파라미터 제거 + `connectors/crosscheck_live.py`·`tests/test_crosscheck_live.py` 삭제. 게이트만 끄려면 `SKAI_CROSSCHECK` 미설정(코드 변경 불요).

---

## 5. 남은 것 / 후속(핸드오프 후보)

1. **인용 UI 반영** — adsb.fi 크레딧 표기(ToS 요건). 라이브 상태 사이드카/프론트에 교차소스 표시.
2. **airplanes.live ToS verbatim 확인** — 봇차단(403) 우회 없이 공식 채널로 ToS 원문 확보 시 2차 소스 승격 가능(현재 옵션·기본 아님).
3. **폴러 P5 전유형 라이브 전환의 데모 영향 점검** — 이전 데모가 "라이브=스쿽만 + 합성주입=P5"를 전제했다면, 이제 라이브에서도 P5가 뜬다. 데모 시나리오/replay와의 정합은 Fable(메인)이 판단.
