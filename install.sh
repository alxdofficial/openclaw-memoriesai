#!/usr/bin/env bash
set -euo pipefail

# ─── agentic-computer-use (DETM) installer ──────────────────────
# Usage:
#   ./install.sh
#   OPENROUTER_API_KEY=sk-or-... ./install.sh
#
# Installs DETM on a Linux box: virtual display, VNC, daemon,
# and hooks into OpenClaw (mcporter + skill + plugin).
#
# Requirements: Linux, Python 3.11+

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
DAEMON_PORT=18790
NOVNC_PORT=6080
VNC_PORT=5901
DETM_DISPLAY=":99"

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

# ─── Sudo helper (works even when stdin is a pipe) ───────────────
# When piped via curl|bash, sudo can't read the password from stdin.
# Pre-authenticate by reading from /dev/tty, then use cached creds.
if ! sudo -n true 2>/dev/null; then
    info "sudo access required — prompting for password..."
    if [ -e /dev/tty ]; then
        sudo -v < /dev/tty
    else
        err "Cannot prompt for sudo password (no TTY). Run with: sudo bash or grant NOPASSWD."
        exit 1
    fi
fi

# ─── Platform check ─────────────────────────────────────────────

if [ "$(uname)" != "Linux" ]; then
    err "DETM requires Linux. You're on $(uname)."
    exit 1
fi

# ─── API key ─────────────────────────────────────────────────────

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
    if [ -t 1 ] && [ -e /dev/tty ]; then
        read -rp "  Enter your OpenRouter API key (or press Enter to skip): " user_key < /dev/tty
        if [ -n "$user_key" ]; then
            export OPENROUTER_API_KEY="$user_key"
            ok "API key set"
        else
            warn "Skipping — GUI agent and cloud vision will not work."
            warn "Set OPENROUTER_API_KEY later and restart the daemon."
        fi
    else
        warn "Non-interactive — pass key via: OPENROUTER_API_KEY=sk-or-... bash"
    fi
fi

# ─── Python ──────────────────────────────────────────────────────

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

# ─── System dependencies ────────────────────────────────────────

MISSING_PKGS=()
command -v xdotool    &>/dev/null || MISSING_PKGS+=("xdotool")
command -v ffmpeg     &>/dev/null || MISSING_PKGS+=("ffmpeg")
command -v xfce4-session &>/dev/null || MISSING_PKGS+=("xfce4" "xfce4-terminal" "xfce4-whiskermenu-plugin" "thunar")
command -v Xvfb       &>/dev/null || MISSING_PKGS+=("xvfb")
command -v scrot      &>/dev/null || MISSING_PKGS+=("scrot")
command -v xdpyinfo   &>/dev/null || MISSING_PKGS+=("x11-utils")
command -v x11vnc     &>/dev/null || MISSING_PKGS+=("x11vnc")
command -v websockify  &>/dev/null || MISSING_PKGS+=("novnc")
command -v xterm      &>/dev/null || MISSING_PKGS+=("xterm")
command -v xclip      &>/dev/null || MISSING_PKGS+=("xclip")
command -v dbus-launch &>/dev/null || MISSING_PKGS+=("dbus-x11")

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    info "Installing system packages: ${MISSING_PKGS[*]}"
    if command -v apt &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq "${MISSING_PKGS[@]}"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y "${MISSING_PKGS[@]}"
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm "${MISSING_PKGS[@]}"
    else
        err "Cannot auto-install: ${MISSING_PKGS[*]}. Install manually and retry."
        exit 1
    fi
fi
ok "System dependencies ready"

# ─── Browser ────────────────────────────────────────────────────

HAS_BROWSER=false
for browser in google-chrome google-chrome-stable chromium-browser chromium firefox firefox-esr; do
    if command -v "$browser" &>/dev/null; then
        HAS_BROWSER=true
        ok "Browser found: $(command -v $browser)"
        break
    fi
done

if [ "$HAS_BROWSER" = false ]; then
    warn "No web browser found. Installing Firefox..."
    if command -v apt &>/dev/null; then
        sudo apt-get install -y -qq firefox 2>/dev/null || sudo apt-get install -y -qq firefox-esr 2>/dev/null || warn "Could not install Firefox"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y firefox
    else
        warn "Install a browser manually (Firefox or Chrome)"
    fi
fi

# ─── Display ────────────────────────────────────────────────────

_has_real_display() {
    for d in "${DISPLAY:-}" :0 :1 :2; do
        [ -z "$d" ] && continue
        if DISPLAY="$d" xdpyinfo &>/dev/null 2>&1; then
            echo "$d"; return 0
        fi
    done
    return 1
}

VIRTUAL_DISPLAY=false

if REAL_DISP=$(_has_real_display); then
    DETM_DISPLAY="$REAL_DISP"
    ok "Real display found: $DETM_DISPLAY (no virtual display needed)"
else
    info "No real display — setting up virtual display on $DETM_DISPLAY..."
    VIRTUAL_DISPLAY=true

    # ── Xvfb service ─────────────────────────────────────────
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

    # ── x11vnc service ───────────────────────────────────────
    sudo tee /etc/systemd/system/detm-vnc.service > /dev/null <<SVCEOF
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
SVCEOF

    # ── noVNC (websockify) service ───────────────────────────
    NOVNC_WEB=""
    for p in /usr/share/novnc /usr/share/novnc/utils; do
        [ -d "$p" ] && NOVNC_WEB="$p" && break
    done
    NOVNC_CMD="websockify"
    [ -n "$NOVNC_WEB" ] && NOVNC_CMD="websockify --web=$NOVNC_WEB"

    sudo tee /etc/systemd/system/detm-novnc.service > /dev/null <<SVCEOF
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
SVCEOF

    # ── Desktop environment (XFCE4) ──────────────────────────
    sudo tee /etc/systemd/system/detm-desktop.service > /dev/null <<SVCEOF
[Unit]
Description=DETM Desktop (XFCE4)
After=detm-xvfb.service
Requires=detm-xvfb.service

[Service]
Type=simple
User=$(whoami)
Environment=DISPLAY=$DETM_DISPLAY
Environment=XDG_SESSION_TYPE=x11
ExecStart=/usr/bin/dbus-run-session /usr/bin/startxfce4
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SVCEOF

    sudo systemctl daemon-reload
    for svc in detm-xvfb detm-desktop detm-vnc detm-novnc; do
        sudo systemctl enable "$svc"
        sudo systemctl restart "$svc"
    done
    sleep 2

    if DISPLAY="$DETM_DISPLAY" xdpyinfo &>/dev/null 2>&1; then
        ok "Virtual display ready: $DETM_DISPLAY"
        ok "noVNC: http://127.0.0.1:$NOVNC_PORT/vnc.html"
    else
        warn "Virtual display may still be starting — check: systemctl status detm-xvfb"
    fi
fi

export DISPLAY="$DETM_DISPLAY"

# ─── XFCE defaults ────────────────────────────────────────────────
# Ensure keyboard shortcuts work (Super → app finder, Ctrl+Alt+T → terminal)

XFCE_KEYS_DIR="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml"
mkdir -p "$XFCE_KEYS_DIR"

if [ ! -f "$XFCE_KEYS_DIR/xfce4-keyboard-shortcuts.xml" ]; then
    info "Configuring XFCE keyboard shortcuts..."
    cat > "$XFCE_KEYS_DIR/xfce4-keyboard-shortcuts.xml" <<'XFCEEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Primary&gt;&lt;Alt&gt;t" type="string" value="xfce4-terminal"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Delete" type="string" value="xfce4-session-logout"/>
      <property name="Super_L" type="string" value="xfce4-popup-whiskermenu"/>
      <property name="&lt;Super&gt;e" type="string" value="thunar"/>
      <property name="Print" type="string" value="xfce4-screenshooter"/>
    </property>
  </property>
  <property name="xfwm4" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Alt&gt;F4" type="string" value="close_window_key"/>
      <property name="&lt;Alt&gt;F9" type="string" value="hide_window_key"/>
      <property name="&lt;Alt&gt;F10" type="string" value="maximize_window_key"/>
      <property name="&lt;Alt&gt;Tab" type="string" value="cycle_windows_key"/>
      <property name="&lt;Super&gt;Up" type="string" value="maximize_window_key"/>
      <property name="&lt;Super&gt;Down" type="string" value="minimize_window_key"/>
      <property name="&lt;Super&gt;Left" type="string" value="tile_left_key"/>
      <property name="&lt;Super&gt;Right" type="string" value="tile_right_key"/>
    </property>
  </property>
</channel>
XFCEEOF
    ok "XFCE keyboard shortcuts configured"
fi

# ─── Python package ──────────────────────────────────────────────

info "Setting up Python environment..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_DIR"
ok "Python package installed"

# ─── DETM daemon service ────────────────────────────────────────

info "Installing DETM daemon service..."

# Build environment block for systemd
ENV_LINES="Environment=DISPLAY=$DETM_DISPLAY
Environment=PYTHONPATH=$REPO_DIR/src"
[ -n "${OPENROUTER_API_KEY:-}" ] && ENV_LINES="$ENV_LINES
Environment=OPENROUTER_API_KEY=$OPENROUTER_API_KEY"
[ -n "${ANTHROPIC_API_KEY:-}" ] && ENV_LINES="$ENV_LINES
Environment=ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
[ -n "${MAVI_API_KEY:-}" ] && ENV_LINES="$ENV_LINES
Environment=MAVI_API_KEY=$MAVI_API_KEY"

sudo tee /etc/systemd/system/detm-daemon.service > /dev/null <<SVCEOF
[Unit]
Description=DETM Daemon
After=network.target detm-xvfb.service
Wants=detm-xvfb.service

[Service]
Type=simple
User=$(whoami)
$ENV_LINES
WorkingDirectory=$REPO_DIR
ExecStart=$VENV_DIR/bin/python3 -m agentic_computer_use.daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable detm-daemon
sudo systemctl restart detm-daemon
sleep 3

if systemctl is-active --quiet detm-daemon; then
    ok "Daemon running on port $DAEMON_PORT"
else
    warn "Daemon may still be starting — check: journalctl -u detm-daemon -f"
fi

# ─── OpenClaw integration ───────────────────────────────────────

info "Configuring OpenClaw..."

OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
MCPORTER_CONFIG="$OC_WORKSPACE/config/mcporter.json"

# Find OpenClaw binary
OPENCLAW_BIN=""
for p in "$HOME/.npm-global/bin/openclaw" "$(which openclaw 2>/dev/null)" "/usr/local/bin/openclaw"; do
    if [ -x "$p" ] 2>/dev/null; then
        OPENCLAW_BIN="$p"
        break
    fi
done
[ -n "$OPENCLAW_BIN" ] && ok "OpenClaw found: $OPENCLAW_BIN" || warn "OpenClaw CLI not found"

# mcporter config
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
        'DISPLAY': '$DETM_DISPLAY',
        'PYTHONPATH': '$REPO_DIR/src'
    }
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
"
ok "mcporter config updated"

# Skill symlink
SKILL_LINK="$OC_WORKSPACE/skills/agentic-computer-use"
[ -L "$SKILL_LINK" ] && rm "$SKILL_LINK"
[ -d "$SKILL_LINK" ] && rm -rf "$SKILL_LINK"
if [ -d "$REPO_DIR/skill" ]; then
    mkdir -p "$(dirname "$SKILL_LINK")"
    ln -s "$REPO_DIR/skill" "$SKILL_LINK"
    ok "Skill symlinked"
fi

# Sub-agents
if [ -f "$REPO_DIR/scripts/deploy-agents.sh" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python3"
    OC_WORKSPACE="$OC_WORKSPACE" VENV_PYTHON="$VENV_PYTHON" source "$REPO_DIR/scripts/deploy-agents.sh"
    if [ -d "${AGENTS_SRC:-}" ]; then
        info "Deploying sub-agents..."
        deploy_agents
        register_agents_config
        ok "Sub-agents deployed"
    fi
fi

# Plugin
PLUGIN_DIR="$REPO_DIR/plugins/detm-tool-logger"
if [ -d "$PLUGIN_DIR" ] && [ -n "$OPENCLAW_BIN" ]; then
    if "$OPENCLAW_BIN" plugins list 2>/dev/null | grep -q "detm-tool-logger"; then
        ok "Plugin: detm-tool-logger (already installed)"
    else
        "$OPENCLAW_BIN" plugins install --link "$PLUGIN_DIR" 2>/dev/null && ok "Plugin installed: detm-tool-logger" || warn "Could not install plugin"
    fi
fi

# ─── Health check ────────────────────────────────────────────────

info "Running health check..."
HEALTH=$(curl -s "http://127.0.0.1:$DAEMON_PORT/health" 2>/dev/null || echo '{}')
if echo "$HEALTH" | grep -q '"ok"'; then
    ok "Health check passed"
else
    warn "Health check failed — daemon may still be starting"
fi

# ─── Summary ────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  DETM installed successfully                    ${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
echo "  Display:   $DETM_DISPLAY"
echo "  Daemon:    http://127.0.0.1:$DAEMON_PORT"
echo "  Dashboard: http://127.0.0.1:$DAEMON_PORT/dashboard"
if [ "$VIRTUAL_DISPLAY" = true ]; then
echo "  noVNC:     http://127.0.0.1:$NOVNC_PORT/vnc.html"
fi
echo ""
echo "  Manage:"
echo "    sudo systemctl restart detm-daemon    # restart daemon"
echo "    sudo systemctl status detm-daemon     # check status"
echo "    journalctl -u detm-daemon -f          # tail logs"
if [ "$VIRTUAL_DISPLAY" = true ]; then
echo "    sudo systemctl restart detm-xvfb      # restart display"
fi
echo ""
echo "  Test:  curl http://127.0.0.1:$DAEMON_PORT/health"
echo ""
if [ -n "$OPENCLAW_BIN" ]; then
echo "  OpenClaw will auto-discover DETM tools via mcporter."
echo "  Reload OpenClaw: kill -HUP \$(pgrep -f openclaw-gateway)"
fi
echo ""
