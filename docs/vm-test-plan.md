# VM Fresh Install Test Plan

## Goal
Simulate a new user installing OpenClaw + DETM on a fresh Ubuntu 22.04 VM, then verify the full end-to-end flow: OpenClaw discovers DETM tools via MCP, user asks OpenClaw to do a desktop task, OpenClaw delegates to gui_agent.

## API Keys Needed

| Key | Value | Purpose |
|-----|-------|---------|
| Vultr API | `YOUR-VULTR-API-KEY` | VM provisioning |
| OpenRouter API | `sk-or-v1-YOUR-KEY-HERE` | LLM for OpenClaw + DETM gui_agent |
| SSH Key ID (Vultr) | `3d7801af-5c07-4fe2-8bd4-551e5786d304` | SSH access to VMs |

## What was done yesterday (2026-03-23)

### PASSED
- [x] install.sh runs clean on fresh Ubuntu 22.04 (all 9 steps)
- [x] Python 3.12 auto-installed from deadsnakes PPA
- [x] XFCE screensaver disabled (was blanking display)
- [x] All 5 systemd services start: detm-xvfb, detm-desktop, detm-vnc, detm-novnc, detm-daemon
- [x] Health endpoint, dashboard, noVNC all return OK
- [x] gui_agent completes a browser task when called directly via HTTP API

### NOT YET TESTED — the real user flow
- [ ] OpenClaw gateway starts and loads MCP tools from mcporter.json
- [ ] OpenClaw auth works with OpenRouter
- [ ] SKILL.md loaded (symlinked to workspace/skills/)
- [ ] User asks OpenClaw via TUI -> OpenClaw sees DETM tools -> delegates to gui_agent
- [ ] Task completes on virtual desktop and shows on dashboard

## Key Research Findings (from 2026-03-24)

### OpenRouter model config
- Model ID format: **`openrouter/google/gemini-2.5-flash-preview`** (must have `openrouter/` prefix)
- Yesterday we used `google/gemini-2.5-flash-preview` which resolved to native `google` provider — wrong
- Set with: `openclaw models set openrouter/google/gemini-2.5-flash-preview`

### OpenRouter auth (3 methods, in priority order)
1. **Env var** (simplest, most reliable): `export OPENROUTER_API_KEY="sk-or-v1-..."`
   - Also works in `~/.openclaw/.env`
   - Or in openclaw.json: `{ "env": { "OPENROUTER_API_KEY": "sk-or-v1-..." } }`
2. **paste-token**: `openclaw models auth paste-token --provider openrouter`
   - Stores in `~/.openclaw/agents/main/agent/auth-profiles.json`
   - Was flaky yesterday — file never got created
3. **Onboard**: `openclaw onboard --auth-choice apiKey --token-provider openrouter --token "sk-or-..."`

**Known bug #51056**: In v2026.3.13, OpenRouter auth header may not attach. If we hit this, try:
- Updating OpenClaw to latest
- Using env var approach
- Writing auth-profiles.json directly as fallback

### MCP tool discovery
- mcporter.json at `~/.openclaw/workspace/config/mcporter.json` — already set up by install.sh
- MCP tools appear to the LLM as `mcp__<server-name>__<tool-name>` function calls
- No gateway restart needed for mcporter.json changes (read at call time)
- Verify with: `openclaw tools list` or check gateway verbose logs

### Skills
- SKILL.md in `~/.openclaw/workspace/skills/agentic-computer-use/SKILL.md` (symlink)
- Auto-loaded by skills watcher — no restart needed
- Check with: `openclaw skills list`

### TUI vs agent command
- `openclaw tui` — interactive terminal UI, full tool use
- `openclaw agent -m "message"` — one-shot, full agent runtime with tool use, scriptable
- Both can use MCP tools
- For testing: start `openclaw tui` in a tmux pane, type messages interactively

## Setup Steps (today)

### 1. Create fresh VM
```bash
export VULTR_API_KEY="YOUR-VULTR-API-KEY"
vultr-cli instance create --label detm-test --region ewr --plan vc2-4c-8gb --os 1743 \
  --ssh-keys 3d7801af-5c07-4fe2-8bd4-551e5786d304
# Wait ~40s for active, get IP
```

### 2. Install OpenClaw
```bash
ssh root@<ip> "curl -fsSL https://openclaw.ai/install.sh | bash"
```

### 3. Clone repo + run install.sh
```bash
ssh root@<ip> "cd ~ && git clone https://github.com/alxdofficial/openclaw-memoriesai.git"
ssh root@<ip> "cd ~/openclaw-memoriesai && OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE ./install.sh"
```

### 4. Configure OpenClaw model + auth
**Primary method: `openclaw configure` (interactive wizard)**
Run in tmux on the VM — walks through provider, model, and API key in one flow:
```bash
ssh -t root@<ip> "openclaw configure"
# Select provider: OpenRouter
# Select model: google/gemini-2.5-flash-preview
# Paste API key: sk-or-v1-YOUR-KEY-HERE
```

**Fallback A: CLI commands (if configure wizard doesn't work over SSH)**
```bash
ssh root@<ip> "openclaw models set openrouter/google/gemini-2.5-flash-preview"
ssh root@<ip> 'echo "OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE" > ~/.openclaw/.env'
```

**Fallback B: Write auth-profiles.json directly**
```bash
ssh root@<ip> 'mkdir -p ~/.openclaw/agents/main/agent && cat > ~/.openclaw/agents/main/agent/auth-profiles.json << EOF
{
  "openrouter:manual": {
    "type": "api_key",
    "provider": "openrouter",
    "key": "sk-or-v1-YOUR-KEY-HERE"
  }
}
EOF'
```

**Verify:**
```bash
ssh root@<ip> "openclaw models status"
# Should show: Providers w/ OAuth/tokens (1): openrouter
```

### 5. Start OpenClaw gateway in tmux
```bash
ssh root@<ip> "tmux new-session -d -s gw 'OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE openclaw gateway --force --auth none --verbose 2>&1 | tee /tmp/openclaw-gw.log'"
```
Check logs for:
- `agentic-computer-use` MCP server loading
- Tool discovery
- No auth errors

### 6. Verify MCP tools are discovered
```bash
ssh root@<ip> "openclaw tools list 2>&1 | grep -i agentic"
# Or
ssh root@<ip> "openclaw skills list 2>&1"
```

### 7. Test via OpenClaw TUI (the real user flow)
Start TUI in a tmux pane:
```bash
ssh -t root@<ip> "tmux new-session -d -s tui 'OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE openclaw tui'"
```

Then send messages via tmux send-keys:
```bash
# Test 1: Does OpenClaw know about DETM?
ssh root@<ip> "tmux send-keys -t tui 'What DETM tools do you have available?' Enter"
# Wait, then capture output
ssh root@<ip> "tmux capture-pane -t tui -p"

# Test 2: Run a DETM task
ssh root@<ip> "tmux send-keys -t tui 'Use DETM to open Firefox and search Google for weather in Tokyo' Enter"
# Wait for completion, capture output + screenshot
```

Alternative (one-shot, scriptable):
```bash
ssh root@<ip> "OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE openclaw agent -m 'What DETM tools do you have available? List them.' --json"
```

### 8. Verify task on dashboard + desktop
```bash
# Screenshot the desktop
ssh root@<ip> "DISPLAY=:99 scrot /tmp/after.png"
scp root@<ip>:/tmp/after.png /tmp/after.png

# Check dashboard for task
ssh root@<ip> "curl -s http://127.0.0.1:18790/tasks | python3 -m json.tool"
```

### 9. Cleanup
```bash
export VULTR_API_KEY="YOUR-VULTR-API-KEY"
vultr-cli instance delete <id>
vultr-cli instance list  # confirm zero
```

## Known Issues to Watch For
- **Bug #51056**: OpenRouter 401 in v2026.3.13 — try env var, update OpenClaw, or write auth-profiles.json directly
- **Model prefix**: Must use `openrouter/` prefix — `google/gemini-2.5-flash-preview` routes to wrong provider
- **Plugin warning**: "plugins.allow is empty" — may need `openclaw config set plugins.allow '["detm-tool-logger"]'`
- **Vision backend**: "ollama" always fails (no local Ollama) — harmless
- **TTY for paste-token**: Interactive prompt may not work over non-TTY SSH — use env var instead

## Success Criteria
1. `./install.sh` completes on fresh Ubuntu 22.04 with OpenClaw pre-installed
2. OpenClaw gateway discovers DETM MCP tools
3. User asks OpenClaw via TUI to do a browser task
4. OpenClaw reads SKILL.md, calls DETM tools (task_register + gui_agent)
5. gui_agent completes the task on the virtual desktop
6. Task visible on DETM dashboard
7. No manual intervention beyond providing the OpenRouter API key
