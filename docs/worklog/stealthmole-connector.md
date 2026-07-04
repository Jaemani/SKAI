# StealthMole 커넥터 구현 로그

날짜: 2026-07-04
단계: 커넥터 1단계 완료 (GM·RM·LM 동기 모듈)

## 구현 내용

### 파일
- `connectors/stealthmole.py` — 커넥터 본체
- `tests/test_stealthmole.py` — 매핑·가드레일·store 왕복 테스트 14개
- `.env` — `STEALTHMOLE_BASE_URL` 라인 추가 (값은 .env에서만, 코드 하드코딩 없음)
- `web/index.html` — `b-stealthmole` 배지 CSS + drawNews 소스 배지 추가

### 모듈 3종 (동기 검색)
| 모듈 | 유형 | 매핑 |
|---|---|---|
| GM | 정부/공공기관 위협 게시글 | title, author, detection_datetime |
| RM | 랜섬웨어 피해 조직 | victim+attack_group → title, sector/country → summary |
| LM | 기업 위협 게시글 | title, author, detection_datetime |

### NewsEvent 매핑 (DR-0010)
- `id` = `sm-{module}-{id}`
- `source` = `"stealthmole"`
- `source_url` = proof_url (무료버전 제한 메시지이면 `stealthmole://{module}/{id}` 내부 URI)
- `ts` = detection_datetime (Unix 정수)
- `confidence` = 0.25 (저신뢰 OSINT, NEWS_MAX_CONFIDENCE 이하)
- `entities` = 제목에서 매칭된 KADIZ 지역 별칭 (gdelt 사전 재사용)
- `attrs.module` = 모듈명, `attrs.author` / `attrs.attack_group` 등

### 가드레일
- 개인정보 모듈(CL·CDS·CB·CDF·DT) 함수 자체 없음
- 응답 텍스트에 이메일 패턴·password= 패턴 포함 레코드는 저장 없이 skip
- TT 비동기 모듈은 미구현 (주석으로 명시)

### 인증
- 요청마다 새 JWT (HS256, nonce=uuid4) — 키 값은 코드·문서에 없음
- Base URL은 환경변수 `STEALTHMOLE_BASE_URL`에서만 참조
- 2초 호출 간격 코드 강제, 401 → JWT 재생성 1회 재시도, 426 → 건너뜀

## 검증 결과

### 테스트
```
14 passed in 10.12s
126 passed (기존 스위트), 2 skipped — 기존 테스트 0 파손
```

### 라이브 1사이클 적재
| 모듈 | totalCount(서버) | 적재 |
|---|---|---|
| GM | 8,673건 | 20건 |
| RM | 30,020건 | 20건 |
| LM | 169,757건 | 20건 |

합계 60건 NewsEvent → 로컬 store 적재 완료 (SKAI_DB 기본 경로).

`/api/news` 응답에 `source: "stealthmole"` 이벤트가 포함됨 (서버 무수정).
웹 뉴스 패널: `다크웹` 배지 표시 + 신뢰도 0.25 표시.

## 남은 것
- **TT 비동기 모듈** (텔레그램 공개채널): indicator 지정 → 202 polling 루프 필요. 별도 설계 후 2단계.
- 항공 키워드 필터 쿼리 (`SM_AVIATION_KEYWORDS`)는 상수로 정의됐으나 현재 빈 쿼리(전체 최신)로 호출 중. 키워드 쿼리 효과 실측 후 활성화 여부 결정.
