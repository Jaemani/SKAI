# 팀 온보딩 — SKAI 현황 (Air ISR Fusion Copilot)

> D4D 해커톤 T2·공중. 팀원이 현재 상태를 5분 안에 파악하도록.
> 최종 갱신: 2026-07-06.

## 지금 어디까지 됐나 (한눈에)

- **로컬 스택 (P0~P6)**: ✅ 완성 — 4소스 융합·이상탐지 7종·citation 강제 코파일럿·지도/타임라인/서브그래프. 테스트 403 통과·4 skip(2026-07-06 실측, `pytest tests/ -q`).
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

Python 3.12 이상(메인 `.venv`는 3.14.5로 실측 검증; Foundry 전용 `.venv312`는 3.12.13 — 아래 표 참조). `.env` 없이 클론만으로 아래 전부 동작한다(.env는 Foundry/StealthMole 라이브 전용 — 없어도 replay·live·테스트는 정상).

```bash
git clone <repo-url> && cd SKAI          # 최초 1회
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/demo.sh replay      # 오프라인 결정적 (합성 시나리오, 발표 백본)
scripts/demo.sh live        # 순수 실데이터 (4소스 연속 폴링)
scripts/demo.sh live --inject military_incursion  # 실데이터 + 군용 합성 시나리오
scripts/demo.sh stop
# → http://localhost:8000
.venv/bin/python -m pytest tests/ -q   # 테스트 403 통과·4 skip
```

`requirements.txt`는 replay+live+테스트 전체에 필요한 8개 직접 의존성만 핀(전이 의존성은 pip 자동 해결). replay만 필요하면 `requirements-demo.txt`(3개)로 충분 — `Dockerfile.demo`가 그쪽을 쓴다.

### Foundry 없이 되는 범위 vs 개별 토큰 필요한 범위

| 범위 | 필요한 것 | Foundry 계정 필요? |
|---|---|---|
| `scripts/demo.sh replay` (발표 백본) | `requirements.txt`만 | ❌ |
| `scripts/demo.sh live` / `live --inject ...` | `requirements.txt`만 (공개 API만 호출) | ❌ |
| `pytest tests/ -q` (테스트 403) | `requirements.txt`만 | ❌ |
| `SKAI_STORE=foundry`(온톨로지 read를 Foundry로) | `.env`(`FOUNDRY_TOKEN`·`FOUNDRY_HOSTNAME`·`FOUNDRY_OSDK_INDEX`) + Python 3.12 전용 `.venv312` + private index 설치(`aip-integration.md` §0-보강·§(3)) | ✅ |
| `SKAI_COPILOT_LLM=aip` / `SKAI_EXPLAINER=aip`(AIP Logic 서술) | 위와 동일 | ✅ |
| `scripts/demo_foundry.sh` | 위와 동일 | ✅ |
| StealthMole 라이브 인제스트 | `.env`의 `STEALTHMOLE_ACCESS_KEY`·`STEALTHMOLE_SECRET_KEY`(팀 채널 공유, NDA) | Foundry는 아니지만 개별 키 필요 |

## Foundry 모드 (개별 토큰 필요)

`.env`·토큰·NDA 문서는 gitignore라 저장소에 없음. Foundry 실연하려면 각자:
- `.env`에 `FOUNDRY_TOKEN`·`FOUNDRY_HOSTNAME`·`FOUNDRY_OSDK_INDEX` (팀 채널로 별도 공유) — **`.env` 자체를 공유하지 말 것**, 값만 개별 전달
- `SKAI_STORE=foundry`(read를 Foundry로) · `SKAI_COPILOT_LLM=aip`(AIP Logic 요약) · `SKAI_EXPLAINER=aip`(AIP Logic 설명)
- `scripts/demo_foundry.sh` — 실 Foundry 인제스트 실연

## 정직 원칙 (발표·문서 공통)

- "AIP가 탐지/판단한다" ❌ — 탐지·상관·평가·근거강제는 우리 엔진. AIP는 **설명·요약 서술 생성**만.
- "정밀도 100%" ❌ — 합성 회귀 검증일 뿐. 라이브 P/R은 ground truth 없어 불가.
- 어필하는 것: ① 스키마 레벨 근거 강제(앱 우회해도 Palantir가 거부) ② citation 11/11 vs 맨몸 LLM 0.
- 상세: `docs/EVALUATION.md` "발표에서 할 말 / 하면 안 될 말".
