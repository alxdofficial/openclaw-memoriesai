#!/usr/bin/env bash
set -euo pipefail

# ─── agentic-computer-use (DETM) uninstaller ─────────────────────
# Reverses everything install.sh did. Safe to run multiple times.
# Does NOT uninstall system packages (xdotool, ffmpeg, firefox, etc.)
# or remove the deadsnakes PPA — those may be used by other software.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
info() { echo -e "${YELLOW}[..]${NC} $*"; }
warn() { echo -e "${RED}[!!]${NC} $*"; }

# ── 1. Stop and remove systemd services ──────────────────────────

info "Stopping systemd services..."
for svc in detm-daemon detm-desktop detm-novnc detm-vnc detm-xvfb; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        sudo systemctl stop "$svc" && ok "Stopped $svc"
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        sudo systemctl disable "$svc" 2>/dev/null
    fi
    if [ -f "/etc/systemd/system/${svc}.service" ]; then
        sudo rm -f "/etc/systemd/system/${svc}.service"
        ok "Removed ${svc}.service"
    fi
done
sudo systemctl daemon-reload 2>/dev/null || true

# ── 2. Unregister OpenClaw MCP server ────────────────────────────

info "Removing OpenClaw MCP server..."
OPENCLAW_BIN=""
for p in "$HOME/.npm-global/bin/openclaw" "$(which openclaw 2>/dev/null || true)" "/usr/local/bin/openclaw"; do
    if [ -n "$p" ] && [ -x "$p" ] 2>/dev/null; then
        OPENCLAW_BIN="$p"
        break
    fi
done

if [ -n "$OPENCLAW_BIN" ]; then
    "$OPENCLAW_BIN" mcp unset agentic-computer-use 2>/dev/null && ok "MCP server unregistered" || true
else
    warn "OpenClaw CLI not found — run 'openclaw mcp unset agentic-computer-use' manually"
fi

# ── 3. Remove OpenClaw plugin ────────────────────────────────────

if [ -n "$OPENCLAW_BIN" ]; then
    if "$OPENCLAW_BIN" plugins list 2>/dev/null | grep -q "detm-tool-logger"; then
        "$OPENCLAW_BIN" plugins uninstall detm-tool-logger 2>/dev/null && ok "Plugin removed: detm-tool-logger" || warn "Could not remove plugin"
    fi
fi

# ── 4. Remove deployed sub-agents ─────────────────────────────────

AGENTS_SRC="$REPO_DIR/openclaw/agents"
if [ -d "$AGENTS_SRC" ]; then
    for agent_dir in "$AGENTS_SRC"/*/; do
        [ ! -d "$agent_dir" ] && continue
        agent_id="$(basename "$agent_dir")"
        dest="$HOME/.openclaw/agents/$agent_id"
        if [ -d "$dest" ]; then
            rm -rf "$dest"
            ok "Removed sub-agent: $agent_id"
        fi
    done
    # Remove agent entries from openclaw.json
    OC_CONFIG="$HOME/.openclaw/openclaw.json"
    if [ -f "$OC_CONFIG" ] && [ -d "$AGENTS_SRC" ]; then
        AGENTS_SRC="$AGENTS_SRC" OC_CONFIG="$OC_CONFIG" python3 - <<'PYEOF'
import json, os, glob

config_path = os.environ['OC_CONFIG']
agents_src = os.environ['AGENTS_SRC']

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    exit(0)

agent_ids = set()
for d in glob.glob(os.path.join(agents_src, '*')):
    if os.path.isdir(d):
        agent_ids.add(os.path.basename(d))

if not agent_ids:
    exit(0)

agent_list = config.get('agents', {}).get('list', [])
before = len(agent_list)
agent_list[:] = [a for a in agent_list if a.get('id') not in agent_ids]
after = len(agent_list)

if before != after:
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f'Removed {before - after} agent(s) from openclaw.json')
PYEOF
    fi
fi

# ── 5. Remove skill symlink ─────────────────────────────────────

SKILL_LINK="$OC_WORKSPACE/skills/agentic-computer-use"
if [ -L "$SKILL_LINK" ] || [ -d "$SKILL_LINK" ]; then
    rm -rf "$SKILL_LINK"
    ok "Removed skill symlink"
fi

# ── 6. Remove DETM section from MEMORY.md ───────────────────────

MEMORY_FILE="$OC_WORKSPACE/MEMORY.md"
if [ -f "$MEMORY_FILE" ]; then
    MEMORY_FILE="$MEMORY_FILE" python3 - <<'PYEOF'
import re, os

mem_path = os.environ.get('MEMORY_FILE', '')
MARKER_BEGIN = '<!-- BEGIN:agentic-computer-use -->'
MARKER_END   = '<!-- END:agentic-computer-use -->'

if not mem_path or not os.path.exists(mem_path):
    exit(0)

with open(mem_path) as f:
    content = f.read()

if MARKER_BEGIN not in content:
    print('No DETM section found in MEMORY.md')
    exit(0)

pattern = r'\n?' + re.escape(MARKER_BEGIN) + r'.*?' + re.escape(MARKER_END) + r'\n?'
content = re.sub(pattern, '\n', content, flags=re.DOTALL)
content = re.sub(r'\n{3,}', '\n\n', content)

with open(mem_path, 'w') as f:
    f.write(content)
print('Removed DETM section from MEMORY.md')
PYEOF
    ok "Cleaned MEMORY.md"
fi

# ── 7. Remove data directory ────────────────────────────────────

if [ -d "$HOME/.agentic-computer-use" ]; then
    rm -rf "$HOME/.agentic-computer-use"
    ok "Removed ~/.agentic-computer-use"
fi

# ── 8. Remove Python virtual environment ────────────────────────

if [ -d "$REPO_DIR/.venv" ]; then
    rm -rf "$REPO_DIR/.venv"
    ok "Removed .venv"
fi

# ── 9. Remove XFCE screensaver config ───────────────────────────

XFCE_SS="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-screensaver.xml"
if [ -f "$XFCE_SS" ]; then
    rm -f "$XFCE_SS"
    ok "Removed xfce4-screensaver.xml"
fi

# ── Done ─────────────────────────────────────────────────────────

echo ""
ok "DETM uninstalled."
info "The repo itself is still at: $REPO_DIR"
info "To fully remove: rm -rf $REPO_DIR"
