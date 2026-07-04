#!/usr/bin/env bash
# docker-entrypoint-demo.sh — Dockerfile.demo 컨테이너 시작점.
#
# scripts/demo.sh replay가 로컬에서 하는 일(결정적 데모 DB 재생성 → now 앵커 고정 →
# SKAI_OFFLINE=1 서버 기동)을 컨테이너 시작 시 그대로 재현한다. 컨테이너를 새로
# 띄울 때마다 데모 DB를 갈아엎으므로 항상 같은 앵커 = 같은 산출(재현성).
#
# 포트: $PORT(Render/Railway/Fly가 주입) 우선, 없으면 $SKAI_PORT, 그것도 없으면 8000.
set -euo pipefail

cd /app

export PYTHONHASHSEED=0
export SKAI_OFFLINE=1
export SKAI_HOST="${SKAI_HOST:-0.0.0.0}"
export SKAI_PORT="${PORT:-${SKAI_PORT:-8000}}"

DEMO_DIR="/app/data/demo"
DEMO_DB="$DEMO_DIR/skai_demo.db"
mkdir -p "$DEMO_DIR"
rm -f "$DEMO_DB" "$DEMO_DB-shm" "$DEMO_DB-wal"

# now 앵커 SSOT = eval.run_eval.EVAL_NOW(demo.sh와 동일 계약). 실패 시 하드코딩 폴백.
ANCHOR="${SKAI_DEMO_ANCHOR:-$(python -c 'from eval.run_eval import EVAL_NOW; print(EVAL_NOW)' 2>/dev/null || echo 1783000000)}"

echo "[entrypoint] replay 데모 DB 주입(scenario=all, anchor=$ANCHOR) -> $DEMO_DB"
python -m scripts.inject_synthetic --scenario all --now "$ANCHOR" --db "$DEMO_DB"

export SKAI_NOW_ANCHOR="$ANCHOR"
export SKAI_DB="$DEMO_DB"

echo "[entrypoint] 서버 기동 host=$SKAI_HOST port=$SKAI_PORT (network 0 · offline guard)"
exec python -m server.app
