---
name: agentic-computer-use
description: Desktop Environment Task Manager (DETM) — hierarchical task tracking, smart visual waiting, GUI automation with NL grounding, and screen recording. Use for multi-step desktop workflows, visual app control, and long-running operations.
---

# agentic-computer-use — DETM

Local daemon on `127.0.0.1:18790` with hierarchical task persistence, async wait engine, GUI grounding, and pluggable vision.

## Architecture

```
OpenClaw LLM → DETM (task hierarchy) → Vision + GUI Agent → Desktop/Xvfb
```

Five layers:
1. **Task Management** — hierarchical: Task → Plan Items → Actions → Logs
2. **Smart Wait** — binary YES/NO vision polling every 1s; timeout is the fallback
3. **GUI Agent** — Gemini Flash supervisor + UI-TARS grounding (precise cursor placement)
4. **Vision** — cloud vision via OpenRouter (default)
5. **Display** — shared system display visible in VNC and the dashboard live view

## When to use DETM vs. other tools

**Default to faster tools first.** DETM is powerful but slow. Always prefer built-in tools when they can do the job.

| Situation | Use instead of DETM |
|---|---|
| General web research, finding facts, news, documentation | `web_search` or `WebFetch` |
| Public APIs with known endpoints | Direct HTTP calls via `Bash` or `httpx` |
| File operations, data processing, code execution | `Bash`, Python, CLI tools |
| Publicly accessible URLs with no login | `WebFetch` or `curl` |

**Use DETM when the task requires visual interaction or an authenticated platform** — anything behind a login, or where you need to click/type/scroll in a GUI.

**When DETM hits a login wall or CAPTCHA:** tell the user via `task_update`, then use `smart_wait` to poll until they resolve it via VNC. Never try to log in yourself or bypass auth.

## Desktop environment

DETM runs on a bare Linux desktop (XFCE on a headless VM). The desktop is minimal — no curated dock, no pinned apps, sparse icons.

**Launch apps via CLI, not gui_agent.** Every app can be launched instantly from the command line. gui_agent wastes 30-60s hunting for icons on a bare desktop.

```bash
firefox https://example.com &
sleep 3
DISPLAY=:99 xdotool windowsize --sync $(xdotool getactivewindow) 1920 1080
DISPLAY=:99 xdotool windowmove --sync $(xdotool getactivewindow) 0 0
```

**Always maximize windows after launching.** Windows open at ~1280x792 on the 1920x1080 desktop. gui_agent and desktop_look will miss content that's off-screen.

**Close popups and overlays** (cookie banners, login modals) before reading the screen — they block content.

## HARD RULES

These are not guidelines. Violating them breaks observability and cancellation.

**1. Always create a task before touching the desktop.**
`task_register` MUST be your first call for any work that involves `desktop_look`, `desktop_action`, `gui_agent`, or `smart_wait`. Without a task, the human cannot cancel you and the dashboard shows nothing.

```
task_register(name="Find reporters on LinkedIn", plan=["Search LinkedIn", "Collect profiles", "Write to sheet"])
task_item_update(task_id=<id>, ordinal=0, status="active")
```

**2. Log desktop actions with `task_log_action`.**
Before each `gui_agent`, `desktop_look`, or `desktop_action` call, log what you're about to do. After, report what you observed via `task_update`. This is how the dashboard shows your progress.

```
task_log_action(task_id=<id>, action_type="gui", summary="Searching LinkedIn for reporters")
gui_agent(instruction="Click the search bar, type TechCrunch AI reporters, press Enter", task_id=<id>)
task_update(task_id=<id>, message="Search submitted, results loading.")
```

You do NOT need to log non-desktop tool calls (web_search, file reads, bash commands). Only log DETM tool calls.

**3. Pass `task_id` to every desktop/GUI/wait call.**

**4. Verify each step before marking it complete.**
After `gui_agent`: trust the `{success, summary}` result — only take `desktop_look` if you need to read specific content. After `desktop_action`: one `desktop_look` to verify is enough.

If a step fails, do NOT create a new task. Use `task_plan_append` to add corrective steps and `task_item_update(status="scrapped")` on stale items.

**5. Check task status after each plan item.**
```
task_item_update(task_id=<id>, ordinal=N, status="completed")
status = task_update(task_id=<id>, query="status")
if status is cancelled or paused → stop immediately
```
This is how the human cancels you from the dashboard.

## Choosing the right tool

| Situation | Tool |
|---|---|
| Launch an app | CLI (`firefox &`, `thunar &`) |
| See the current screen state | `desktop_look` |
| Complete a multi-step UI workflow | `gui_agent` with a full subtask instruction |
| One precise click at known coordinates | `desktop_action` |
| Wait for something to appear/finish | `smart_wait` |
| Read content from a public URL | `curl` or `WebFetch` (faster than screenshotting) |

### gui_agent — delegate whole subtasks

Give `gui_agent` a coherent goal, not individual clicks. It handles 10-30 step workflows autonomously.

```
# Good — one coherent subtask:
gui_agent(instruction="Search for flights from NYC to London on March 28 and filter for nonstop", task_id=<id>, timeout=120)

# Bad — micromanaging individual clicks:
gui_agent(instruction="Click the search bar", task_id=<id>)
gui_agent(instruction="Type NYC to London", task_id=<id>)
```

Break the user's overall task into 2-5 subtasks. Check in between with `desktop_look` when you need to read results or make decisions.

### desktop_look — observe and decide

Take a screenshot and reason about it yourself. No model is invoked — you interpret the image directly.

### desktop_action — raw pixel control

Use when you have exact coordinates from `desktop_look`. Always click a text field before typing into it.

```
desktop_action(action="click", x=640, y=400, task_id=<id>)    # click field
desktop_action(action="type", text="hello world", task_id=<id>) # type into it
desktop_action(action="press_key", text="Return", task_id=<id>) # press Enter
```

## Browser interaction

**Prefer visual interaction** for authenticated sites and interactive web apps — `gui_agent` for clicking/typing, `desktop_look` for reading.

**Be pragmatic.** If a CLI approach is faster and more reliable, use it:
- `curl`/`wget` to fetch a public URL
- `firefox <url>` to navigate directly instead of clicking through menus
- CLI tools to process downloaded data

**The goal is task completion, not visual purity.**

## Task lifecycle

```
1. task_register → create task with plan items
2. task_item_update(ordinal=0, status="active")
3. Do the work (gui_agent, desktop_look, CLI, etc.)
4. task_item_update(ordinal=0, status="completed")
5. task_update(query="status") → check for cancellation
6. Repeat for each plan item
7. task_update(status="completed")
```

If reality diverges from the plan, use `task_item_update(status="scrapped")` + `task_plan_append` to revise.

## Tool reference

### Task Management
- `task_register` — create task with plan items
- `task_update` — post message, change status, query state
- `task_item_update` — update plan item status (pending/active/completed/failed/skipped/scrapped)
- `task_plan_append` — append new plan items to an existing task
- `task_log_action` — log action under a plan item (cli/gui/wait/vision/reasoning)
- `task_summary` — task overview (items/actions/full/focused)
- `task_drill_down` — expand one plan item's actions and logs
- `task_list` — list tasks by status

### Smart Wait
- `smart_wait` — delegate visual monitoring (polls screen with vision model)
- `wait_status` / `wait_update` / `wait_cancel`

### GUI Agent
- `gui_agent` — autonomous GUI agent (Gemini Flash + UI-TARS). Handles clicks, typing, scrolling, navigation, form filling. Returns `{success, summary, escalated, actions_taken}`.

### Desktop Control
- `desktop_action` — click(x,y), type, press_key, drag, window management
- `desktop_look` — screenshot returned as image (you interpret it)
- `video_record` — record screen/window clip

### Video Intelligence
- `mavi_understand` — record screen, ask a question about the video (for audio/motion content like TikTok sounds, animations, live feeds)

### System
- `health_check` — daemon + vision + system status
- `memory_search`, `memory_read`, `memory_append` — workspace memory files

## Escalation scenarios

- **Login / auth wall** — tell the user, use `smart_wait` to poll until they log in via VNC
- **CAPTCHA** — tell the user, wait for them to solve it
- **2FA prompt** — tell the user, wait for them to enter the code
- **gui_agent escalated** — relay `escalation_reason` to the user

## Stuck detection and automatic resumption

If an active task has no updates for 5+ minutes, the daemon sends a `[task_stuck_resume]` event with a resume packet containing task state, plan items, and recent actions. Use it to orient yourself and continue.

If the resume packet contains `agent_id`, spawn that sub-agent to continue:
```
/subagents spawn <agent_id> "Resume DETM task <task_id>. <resume context>"
```

## Dashboard

The daemon serves a web dashboard at `http://127.0.0.1:18790/dashboard`. The human can see tasks, plan items, screenshots, live screen stream, and cancel/pause tasks.
