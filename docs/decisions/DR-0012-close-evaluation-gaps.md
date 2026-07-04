# DR-0012 — EVALUATION.md 갭 종결 계획

- 날짜: 2026-07-04
- 상태: 채택
- 근거: 사용자 지시("EVALUATION.md대로 진행"). 평가서의 OVERSOLD/GAP를 실제로 닫아 과장을 사실로 전환.

## 원칙
"완성 보고" 금지. 각 항목은 **OVERSOLD/GAP → 사실 전환**으로만 닫는다. 닫히기 전엔 발표 문구에서 뺀다(EVALUATION §하면 안 될 말).

## 매핑 (평가 항목 → 조치 → 담당 → 우선순위)

| # | 평가 항목 | 조치 | 담당 | 우선 |
|---|---|---|---|---|
| 1 | **OVERSOLD: "AIP가 추론"** / GAP: AIP Logic 미사용(스텁) | Foundry **AIP Logic 함수 AnomalyExplainer 실제 생성** + `AipLogicExplainer` 배선 → "AIP가 이상징후 설명을 생성"이 사실이 됨 | **사용자(노코드 UI) + 코드(배선)** | ★1 |
| 2 | **OVERSOLD: "Foundry 위에서 돈다"** / GAP: read 권위 로컬 | **Foundry-primary read 모드** 추가(SKAI_STORE=foundry 시 화면이 Foundry에서 read). **로컬 기본 유지**(오프라인 replay 결정성 불변, DR-0008) | 코드 | ★2 |
| 3 | 반쪽 실시간 / 원소스 연결 안 됨 | live 폴러에 **뉴스·위성·기상 연속 폴링** 추가(현재 OpenSky만) → 실 GDELT URL로 원소스 연결 | 코드 | ★2 |
| 4 | OVERSOLD: "OSDK 타입드 read"(실제 저수준) | read를 OSDK 타입드 클래스로 전환(0.8.0 발행분) | 코드 | 3 |
| 5 | OVERSOLD: "P/R 100%"(합성) | 발표서 "정밀도 100%" 삭제 + 홀드아웃/실데이터 소규모 eval 추가(가능분) | 코드+프레이밍 | 3 |
| 6 | 교차소스 dropout 라이브 미배선 | 2차 ADS-B 피드(adsb.fi 등) ToS 확인 후 배선 or "인터페이스만"으로 정직 유지 | 코드(신중) | 4 |
| 7 | PARTIAL: 엔티티해소 얕음 | 진짜 교차소스 ER은 해커톤 범위 밖 — **어필 안 함**(정직 유지) | — | 보류 |

## 병행 사항
- **프론트 질의 렌더 확인**: API는 실측 작동(intent·cites·cited_objects 정상). 브라우저 미작동은 캐시 or 렌더 버그 — 실 브라우저 재현으로 확정(별도).
- live 모드가 합성(narrative)을 얹는 것 → "순수 라이브"와 "데모(합성 얹음)" 명확 분리.
- 누적 skai.db(옛 합성+실데이터 혼재) 정리.

## 되돌리기
- Foundry read 모드·live 폴링은 환경변수 게이트(기본 로컬·OpenSky만) — 미설정 시 기존 동작 불변.
- AIP Logic 배선은 SKAI_EXPLAINER=aip일 때만(기본 template).
