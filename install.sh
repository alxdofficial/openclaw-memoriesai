#!/usr/bin/env bash
set -euo pipefail

# ─── openclaw-memoriesai installer ──────────────────────────────
# Usage: ./install.sh
# Installs the daemon, configures systemd, and hooks into OpenClaw.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
SERVICE_NAME="memoriesai-daemon"
DAEMON_PORT=18790
OLLAMA_MODEL="minicpm-v"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Detect environment ────────────────────────────────────────

info "Detecting environment..."

# Python
PYTHON=""
for p in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$p" &>/dev/null; then
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

# Display
DISPLAY="${DISPLAY:-}"
if [ -z "$DISPLAY" ]; then
    # Check for Xvfb
    if pgrep -x Xvfb &>/dev/null; then
        DISPLAY=":99"
        warn "No DISPLAY set, detected Xvfb — using :99"
    else
        warn "No DISPLAY set and no Xvfb found. Smart Wait screen capture won't work."
        warn "Install Xvfb: sudo apt install xvfb && Xvfb :99 -screen 0 1920x1080x24 &"
        DISPLAY=":99"
    fi
fi

# GPU
HAS_GPU=false
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    HAS_GPU=true
    ok "NVIDIA GPU detected"
else
    warn "No NVIDIA GPU detected. Vision model will run on CPU (slower)."
fi

# ─── Install Python package ────────────────────────────────────

info "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_DIR"
ok "Python package installed"

# ─── Install system dependencies ───────────────────────────────

MISSING_PKGS=()
command -v xdotool &>/dev/null || MISSING_PKGS+=("xdotool")
command -v ffmpeg &>/dev/null || MISSING_PKGS+=("ffmpeg")
command -v fluxbox &>/dev/null || MISSING_PKGS+=("fluxbox")

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    info "Installing system packages: ${MISSING_PKGS[*]}"
    if command -v apt &>/dev/null; then
        sudo apt install -y "${MISSING_PKGS[@]}"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y "${MISSING_PKGS[@]}"
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm "${MISSING_PKGS[@]}"
    else
        warn "Cannot auto-install: ${MISSING_PKGS[*]}. Please install manually."
    fi
fi
ok "System dependencies ready"

# ─── Install Ollama + model ────────────────────────────────────

if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
ok "Ollama installed: $(ollama --version 2>/dev/null || echo 'unknown')"

# Check if model is pulled
if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    info "Pulling vision model ($OLLAMA_MODEL)... this may take a few minutes."
    ollama pull "$OLLAMA_MODEL"
fi
ok "Vision model ready: $OLLAMA_MODEL"

# ─── Create systemd service ────────────────────────────────────

info "Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat <<EOF | sudo tee "$SERVICE_FILE" > /dev/null
[Unit]
Description=OpenClaw MemoriesAI Daemon
After=network.target ollama.service

[Service]
Type=simple
User=$(whoami)
Environment=DISPLAY=$DISPLAY
Environment=PYTHONPATH=$REPO_DIR/src
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_DIR/bin/python3 -m openclaw_memoriesai.daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

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

# ─── Configure OpenClaw (mcporter) ─────────────────────────────

info "Configuring OpenClaw integration..."

# Find OpenClaw workspace
OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
MCPORTER_CONFIG="$OC_WORKSPACE/config/mcporter.json"

# Find openclaw binary
OPENCLAW_BIN=""
for p in "$HOME/.npm-global/bin/openclaw" "$(which openclaw 2>/dev/null)" "/usr/local/bin/openclaw"; do
    if [ -x "$p" ] 2>/dev/null; then
        OPENCLAW_BIN="$p"
        break
    fi
done

if [ -n "$OPENCLAW_BIN" ]; then
    ok "OpenClaw found: $OPENCLAW_BIN"
else
    warn "OpenClaw CLI not found. You'll need to configure mcporter manually."
fi

# Write mcporter config
mkdir -p "$(dirname "$MCPORTER_CONFIG")"

if [ -f "$MCPORTER_CONFIG" ]; then
    # Merge — check if memoriesai already configured
    if grep -q '"memoriesai"' "$MCPORTER_CONFIG" 2>/dev/null; then
        info "mcporter config already has memoriesai entry — updating"
    fi
fi

# Use python to safely merge JSON
"$VENV_DIR/bin/python3" -c "
import json, os

config_path = '$MCPORTER_CONFIG'
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['memoriesai'] = {
    'command': '$VENV_DIR/bin/python3',
    'args': ['-m', 'openclaw_memoriesai.server'],
    'cwd': '$REPO_DIR',
    'env': {
        'DISPLAY': '$DISPLAY',
        'PYTHONPATH': '$REPO_DIR/src'
    }
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print(f'Written: {config_path}')
"
ok "mcporter config updated"

# Install OpenClaw skill (SKILL.md)
SKILL_DIR="$OC_WORKSPACE/skills/memoriesai"
mkdir -p "$SKILL_DIR"
if [ -f "$REPO_DIR/skill/SKILL.md" ]; then
    cp "$REPO_DIR/skill/SKILL.md" "$SKILL_DIR/SKILL.md"
    ok "OpenClaw skill installed: $SKILL_DIR/SKILL.md"
else
    warn "skill/SKILL.md not found in repo — skill not installed"
fi

# ─── Verify ────────────────────────────────────────────────────

info "Running health check..."
HEALTH=$(curl -s "http://127.0.0.1:$DAEMON_PORT/health" 2>/dev/null || echo '{}')
if echo "$HEALTH" | grep -q '"ok"'; then
    ok "Health check passed"
else
    warn "Health check failed — daemon may still be starting"
fi

# ─── Summary ───────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     openclaw-memoriesai installed successfully   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Daemon:    http://127.0.0.1:$DAEMON_PORT"
echo "  Service:   sudo systemctl status $SERVICE_NAME"
echo "  Logs:      journalctl -u $SERVICE_NAME -f"
echo ""
echo "  Test:      mcporter call memoriesai.health_check"
echo "  Docs:      $REPO_DIR/docs/"
echo ""
if [ -n "$OPENCLAW_BIN" ]; then
    echo "  OpenClaw will auto-discover tools via mcporter."
    echo "  Try: mcporter call memoriesai.smart_wait target=screen wake_when=\"page loaded\""
fi
echo ""
