---
name: memoriesai
description: Smart waiting, task memory, and desktop vision via the memoriesai daemon. Use for long-running operations (builds, downloads, page loads), task tracking across session boundaries, and reading screen contents.
---

# memoriesai — Smart Wait + Task Memory + Desktop Vision

Local daemon (port 18790) with MiniCPM-o vision on GPU. Tools accessed via `mcporter call` or `curl`.

## When to Use

- **Waiting for something** (build, install, page load, deployment): `smart_wait`
- **Multi-step complex tasks** that span compactions: `task_register` → `task_update` → `task_thread`
- **Reading the screen** (OCR, UI state): `desktop_look`
- **Typing/clicking on desktop**: `desktop_action`

## Quick Reference

All tools via mcporter (preferred):
```bash
mcporter call memoriesai.<tool> [key=value ...] --config /home/alex/.openclaw/workspace/config/mcporter.json
```

Or curl to daemon directly:
```bash
curl -s -X POST http://127.0.0.1:18790/<tool> -H 'Content-Type: application/json' -d '{...}'
```

## Tools

### Smart Wait
```bash
# Wait for screen condition (vision-evaluated)
mcporter call memoriesai.smart_wait target=screen wake_when="npm install finished" timeout:=120

# Wait for PTY output (regex fast-path, faster for terminal)
mcporter call memoriesai.smart_wait target=pty wake_when="build completed" timeout:=60

# Check active waits
mcporter call memoriesai.wait_status

# Cancel a wait
mcporter call memoriesai.wait_cancel job_id=<id>
```

When resolved/timed out, the daemon injects a system event to wake you automatically.

### Task Memory
```bash
# Register a task with plan steps
mcporter call memoriesai.task_register name="Deploy app" plan='["Build Docker image","Push to registry","Update k8s"]'

# Post progress update (mark step 0 done, 0-indexed)
mcporter call memoriesai.task_update task_id=<id> status=active message="Image built successfully" step_done:=0

# Recover full context after compaction
mcporter call memoriesai.task_thread task_id=<id>

# List tasks
mcporter call memoriesai.task_list status=in_progress
```

### Desktop Vision
```bash
# Read what's on screen (OCR via MiniCPM-o)
mcporter call memoriesai.desktop_look question="What text is in the terminal?"

# Perform desktop action
mcporter call memoriesai.desktop_action action_type=type text="hello world"
```

### Health
```bash
mcporter call memoriesai.health_check
# or: curl -s http://127.0.0.1:18790/health
```

## Task Memory Workflow

For complex multi-step work:

1. **Register** task at start with plan steps
2. **Update** after each step (`step_done` tracks progress %)
3. If session compacts, call **`task_thread`** to recover full context
4. **Complete** with `status=done` or `status=failed`

Stuck detection runs in background — if no updates for 5 min, daemon alerts via system event.

## Architecture Notes

- Daemon is systemd service `memoriesai-daemon` (auto-restarts)
- Vision model: MiniCPM-o on Ollama (RTX 3070, ~5.4GB VRAM)
- Screen capture: python-xlib on Xvfb `:99` (1920x1080)
- DB: SQLite at `~/.openclaw-memoriesai/data.db`
- Debug log: `~/.openclaw-memoriesai/logs/debug.log`

## Service Management
```bash
sudo systemctl status memoriesai-daemon
sudo systemctl restart memoriesai-daemon
```
