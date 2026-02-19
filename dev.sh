#!/usr/bin/env bash
# dev.sh — run DETM daemon in a tmux session with live debug logs
#
# Usage:
#   ./dev.sh          — start (or reattach to) the dev session
#   ./dev.sh stop     — kill the session
#   ./dev.sh logs     — tail the log file in the current terminal
#   ./dev.sh status   — show daemon HTTP health
#
# Logs are visible to both you and Claude Code:
#   • You   → the tmux session shows colored live logs
#   • Claude→ reads ~/.agentic-computer-use/logs/debug.log

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$REPO_DIR/.venv/bin/python3"
SESSION="detm-dev"
DAEMON_PORT=18790
LOG_FILE="${ACU_DATA_DIR:-$HOME/.agentic-computer-use}/logs/debug.log"

# ── helpers ──────────────────────────────────────────────────────────────────

color() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
info()  { color "36" "  $*"; }
ok()    { color "32" "✓ $*"; }
err()   { color "31" "✗ $*"; exit 1; }

session_running() { tmux has-session -t "$SESSION" 2>/dev/null; }

# ── subcommands ───────────────────────────────────────────────────────────────

cmd_stop() {
    if session_running; then
        tmux kill-session -t "$SESSION"
        ok "Session '$SESSION' stopped."
    else
        info "Session '$SESSION' is not running."
    fi
}

cmd_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        info "Log file not yet created: $LOG_FILE"
        info "Start the daemon first: ./dev.sh"
        exit 0
    fi
    info "Tailing $LOG_FILE  (Ctrl-C to stop)"
    tail -f "$LOG_FILE"
}

cmd_status() {
    echo ""
    info "DETM daemon health check (port $DAEMON_PORT):"
    curl -s "http://127.0.0.1:$DAEMON_PORT/health" 2>/dev/null | python3 -m json.tool || \
        color "31" "  Daemon not responding on port $DAEMON_PORT"
    echo ""
    if session_running; then
        ok "tmux session '$SESSION' is running"
        info "Attach: tmux attach -t $SESSION"
    else
        color "33" "  tmux session '$SESSION' is not running"
    fi
}

cmd_start() {
    if session_running; then
        info "Killing existing session '$SESSION'..."
        tmux kill-session -t "$SESSION"
        sleep 0.5
    fi

    if lsof -ti tcp:$DAEMON_PORT &>/dev/null 2>&1; then
        info "Killing stale process on port $DAEMON_PORT..."
        kill "$(lsof -ti tcp:$DAEMON_PORT)" 2>/dev/null || true
        sleep 0.5
    fi

    mkdir -p "$(dirname "$LOG_FILE")"

    RUN_CMD="ACU_DEBUG=1 DISPLAY=${DISPLAY:-:99} PYTHONPATH=$REPO_DIR/src $VENV -m agentic_computer_use.daemon"

    tmux new-session -d -s "$SESSION" -x 220 -y 50 \; \
        send-keys "$RUN_CMD" Enter

    sleep 1

    if session_running; then
        echo ""
        ok "DETM daemon started in tmux session '$SESSION'"
        echo ""
        info "Attach to watch live:    tmux attach -t $SESSION"
        info "Detach (stay running):   Ctrl-B  D"
        info "Tail log file:           ./dev.sh logs"
        info "Health check:            ./dev.sh status"
        info "Stop:                    ./dev.sh stop"
        echo ""
        info "Log file (for Claude):   $LOG_FILE"
        echo ""
    else
        err "Failed to start tmux session. Check: tmux ls"
    fi
}

# ── dispatch ──────────────────────────────────────────────────────────────────

case "${1:-start}" in
    start)  cmd_start  ;;
    stop)   cmd_stop   ;;
    logs)   cmd_logs   ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 {start|stop|logs|status}"
        exit 1
        ;;
esac
