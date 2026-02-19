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

Five layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — vision-based async monitoring with diff gate + adaptive polling
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (Ollama, vLLM, Claude, passthrough)
5. **Display Manager** — per-task virtual displays (Xvfb) for isolated screen environments

## Per-task virtual displays

Each task gets its own Xvfb display at registration time. Pass `display_width` and `display_height` in `metadata` to override the default 1280x720 resolution.

```
task_register(name="Edit video", plan=[...], metadata={"display_width": 1920, "display_height": 1080})
```

All `desktop_action`, `gui_do`, `desktop_look`, and `smart_wait` calls that include `task_id` automatically target the task's display. No need to manage DISPLAY env vars manually.

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

## Narrating your work — MANDATORY

Narration is **not optional**. Every action must be announced before it happens and reported after it completes. The dashboard shows the human exactly what you're doing in real time, but only if you narrate.

### The required workflow — follow this exactly

For **every** tool call that involves the desktop or GUI:

```
1. ANNOUNCE  →  task_log_action(task_id=<id>, action_type=<type>, summary="<what I'm about to do and why>", status="started")
2. EXECUTE   →  call the tool (desktop_look, gui_do, desktop_action, etc.)
3. REPORT    →  task_update(task_id=<id>, message="<what I observed or what happened>")
```

Do not skip steps. The announce creates a visible entry in the dashboard. The report records your observation.

### action_type values

| action_type | Use for |
|---|---|
| `vision` | desktop_look, observing the screen |
| `gui` | gui_do, desktop_action (click/type/key) |
| `cli` | shell commands |
| `wait` | smart_wait calls |
| `reasoning` | decision-making, planning, deductions |

### Examples

**Looking at the screen:**
```
task_log_action(task_id=<id>, action_type="vision", summary="Taking screenshot to assess current state of DaVinci export dialog", status="started")
desktop_look(task_id=<id>)
task_update(task_id=<id>, message="I can see the Export dialog is open. Format is H.264, output path is ~/Desktop/output.mp4. I need to change resolution to 4K before confirming.")
```

**Clicking a button:**
```
task_log_action(task_id=<id>, action_type="gui", summary="Clicking the Export button to open render dialog", status="started")
gui_do(instruction="click the Export button", task_id=<id>)
task_update(task_id=<id>, message="Export button clicked. Render dialog appeared with codec options.")
```

**Shell command:**
```
task_log_action(task_id=<id>, action_type="cli", summary="Running ffmpeg to convert video to H.264", input_data="ffmpeg -i input.mp4 -c:v libx264 output.mp4")
# run the command via Bash
task_update(task_id=<id>, message="ffmpeg completed in 12s. Output file is 45MB at ~/Desktop/output.mp4.")
```

**Decision / reasoning:**
```
task_log_action(task_id=<id>, action_type="reasoning", summary="DaVinci Resolve is still loading — splash screen visible. Will wait 10s before attempting GUI interaction.")
```

### When a user message is task-relevant

Post it to task history so the context is preserved:
```
task_update(task_id=<id>, message="[user] Change the output format to ProRes instead of H.264")
```

## Tool reference

### Task Management (hierarchical)
- `task_register` — create task with plan items
- `task_update` — post message, change status, query state
- `task_item_update` — update plan item status (pending/active/completed/failed/skipped)
- `task_log_action` — log discrete action under a plan item (cli/gui/wait/vision/reasoning)
- `task_summary` — item-level overview (default), actions, full, or focused (expand only active item)
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
- `desktop_look` — returns a screenshot **image** directly to you. Use this to observe the current screen state before deciding your next action. You interpret the image yourself — no local vision model is involved.
- `video_record` — record screen/window clip

### System
- `health_check` — daemon + vision backend + system status
- `memory_search`, `memory_read`, `memory_append` — workspace memory files

## Examples

### Using desktop_look — observe, describe, act
```
→ desktop_look(task_id=<id>)
  # You receive an image — look at it carefully
→ task_update(task_id=<id>, message="Screen shows DaVinci Resolve with the timeline open. The Export button is visible in the top-right toolbar. I'll click it now.")
→ gui_do(instruction="click the Export button", task_id=<id>)
→ task_update(task_id=<id>, message="Clicked Export. A render dialog appeared with format options.")
→ desktop_look(task_id=<id>)
  # Observe the dialog
→ task_update(task_id=<id>, message="Export dialog shows H.264 selected. Output path is ~/Desktop/output.mp4. Setting 4K resolution before confirming.")
```

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

## Dashboard

The daemon serves a web dashboard at `http://127.0.0.1:18790/dashboard`. The human can:
- See all tasks with status and progress
- Expand plan items to view actions, screenshots, logs
- Watch a live MJPEG screen stream per task
- Read the color-coded message feed (your narration, wait events, system messages)
- **Cancel or pause a task** from the dashboard — this sets the task status and frees up the LLM

When a task is cancelled or paused from the dashboard, you'll see the status change on your next `task_summary` or `task_update` call. Respect it — do not continue working on a cancelled task.

## Stuck detection and automatic resumption

If an active task has no updates and no active smart waits for 5+ minutes, the daemon automatically wakes you with a `[task_stuck_resume]` event containing a resume packet. The resume packet includes:
- Task name, status, progress percentage
- Completed/current/remaining plan items
- The active item expanded with full action details and logs
- Last 5 messages from the task thread
- Wait state summary

When you receive this event, use the resume packet to orient yourself and continue the task. You do NOT need to call `task_summary` — the packet already contains everything.

## Logs & debugging
- Debug log: `~/.agentic-computer-use/logs/debug.log`
- Set `ACU_DEBUG=1` for verbose colored logging
- Both the human and Claude Code can tail the log file
- Use `./dev.sh logs` for live log tail

## Storage
- Data: `~/.agentic-computer-use/`
- Database: `~/.agentic-computer-use/data.db`
