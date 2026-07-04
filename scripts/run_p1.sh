#!/usr/bin/env bash
# run_p1.sh — P1 폴러 + 서버 기동/중지 (개발용)
#
# 사용법:
#   scripts/run_p1.sh start   폴러(항적 수집) + 서버(지도) 백그라운드 기동
#   scripts/run_p1.sh stop    둘 다 중지
#   scripts/run_p1.sh status  실행 상태 확인
#
# 환경변수(선택):
#   POLL_INTERVAL  폴링 간격(초). 기본 15.
#   MAX_CYCLES     폴러 사이클 수. 기본 4(3~4 후 자동 종료 = 러너웨이 방지).
#                  연속 수집을 원하면 MAX_CYCLES=0 (무한, 반드시 stop 으로 중지).
#   SKAI_PORT      서버 포트. 기본 8000.
#
# 지도: http://localhost:${SKAI_PORT:-8000}
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
DATA="$ROOT/data"
mkdir -p "$DATA"
SERVER_PID="$DATA/server.pid"
POLLER_PID="$DATA/poller.pid"
SERVER_LOG="$DATA/server.log"
POLLER_LOG="$DATA/poller.log"

PORT="${SKAI_PORT:-8000}"

start() {
  # 서버 (지속) — 지도 + API
  if [ -f "$SERVER_PID" ] && kill -0 "$(cat "$SERVER_PID")" 2>/dev/null; then
    echo "서버 이미 실행 중 (pid $(cat "$SERVER_PID"))"
  else
    SKAI_PORT="$PORT" nohup "$PY" -m server.app >"$SERVER_LOG" 2>&1 &
    echo $! >"$SERVER_PID"
    echo "서버 기동: pid $(cat "$SERVER_PID")  → http://localhost:$PORT  (로그: $SERVER_LOG)"
  fi

  # 폴러 (기본 4사이클 후 자동 종료) — 항적 수집
  POLL_INTERVAL="${POLL_INTERVAL:-15}" MAX_CYCLES="${MAX_CYCLES:-4}" \
    nohup "$PY" -m connectors.opensky >"$POLLER_LOG" 2>&1 &
  echo $! >"$POLLER_PID"
  echo "폴러 기동: pid $(cat "$POLLER_PID")  interval=${POLL_INTERVAL:-15}s max_cycles=${MAX_CYCLES:-4}  (로그: $POLLER_LOG)"
  echo ""
  echo "중지: scripts/run_p1.sh stop"
}

stop() {
  for f in "$POLLER_PID" "$SERVER_PID"; do
    if [ -f "$f" ]; then
      pid="$(cat "$f")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "중지: pid $pid ($(basename "$f"))"
      fi
      rm -f "$f"
    fi
  done
}

status() {
  for f in "$SERVER_PID" "$POLLER_PID"; do
    if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then
      echo "실행 중: $(basename "$f") pid $(cat "$f")"
    else
      echo "정지: $(basename "$f")"
    fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "사용법: $0 {start|stop|status}"; exit 1 ;;
esac
