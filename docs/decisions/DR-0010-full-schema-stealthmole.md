# DR-0010 — Foundry 전량 스키마 구축 + StealthMole 편입

- 날짜: 2026-07-04
- 상태: 채택
- 근거: 사용자 지시("object들 필요한 거 지금 다 만들자" + StealthMole 키 사용 가능) · P7 §7 실측

## 결정
1. **Foundry 온톨로지를 v0.1 전량(11객체·링크·액션 4종)으로 구축** — 사용자 UI 작업, 가이드 = `docs/foundry-build-guide.md`. 하이브리드(DR-0009)는 과도기 유지, 스키마 완성 시 코드측 store_foundry를 객체 단위로 확장.
2. **다형 링크는 대상 타입별 분리**: evidenced_by/mentions/cites를 Foundry에서 `*_observation`·`*_news`·`*_orbitpass` 등으로 분해(N:M 링크의 실측 형태에 정합). 코드 매핑 레이어가 v0.1 논리 링크명 ↔ Foundry 물리 링크명을 흡수.
3. **StealthMole = NewsEvent로 매핑, 새 객체 타입 안 만듦**: 다크웹/위협 인텔도 "저신뢰 OSINT 증거" 역할이 동일 — 스멜테스트상 새 도메인 개념이 아니라 source 값("stealthmole")과 confidence로 구분. direction.md §3의 기존 언급("특수상황 트랙 우선")과 정합.
4. StealthMole 연동 절차: API 정찰(완료 — `docs/worklog/stealthmole-recon.md`) → data-sources.md 갱신 → 커넥터(`connectors/stealthmole.py`) → NewsEvent(+mentions 링크). **키는 `~/SKAI/.env`에 `STEALTHMOLE_ACCESS_KEY`+`STEALTHMOLE_SECRET_KEY`로**(정찰 결과 JWT 방식 — 단일 키 아님, gitignore), 코드·문서에 값 기재 금지.
5. **사용 모듈 한정 = DT(다크웹)·TT(텔레그램 공개채널)·GM(정부) + confidence 0.25 저가중.** CL/CDS/CB/UB(개인 크리덴셜·유출 계정 조회)는 **사용 금지** — 합법 가드레일(공개소스·상황인식까지). 개인정보 포함 응답은 DB 적재 금지.

## 기각 대안
- ThreatIntel 별도 객체 타입 — NewsEvent와 역할·링크가 동일(증거·mentions). 객체 수만 늘리는 과설계, 스멜테스트 "그건 속성이지 객체가 아니다". 기각.
- 전량 스키마 전 StealthMole 선행 — 증거 객체(NewsEvent)가 Foundry에 없으면 이관 대상이 없음. 스키마가 선행. 기각.

## 영향
- v0.1 스키마 의미 불변(물리 링크 분해는 구현 세부). CHANGELOG에 구축 완료 시 기록.
- 데모: "다크웹 OSINT까지 융합" 서사 추가 가능(신뢰도 저가중 유지 — 확증 아님 원칙).
