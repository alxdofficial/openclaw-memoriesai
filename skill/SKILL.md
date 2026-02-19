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

## Narrating your work

Always narrate your reasoning and actions into the task history. This is **required** — it makes your thought process visible to the human and creates a recovery trail for task resumption.

### When to narrate

- **Before starting a plan item** — what you intend to do and why
- **After completing an action** — what happened, what you observed
- **After every `desktop_look`** — describe what you see and what you will do next (**mandatory**)
- **After every `desktop_action` or `gui_do`** — narrate what you did and its result (**mandatory**)
- **When making a decision** — why you chose one approach over another
- **When encountering an error** — what went wrong, your recovery plan
- **When a user message is task-relevant** — summarize it into the task context

### How to narrate

Use `task_update(task_id=<id>, message="...")`. Messages appear in the dashboard feed with "LLM → task" headers in blue.

```
task_update(task_id=<id>, message="Opening the DaVinci Resolve project at ~/Projects/edit.drp via File > Open Recent.")
task_update(task_id=<id>, message="Export dialog appeared. Setting H.264 4K (3840x2160).")
task_update(task_id=<id>, message="Build failed (exit 1): missing libfoo. Installing and retrying.")
```

### User messages

If the user sends a message relevant to the active task, post it to task history:
```
task_update(task_id=<id>, message="[user] Change the output format to ProRes instead of H.264")
```

Only do this when the message is clearly task-related. General conversation or unrelated questions should not be posted.

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
