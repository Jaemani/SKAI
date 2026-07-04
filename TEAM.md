# 팀 온보딩 — SKAI 현황 (Air ISR Fusion Copilot)

> D4D 해커톤 T2·공중. 팀원이 현재 상태를 5분 안에 파악하도록.
> 최종 갱신: 2026-07-05.

## 지금 어디까지 됐나 (한눈에)

- **로컬 스택 (P0~P6)**: ✅ 완성 — 4소스 융합·이상탐지 7종·citation 강제 코파일럿·지도/타임라인/서브그래프. 테스트 326 통과.
- **Palantir Foundry 이관**: ✅ 온톨로지 11객체·36액션·OSDK 0.12.0·AIP Logic 2함수·staged review·Automation. read/write 라이브 검증.
- **실시간**: ✅ 4소스 연속 폴링(OpenSky 25s·GDELT 5m·METAR 30m·Celestrak 12h) + RSS. 항적 실시간 부드러운 이동(추측항법).
- **정직 평가**: `docs/EVALUATION.md` 참조 — 무엇이 진짜고 무엇이 한계인지 냉정하게.

## ⚠️ 논의 중인 핵심 방향 (2026-07-05)

**"AIP를 로컬 대체가 아니라, AIP여야 풀리는 문제에 써야 한다"** — 현재 AIP Logic 2함수(설명·요약 생성)는 로컬로도 되던 걸 옮긴 수준. 다음 방향 후보: **dropout 노이즈(정상 여객기 구역이탈 오탐 40건)를 AIP Agent triage로 해결** — 후보+온톨로지 서브그래프를 AIP가 traverse해 진짜 신호/노이즈 분류. 온톨로지(그래프)+AIP(추론)가 함께 값을 하는 지점. **미착수 — 팀 결정 필요.**

**알려진 이슈**: dropout 룰이 bbox 이탈을 오탐(40건, 전부 상용 여객기·conf 0.42 동일) → 룰 정밀화 또는 AIP triage 필요.

## 문서 지도 (어디부터 보나)

| 알고 싶은 것 | 파일 |
|---|---|
| **뭐가 되고 뭐가 안 되나 (정직)** | `docs/EVALUATION.md` ← 여기부터 |
| 프로젝트가 뭔지 | `README.md` · `direction.md` |
| 온톨로지 (핵심 설계) | `ontology.md` |
| 시간순 변경 이력 | `docs/CHANGELOG.md` |
| 왜 이렇게 결정했나 | `docs/decisions/DR-0001~0012` |
| 발표 대본 (3분) | `demo.md` |
| 사용법·화면 설명 | `docs/USER-GUIDE.md` |
| 각 작업 상세 로그 | `docs/worklog/` |
| Foundry 구축 가이드 | `docs/foundry-*.md` |

## 실행

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt  # 최초 1회
scripts/demo.sh replay      # 오프라인 결정적 (합성 시나리오, 발표 백본)
scripts/demo.sh live        # 순수 실데이터 (4소스 연속 폴링)
scripts/demo.sh live --inject military_incursion  # 실데이터 + 군용 합성 시나리오
scripts/demo.sh stop
# → http://localhost:8000
.venv/bin/python -m pytest tests/ -q   # 테스트 326
```

## Foundry 모드 (개별 토큰 필요)

`.env`·토큰·NDA 문서는 gitignore라 저장소에 없음. Foundry 실연하려면 각자:
- `.env`에 `FOUNDRY_TOKEN`·`FOUNDRY_HOSTNAME`·`FOUNDRY_OSDK_INDEX` (팀 채널로 별도 공유)
- `SKAI_STORE=foundry`(read를 Foundry로) · `SKAI_COPILOT_LLM=aip`(AIP Logic 요약) · `SKAI_EXPLAINER=aip`(AIP Logic 설명)
- `scripts/demo_foundry.sh` — 실 Foundry 인제스트 실연

## 정직 원칙 (발표·문서 공통)

- "AIP가 탐지/판단한다" ❌ — 탐지·상관·평가·근거강제는 우리 엔진. AIP는 **설명·요약 서술 생성**만.
- "정밀도 100%" ❌ — 합성 회귀 검증일 뿐. 라이브 P/R은 ground truth 없어 불가.
- 어필하는 것: ① 스키마 레벨 근거 강제(앱 우회해도 Palantir가 거부) ② citation 11/11 vs 맨몸 LLM 0.
- 상세: `docs/EVALUATION.md` "발표에서 할 말 / 하면 안 될 말".
