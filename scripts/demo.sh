#!/usr/bin/env bash
# demo.sh — P6 발표 데모 이중 모드 (DR-0008).
#
#   scripts/demo.sh replay   네트워크 0 재생 — 데모 전용 DB에 선언적 시나리오 전체 주입 +
#                            now 앵커링 + 서버만 기동. 오프라인에서도 즉시 동작(발표 기본).
#   scripts/demo.sh live     라이브 — OpenSky 폴러 + 서버 기동(실 API) + 내러티브 합성 가미.
#                            네트워크·API 정상일 때 임팩트용 오프닝.
#   scripts/demo.sh stop     두 모드의 모든 프로세스 중지 + pid 정리.
#   scripts/demo.sh status   실행 상태.
#
# 환경변수(선택):
#   SKAI_PORT          서버 포트. 기본 8000.
#   SKAI_DEMO_ANCHOR   replay now 앵커(초). 기본 = eval.EVAL_NOW(SSOT). 재현성 위해 고정.
#   SKAI_POLL_INTERVAL live 폴링 간격(초). 기본 25(하한 10). 크레딧 안전.
#   LIVE_MAX_CYCLES    live 폴러 사이클 수. 기본 0=연속(무한, DR-0011). 유한값은 검증용.
#
# 격리: replay는 data/demo/skai_demo.db(런타임 data/skai.db와 분리). replay는 SKAI_OFFLINE=1로
# 외부 egress를 소켓 레벨 차단(network 0 증명). 재생 질의는 web 프리셋 3개와 동일하다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
DATA="$ROOT/data"
DEMO_DIR="$DATA/demo"
DEMO_DB="$DEMO_DIR/skai_demo.db"
mkdir -p "$DEMO_DIR"

SERVER_PID="$DATA/demo_server.pid"       # replay/live 서버 공용 pid
POLLER_PID="$DATA/demo_poller.pid"       # live 폴러 pid
SERVER_LOG="$DATA/demo_server.log"
POLLER_LOG="$DATA/demo_poller.log"

PORT="${SKAI_PORT:-8000}"

# now 앵커 SSOT = eval.EVAL_NOW(합성 시나리오가 이 시각 상대). 실패 시 하드코딩 폴백.
ANCHOR="${SKAI_DEMO_ANCHOR:-$("$PY" -c 'from eval.run_eval import EVAL_NOW; print(EVAL_NOW)' 2>/dev/null || echo 1783000000)}"

# 발표 질의(web 프리셋과 동일 — 딥링크 재현·리허설용).
Q1="지금 KADIZ 근방 이상한 거 있어?"
Q2="최근 1시간 위성 통과랑 겹치는 이상징후는?"
Q3="서해 쪽 기상이랑 뉴스 맥락 요약해줘"

_urlencode() { "$PY" -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1"; }

_stop_pidfile() {
  local f="$1"
  if [ -f "$f" ]; then
    local pid; pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "중지: pid $pid ($(basename "$f"))"
    fi
    rm -f "$f"
  fi
}

_wait_health() {
  # 서버가 뜰 때까지 최대 ~10초 폴링(로컬 루프백만 — 오프라인 무관).
  for _ in $(seq 1 50); do
    if curl -sf "http://127.0.0.1:$PORT/api/stats" >/dev/null 2>&1; then return 0; fi
    sleep 0.2
  done
  return 1
}

_print_deeplinks() {
  echo ""
  echo "발표 딥링크(질의 자동 실행 + 서브그래프 자동 오픈):"
  echo "  ① http://localhost:$PORT/?q=$(_urlencode "$Q1")"
  echo "  ② http://localhost:$PORT/?q=$(_urlencode "$Q2")"
  echo "  ③ http://localhost:$PORT/?q=$(_urlencode "$Q3")&sg=1"
  echo "  지도: http://localhost:$PORT"
}

replay() {
  echo "=== REPLAY (네트워크 0 · now 앵커 $ANCHOR) ==="
  # set 반복순서를 고정 → 응답/서브그래프 직렬화 순서까지 바이트 단위 결정적(스크린샷 안정·
  # 연속 실행 동일). 내용 자체는 hashseed와 무관하게 결정적(정규화 비교로 별도 증명).
  export PYTHONHASHSEED=0
  stop  # 기존 데모 프로세스 정리
  # 데모 DB를 매번 새로 빌드(결정적 — 같은 앵커 = 같은 산출).
  rm -f "$DEMO_DB" "$DEMO_DB-shm" "$DEMO_DB-wal"
  echo "시나리오 전체 주입(all) → $DEMO_DB"
  "$PY" -m scripts.inject_synthetic --scenario all --now "$ANCHOR" --db "$DEMO_DB" \
    | sed 's/^/  /'

  # 서버만 기동 — SKAI_OFFLINE=1(외부 egress 차단), SKAI_NOW_ANCHOR(질의 now 고정), 데모 DB.
  SKAI_OFFLINE=1 SKAI_NOW_ANCHOR="$ANCHOR" SKAI_DB="$DEMO_DB" SKAI_PORT="$PORT" \
    nohup "$PY" -m server.app >"$SERVER_LOG" 2>&1 &
  echo $! >"$SERVER_PID"
  echo "서버 기동: pid $(cat "$SERVER_PID")  (offline·anchored, 로그: $SERVER_LOG)"
  if _wait_health; then echo "헬스체크 OK (/api/stats 응답)"; else
    echo "경고: 헬스체크 실패 — 로그 확인($SERVER_LOG)"; fi
  _print_deeplinks
  echo ""
  echo "중지: scripts/demo.sh stop"
}

live() {
  echo "=== LIVE (실 API 폴링 + 내러티브 합성 가미) ==="
  stop
  local live_db="$DATA/skai.db"          # 런타임 DB(데모 DB와 분리)
  local now; now="$(date +%s)"

  # 서버 — 벽시계 now(정직한 '지금', 앵커 없음), 오프라인 아님(라이브 fetch 필요).
  SKAI_DB="$live_db" SKAI_PORT="$PORT" \
    nohup "$PY" -m server.app >"$SERVER_LOG" 2>&1 &
  echo $! >"$SERVER_PID"
  echo "서버 기동: pid $(cat "$SERVER_PID")  → http://localhost:$PORT"

  # 라이브 연속 폴러(DR-0011). 기본 무한(MAX_CYCLES=0)·간격 25s(하한 10s). stop이 SIGTERM으로
  # 정리 종료(러너웨이 방지). 사이클마다 last_poll_ts를 사이드카에 기록 → /api/live LIVE 표시.
  SKAI_POLL_INTERVAL="${SKAI_POLL_INTERVAL:-25}" MAX_CYCLES="${LIVE_MAX_CYCLES:-0}" \
    SKAI_DB="$live_db" nohup "$PY" -m connectors.opensky >"$POLLER_LOG" 2>&1 &
  echo $! >"$POLLER_PID"
  echo "연속 폴러 기동: pid $(cat "$POLLER_PID")  interval=${SKAI_POLL_INTERVAL:-25}s max_cycles=${LIVE_MAX_CYCLES:-0}(0=연속) (로그: $POLLER_LOG)"

  # 내러티브 합성 가미 — 라이브 KADIZ엔 이상징후가 상시 없으므로(재현성) '지금' 창에
  # 은닉 정황 1건을 주입해 데모 서사를 보장한다(실 항적과 공존). now=현재 시각.
  echo "내러티브 합성 주입(narrative_hidden · now=$now) → $live_db"
  SKAI_DB="$live_db" "$PY" -m scripts.inject_synthetic \
    --scenario narrative_hidden --now "$now" --db "$live_db" | sed 's/^/  /' || true

  if _wait_health; then echo "헬스체크 OK"; else echo "경고: 헬스체크 실패"; fi
  _print_deeplinks
  echo ""
  echo "라이브 API 실패 시 → scripts/demo.sh replay 로 즉시 전환(네트워크 0)."
  echo "중지: scripts/demo.sh stop"
}

stop() {
  _stop_pidfile "$POLLER_PID"
  _stop_pidfile "$SERVER_PID"
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
  replay) replay ;;
  live) live ;;
  stop) stop ;;
  status) status ;;
  *) echo "사용법: $0 {replay|live|stop|status}"; exit 1 ;;
esac
