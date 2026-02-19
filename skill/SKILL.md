---
name: agentic-computer-use
description: Desktop Environment Task Manager (DETM) — hierarchical task tracking, smart visual waiting, GUI automation with NL grounding, pluggable vision backends, and screen recording. Use for multi-step desktop workflows, visual app control, and long-running operations.
---

# agentic-computer-use — DETM

Local daemon on `127.0.0.1:18790` with hierarchical task persistence, async wait engine, GUI grounding, and pluggable vision.

## Architecture

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Four layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, passthrough)

## When to use GUI vs CLI

| Scenario | Prefer | Why |
|---|---|---|
| Visual apps (DaVinci Resolve, Blender, VS Code GUI) | `gui_do` / `desktop_action` | No CLI equivalent |
| File operations, git, builds | CLI/exec | Faster, deterministic |
| Web forms, dialogs, popups | `gui_do` | Visual interaction required |
| Status monitoring | `smart_wait` with window target | Vision handles any app |
| Scripting, automation | CLI/exec | Structured output |

## Task lifecycle pattern

For any multi-step or long-running work:

```
1. task_register → creates hierarchical plan items
2. task_item_update(ordinal=0, status="active") → start first item
3. task_log_action(action_type="cli", summary="ran ffmpeg...") → log work
4. task_item_update(ordinal=0, status="completed") → finish item
5. smart_wait with task_id → async visual monitoring
6. task_summary → check progress at item level
7. task_drill_down(ordinal=N) → inspect specific item's actions
8. task_update(status="completed") → close task
```

## Tool reference

### Task Management (hierarchical)
- `task_register` — create task with plan items
- `task_update` — post message, change status, query state
- `task_item_update` — update plan item status (pending/active/completed/failed/skipped)
- `task_log_action` — log discrete action under a plan item (cli/gui/wait/vision/reasoning)
- `task_summary` — item-level overview (default), or expand to actions/full
- `task_drill_down` — expand one plan item to see all actions + logs
- `task_thread` — legacy message thread view
- `task_list` — list tasks by status

### Smart Wait
- `smart_wait` — delegate visual monitoring to local model
- `wait_status` — check active wait jobs
- `wait_update` — refine condition or extend timeout
- `wait_cancel` — cancel a wait job

### GUI Agent
- `gui_do` — execute NL or coordinate action ("click the Export button" or "click(847, 523)")
- `gui_find` — locate element by description, return coords without acting

### Desktop Control
- `desktop_action` — raw xdotool: click, type, press_key, window management
- `desktop_look` — screenshot + vision description
- `video_record` — record screen/window clip

### System
- `health_check` — daemon + vision backend + system status
- `memory_search`, `memory_read`, `memory_append` — workspace memory files

## Examples

### Multi-step video export with GUI
```
→ task_register(name="Export 4K video", plan=["Open project", "Set export settings", "Start export", "Verify output"])
→ task_item_update(task_id=<id>, ordinal=0, status="active")
→ gui_do(instruction="click the Export button", window_name="DaVinci Resolve", task_id=<id>)
→ task_item_update(task_id=<id>, ordinal=0, status="completed")
→ task_item_update(task_id=<id>, ordinal=2, status="active")
→ smart_wait(target=window:DaVinci Resolve, wake_when="export progress reaches 100% or error dialog", task_id=<id>)
→ task_summary(task_id=<id>, detail_level="items")
```

### Monitoring a build
```
→ task_register(name="Build project", plan=["Run build", "Wait for completion", "Check output"])
→ task_item_update(task_id=<id>, ordinal=0, status="active")
→ task_log_action(task_id=<id>, action_type="cli", summary="make -j8", input_data="make -j8")
→ smart_wait(target=window:Terminal, wake_when="build succeeds or fails", task_id=<id>)
```

## Targeting guidance
- Prefer `window:<name_or_id>` so Smart Wait evaluates one specific app window
- Use `desktop_action(action=list_windows|find_window)` to discover window targets
- `screen` watches the entire desktop (noisier, use as fallback)

## Logs & debugging
- Debug log: `~/.agentic-computer-use/logs/debug.log`
- Set `ACU_DEBUG=1` for verbose colored logging
- Both the human and Claude Code can tail the log file
- Use `./dev.sh logs` for live log tail

## Storage
- Data: `~/.agentic-computer-use/`
- Database: `~/.agentic-computer-use/data.db`
