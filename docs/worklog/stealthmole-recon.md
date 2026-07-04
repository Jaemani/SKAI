# StealthMole API 정찰 보고서

작성일: 2026-07-04  
상태: **매뉴얼 실측으로 정정됨(2026-07-04)** — 아래 §0 정정 우선. 원 본문은 공개 웹조사 기반이라 일부 오류.  
목적: DR-0010 §4 "API 정찰" 단계 이행. connectors/stealthmole.py 설계 근거.  
⚠️ **NDA**: 공식 매뉴얼은 공유 금지 문서. 엔드포인트·필드 상세는 **로컬 노트**(`stealthmole-manual-notes.md`, gitignore)에만. 이 문서엔 통합 설계 수준만.

## 0. 매뉴얼 실측 정정 (공개 웹조사 대비)
- **DT(Darkweb Tracker)·UB는 이번 해커톤 미제공** — §3 표가 DT를 주력으로 잡은 것은 **오류**. 실사용 불가.
- **실사용 모듈 = GM(정부)·RM(랜섬웨어)·LM(기업)·TT(텔레그램 공개채널)**. GM/RM/LM은 동기 목록조회(쉬움), TT는 비동기 polling.
- **인증 = access_key+secret_key JWT를 요청마다 생성**(HS256, nonce=uuid4, iat). 재사용 시 401. Base URL은 해커톤 전용 호스트.
- 배제(개인정보) = CL·CDS·CB·CDF. 상세 스키마·쿼터는 로컬 노트 참조.
- 아래 원 본문(§1~)의 DT 중심 서술은 위 정정으로 대체.

---

## 1. API 존재 여부 및 구조 요약

StealthMole은 REST API와 MCP(Model Context Protocol) 서버를 공식 제공한다.  
공개 소스: GitHub `StealthMole/stealthmole-mcp` (2026-07-04 현재 WebFetch 404 — private 또는 이름 변경 가능성 있음), Glama MCP 디렉터리, mcpmarket.com.

**[확인됨]** 핵심 기술 구조:
- REST API 위에 MCP 서버가 래핑된 형태
- 기반 URL: 공개 문서에 명시 없음 → **미확인**. `api.stealthmole.com` 또는 `platform.stealthmole.com` 추정이나, 키 수령 후 실측 필요
- MCP SDK가 노출하는 tool 이름(dt_search_target 등)이 내부 REST 엔드포인트와 1:1 대응하는지 미확인

---

## 2. 인증 방식

**[확인됨]** JWT 기반 인증 (HS256 서명).  
환경변수 2개 필요:
```
STEALTHMOLE_ACCESS_KEY=<발급된 키>
STEALTHMOLE_SECRET_KEY=<발급된 시크릿>
```
MCP 서버가 세션 레벨에서 JWT를 자동 생성·관리한다.  
헤더 형식(Bearer vs 커스텀 헤더) 및 토큰 만료 주기: **미확인** — 키 수령 후 실측.

DR-0010에 따라 키는 `~/SKAI/.env`에 `STEALTHMOLE_API_KEY`로 보관 (gitignore). 실제로는 ACCESS_KEY + SECRET_KEY 두 변수가 필요하므로, `.env`에 양쪽 기재 예정.

---

## 3. 제공 모듈 (공개 문서 기반)

| 모듈 ID | 이름 | 설명 | ISR 활용 가능성 |
|---|---|---|---|
| DT | Darkweb Tracker | 다크웹·딥웹 52개 인디케이터 키워드/IP/도메인 검색 | **높음** — 공역·항공 관련 위협 언급 수집 |
| TT | Telegram Tracker | 텔레그램 채널·유저·메시지 검색 | **높음** — 군사·위협 정보 오픈채널 모니터링 |
| RM | Ransomware Monitoring | 랜섬웨어 그룹 피해기관 트래킹 | 낮음 (항공 인프라 피해 정도) |
| GM | Government Monitoring | 정부 기관 대상 위협 모니터링 | 중간 — 방공 관련 정부기관 위협 |
| LM | Leaked Monitoring | 기업 대상 위협 | 낮음 |
| CL | Credential Lookout | 유출 크리덴셜 검색 (domain/email/id/pw) | **사용 안 함** (가드레일 §5 참조) |
| CDS | Compromised Data Set | 인포스틸러 감염 기기 유출 | **사용 안 함** |
| CB / UB | Combo / ULP Binder | ID-PW / URL-Login-PW 콤보 검색 | **사용 안 함** |

Air ISR 프로젝트에서 실사용 대상: **DT + TT + GM**. CL/CDS/CB/UB는 개인정보·크리덴셜 직접 조회로 가드레일 위반.

---

## 4. 주요 API 함수 및 파라미터 (MCP tool 단위)

Glama 디렉터리 기준 (확인됨):

### Darkweb Tracker
```
dt_search_targets(indicator: str)          # 해당 인디케이터로 검색 가능한 타깃 목록
dt_search_target(target, text, limit=100, order_type, order_dir)  # 특정 타깃 검색
dt_search_all(indicator, text, limit=100)  # 전체 타깃 일괄 검색
dt_search_by_id(search_id, cursor)         # 이전 검색 결과 페이지네이션
dt_get_node_details(node_id, include_html=False)  # 노드 상세 (URL·HTML 옵션)
```

### Telegram Tracker
```
tt_search_targets(indicator: str)
tt_search_target(target, text, limit=100)
tt_get_node_details(node_id)
```

### 관제/모니터링
```
rm_search(query=None, order_type=None)     # 랜섬웨어
gm_search(query=None, order_type=None)     # 정부
lm_search(query=None, order_type=None)     # 기업
get_user_quotas()                          # 월간 API 사용량 조회
```

**리밋 (확인됨)**:
- DT: 최대 100건/요청
- CL·CDS·CB·UB: 최대 50건/요청
- 쿼리 연산자: OR 최대 3개, 전체 연산자 최대 5개
- 월 총 쿼터: **미확인** — `get_user_quotas()` 첫 호출로 확인 예정

**쿼리 문법 예시**:
```
keyword:"KADIZ" AND after:2026-06
domain:korea.go.kr AND after:2026-01
```

---

## 5. 응답 포맷

공개 문서에 JSON 스키마 예시 없음 → **미확인 (실측 필요)**.  
MCP 서버 동작 기준으로 추정되는 구조:
```json
{
  "results": [
    {
      "id": "<node_id>",
      "title": "...",
      "source": "<포럼/채널명>",
      "url": "...",
      "timestamp": "...",
      "content_snippet": "...",
      "indicator_matches": [...]
    }
  ],
  "next_cursor": "...",
  "total": 42
}
```
**주의**: 위는 추정 구조. 키 수령 후 dt_search_all로 실측 필요.

---

## 6. NewsEvent 매핑 초안

DR-0010 §3 기준: StealthMole 결과 → `NewsEvent` (source="stealthmole", confidence 저가중).

```python
# connectors/stealthmole.py 설계 초안
def stealthmole_to_news_event(raw: dict) -> dict:
    return {
        "newsId":      f"sm-{raw['id']}",
        "source":      "stealthmole",
        "url":         raw.get("url", ""),
        "ts":          parse_ts(raw.get("timestamp")),   # ISO8601로 정규화
        "title":       raw.get("title", ""),
        "summary":     raw.get("content_snippet", ""),
        "entitiesJson": json.dumps({
            "indicator_matches": raw.get("indicator_matches", []),
            "dark_source":       raw.get("source", ""),
        }),
        "confidence":  0.25,   # 다크웹 OSINT = 저신뢰 (교차검증 전)
    }
```

`confidence: 0.25` 근거: 다크웹 정보는 출처 비검증·허위 가능성 높음. 항적·궤도(0.9~1.0) 및 공식 뉴스(0.5~0.7)보다 낮게. AnomalyRecord에 연결 시 `evidence_weight` 축소 필요.

---

## 7. Air ISR 합법·윤리 활용각

### 사용 가능 (가드레일 통과)

1. **공역 관련 위협 언급 모니터링 (DT)**  
   키워드: `"KADIZ"`, `"Korea Air Defense"`, `"ADIZ"` 다크웹 포럼 언급 탐지.  
   활용: 특정 공역 위협 토론이 다크웹에 등장 → NewsEvent(저신뢰) → AnomalyRecord 증거.

2. **항공기·군용기 관련 위협 인텔 (DT + TT)**  
   키워드: `"military aircraft"`, 특정 기지명 공개 정보.  
   활용: 오픈 텔레그램 채널의 군사 관련 언급 수집 → 맥락 강화.

3. **항공 인프라 대상 랜섬웨어/사이버 위협 (RM + GM)**  
   공항·항공청(MOLIT·FAA 등) 피해 공식 발표 전 다크웹 선행 탐지.  
   활용: 항공 NOTAM 비정상 배경 맥락으로 활용.

4. **공개 Telegram 정보 수집 (TT)**  
   군사 관련 공개 텔레그램 채널 언급 분석.  
   주의: 공개 채널만. 비공개 채널 크랙킹 시도는 ToS·합법성 위반.

5. **ADIZ 접근·항적 이상 관련 언급 교차검증 (DT)**  
   OpenSky에서 ADS-B dropout 탐지 시 → 동일 시각대 다크웹 언급 검색 → 의도적 소등 판정 근거 보완.  
   이 활용이 ISR 도메인에서 가장 직접적인 온톨로지 연결.

### 사용 안 함 (가드레일)

- **CL / CDS / CB / UB**: 개인 크리덴셜·유출 계정 직접 조회. 개인정보 취급, 우리 산출 목적과 무관.
- **개인 식별 정보 저장**: 응답에 개인 이메일·비밀번호 포함 시 즉시 제거, DB 적재 금지.
- **비공개 그룹 크랙킹**: TT는 공개 채널·로그 수준만 사용.
- **레이트리밋 우회**: 해커톤 키 남용 금지.

---

## 8. D4D 해커톤 제공 범위

**미확인.** 웹에서 "D4D + StealthMole" 조합 공개 정보 없음.  
추정: 해커톤 파트너로 키를 제공한다면 DT + TT 기본 쿼터가 일반적. 고급 모듈(CDS 등) 제한 가능.  
키 수령 시 `get_user_quotas()`로 실제 허용 모듈과 월 쿼터 즉시 확인 필요.

---

## 9. 키 수령 후 검증 절차 (1회 호출 스크립트 스펙)

```python
# tools/verify_stealthmole.py
# 목적: 키 유효성 + 쿼터 + 기본 응답 구조 확인
# 실행: python tools/verify_stealthmole.py

import os, json
# Step 1: 쿼터 확인
#   get_user_quotas() → 허용 모듈·잔여 쿼터 출력

# Step 2: DT 기본 검색 (1건만)
#   dt_search_all(indicator="keyword", text="KADIZ", limit=1)
#   → 응답 구조 전체 raw print → 실제 필드명 확인

# Step 3: TT 기본 검색 (1건만)
#   tt_search_target(target=<first_target>, text="military aircraft", limit=1)

# 확인 항목:
# - HTTP status 200
# - 응답 필드명 목록 (id/title/url/timestamp/content 등 실측)
# - timestamp 포맷 (ISO8601? epoch? 시간대?)
# - url 필드 존재 여부
# - 에러 응답 형식

# 결과를 docs/worklog/stealthmole-probe-result.json으로 저장
# (개인정보 포함 시 저장 전 필터링, git commit 금지)
```

이 스크립트 실행 후 실제 필드 확인 → `stealthmole_to_news_event()` 매핑 함수 확정 → `connectors/stealthmole.py` 구현.

---

## 10. data-sources.md 추가 초안 문단

```markdown
## 5. 다크웹 OSINT (StealthMole) — 특수상황 트랙

### StealthMole Darkweb Tracker + Telegram Tracker
- 용도: 다크웹·딥웹 포럼, 텔레그램 공개채널의 공역·항공 관련 위협 언급 탐지.
  저신뢰 증거로 활용 — 항적·궤도 이상징후 맥락 보강용.
- 인증: STEALTHMOLE_ACCESS_KEY + STEALTHMOLE_SECRET_KEY → JWT 자동 생성.
  키는 ~/SKAI/.env에만 (gitignore, 코드·문서 기재 금지).
- 주요 모듈: DT(darkweb), TT(telegram), GM(government) — CL/CDS/CB/UB 사용 금지(개인정보).
- 리밋: DT 최대 100건/요청, 연산자 최대 5개(OR 3개). 월 쿼터: 키 수령 후 get_user_quotas()로 확인.
- 응답 → NewsEvent 매핑: source="stealthmole", confidence=0.25(저신뢰, 교차검증 전).
- 주의: 개인 크리덴셜 포함 응답은 DB 저장 금지. 공개 채널·공개 포럼 결과만 적재.
- 실측 여부: 키 수령 전 (2026-07-04). tools/verify_stealthmole.py로 검증 예정.
```

---

## 미확인 목록 (키 수령 후 확인 필요)

| 항목 | 현재 상태 |
|---|---|
| REST API 기반 URL | 미확인 (MCP 서버 레이어 뒤에 숨겨짐) |
| 헤더 형식 (Bearer/커스텀) | 미확인 |
| JWT 만료 주기 | 미확인 |
| 응답 JSON 실제 필드명 | 미확인 (추정 구조) |
| timestamp 포맷 | 미확인 |
| 해커톤 키 허용 모듈 범위 | 미확인 |
| 월 쿼터 수치 | 미확인 |
| ToS 상 군사·방산 활용 조항 | 미확인 (판매팀 문의 필요) |
| D4D 파트너십 공식 범위 | 미확인 |

---

## 출처

- Glama MCP 디렉터리: https://glama.ai/mcp/servers/@StealthMole/stealthmole-mcp
- StealthMole 공식 사이트: https://www.stealthmole.com/
- Darkweb Tracker 제품 페이지: https://www.stealthmole.com/products/darkweb-tracker
- Telegram Tracker 제품 페이지: https://www.stealthmole.com/products/telegram-tracker
- GitHub org (접근 실패 — private 가능성): https://github.com/StealthMole
- MCPMarket: https://mcpmarket.com/server/stealthmole
