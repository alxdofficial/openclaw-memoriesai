#!/usr/bin/env bash
set -euo pipefail

# ─── agentic-computer-use (DETM) installer ──────────────────────
# Usage:
#   ./install.sh              # auto-detect: Docker if available, bare-metal on Linux
#   ./install.sh --docker     # force Docker mode
#   ./install.sh --bare-metal # force bare-metal mode (Linux only)
#
# Environment:
#   OPENROUTER_API_KEY    — required for cloud vision/grounding (recommended)
#   ANTHROPIC_API_KEY     — required for Claude vision backend
#   DETM_DATA_DIR         — data directory (default: ~/.agentic-computer-use)
#   DETM_DAEMON_PORT      — daemon port (default: 18790)
#   DETM_NOVNC_PORT       — noVNC port (default: 6080)

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON_PORT="${DETM_DAEMON_PORT:-18790}"
NOVNC_PORT="${DETM_NOVNC_PORT:-6080}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[detm]${NC} $*"; }
ok()    { echo -e "${GREEN}[detm]${NC} $*"; }
warn()  { echo -e "${YELLOW}[detm]${NC} $*"; }
err()   { echo -e "${RED}[detm]${NC} $*"; }

# ─── Mode selection ──────────────────────────────────────────────

MODE=""
for arg in "$@"; do
    case "$arg" in
        --docker)     MODE="docker" ;;
        --bare-metal) MODE="bare-metal" ;;
        --help|-h)
            echo "Usage: $0 [--docker|--bare-metal]"
            echo ""
            echo "  --docker       Force Docker mode (works on any OS)"
            echo "  --bare-metal   Force bare-metal mode (Linux only)"
            echo "  (default)      Auto-detect: Docker if available, bare-metal on Linux"
            exit 0
            ;;
    esac
done

if [ -z "$MODE" ]; then
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        # Docker available — use it unless we're on Linux with a real display
        if [ "$(uname)" = "Linux" ] && [ -n "${DISPLAY:-}" ] && xdpyinfo &>/dev/null 2>&1; then
            info "Linux with real display detected. Using bare-metal mode."
            info "Use --docker to force Docker mode."
            MODE="bare-metal"
        else
            MODE="docker"
        fi
    elif [ "$(uname)" = "Linux" ]; then
        MODE="bare-metal"
    else
        err "Docker is required on $(uname). Install Docker and retry."
        err "  macOS: brew install --cask docker"
        err "  Windows: https://docs.docker.com/desktop/install/windows-install/"
        exit 1
    fi
fi

info "Install mode: $MODE"

# ─── API key check ───────────────────────────────────────────────

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    warn "OPENROUTER_API_KEY is not set."
    echo ""
    echo "  DETM needs an OpenRouter API key for:"
    echo "    - GUI agent (Gemini Flash supervisor)"
    echo "    - UI-TARS grounding (precise cursor placement)"
    echo "    - Cloud vision (SmartWait screen polling)"
    echo ""
    echo "  Get one free at: https://openrouter.ai/keys"
    echo ""
    if [ -t 0 ]; then
        read -rp "  Enter your OpenRouter API key (or press Enter to skip): " user_key
        if [ -n "$user_key" ]; then
            export OPENROUTER_API_KEY="$user_key"
            ok "API key set"
        else
            warn "Skipping — GUI agent and cloud vision will not work."
            warn "Set OPENROUTER_API_KEY later and restart the daemon."
        fi
    else
        warn "Non-interactive shell — set OPENROUTER_API_KEY and re-run."
    fi
fi

# ═══════════════════════════════════════════════════════════════════
# DOCKER MODE
# ═══════════════════════════════════════════════════════════════════

install_docker() {
    info "Building DETM Docker image..."
    docker build -t detm:latest -f "$REPO_DIR/docker/Dockerfile" "$REPO_DIR"
    ok "Docker image built"

    # Start container
    info "Starting DETM container..."
    "$REPO_DIR/docker/detm-docker.sh" start
}

# ═══════════════════════════════════════════════════════════════════
# BARE-METAL MODE
# ═══════════════════════════════════════════════════════════════════

install_bare_metal() {
    local VENV_DIR="$REPO_DIR/.venv"
    local SERVICE_NAME="detm-daemon"
    local OLLAMA_MODEL="minicpm-v"
    local DETM_DISPLAY=":99"

    # ─── Python ──────────────────────────────────────────────
    local PYTHON=""
    for p in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$p" &>/dev/null; then
            local ver major minor
            ver=$("$p" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON="$p"
                break
            fi
        fi
    done
    if [ -z "$PYTHON" ]; then
        err "Python 3.11+ required. Install it and retry."
        exit 1
    fi
    ok "Python: $($PYTHON --version)"

    # ─── Display detection ───────────────────────────────────
    local VIRTUAL_DISPLAY=false

    _has_real_display() {
        for d in "${DISPLAY:-}" :0 :1 :2; do
            [ -z "$d" ] && continue
            if DISPLAY="$d" xdpyinfo &>/dev/null 2>&1; then
                echo "$d"; return 0
            fi
        done
        return 1
    }

    if REAL_DISP=$(_has_real_display); then
        DETM_DISPLAY="$REAL_DISP"
        ok "Real display found: $DETM_DISPLAY"
    else
        info "No real display — setting up virtual display..."
        VIRTUAL_DISPLAY=true

        VNC_PKGS=()
        command -v x11vnc    &>/dev/null || VNC_PKGS+=("x11vnc")
        command -v websockify &>/dev/null || VNC_PKGS+=("novnc")
        if [ ${#VNC_PKGS[@]} -gt 0 ]; then
            info "Installing: ${VNC_PKGS[*]}"
            if command -v apt &>/dev/null; then
                sudo apt install -y "${VNC_PKGS[@]}"
            else
                warn "Cannot auto-install ${VNC_PKGS[*]} — install manually"
            fi
        fi

        sudo tee /etc/systemd/system/detm-xvfb.service > /dev/null <<SVCEOF
[Unit]
Description=DETM Virtual Display (Xvfb $DETM_DISPLAY)
After=network.target

[Service]
Type=simple
User=$(whoami)
ExecStart=/usr/bin/Xvfb $DETM_DISPLAY -screen 0 1920x1080x24 -nolisten tcp
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SVCEOF

        sudo tee /etc/systemd/system/detm-vnc.service > /dev/null <<SVCEOF
[Unit]
Description=DETM VNC Server
After=detm-xvfb.service
Requires=detm-xvfb.service

[Service]
Type=simple
User=$(whoami)
Environment=DISPLAY=$DETM_DISPLAY
ExecStart=/usr/bin/x11vnc -display $DETM_DISPLAY -nopw -listen 127.0.0.1 -forever -shared -rfbport 5901
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SVCEOF

        local NOVNC_WEB=""
        for p in /usr/share/novnc /usr/share/novnc/utils; do
            [ -d "$p" ] && NOVNC_WEB="$p" && break
        done
        local NOVNC_CMD="websockify"
        [ -n "$NOVNC_WEB" ] && NOVNC_CMD="websockify --web=$NOVNC_WEB"

        sudo tee /etc/systemd/system/detm-novnc.service > /dev/null <<SVCEOF
[Unit]
Description=DETM noVNC Web Client
After=detm-vnc.service
Requires=detm-vnc.service

[Service]
Type=simple
User=$(whoami)
ExecStart=/usr/bin/$NOVNC_CMD $NOVNC_PORT 127.0.0.1:5901
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SVCEOF

        sudo systemctl daemon-reload
        for svc in detm-xvfb detm-vnc detm-novnc; do
            sudo systemctl enable "$svc"
            sudo systemctl restart "$svc"
        done
        sleep 1

        if DISPLAY="$DETM_DISPLAY" xdpyinfo &>/dev/null 2>&1; then
            ok "Virtual display ready: $DETM_DISPLAY"
        else
            warn "Virtual display may still be starting"
        fi
    fi

    export DISPLAY="$DETM_DISPLAY"

    # ─── Python package ─────────────────────────────────────
    info "Setting up Python environment..."
    if [ ! -d "$VENV_DIR" ]; then
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_DIR"
    ok "Python package installed"

    # ─── System dependencies ─────────────────────────────────
    local MISSING_PKGS=()
    command -v xdotool &>/dev/null || MISSING_PKGS+=("xdotool")
    command -v ffmpeg &>/dev/null  || MISSING_PKGS+=("ffmpeg")
    command -v fluxbox &>/dev/null || MISSING_PKGS+=("fluxbox")
    command -v Xvfb &>/dev/null    || MISSING_PKGS+=("xvfb")
    command -v scrot &>/dev/null   || MISSING_PKGS+=("scrot")

    if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
        info "Installing: ${MISSING_PKGS[*]}"
        if command -v apt &>/dev/null; then
            sudo apt install -y "${MISSING_PKGS[@]}"
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y "${MISSING_PKGS[@]}"
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm "${MISSING_PKGS[@]}"
        else
            warn "Cannot auto-install: ${MISSING_PKGS[*]}"
        fi
    fi
    ok "System dependencies ready"

    # ─── Ollama (optional) ───────────────────────────────────
    if [ -z "${ACU_VISION_BACKEND:-}" ] || [ "${ACU_VISION_BACKEND:-}" = "ollama" ]; then
        if ! command -v ollama &>/dev/null; then
            info "Installing Ollama..."
            curl -fsSL https://ollama.com/install.sh | sh
        fi
        ok "Ollama: $(ollama --version 2>/dev/null || echo 'installed')"

        if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
            info "Pulling vision model ($OLLAMA_MODEL)..."
            ollama pull "$OLLAMA_MODEL"
        fi
        ok "Vision model ready: $OLLAMA_MODEL"
    fi

    # ─── Systemd daemon service ──────────────────────────────
    info "Installing systemd service..."
    sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<SVCEOF
[Unit]
Description=DETM Daemon
After=network.target ollama.service

[Service]
Type=simple
User=$(whoami)
Environment=DISPLAY=$DISPLAY
Environment=PYTHONPATH=$REPO_DIR/src
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_DIR/bin/python3 -m agentic_computer_use.daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Daemon running on port $DAEMON_PORT"
    else
        err "Daemon failed to start. Check: journalctl -u $SERVICE_NAME"
        exit 1
    fi
}

# ═══════════════════════════════════════════════════════════════════
# OPENCLAW INTEGRATION (shared by both modes)
# ═══════════════════════════════════════════════════════════════════

configure_openclaw() {
    info "Configuring OpenClaw integration..."

    local OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
    local MCPORTER_CONFIG="$OC_WORKSPACE/config/mcporter.json"

    # Find OpenClaw binary
    local OPENCLAW_BIN=""
    for p in "$HOME/.npm-global/bin/openclaw" "$(which openclaw 2>/dev/null)" "/usr/local/bin/openclaw"; do
        if [ -x "$p" ] 2>/dev/null; then
            OPENCLAW_BIN="$p"
            break
        fi
    done

    if [ -n "$OPENCLAW_BIN" ]; then
        ok "OpenClaw found: $OPENCLAW_BIN"
    else
        warn "OpenClaw CLI not found. Configure mcporter manually if needed."
    fi

    mkdir -p "$(dirname "$MCPORTER_CONFIG")"

    # Configure MCP server in mcporter
    if [ "$MODE" = "docker" ]; then
        # Docker mode: MCP server runs on host, proxies to container
        local MCP_CMD
        local MCP_ARGS
        local MCP_ENV

        if [ -d "$REPO_DIR/.venv" ]; then
            MCP_CMD="$REPO_DIR/.venv/bin/python3"
        else
            MCP_CMD="python3"
        fi

        python3 -c "
import json, os

config_path = '$MCPORTER_CONFIG'
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['agentic-computer-use'] = {
    'command': '$MCP_CMD',
    'args': ['-m', 'agentic_computer_use.server'],
    'cwd': '$REPO_DIR',
    'env': {
        'DISPLAY': ':99',
        'PYTHONPATH': '$REPO_DIR/src',
        'ACU_DAEMON_URL': 'http://127.0.0.1:$DAEMON_PORT'
    }
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print(f'Written: {config_path}')
"
    else
        # Bare-metal mode: MCP server runs from venv
        local VENV_DIR="$REPO_DIR/.venv"
        python3 -c "
import json, os

config_path = '$MCPORTER_CONFIG'
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['agentic-computer-use'] = {
    'command': '$VENV_DIR/bin/python3',
    'args': ['-m', 'agentic_computer_use.server'],
    'cwd': '$REPO_DIR',
    'env': {
        'DISPLAY': '${DISPLAY:-:99}',
        'PYTHONPATH': '$REPO_DIR/src'
    }
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print(f'Written: {config_path}')
"
    fi
    ok "mcporter config updated"

    # Symlink skill
    local SKILL_LINK="$OC_WORKSPACE/skills/agentic-computer-use"
    if [ -L "$SKILL_LINK" ]; then
        rm "$SKILL_LINK"
    elif [ -d "$SKILL_LINK" ]; then
        rm -rf "$SKILL_LINK"
    fi
    if [ -d "$REPO_DIR/skill" ]; then
        mkdir -p "$(dirname "$SKILL_LINK")"
        ln -s "$REPO_DIR/skill" "$SKILL_LINK"
        ok "Skill symlinked: $SKILL_LINK"
    fi

    # Deploy sub-agents
    if [ -f "$REPO_DIR/scripts/deploy-agents.sh" ]; then
        # shellcheck source=scripts/deploy-agents.sh
        local VENV_PYTHON="${REPO_DIR}/.venv/bin/python3"
        [ ! -x "$VENV_PYTHON" ] && VENV_PYTHON="python3"
        OC_WORKSPACE="$OC_WORKSPACE" VENV_PYTHON="$VENV_PYTHON" source "$REPO_DIR/scripts/deploy-agents.sh"
        if [ -d "${AGENTS_SRC:-}" ]; then
            info "Deploying sub-agents..."
            deploy_agents
            register_agents_config
            ok "Sub-agents deployed"
        fi
    fi

    # Install plugin
    local PLUGIN_DIR="$REPO_DIR/plugins/detm-tool-logger"
    if [ -d "$PLUGIN_DIR" ] && [ -n "$OPENCLAW_BIN" ]; then
        if "$OPENCLAW_BIN" plugins list 2>/dev/null | grep -q "detm-tool-logger"; then
            ok "Plugin already installed: detm-tool-logger"
        else
            "$OPENCLAW_BIN" plugins install --link "$PLUGIN_DIR" 2>/dev/null && ok "Plugin installed: detm-tool-logger" || warn "Could not install detm-tool-logger plugin"
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════════
# VERIFY & SUMMARY
# ═══════════════════════════════════════════════════════════════════

verify_and_summarize() {
    info "Running health check..."
    local HEALTH
    HEALTH=$(curl -s "http://127.0.0.1:$DAEMON_PORT/health" 2>/dev/null || echo '{}')
    if echo "$HEALTH" | grep -q '"ok"'; then
        ok "Health check passed"
    else
        warn "Health check failed — daemon may still be starting"
    fi

    echo ""
    echo -e "${GREEN}================================================${NC}"
    echo -e "${GREEN}  agentic-computer-use installed successfully    ${NC}"
    echo -e "${GREEN}================================================${NC}"
    echo ""
    echo "  Mode:      $MODE"
    echo "  Daemon:    http://127.0.0.1:$DAEMON_PORT"
    echo "  Dashboard: http://127.0.0.1:$DAEMON_PORT/dashboard"
    echo "  noVNC:     http://127.0.0.1:$NOVNC_PORT/vnc.html"
    echo ""
    if [ "$MODE" = "docker" ]; then
        echo "  Manage:    ./docker/detm-docker.sh {start|stop|restart|status|logs}"
    else
        echo "  Service:   sudo systemctl status detm-daemon"
        echo "  Logs:      journalctl -u detm-daemon -f"
    fi
    echo ""
    echo "  Test:      curl http://127.0.0.1:$DAEMON_PORT/health"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if [ "$MODE" = "docker" ]; then
    # Docker mode needs a venv for the MCP server proxy on the host
    if [ ! -d "$REPO_DIR/.venv" ]; then
        info "Creating host venv for MCP server proxy..."
        python3 -m venv "$REPO_DIR/.venv"
        "$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
        "$REPO_DIR/.venv/bin/pip" install --quiet -e "$REPO_DIR"
    fi
    install_docker
else
    install_bare_metal
fi

configure_openclaw
verify_and_summarize
