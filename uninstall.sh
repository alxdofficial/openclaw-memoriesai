#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="detm-daemon"
OC_WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"

echo "Stopping and removing $SERVICE_NAME service..."
sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
sudo rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload

echo "Removing data directory..."
rm -rf "$HOME/.agentic-computer-use"

# Remove OpenClaw skill symlink
SKILL_LINK="$OC_WORKSPACE/skills/agentic-computer-use"
if [ -L "$SKILL_LINK" ]; then
    rm "$SKILL_LINK"
    echo "Removed skill symlink: $SKILL_LINK"
fi

# Remove DETM section from MEMORY.md
MEMORY_FILE="$OC_WORKSPACE/MEMORY.md"
if [ -f "$MEMORY_FILE" ]; then
    MEMORY_FILE="$MEMORY_FILE" python3 - <<'PYEOF'
import re, os

mem_path = os.environ.get('MEMORY_FILE', '')
MARKER_BEGIN = '<!-- BEGIN:agentic-computer-use -->'
MARKER_END   = '<!-- END:agentic-computer-use -->'

if not mem_path or not os.path.exists(mem_path):
    print('MEMORY.md not found — nothing to remove')
    exit(0)

with open(mem_path) as f:
    content = f.read()

if MARKER_BEGIN not in content:
    print('No DETM section found in MEMORY.md — nothing to remove')
    exit(0)

# Remove the marked block (including surrounding blank lines)
pattern = r'\n?' + re.escape(MARKER_BEGIN) + r'.*?' + re.escape(MARKER_END) + r'\n?'
content = re.sub(pattern, '\n', content, flags=re.DOTALL)
# Clean up any triple+ newlines left behind
content = re.sub(r'\n{3,}', '\n\n', content)

with open(mem_path, 'w') as f:
    f.write(content)
print('Removed DETM section from MEMORY.md')
PYEOF
fi

# Remove mcporter entry
MCPORTER_CONFIG="$OC_WORKSPACE/config/mcporter.json"
if [ -f "$MCPORTER_CONFIG" ]; then
    MCPORTER_CONFIG="$MCPORTER_CONFIG" python3 - <<'PYEOF'
import json, os

config_path = os.environ.get('MCPORTER_CONFIG', '')
if not config_path or not os.path.exists(config_path):
    exit(0)

try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    exit(0)

servers = config.get('mcpServers', {})
if 'agentic-computer-use' in servers:
    del servers['agentic-computer-use']
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f'Removed agentic-computer-use from mcporter config')
PYEOF
fi

echo "Done. To fully remove, also delete this repo."
