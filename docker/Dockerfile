FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# ── System dependencies ──────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Python
    python3 python3-venv python3-pip python3-dev \
    # X11 virtual display + desktop environment
    xvfb xfce4 xfce4-terminal xfce4-whiskermenu-plugin xterm x11-utils \
    # Desktop control
    xdotool scrot xclip xsel \
    # VNC + web viewer
    x11vnc novnc websockify \
    # Media
    ffmpeg \
    # Fonts (prevents missing-glyph rendering bugs)
    fonts-liberation fonts-dejavu-core fonts-noto-color-emoji \
    # System libs
    libx11-dev libxext-dev libxrender-dev libxrandr-dev \
    dbus-x11 ca-certificates curl wget git \
    && rm -rf /var/lib/apt/lists/*

# ── Desktop apps ─────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    firefox \
    thunar \
    && rm -rf /var/lib/apt/lists/*

# ── Python package ────────────────────────────────────────────────
WORKDIR /opt/detm
COPY pyproject.toml .
COPY src/ src/

RUN python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir --upgrade pip \
    && .venv/bin/pip install --no-cache-dir -e ".[docker]" 2>/dev/null \
    || .venv/bin/pip install --no-cache-dir -e .

# ── Entrypoint ────────────────────────────────────────────────────
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Environment ───────────────────────────────────────────────────
ENV DISPLAY=:99
ENV PYTHONPATH=/opt/detm/src
ENV ACU_DAEMON_HOST=0.0.0.0
ENV ACU_DATA_DIR=/data

# ── Ports ─────────────────────────────────────────────────────────
# 18790 = DETM daemon (API + dashboard)
# 6080  = noVNC web viewer
EXPOSE 18790 6080

# ── Volumes ───────────────────────────────────────────────────────
# Persist DB, screenshots, recordings across container restarts
VOLUME ["/data"]

ENTRYPOINT ["/entrypoint.sh"]
