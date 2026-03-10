#!/bin/bash
set -e

# ── Xvfb virtual display ──────────────────────────────────────────
SCREEN_WIDTH="${SCREEN_WIDTH:-1920}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1080}"

echo "[detm] Starting Xvfb :99 (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24)"
Xvfb :99 -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" -nolisten tcp &
XVFB_PID=$!

# Wait for display to be ready
for i in $(seq 1 30); do
    if xdpyinfo -display :99 &>/dev/null; then
        break
    fi
    sleep 0.2
done

if ! xdpyinfo -display :99 &>/dev/null; then
    echo "[detm] ERROR: Xvfb failed to start"
    exit 1
fi
echo "[detm] Xvfb ready"

# ── Desktop environment ───────────────────────────────────────────
echo "[detm] Starting XFCE4 desktop"
DISPLAY=:99 XDG_SESSION_TYPE=x11 dbus-run-session startxfce4 &>/dev/null &

# ── VNC server ─────────────────────────────────────────────────────
echo "[detm] Starting x11vnc on :5901"
x11vnc -display :99 -nopw -listen 0.0.0.0 -forever -shared -rfbport 5901 -q &>/dev/null &

# ── noVNC (websockify) ─────────────────────────────────────────────
NOVNC_WEB=""
for p in /usr/share/novnc /usr/share/novnc/utils; do
    [ -d "$p" ] && NOVNC_WEB="$p" && break
done

if [ -n "$NOVNC_WEB" ]; then
    echo "[detm] Starting noVNC on :6080"
    websockify --web="$NOVNC_WEB" 6080 localhost:5901 &>/dev/null &
else
    echo "[detm] Starting websockify on :6080 (no noVNC web UI)"
    websockify 6080 localhost:5901 &>/dev/null &
fi

# ── Data directory ─────────────────────────────────────────────────
mkdir -p "${ACU_DATA_DIR:-/data}"

# ── DETM daemon (foreground) ──────────────────────────────────────
echo "[detm] Starting DETM daemon on :18790"
echo "[detm] Dashboard: http://localhost:18790/dashboard"
echo "[detm] noVNC:     http://localhost:6080/vnc.html"

exec /opt/detm/.venv/bin/python3 -m agentic_computer_use.daemon
