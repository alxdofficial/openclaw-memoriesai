#!/usr/bin/env bash
set -euo pipefail

# ─── agentic-computer-use (DETM) installer ──────────────────────
# Usage: ./install.sh
# Installs the daemon, configures systemd, and hooks into OpenClaw.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
SERVICE_NAME="detm-daemon"
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

# ─── Display detection / setup ─────────────────────────────────

VIRTUAL_DISPLAY=false
NOVNC_PORT=6080
VNC_PORT=5901
DETM_DISPLAY=":99"

# Check for a real X display first
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
    ok "Real display found: $DETM_DISPLAY (no virtual display needed)"
else
    info "No real display found — setting up virtual display infrastructure..."
    VIRTUAL_DISPLAY=true

    # Install x11vnc + novnc if missing
    VNC_PKGS=()
    command -v x11vnc    &>/dev/null || VNC_PKGS+=("x11vnc")
    command -v websockify &>/dev/null || VNC_PKGS+=("novnc")
    if [ ${#VNC_PKGS[@]} -gt 0 ]; then
        info "Installing: ${VNC_PKGS[*]}"
        if command -v apt &>/dev/null; then
            sudo apt install -y "${VNC_PKGS[@]}"
        else
            warn "Cannot auto-install ${VNC_PKGS[*]} — install manually then re-run"
        fi
    fi

    # ── Xvfb service ──────────────────────────────────────────
    sudo tee /etc/systemd/system/detm-xvfb.service > /dev/null <<EOF
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
EOF

    # ── x11vnc service ─────────────────────────────────────────
    sudo tee /etc/systemd/system/detm-vnc.service > /dev/null <<EOF
[Unit]
Description=DETM VNC Server
After=detm-xvfb.service
Requires=detm-xvfb.service

[Service]
Type=simple
User=$(whoami)
Environment=DISPLAY=$DETM_DISPLAY
ExecStart=/usr/bin/x11vnc -display $DETM_DISPLAY -nopw -listen 127.0.0.1 -forever -shared -rfbport $VNC_PORT
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    # ── noVNC (websockify) service ─────────────────────────────
    NOVNC_WEB=""
    for p in /usr/share/novnc /usr/share/novnc/utils; do
        [ -d "$p" ] && NOVNC_WEB="$p" && break
    done
    NOVNC_CMD="websockify"
    [ -n "$NOVNC_WEB" ] && NOVNC_CMD="websockify --web=$NOVNC_WEB"

    sudo tee /etc/systemd/system/detm-novnc.service > /dev/null <<EOF
[Unit]
Description=DETM noVNC Web Client
After=detm-vnc.service
Requires=detm-vnc.service

[Service]
Type=simple
User=$(whoami)
ExecStart=/usr/bin/$NOVNC_CMD $NOVNC_PORT 127.0.0.1:$VNC_PORT
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    for svc in detm-xvfb detm-vnc detm-novnc; do
        sudo systemctl enable "$svc"
        sudo systemctl restart "$svc"
    done
    sleep 1

    if DISPLAY="$DETM_DISPLAY" xdpyinfo &>/dev/null 2>&1; then
        ok "Virtual display ready: $DETM_DISPLAY"
        ok "noVNC available at: http://127.0.0.1:$NOVNC_PORT/vnc.html"
    else
        warn "Virtual display may still be starting — check: systemctl status detm-xvfb"
    fi
fi

export DISPLAY="$DETM_DISPLAY"

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
command -v ffmpeg &>/dev/null  || MISSING_PKGS+=("ffmpeg")
command -v fluxbox &>/dev/null || MISSING_PKGS+=("fluxbox")
command -v Xvfb &>/dev/null    || MISSING_PKGS+=("xvfb")
command -v scrot &>/dev/null   || MISSING_PKGS+=("scrot")

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

if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    info "Pulling vision model ($OLLAMA_MODEL)... this may take a few minutes."
    ollama pull "$OLLAMA_MODEL"
fi
ok "Vision model ready: $OLLAMA_MODEL"

# ─── Vision backend selection ──────────────────────────────────

info "Vision backend: ollama (default). Set ACU_VISION_BACKEND=vllm|claude|passthrough to change."
info "GUI agent backend: direct (default). Set ACU_GUI_AGENT_BACKEND=uitars|claude_cu to change."

# ─── Create systemd service ────────────────────────────────────

info "Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat <<EOF | sudo tee "$SERVICE_FILE" > /dev/null
[Unit]
Description=Agentic Computer Use — DETM Daemon
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

OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
MCPORTER_CONFIG="$OC_WORKSPACE/config/mcporter.json"

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

mkdir -p "$(dirname "$MCPORTER_CONFIG")"

"$VENV_DIR/bin/python3" -c "
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
        'DISPLAY': '$DISPLAY',
        'PYTHONPATH': '$REPO_DIR/src'
    }
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print(f'Written: {config_path}')
"
ok "mcporter config updated"

# Install OpenClaw skill (symlink so edits to repo auto-propagate)
SKILL_LINK="$OC_WORKSPACE/skills/agentic-computer-use"
if [ -L "$SKILL_LINK" ]; then
    rm "$SKILL_LINK"
elif [ -d "$SKILL_LINK" ]; then
    rm -rf "$SKILL_LINK"
fi
if [ -d "$REPO_DIR/skill" ]; then
    ln -s "$REPO_DIR/skill" "$SKILL_LINK"
    ok "OpenClaw skill symlinked: $SKILL_LINK → $REPO_DIR/skill"
else
    warn "skill/ directory not found in repo — skill not installed"
fi

# Inject DETM guidance into OpenClaw MEMORY.md (idempotent via markers)
MEMORY_FILE="$OC_WORKSPACE/MEMORY.md"
FRAGMENT_FILE="$REPO_DIR/openclaw/MEMORY-fragment.md"

if [ -f "$FRAGMENT_FILE" ]; then
    REPO_DIR="$REPO_DIR" OC_WORKSPACE="$OC_WORKSPACE" "$VENV_DIR/bin/python3" - <<'PYEOF'
import re, os, sys

repo_dir  = os.environ.get('REPO_DIR', '')
oc_ws     = os.environ.get('OC_WORKSPACE', os.path.expanduser('~/.openclaw/workspace'))
mem_path  = os.path.join(oc_ws, 'MEMORY.md')
frag_path = os.path.join(repo_dir, 'openclaw', 'MEMORY-fragment.md')

MARKER_BEGIN = '<!-- BEGIN:agentic-computer-use -->'
MARKER_END   = '<!-- END:agentic-computer-use -->'
OLD_HEADER   = '## Desktop & GUI Automation — DETM Workflow (Always Use This)'

with open(frag_path) as f:
    fragment = f.read().strip()

new_block = f'{MARKER_BEGIN}\n{fragment}\n{MARKER_END}'

if not os.path.exists(mem_path):
    os.makedirs(os.path.dirname(mem_path), exist_ok=True)
    content = '# MEMORY.md - Your Long-Term Memory\n'
else:
    with open(mem_path) as f:
        content = f.read()

if MARKER_BEGIN in content:
    # Already injected — update content between markers
    pattern = re.escape(MARKER_BEGIN) + r'.*?' + re.escape(MARKER_END)
    content = re.sub(pattern, new_block, content, flags=re.DOTALL)
    print('Updated DETM section in MEMORY.md')
else:
    # Remove any old manually-added DETM section (no markers)
    if OLD_HEADER in content:
        pattern = re.escape(OLD_HEADER) + r'.*?(?=\n## |\Z)'
        content = re.sub(pattern, '', content, flags=re.DOTALL)
        # Clean up extra blank lines after removal
        content = re.sub(r'\n{3,}', '\n\n', content)
        print('Removed old untracked DETM section from MEMORY.md')

    # Insert after the title line (first line starting with #)
    lines = content.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith('# '):
            insert_at = i + 1
            break
    lines.insert(insert_at, f'\n{new_block}\n')
    content = ''.join(lines)
    print('Injected DETM section into MEMORY.md')

with open(mem_path, 'w') as f:
    f.write(content)
PYEOF
    ok "MEMORY.md updated with DETM workflow guidance"
else
    warn "openclaw/MEMORY-fragment.md not found — skipping MEMORY.md injection"
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
echo -e "${GREEN}║   agentic-computer-use installed successfully    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Daemon:    http://127.0.0.1:$DAEMON_PORT"
echo "  Dashboard: http://127.0.0.1:$DAEMON_PORT/dashboard"
echo "  Display:   $DETM_DISPLAY"
if [ "$VIRTUAL_DISPLAY" = true ]; then
echo "  noVNC:     http://127.0.0.1:$NOVNC_PORT/vnc.html"
fi
echo "  Service:   sudo systemctl status $SERVICE_NAME"
echo "  Logs:      journalctl -u $SERVICE_NAME -f"
echo ""
echo "  Test:      curl http://127.0.0.1:$DAEMON_PORT/health"
echo ""
if [ -n "$OPENCLAW_BIN" ]; then
    echo "  OpenClaw will auto-discover tools via mcporter."
fi
echo ""
