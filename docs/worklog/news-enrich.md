# worklog: 뉴스 파이프라인 보강 (A4 RSS + A5 엔티티 링킹)

> 작성일 2026-07-04. 초기계획 잔여 A4(보조 뉴스 소스)+A5(mentions 엔티티 링킹 심화).
> 목표: 뉴스 소스 다변화(키 불요 RSS) + gdelt 내부 키워드 링킹을 공용 모듈로 승격해
> EVALUATION.md §3 "엔티티 해소=얕음(키워드 매칭뿐)" 딱지를 "사전+패턴+실존대조"로 올린다.
> **정직 원칙: 진짜 NER이라 주장하지 않는다.**

---

## 1. A4 — 보조 뉴스 소스 (RSS)

### 선정 피드 (2026-07-04 라이브 + robots.txt 검증)

| 피드명 | URL | 도메인 적합 | 검증 |
|---|---|---|---|
| `aviationist` | https://theaviationist.com/feed/ | 군용 항공(콜사인·기종·공군 언급 잦음) | HTTP 200, rss 15 items, robots 허용 |
| `twz` | https://www.twz.com/feed | The War Zone(군사 항공/방위) | HTTP 200, rss 38 items, robots 허용 |
| `defensenews-air` | https://www.defensenews.com/arc/outboundfeeds/rss/category/air/?outputType=xml | Defense News 항공 카테고리 | HTTP 200, rss 25 items, robots 허용 |
| `yonhap` | https://en.yna.co.kr/RSS/news.xml | 연합뉴스 영문(한반도·지역 링킹 신호) | HTTP 200, rss 100 items, robots 허용 |

- 전부 표준 RSS 2.0(`channel/item`), `item`에 `title`·`link`·`pubDate`·`description`. `application/rss+xml` 응답.
- **탈락 후보**: `thedefensepost`(RSS 아닌 HTML/DOCTYPE 반환), `en.yna.co.kr/RSS/northkorea.xml`(404). USNI·Naval News·Breaking Defense는 정상이나 4종으로 충분해 미채택.
- robots.txt 5종 전부 피드 URL `can_fetch=True`(aviationist는 `/wp-feed`만 Disallow, `/feed`는 허용). 공개 구독 피드의 정상 사용, 폴 간격 15분+.

### 구현 (`connectors/rss.py`)
- 파싱: **stdlib `xml.etree.ElementTree`만** 사용(새 패키지 미추가 — feedparser/lxml/defusedxml 부재 확인).
  `pubDate`(RFC 822)는 stdlib `email.utils.parsedate_to_datetime`로 파싱. `description`은 HTML 태그 제거 후 요약.
- 매핑: `NewsEvent(source="rss:<피드명>", source_url=실 기사 URL, confidence=0.30 base, id=news-<url 해시>)`.
  URL 해시 id는 **gdelt와 동일 규약** → 소스 간 같은 기사 URL은 자연 dedup.
- 폴러 등록: `opensky.SOURCE_INTERVALS['rss']=900s(15분)`, `_ingest_source`에 rss 분기 추가.
  `resolve_sources`가 자동 인식(`SKAI_POLL_SOURCES=opensky,rss`로 활성).

### 판단 기록
- **NewsAPI**: 스킵. API 키 필요 → 기본 no-op. `newsapi_enabled()`가 `NEWSAPI_KEY` 있을 때만 True를 반환하는 가드 스텁만 두고 fetch는 미구현. 사유: RSS가 이미 "키 불요 보조 뉴스" 목표를 충족하므로 키 필요 소스를 무리해 넣지 않음(키가 생기면 그 분기에서 구현).
- **기본 소스 미포함(옵트인)**: rss는 `DEFAULT_LIVE_SOURCES`에 넣지 않음. 외부 피드 안정성은 관측 후 승격 판단 — 폴러는 소스별 실패를 개별 격리하지만(한 피드 죽어도 루프 지속), 기본 라이브 경로는 검증된 소스(opensky/gdelt/metar/celestrak)로 유지하고 rss는 `SKAI_POLL_SOURCES=...,rss` 옵트인으로 켠다. 라이브 1사이클이 무결하므로 승격은 저위험.
- **XML 안전**: stdlib xml.etree는 신뢰 못 할 입력에 안전하지 않음(공식 문서). 방어 2겹 — ① 응답 크기 상한 5MB ② **DOCTYPE/`<!ENTITY>` 포함 응답 파싱 거부**(엔티티 확장 공격 차단). 피드는 평판 매체 공개 피드로 한정. 이 방어가 완전한 XXE 대책은 아님(정직 한계) — 새 패키지 금지 제약 하의 실용적 절충.

---

## 2. A5 — mentions 엔티티 링킹 심화

### before → after

| 항목 | before | after |
|---|---|---|
| 위치 | gdelt 내부 `REGION_ALIASES`·콜사인 substring 매칭(gdelt 전용) | **공용 `ontology/entity_linking.py`** (gdelt·rss·stealthmole 공유 SSOT) |
| 지역 | KADIZ 별칭 substring | 동일(승격, 다지역 확장 가능한 `match_regions`) |
| 항공기 | 제목에 콜사인이 `" CS "` 형태로 등장 시(공백 padding substring) | **정규식 패턴 추출**(`[A-Z]{2,3}[0-9]{2,4}` 콜사인 + `[0-9a-f]{6}` icao24 hex) → **store 실존 대조** 통과분만 링크 |
| 오퍼레이터 | 없음(스키마만 존재, 미사용) | **항공사/공군 명칭 사전**(대한항공/KAL, 아시아나/AAR, USAF, PLAAF, VKS, ROKAF, JASDF) + **시드 Operator 7종 적재** → mentions→Operator가 실체를 가리킴 |
| provenance | confidence만 소폭 상향 | **방식별 신뢰 라벨**(콜사인 exact > icao24 hex > 오퍼레이터 사전 > 지역 키워드)을 `NewsEvent.attrs['linking']`에 기록 |

### 무존재 링크 금지 (핵심)
- `build_aircraft_index(store)`로 실존 Aircraft(콜사인→icao24, icao24 집합) 인덱스 구성.
- `match_aircraft`는 패턴이 잡혀도 인덱스에 **실재하지 않으면 링크하지 않는다.** 6-hex 토큰 오탐("beefed" 등)은 실존 set 대조로 구조적으로 걸러짐.

### confidence 반영
- base(gdelt/rss 0.30, stealthmole 0.25) + 지역 0.05 + 오퍼레이터 0.05 + 항공기 exact 0.10, **상한 0.4 clamp**.
- 지역+오퍼레이터+항공기 동시 매칭 = 0.50 → 0.40으로 clamp(뉴스는 확증 아님, DR-0005 유지).

### 정직한 한계 (EVALUATION.md §3에 대한 응답)
- 이것은 **진짜 NER이 아니다.** 사전(지역·오퍼레이터 별칭) + 패턴(콜사인·icao24) + 실존 대조의 조합이다.
  문맥이해·중의성 해소는 없다. "키워드 매칭뿐" → "사전+패턴+실존대조"로 한 단계 올라가되 그 이상을 주장하지 않음.
- 오퍼레이터 사전은 큐레이션한 소수(7종)라 커버리지 제한적. 단어경계(`\b`) 매칭으로 "Korean airline"이 "Korean Air"로 오탐되지 않게 막았으나 한글 별칭은 substring(경계 불가).
- **라이브 항공기 링크는 드묾**: 뉴스 헤드라인에 ADS-B 콜사인/icao24가 실릴 확률이 낮다. 실존 대조 로직 자체는 합성 테스트로 증명(아래).

---

## 3. 검증 결과

### 단위 테스트 (`tests/test_rss.py`, 신규 20건)
- RSS 파싱: item→NewsEvent, HTML 제거, RFC822 pubDate, link 없는 item skip, **DOCTYPE/ENTITY 거부**, 파싱오류→[].
- 링킹: 지역·오퍼레이터 사전, **오퍼레이터 단어경계 오탐 방지**, 콜사인/icao24 **실존 대조**(무존재 스킵), link_newsevent 통합(mentions·attrs·confidence·entities), 시드·인덱스.
- **합성 교차소스**: 실존 Aircraft(KAL092) ↔ 뉴스 콜사인 → `NewsEvent —mentions→ Aircraft` 왕복(라이브 항적 대체 검증).
- 폴러 등록: SOURCE_INTERVALS·resolve_sources·`_ingest_source('rss')` dispatch.
- **전체 스위트: 305 passed, 4 skipped**(기존 285 + 신규 20, 회귀 0).

### 라이브 (scratch DB)
- `SKAI_POLL_SOURCES=opensky,rss` 유한 1사이클: opensky **44 항적** 적재 → rss **60 NewsEvent**(4피드×15) 적재, mode=stopped, 소스별 신선도 사이드카 기록(opensky·rss 둘 다 ok, last_poll>0).
- **A5 라이브 링킹 확인**: aviationist 실제 헤드라인 "Trump Flies Aboard The New Air Force One…", "U.S. Air Force Plans Major Investment…"에서 **op-usaf 오퍼레이터 링킹**(method=operator_name, label=medium, confidence 0.30→0.35). 라이브 데이터에서 사전 링킹이 실제로 작동함.
- 라이브 aircraft mentions **0건**(예상된 정직 한계 — 헤드라인에 ADS-B 콜사인 부재. 실존 대조 로직은 합성 테스트로 증명).
- gdelt는 429(사전 프로브 버스트 잔여 레이트리밋) → 소스 격리로 루프 지속. rss 경로는 완전 정상.

---

## 4. 변경 파일
- 신설: `ontology/entity_linking.py`(공용 링킹 SSOT), `connectors/rss.py`(RSS 커넥터), `tests/test_rss.py`(20건).
- 수정: `connectors/gdelt.py`(REGION_ALIASES·match_region_aliases 재노출, ingest가 공용 링킹 사용),
  `connectors/stealthmole.py`(공용 링킹 사용), `connectors/opensky.py`(rss 폴러 등록).
- 하위호환 보존: `gdelt.REGION_ALIASES`·`gdelt.match_region_aliases`·`gdelt.gdelt_response_to_news`(entities/confidence)·`from connectors.gdelt import REGION_ALIASES` 전부 불변(test_p3·test_stealthmole 통과).
- 제약 준수: web/ 미수정, git commit 안 함, 새 패키지 0, 문서·주석 한국어.
