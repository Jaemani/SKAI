# DR-0007 — P5: 이상탐지 확장 + correlated_with 영속 + 평가 하네스

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P4 검증 통과 (`docs/worklog/P4-copilot.md` §8) · PROMPTS.md P5 · CLAUDE.md 기술기준(교차검증)

## 맥락
P5 = 이상탐지 3~5종 + 교차소스 내러티브 + 성능 증명. 열린 결정: ① dropout 교차소스(라이브 2차 소스가 없음) ② 군용기 판정(OpenSky 익명엔 is_military 없음 — P1 발견 #4) ③ 상관의 영속 형태 ④ 평가 방법.

## 결정
1. **dropout은 "단정 금지" 원칙을 코드로**: CrossCheckSource 인터페이스를 두고 — 교차 미확인 단일 결측 = **저신뢰(0.4대) candidate**, 교차소스 미러가 부재를 확인해줄 때만 상향(0.7대). 라이브 2차 소스(adsb.fi 등)는 ToS가 명확히 허용할 때만 옵션 연결, **데모 기본은 합성 교차 미러**(주입기) — 재현성 우선.
2. **군용기 판정은 저신뢰 휴리스틱 + 합성**: 군 콜사인 프리픽스·알려진 군용 icao24 대역 사전(공개 지식) = 저신뢰, 합성 군용기 주입이 데모 기본. **OpArea 소구역 1개를 Region으로 추가**(ontology.md classification에 이미 존재 — 스키마 변경 아님, 데이터 추가).
3. **위성 근접을 Anomaly로 승격**(P4 발견 #3): OrbitPass over Region during window → 저신뢰 Anomaly(evidenced_by OrbitPass). 상관 문장으로만 남기지 않는다.
4. **correlated_with를 온톨로지 링크로 영속**(P4 발견 #1): 시공간 버킷(±60분·공간 겹침)으로 Anomaly—correlated_with→Anomaly/NewsEvent/OrbitPass 저장. "은닉 정황" 내러티브 1건 = ontology.md §2 깊이 증명 질의(dropout+위성통과+뉴스 mentions) 그대로 재현. 뉴스↔이상징후도 시공간 버킷(콜사인 링킹은 상용기 한정 — P3 발견 #2).
5. **평가 = 라벨된 합성 세트 + 구조 비교**:
   - 주입기 확장(dropout·로이터링·군용기·뉴스·기상 시나리오 + 정상 트래픽) → ground truth 라벨 → 탐지 실행 → **precision/recall 표**.
   - 맨몸 LLM 비교: 같은 질의를 (a) 파이프라인 (b) `claude -p` 단독에 던져 사실성·출처·환각을 비교(타임아웃 120s, 중첩 실패 시 정성 구조 비교표로 대체 — 비교의 본질은 provenance 유무).
6. P4 이월 #4(claude 서술 경로 1회 실호출 확인)도 이번에 소화.

## 기각 대안
- 라이브 2차 ADS-B 소스 필수화 — ToS 불확실 + 데모 재현성 저하. 합성 미러가 판정 로직을 동일하게 증명. 기각(옵션으로만).
- icao24만으로 군용 단정 — 오탐 양산(대역은 국가 할당이지 군용 표식 아님). 저신뢰 표기 없는 단정 기각.
- 평가 생략(시간 압박) — architecture.md §7 "숫자로 증명"은 심사 Technical Execution의 직접 점수원. 기각.

## 영향
- 온톨로지 v0.1 링크 correlated_with가 코드 구현되면 링크 11종 전부 사용 상태에 근접. 서브그래프 노드 증가 → P4 발견 #6(레이아웃 상한) 주의.
- P6 스냅샷 재생이 이 합성 세트를 그대로 재사용(P4 발견 #5의 now 앵커링 포함).
