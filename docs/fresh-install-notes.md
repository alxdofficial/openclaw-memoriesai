# Fresh Install Test Notes

## VM Details
- Provider: Vultr
- Plan: vc2-4c-8gb (4 vCPU, 8GB RAM, 160GB disk)
- OS: Ubuntu 22.04 x64
- Region: ewr (New Jersey)

## Full Install Procedure (verified working 2026-03-24)

### Prerequisites
- Fresh Ubuntu 22.04 server with SSH access
- An OpenRouter API key (get one at https://openrouter.ai/keys)

### Step 1: Install OpenClaw
```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```
- Installs Node.js 22 + OpenClaw via npm
- Exit code 1 at the end is harmless (onboarding wizard fails without TTY)

### Step 2: Install DETM
```bash
git clone https://github.com/alxdofficial/openclaw-memoriesai.git
cd openclaw-memoriesai
OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE ./install.sh
```
- Auto-installs Python 3.12 from deadsnakes PPA if needed
- Installs system deps (xfce4, xvfb, vnc, ffmpeg, etc.)
- Sets up 5 systemd services (display, desktop, vnc, novnc, daemon)
- Registers DETM as an MCP server in OpenClaw config
- Symlinks SKILL.md into OpenClaw workspace
- Health check should pass at the end

### Step 3: Configure OpenClaw model
```bash
# Set model (must include openrouter/ prefix)
openclaw models set openrouter/google/gemini-2.5-flash

# Write API key for the gateway
echo "OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE" > ~/.openclaw/.env

# Set gateway mode
openclaw config set gateway.mode local
```

**Alternative**: Run `openclaw configure` interactively to set provider, model, and API key in one flow.

### Step 4: Start OpenClaw gateway
```bash
# In tmux or screen
OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE openclaw gateway --force --auth none
```

### Step 5: Verify
```bash
# Check DETM health
curl http://127.0.0.1:18790/health

# Check MCP tools are registered
openclaw mcp list
# Should show: agentic-computer-use

# Test via OpenClaw agent
OPENROUTER_API_KEY=sk-or-v1-YOUR-KEY-HERE openclaw agent --agent main -m "What DETM tools do you have?"
# Should list: task_register, gui_agent, desktop_look, desktop_action, etc.
```

### Step 6: Remote access (from laptop)
```bash
ssh -L 18790:localhost:18790 -L 6080:localhost:6080 root@your-server
# Dashboard: http://localhost:18790/dashboard
# VNC: http://localhost:6080/vnc.html
```

## Smoke Test Results (2026-03-24)

| Test | Result | Notes |
|------|--------|-------|
| Health endpoint | PASS | `"server": "ok"` |
| Dashboard | PASS | HTTP 200, full UI |
| noVNC | PASS | HTTP 200, virtual desktop visible |
| XFCE desktop | PASS | Screensaver disabled, desktop renders properly |
| desktop_look | PASS | Returns screenshot, LLM accurately describes screen contents |
| gui_agent (Google search) | PASS | Searched "weather in San Francisco" — 58°F displayed |
| gui_agent (Wikipedia) | PASS | Navigated to Alan Turing article, reported birth year 1912 |
| gui_agent (Google search #2) | PASS | Searched "population of Tokyo 2025" — 33.4M displayed |
| task_register + gui_agent | PASS | Task created in DB, gui_agent attempted Reddit (blocked by site, not DETM) |
| desktop_action (keyboard) | PARTIAL | Key shortcuts work but OpenClaw's exec doesn't pass DISPLAY env |
| OpenClaw tool discovery | PASS | All DETM MCP tools visible to agent |
| OpenClaw -> DETM delegation | PASS | OpenClaw successfully delegates browser tasks to gui_agent |

## Issues Found & Fixed

| # | Issue | Severity | Fix | Commit |
|---|-------|----------|-----|--------|
| 1 | Python 3.11+ missing on Ubuntu 22.04 | Blocker | Auto-install Python 3.12 from deadsnakes PPA | `733cf3b` |
| 2 | XFCE screensaver blanks virtual display | Major | Disable via xfce4-screensaver.xml + killall | `6fdda1d` |
| 3 | MCP tools not discovered by OpenClaw | Blocker | Use `openclaw mcp set` instead of legacy mcporter.json | `09fd2b2` |

## Known Limitations

1. **Model prefix bug**: OpenClaw may strip the `openrouter/` prefix from some model IDs (e.g. `openrouter/google/gemini-2.5-flash-preview` becomes `google/gemini-2.5-flash-preview`). Use stable model names like `openrouter/google/gemini-2.5-flash` instead of `-preview` variants.

2. **gui_agent timeouts**: First gui_agent call sometimes times out (browser cold start). OpenClaw retries with increased timeout and succeeds. Not a blocker.

3. **OpenClaw exec lacks DISPLAY**: OpenClaw's built-in `exec` tool doesn't inherit `DISPLAY=:99`, so you can't launch GUI apps via `exec`. Use `desktop_action` or `gui_agent` for GUI interactions.

4. **Task list filtering**: `task_list` only shows active tasks by default. Use `{"status": "all"}` to see completed/failed tasks.

5. **Vision backend "ollama" fails**: Health shows `"vision": {"ok": false}`. Expected without local Ollama — only affects `smart_wait`. GUI agent works fine.

6. **Plugin warning**: "plugins.allow is empty" appears in all OpenClaw commands. Harmless — the detm-tool-logger plugin loads correctly.

7. **Datacenter IP blocking**: Some sites (Reddit, etc.) block requests from cloud IPs. This is a site policy, not a DETM bug.

## FAQ

**Q: How do I update DETM?**
```bash
cd ~/openclaw-memoriesai && git pull && ./install.sh
```

**Q: How do I restart the DETM daemon?**
```bash
sudo systemctl restart detm-daemon
```

**Q: How do I check what MCP tools OpenClaw sees?**
```bash
openclaw mcp list                    # Shows configured MCP servers
openclaw agent --agent main -m "List your DETM tools"  # Ask the agent
```

**Q: The virtual desktop is black in VNC. What do I do?**
```bash
DISPLAY=:99 killall xfce4-screensaver   # Kill screensaver
sudo systemctl restart detm-desktop      # Or restart XFCE
```

**Q: OpenClaw says "400 not a valid model ID". What's wrong?**
The `openrouter/` prefix is getting stripped. Try a different model name:
```bash
openclaw models set openrouter/google/gemini-2.5-flash
```

**Q: How do I reset the VM for a clean test?**
```bash
vultr-cli instance reinstall <instance-id> --host ""
# Wait ~40s, then:
ssh-keygen -R <ip>
ssh root@<ip>
```
