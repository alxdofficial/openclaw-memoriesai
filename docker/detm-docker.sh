#!/usr/bin/env bash
set -euo pipefail

# ─── DETM Docker helper ─────────────────────────────────────────
# Usage:
#   detm-docker.sh start   [-d DATA_DIR] [-p DAEMON_PORT] [-n NOVNC_PORT]
#   detm-docker.sh stop
#   detm-docker.sh restart
#   detm-docker.sh status
#   detm-docker.sh logs [-f]
#   detm-docker.sh build
#   detm-docker.sh shell

CONTAINER_NAME="detm"
IMAGE_NAME="detm:latest"
DEFAULT_DATA_DIR="$HOME/.agentic-computer-use"
DEFAULT_DAEMON_PORT=18790
DEFAULT_NOVNC_PORT=6080

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[detm]${NC} $*"; }
ok()    { echo -e "${GREEN}[detm]${NC} $*"; }
err()   { echo -e "${RED}[detm]${NC} $*"; }
warn()  { echo -e "${YELLOW}[detm]${NC} $*"; }

# ─── Resolve repo root (script lives in docker/) ────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

_is_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER_NAME"
}

_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER_NAME"
}

cmd_build() {
    info "Building DETM Docker image..."
    docker build -t "$IMAGE_NAME" -f "$REPO_DIR/docker/Dockerfile" "$REPO_DIR"
    ok "Image built: $IMAGE_NAME"
}

cmd_start() {
    local data_dir="${DETM_DATA_DIR:-$DEFAULT_DATA_DIR}"
    local daemon_port="${DETM_DAEMON_PORT:-$DEFAULT_DAEMON_PORT}"
    local novnc_port="${DETM_NOVNC_PORT:-$DEFAULT_NOVNC_PORT}"

    # Parse args
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -d|--data)    data_dir="$2"; shift 2 ;;
            -p|--port)    daemon_port="$2"; shift 2 ;;
            -n|--novnc)   novnc_port="$2"; shift 2 ;;
            *) err "Unknown option: $1"; exit 1 ;;
        esac
    done

    if _is_running; then
        warn "Container '$CONTAINER_NAME' is already running"
        cmd_status
        return 0
    fi

    # Remove stopped container if it exists
    if _exists; then
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1
    fi

    # Check if image exists
    if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        info "Image not found. Building..."
        cmd_build
    fi

    mkdir -p "$data_dir"

    info "Starting DETM container..."
    info "  Data:      $data_dir"
    info "  Daemon:    http://127.0.0.1:$daemon_port"
    info "  Dashboard: http://127.0.0.1:$daemon_port/dashboard"
    info "  noVNC:     http://127.0.0.1:$novnc_port/vnc.html"

    # Pass through API keys from environment
    local env_args=()
    [ -n "${OPENROUTER_API_KEY:-}" ]  && env_args+=(-e "OPENROUTER_API_KEY=$OPENROUTER_API_KEY")
    [ -n "${ANTHROPIC_API_KEY:-}" ]   && env_args+=(-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")
    [ -n "${MAVI_API_KEY:-}" ]        && env_args+=(-e "MAVI_API_KEY=$MAVI_API_KEY")
    [ -n "${OLLAMA_URL:-}" ]          && env_args+=(-e "OLLAMA_URL=$OLLAMA_URL")

    # Pass through ACU_* config
    while IFS='=' read -r key val; do
        env_args+=(-e "$key=$val")
    done < <(env | grep '^ACU_' || true)

    docker run -d \
        --name "$CONTAINER_NAME" \
        -p "$daemon_port:18790" \
        -p "$novnc_port:6080" \
        -v "$data_dir:/data" \
        "${env_args[@]}" \
        --restart unless-stopped \
        "$IMAGE_NAME"

    # Wait for health
    info "Waiting for daemon..."
    for i in $(seq 1 30); do
        if curl -s "http://127.0.0.1:$daemon_port/health" 2>/dev/null | grep -q '"ok"'; then
            ok "DETM is ready"
            return 0
        fi
        sleep 1
    done
    warn "Daemon may still be starting. Check: docker logs $CONTAINER_NAME"
}

cmd_stop() {
    if _is_running; then
        info "Stopping DETM..."
        docker stop "$CONTAINER_NAME" >/dev/null
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1
        ok "Stopped"
    else
        info "Not running"
    fi
}

cmd_restart() {
    cmd_stop
    cmd_start "$@"
}

cmd_status() {
    if _is_running; then
        ok "DETM is running"
        docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Status}}\t{{.Ports}}"
        # Health check
        local health
        health=$(curl -s "http://127.0.0.1:${DETM_DAEMON_PORT:-$DEFAULT_DAEMON_PORT}/health" 2>/dev/null || echo "{}")
        echo "  Health: $health"
    else
        info "DETM is not running"
        return 1
    fi
}

cmd_logs() {
    if _exists; then
        docker logs "$@" "$CONTAINER_NAME"
    else
        err "No container found"
        return 1
    fi
}

cmd_shell() {
    if _is_running; then
        docker exec -it "$CONTAINER_NAME" bash
    else
        err "Container is not running. Start it first."
        return 1
    fi
}

# ─── Main ────────────────────────────────────────────────────────
case "${1:-help}" in
    build)   cmd_build ;;
    start)   shift; cmd_start "$@" ;;
    stop)    cmd_stop ;;
    restart) shift; cmd_restart "$@" ;;
    status)  cmd_status ;;
    logs)    shift; cmd_logs "$@" ;;
    shell)   cmd_shell ;;
    help|*)
        echo "Usage: $0 {build|start|stop|restart|status|logs|shell}"
        echo ""
        echo "Environment variables:"
        echo "  DETM_DATA_DIR      Data directory (default: ~/.agentic-computer-use)"
        echo "  DETM_DAEMON_PORT   Daemon port (default: 18790)"
        echo "  DETM_NOVNC_PORT    noVNC port (default: 6080)"
        echo "  OPENROUTER_API_KEY Pass to container for cloud vision/grounding"
        echo "  ANTHROPIC_API_KEY  Pass to container for Claude vision backend"
        echo "  ACU_*              All ACU_ variables forwarded to container"
        ;;
esac
