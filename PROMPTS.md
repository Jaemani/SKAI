# PROMPTS.md — 콜드스타트 실행 프롬프트 (Air ISR)

AI 에이전트에게 **순서대로** 던진다. 한 번에 완성 아님 — 각 단계가 산출물을 내고, 실패 로그로 다음을 조정. 각 프롬프트 = 무엇을/성공기준/제약.

공통 제약(매 프롬프트): `direction.md`·`ontology.md`·`aip-integration.md`·`architecture.md`·`data-sources.md` 준수. **AIP-spine**(온톨로지 중심). 공개·합법 소스만. citation(evidence 링크) 강제. 라이브 우선. 온톨로지 스멜테스트(ontology.md §0) 통과. 깊이 타협 금지.

---

## P0 — 이중 정찰: 공개소스 + AIP 파이프 뚫기
```
목표: (A) 공개 소스가 살아있는지, (B) Foundry/OSDK 파이프가 도는지 동시에 확인. 둘 다 P1 전에 검증.
할 일:
A. data-sources.md의 4소스(OpenSky KADIZ bbox / Celestrak TLE+sgp4 / METAR RKSI / GDELT) 각 1회 호출, 응답 스키마 기록.
B. Foundry Dev Tier에서 온톨로지에 Object Type 1개(Aircraft) + Link 1개(observed_as) + Action 1개(CreateAnomaly) 생성. Developer Console에서 OSDK 발행 → pip 설치 → Python으로 Aircraft 객체 1건 write/read 왕복.
성공기준: A) 4소스 200+파싱. B) OSDK로 온톨로지 객체 read/write 왕복 성공.
제약: 익명/무료 범위. 키는 환경변수. AIP 막히면 원인 기록 + Morph 멘토 질문 목록화(폴백은 보험이지 전환 아님).
산출: data-sources.md 실응답 예시 갱신, aip-integration.md에 "확인된 OSDK 호출 스니펫" 추가.
```

## P1 — 수직관통: OpenSky → 온톨로지 → 지도
```
목표: OpenSky 하나로 항적을 받아 온톨로지(Aircraft/Observation/Track)에 write하고 OSDK로 read해 지도에 띄우는 최소 파이프.
할 일:
1. connectors/opensky.py: bbox 폴링 → Observation 객체 write(source_url, ts 포함), Aircraft observed_as 링크.
2. Track custody: icao24로 묶어 Track, has_gap 플래그.
3. 프론트: Leaflet 지도에 OSDK read로 항적 점+Track, 30초 새로고침.
성공기준: 브라우저에 실 항적이 온톨로지 경유로 뜨고 갱신됨.
제약: 저장은 Foundry 온톨로지(폴백 시 동일 스키마 SQLite). 동작 우선.
산출: 실행법, 스크린샷.
```

## P2 — 이상탐지 1종(비상 스쿽) + Action + provenance
```
목표: "비상 스쿽" 1종을 룰→AIP Logic 설명→CreateAnomaly(evidence)→화면까지 끝단 구현.
할 일:
1. anomaly/rules.py: squawk ∈ {7500,7600,7700} 후보 탐지.
2. AIP Logic 함수 AnomalyExplainer: 후보+근거 Observation → 설명·신뢰도.
3. CreateAnomaly Action: evidence 링크 필수(없으면 거부). 라이브 부재 대비 합성 스쿽 주입기.
4. 지도/타임라인에 Anomaly 마커 + 근거 클릭 → 원 Observation.
성공기준: 주입 시나리오가 탐지→AIP 설명→Anomaly 객체(근거 링크)→화면. confirm/dismiss 액션 동작.
제약: 근거 없는 Anomaly 생성 금지(Action 레벨에서 강제).
산출: 데모 GIF, 주입기 사용법.
```

## P3 — 융합 확장 (위성 + 기상 + 뉴스) 온톨로지 통합
```
목표: 나머지 소스를 온톨로지 객체로 통합.
할 일:
1. Celestrak TLE+sgp4 → OrbitPass 객체(over Region, window) + 지상궤적 레이어.
2. METAR → WeatherState 객체 + 기상 카드.
3. GDELT → NewsEvent 객체(저신뢰), mentions Region/Operator 링크(엔티티 링킹).
성공기준: 4종 소스가 한 온톨로지에 시공간 정렬.
제약: 뉴스는 확증 아님 → confidence 낮춤, 하드 소스로 교차검증.
산출: 통합 화면, 소스별 객체 카운트.
```

## P4 — 코파일럿 (자연어 질의 → citation Assessment)
```
목표: 자연어 질의 → OSDK/AIP 오케스트레이션 → 출처 달린 SituationAssessment.
할 일:
1. 툴화: query_flights/sat_passes/metar/news = OSDK 객체 질의.
2. 흐름: 지역·시간창 파싱 → 병렬 read → 이상탐지 병합 → GenerateSituationAssessment(문장별 cites).
3. citation 강제: cites 링크 없는 문장 거부/플래그.
성공기준: "지금 KADIZ 근방 이상한 거 있어?" → 요약+이상징후+근거링크. 온톨로지 서브그래프 뷰.
제약: 근거 없는 주장 금지. confidence(0~1) 표기.
산출: 질의/답변 예시 3개, citation 클릭 동작.
```

## P5 — 이상탐지 확장 + 교차소스 내러티브 + 평가
```
목표: 이상탐지 3~5종 + 교차소스 correlated_with 내러티브 + 성능 증명.
할 일:
1. ADS-B dropout(교차소스 판정), 로이터링, 군용기 지오펜스 접근 추가.
2. Anomaly correlated_with(dropout+위성통과+뉴스) = "은닉 정황" 내러티브(ontology.md §2 예시).
3. 합성 세트로 precision/recall + 맨몸 LLM vs 온톨로지+AIP 비교표.
성공기준: 이상탐지 3종+ 동작, 내러티브 1건, 평가 수치 1장.
제약: dropout 단일소스 결측만으로 단정 금지.
산출: 평가표, 비교표.
```

## P6 — 데모·피칭 패키징
```
목표: 3분 발표용 라이브 데모 + 백업.
할 일:
1. 관심지역 고정, 데모 스크립트(질의 3개 흐름 + Action 시연 = 온톨로지 상태 전이).
2. 라이브 실패 대비 스냅샷 재생 모드.
3. 심사 매핑(README 지표표)으로 슬라이드 골자.
성공기준: 네트워크 없어도 재현 + 심사 4항목 각 한 문장 대응 + "AIP 얕지 않음" 시연(Action→상태전이→provenance).
산출: demo.md(대본), 백업 스냅샷.
```

---

### 반복 규칙
- 실행 후 실패 로그를 다음 프롬프트에 붙여 조정. 한 번에 완성 기대 금지.
- 매 단계 `direction.md` 5요소 + `ontology.md` 스멜테스트와 어긋남 자문. 어긋나면 되돌리기.
- 막히면 넓히지 말고 수직관통(P1) 사수 후 재확장. **온톨로지 깊이는 타협 금지.**
