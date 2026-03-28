#!/usr/bin/env bash
# deploy-agents.sh — shared agent deployment logic for dev.sh and install.sh
#
# Expects these variables to be set by the caller:
#   REPO_DIR          — repo root
#   OC_WORKSPACE      — OpenClaw workspace dir (default: ~/.openclaw/workspace)
#
# Optional:
#   VENV_PYTHON       — path to venv python3 (needed for register_agents_config)
#
# Provides:
#   deploy_agents()           — sync agent files to ~/.openclaw/agents/*/workspace/
#   register_agents_config()  — register agents in openclaw.json (idempotent)

AGENTS_SRC="${REPO_DIR}/openclaw/agents"
OC_WORKSPACE="${OC_WORKSPACE:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"

deploy_agents() {
    [ ! -d "$AGENTS_SRC" ] && return

    for agent_dir in "$AGENTS_SRC"/*/; do
        [ ! -d "$agent_dir" ] && continue
        local agent_id="$(basename "$agent_dir")"
        local dest="$HOME/.openclaw/agents/$agent_id"
        local ws="$dest/workspace"

        mkdir -p "$ws/skills" "$ws/config"

        # Copy SOUL.md and AGENTS.md into workspace
        for mdfile in SOUL.md AGENTS.md; do
            [ -f "$agent_dir/$mdfile" ] && cp "$agent_dir/$mdfile" "$ws/$mdfile"
        done

        # Symlink DETM skill
        local skill_link="$ws/skills/agentic-computer-use"
        [ -L "$skill_link" ] && rm "$skill_link"
        [ -d "$skill_link" ] && rm -rf "$skill_link"
        [ -d "$REPO_DIR/skill" ] && ln -s "$REPO_DIR/skill" "$skill_link"

        # Copy mcporter.json so sub-agents can discover DETM MCP tools
        local main_mcporter="$OC_WORKSPACE/config/mcporter.json"
        [ -f "$main_mcporter" ] && cp "$main_mcporter" "$ws/config/mcporter.json"

        # Print status (uses ok() from caller if available, else echo)
        if type ok &>/dev/null; then
            ok "Agent synced: $agent_id"
        else
            echo "[OK] Agent synced: $agent_id"
        fi
    done
}

register_agents_config() {
    [ ! -d "$AGENTS_SRC" ] && return

    local oc_config="$HOME/.openclaw/openclaw.json"
    [ ! -f "$oc_config" ] && return

    local python="${VENV_PYTHON:-python3}"

    "$python" -c "
import json, os, glob

config_path = '$oc_config'
agents_src = '$AGENTS_SRC'

with open(config_path) as f:
    config = json.load(f)

agents_cfg = config.setdefault('agents', {})
agent_list = agents_cfg.setdefault('list', [])
existing = {}
for a in agent_list:
    existing[a['id']] = a

for d in sorted(glob.glob(os.path.join(agents_src, '*'))):
    if not os.path.isdir(d):
        continue
    agent_id = os.path.basename(d)
    ws_path = os.path.expanduser(f'~/.openclaw/agents/{agent_id}/workspace')
    if agent_id in existing:
        # Ensure workspace is set
        if 'workspace' not in existing[agent_id]:
            existing[agent_id]['workspace'] = ws_path
            print(f'Updated workspace for agent: {agent_id}')
        else:
            print(f'Agent already registered: {agent_id}')
    else:
        agent_list.append({
            'id': agent_id,
            'workspace': ws_path,
            'subagents': {'allowAgents': ['main']},
        })
        print(f'Registered agent: {agent_id}')

# Ensure main agent can spawn all sub-agents
sub_ids = [os.path.basename(d) for d in sorted(glob.glob(os.path.join(agents_src, '*'))) if os.path.isdir(d)]
for a in agent_list:
    if a['id'] == 'main':
        a.setdefault('subagents', {})['allowAgents'] = sub_ids
        print(f'Updated main agent allowAgents: {sub_ids}')
        break

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
"
}
