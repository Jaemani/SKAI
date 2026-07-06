# architecture.md — 기술 설계 (Air ISR Fusion Copilot)

빌드용 정밀 설계. 전제: `direction.md`(백본) · `ontology.md`(척추) · `aip-integration.md`(AIP 경로) · `data-sources.md`(소스).

---

## 0. 설계 원칙
- **AIP-spine**: 온톨로지가 중심. 커넥터는 온톨로지에 write, AIP Logic이 Anomaly/Assessment를 Action으로 생성, 프론트는 OSDK로 read.
- **라이브 우선**: 정적 덤프 말고 실 API. 화면에서 움직여야 심사 임팩트.
- **citation 강제**: 모든 주장 → 근거 객체(evidence 링크). 근거 없으면 Action이 거부(환각 방지, 논문 ① "546" 교훈).
- **얇게 수직관통 먼저**: 한 소스→온톨로지 write→이상탐지 1종→요약 Action→지도, 끝단까지 한 줄 먼저.
- **깊이 타협 금지**: 시간 걱정 말고 온톨로지·AIP 깊이 확보.

## 1. 컴포넌트
```
[Connectors]  OpenSky / Celestrak / METAR / GDELT   (async pollers)
      │  ingest → Foundry Dataset
      ▼
[Ontology (Foundry)]  Aircraft·Observation·Track·Satellite·OrbitPass·Region·NewsEvent·Anomaly·Assessment
      │  OSDK 타입드 접근                       ▲ Actions (human review)
      ├──► [Anomaly Engine]  룰 후보 → AIP Logic 설명·신뢰도 → CreateAnomaly(evidence 필수)
      ├──► [Correlation]  시공간 버킷 → Anomaly correlated_with Anomaly/NewsEvent
      └──► [Agent Orchestrator]  자연어 질의 → OSDK read + AIP Logic → GenerateSituationAssessment
                    │
                    ▼
           [Frontend]  지도(항적/위성궤적/지오펜스) + 타임라인 + 채팅 (OSDK read, Action 호출)
```

## 2. 데이터 파이프라인
1. **Poll**: 관심지역 bbox로 커넥터 주기 폴링(항적 5~10s, TLE 12h, METAR 30m, 뉴스 5m).
2. **Ingest→Ontology**: 원응답 → Foundry Dataset → Object로 매핑. `source`·`source_url`·`ts` 필수(provenance).
3. **Track custody**: Observation을 icao24로 묶어 Track. gap 플래그(entity resolution across 소스/공백).
4. **위성 통과창**: TLE + sgp4 → OrbitPass 객체(over Region, window, max_elevation) + 지상궤적.
5. **상관**: 시공간 버킷으로 교차 → Anomaly correlated_with(항적↔뉴스↔위성).

## 3. 이상탐지 (Anomaly Engine) — 핵심 차별점
룰(설명가능·빠름)로 후보 → AIP Logic(AnomalyExplainer)이 맥락·설명·신뢰도. 결과는 `CreateAnomaly` Action(evidence 링크 필수).

| 유형 | 룰 신호(그래프 패턴) | 근거 객체 |
|---|---|---|
| 비상 스쿽 | Observation.squawk ∈ {7500,7600,7700} | Observation |
| ADS-B dropout | 민감구역 내 **현재 신호 침묵**(now−마지막 관측 > 폴간격 인지 임계) + 교차 미확인. 신선한 관측이면 과거 gap 이력 무관하게 비발화, 침묵 이벤트당 1건 | Track+교차 미러 |
| 군용기 접근 | Aircraft.is_military + Observation within Region(OpArea) | Observation |
| 로이터링 | Track 경로 반경 내 반복/원형 | Track |
| 급기동 | 고도·속도 급변(임계) | Observation 시퀀스 |
| 위성 근접/통과 | OrbitPass over Region during window | OrbitPass |
| 항적↔뉴스 상관 | Anomaly(track) correlated_with NewsEvent(mentions Region) | 다중 |

**dropout/spoofing 판정은 교차소스 필수**(단일 결측=송신기 문제일 수 있음 → 신뢰도 낮춤).

## 4. 에이전트 오케스트레이션
- **툴 = OSDK + AIP Logic**: `query_flights`, `sat_passes`, `metar`, `news`를 OSDK 객체 질의로. 추론은 AIP Logic 함수.
- 흐름: 질의 파싱 → 지역·시간창 확정 → OSDK 병렬 read → 이상탐지 병합 → `GenerateSituationAssessment`(문장별 cites) → 결과.
- **citation 강제**: Assessment의 각 문장에 cites 링크(Observation/NewsEvent). 링크 없는 문장은 Action이 거부/플래그.

## 5. 시각화
- 지도: Leaflet/deck.gl. 레이어 = 항적(점+Track), 위성 지상궤적(라인), Region 지오펜스(폴리곤), Anomaly(마커+색).
- 타임라인: 이벤트·Anomaly를 시간축에. 클릭 → 지도 하이라이트 + 근거 객체.
- 온톨로지 그래프 뷰: Aircraft-Observation-Anomaly-NewsEvent 서브그래프(AIP 깊이 시연).
- 채팅: 질의/답변, citation 클릭 → 원 소스.

## 6. 스택
- Ingest/커넥터: Python(asyncio, httpx), sgp4.
- 온톨로지·추론: **Foundry Ontology + OSDK(Python/TS) + AIP Logic** (spine). 상세 `aip-integration.md`.
- 프론트: React/Vite + Leaflet, OSDK로 데이터.
- LLM: AIP Logic 내장 모델(온톨로지 추론) / 커스텀은 Claude(`claude-opus-4-8` 등).
- 폴백(보험): 로컬 SQLite로 동일 온톨로지 스키마 → 후에 Foundry 이관.

## 7. 평가 (숫자로 증명)
- **이상탐지**: 알려진 케이스(비상 스쿽 등) + 합성 시나리오 주입으로 precision/recall.
- **citation 정확도**: Assessment 문장 중 유효 cites 비율.
- **맨몸 챗봇 대비**: 같은 질의 (a)LLM 단독 (b)온톨로지+AIP 파이프라인 → 사실성·출처 우위표.

## 8. 리스크 & 대응
- API 리밋/다운 → 캐시 + 폴백 미러 + 스냅샷 재생.
- 라이브에 이상징후 안 뜸 → **합성 시나리오 주입기**(가짜 dropout/스쿽)로 데모 재현성.
- AIP 러닝커브 → P0에서 1객체+1액션 먼저 뚫기(§`aip-integration.md`), Morph 멘토.
- 시간부족 → MVP 수직관통(§0) 사수, 넓히기는 그 다음. **단 온톨로지 깊이는 타협 금지.**
