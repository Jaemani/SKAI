#!/usr/bin/env bash
# demo_foundry.sh — 발표용 라이브 Foundry 세그먼트 원커맨드 (실연).
#
#   scripts/demo_foundry.sh          (a) OpenSky 1사이클 인제스트 → Palantir Aircraft/Observation
#                                    (b) 합성 비상 스쿽(7500) → write_anomaly(근거 강제·에러 흡수)
#                                        → Foundry Anomaly + evidenced_by·involves 엣지
#                                    (c) confirm 전이 → Foundry confirm-anomaly 동기
#   scripts/demo_foundry.sh cleanup  발표 종료 후 데모 자산 전량 삭제(합성 Anomaly·Obs·Aircraft)
#
# 데모 자산: (b)(c) 산출물은 발표 직후 Object Explorer에서 보여주려고 **남긴다**(P7 §13 '순증 0'과
# 구분). 과도 누적 방지 — 매 실행이 직전 데모 자산을 먼저 정리한다. 상세는 scripts/demo_foundry.py.
#
# 선행조건(발표 전 준비): .env(FOUNDRY_TOKEN·FOUNDRY_HOSTNAME) · .venv312(foundry_sdk 1.97+) ·
# Palantir 로그인 탭 + Object Explorer를 미리 열어둘 것(demo.md §3 · P6-demo.md §6).
#
# 실패 안전: Foundry 연결 실패(exit 3)면 이 세그먼트만 스킵하고 scripts/demo.sh replay로 계속.
# OpenSky/네트워크만 실패하면 (a)만 건너뛰고 (b)(c)는 진행(이상징후·confirm 서사 보존).
# OpenSky 호출은 실행당 1회. 토큰 값은 출력하지 않는다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv312/bin/python"   # Foundry SDK는 Python 3.12 환경(.venv312)에만 있음
if [ ! -x "$PY" ]; then
  echo "오류: $PY 없음 — .venv312(foundry_sdk)가 준비돼야 합니다." >&2
  echo "폴백: 이 세그먼트를 스킵하고 scripts/demo.sh replay 로 로컬 데모를 진행하세요." >&2
  exit 3
fi
if [ ! -f "$ROOT/.env" ]; then
  echo "오류: .env 없음 — FOUNDRY_TOKEN·FOUNDRY_HOSTNAME 필요." >&2
  echo "폴백: scripts/demo.sh replay 로 로컬 데모를 진행하세요." >&2
  exit 3
fi

MODE="${1:-run}"
# set -e 하에서도 rc를 회수하려면 || 로 errexit를 억제(비정상 종료 시 폴백 안내를 찍기 위함).
rc=0
PYTHONPATH="$ROOT" SKAI_STORE=foundry "$PY" -m scripts.demo_foundry "$MODE" || rc=$?

if [ "$rc" -ne 0 ] && [ "$MODE" = "run" ]; then
  echo ""
  echo "※ demo_foundry 비정상 종료(rc=$rc). 발표는 scripts/demo.sh replay 로 이어가세요."
fi
exit "$rc"
