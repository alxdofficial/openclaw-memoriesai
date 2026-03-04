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
2. **Smart Wait** — binary YES/NO vision polling every 1s; timeout is the fallback
3. **GUI Agent** — NL-to-coordinates grounding (UI-TARS, Claude CU, or direct xdotool)
4. **Vision** — pluggable backends (OpenRouter, Ollama, vLLM, Claude, passthrough)
5. **Display Manager** — shared system display visible in VNC and the dashboard live view

## Displays

All tasks share the **single system display** — the same desktop the human sees in VNC and in the dashboard live view. Everything you do on the desktop is visible to the human in real time.

## When to use DETM vs. other tools

**Default to faster tools first.** DETM (visual browser + desktop control) is powerful but slow. Always prefer built-in tools when they can do the job.

| Situation | Use instead of DETM |
|---|---|
| General web research, finding facts, news, documentation | `web_search` or `WebFetch` |
| Public APIs with known endpoints | Direct HTTP calls via `Bash` or `httpx` |
| File operations, data processing, code execution | `Bash`, Python, CLI tools |
| Publicly accessible URLs with no login | `WebFetch` |

**Use DETM when the task requires a specific authenticated platform.** If the data or action lives behind a login — or on a platform where no API or public index exists — DETM is the only route. Examples:

- **TikTok** — feed, search results, video audio, For You page (not indexed, requires browsing)
- **Instagram** — posts, Reels, Stories, DMs (authenticated, not crawlable)
- **LinkedIn** — profiles, messages, search, connection requests (authenticated)
- **Outlook / Gmail** — reading or sending email from a specific account
- **Canva** — creating or editing designs in a workspace
- **Google Sheets / Docs / Drive** — reading or editing files in a specific account
- **Any org-specific web platform** — internal tools, CRMs, dashboards, intranets

The signal: if a human would need to be logged in to do it, DETM is the right tool.

**When DETM hits a login wall or CAPTCHA:** use `task_update` to tell the user, then poll until they confirm they've resolved it in the browser (display :99). Never skip or bypass auth — escalate and wait.

## HARD RULES — no exceptions

These are not guidelines. Violating them breaks observability, cancellation, and debugging.

**0. When the prompt says "DETM ONLY" — web_search and WebFetch are banned for that task.**
If the human's request contains the phrase **DETM ONLY**, you must do ALL research by opening a real browser visually on the desktop. You may not call `web_search`, `WebFetch`, or any equivalent built-in fetch tool for the duration of that task — not even once, not even "just to verify". The only permitted research method is: open Firefox via `gui_do`, navigate with `desktop_action`, and read content via `desktop_look`. CLI tools (bash, python, ffmpeg, etc.) are still allowed for non-research steps like writing files.

```
# WRONG — even if faster, even if just one call:
web_search("TechCrunch AI reporters")

# RIGHT — open the browser and search visually:
task_log_action(task_id=<id>, action_type="gui", summary="Opening Firefox to search Google for TechCrunch AI reporters", status="started")
gui_do("open Firefox", task_id=<id>)
smart_wait(target="window:Firefox", wake_when="browser is open", task_id=<id>)
gui_do("click the address bar", task_id=<id>)
desktop_action(action="type", text="https://google.com", task_id=<id>)
desktop_action(action="press_key", text="Return", task_id=<id>)
```

**1. Always create a task before touching the desktop.**
`task_register` MUST be your first call for any work that involves `desktop_look`, `desktop_action`, `gui_do`, or `smart_wait`. No exceptions. Even a single screenshot requires a task.

```
# WRONG — never do this:
desktop_look()
gui_do("click the search bar")

# RIGHT — always do this first:
task_register(name="Find reporters on LinkedIn", plan=["Search LinkedIn", "Collect profiles", "Write to sheet"])
task_item_update(task_id=<id>, ordinal=0, status="active")
desktop_look(task_id=<id>)
```

Without a task: the human cannot cancel you, the dashboard shows nothing, there is no recovery if you get stuck.

**2. Log every tool call with `task_log_action` — before you make it.**
When a DETM task is active, EVERY tool call must be preceded by a `task_log_action`. This includes desktop tools AND native tools like `web_search`, `WebFetch`, `Bash`, file reads/writes, etc. This is the only way the human can see what you're doing in the dashboard — otherwise all non-desktop work is completely invisible to them.

```
# WRONG — tool call with no log entry:
web_search("TechCrunch AI reporters 2025")

# WRONG — desktop call with no log entry:
gui_do("click the search bar", task_id=<id>)

# RIGHT — log every tool call first:
task_log_action(task_id=<id>, action_type="cli", summary="Searching web for TechCrunch AI reporters")
web_search("TechCrunch AI reporters 2025")
task_update(task_id=<id>, message="Found 8 results. Top result is techcrunch.com/author/...")

task_log_action(task_id=<id>, action_type="gui", summary="Clicking LinkedIn search bar")
gui_do("click the search bar", task_id=<id>)
task_update(task_id=<id>, message="Clicked. Now typing query.")

task_log_action(task_id=<id>, action_type="vision", summary="Taking screenshot to read search results")
desktop_look(task_id=<id>)
task_update(task_id=<id>, message="Can see 8 reporter profiles in results: ...")
```

Every tool call = one `task_log_action` before + one `task_update` after. No exceptions.

**3. Pass `task_id` to every desktop/GUI/wait call.**
Every `desktop_look`, `desktop_action`, `gui_do`, and `smart_wait` call must include `task_id=<id>`. This is how DETM tracks what you're doing and links screenshots to your plan.

**4. Check task status after each plan item.**
After completing a plan item, check if the human has cancelled or paused the task before continuing:

```
task_item_update(task_id=<id>, ordinal=N, status="completed")
status = task_update(task_id=<id>, query="status")  # check before continuing
if status is cancelled or paused → stop immediately
task_item_update(task_id=<id>, ordinal=N+1, status="active")
```

**Why cancel requires your cooperation:** When the human clicks Cancel in the dashboard, it sets your task status to "cancelled" in the database — but it does NOT interrupt your current tool call. You will only see the cancellation on your next DETM call. This means: if you stop checking status between plan items, the human has no way to stop you. Check every time.

## When to use GUI vs CLI

| Scenario | Prefer | Why |
|---|---|---|
| Visual apps (DaVinci Resolve, Blender, VS Code GUI) | `gui_do` / `desktop_action` | No CLI equivalent |
| File operations, git, builds | CLI/exec | Faster, deterministic |
| Web forms, dialogs, popups | `gui_do` | Visual interaction required |
| Status monitoring | `smart_wait` with window target | Vision handles any app |
| Scripting, automation | CLI/exec | Structured output |
| Web browsing and research | `gui_do` / `desktop_look` | See below |

## Choosing between desktop_look, gui_do, and live_ui

There are three tiers of GUI interaction. Choose based on how much of the sequence you know ahead of time and how many steps are involved.

### Tier 1 — `desktop_look` (orientation)
Take a screenshot and reason about it yourself. Use this whenever you need to understand the current state of the screen before deciding what to do next. It is always valid. No model is invoked — you interpret the image directly.

```
→ desktop_look(task_id=<id>)   # see what's on screen
→ task_update(...)             # describe what you observe
→ decide your next action
```

### Tier 2 — `gui_do` / `desktop_action` (single known action)
Use when you know the next action but not what comes after — i.e. you need to see the result before deciding step N+2. UI-TARS grounds your natural language description to screen coordinates and executes via xdotool.

**Use this when:**
- You are taking one action at a time with LLM reasoning between each step
- The next page or result is unpredictable and you need to re-orient after every click
- You want precise pixel control (`desktop_action` with exact coordinates from `desktop_look`)

```
→ desktop_look(task_id=<id>)           # orient
→ gui_do("click the search bar", ...)  # act
→ desktop_look(task_id=<id>)           # re-orient before deciding next step
→ gui_do("type 'sourdough bread'", ...)
```

### Tier 3 — `live_ui` (delegated multi-step sequence)
Use when you have a chain of actions that a live AI vision model can handle without needing you in the loop for every step. The model watches the screen, takes actions, and calls `done()` or `escalate()` when finished.

**Use this when:**
- The workflow has many steps and `gui_do` + `desktop_look` round-trips would be too slow
- UI-TARS is consistently mis-clicking or struggling with the UI (complex layouts, overlapping elements, dynamic menus)
- The sequence is predictable enough for the model to complete autonomously (login flows, search + scroll, form filling, multi-page navigation)
- You know the goal but not each individual step — describe the outcome, let `live_ui` figure out the path

**Do not use this when:**
- The next step depends on complex reasoning that only you can do (e.g. reading a report and deciding whether a number is acceptable)
- You need the result of each step before proceeding (use `gui_do` + `desktop_look` instead)

```
→ live_ui(task_id=<id>, instruction="Navigate to Settings > Export and click Export as PDF", timeout=60)
→ # model handles the full chain; returns {success, summary, actions_taken}
→ desktop_look(task_id=<id>)   # re-orient after live_ui returns
```

### Decision guide

| Situation | Tool |
|---|---|
| "I need to see where I am before deciding anything" | `desktop_look` |
| "I know the next click but not what comes after" | `gui_do` + `desktop_look` |
| "UI-TARS keeps missing, or I need 8+ steps" | `live_ui` |
| "I have a predictable multi-step flow (login, form, search)" | `live_ui` |
| "I need to re-orient after a `live_ui` call" | `desktop_look` |
| "One explicit pixel-precise click from known coordinates" | `desktop_action` |

## Browser interaction — visual only

**Always interact with browsers visually.** This is the primary way to use the browser with DETM.

- Use `desktop_look` to see what's on screen, then `gui_do` or `desktop_action` to click, scroll, and type
- Use `gui_do("click the search bar")`, `gui_do("scroll down")`, `gui_do("click the first result")` etc.
- Read content by looking at the screenshot — not by extracting it programmatically

**Never use:**
- Browser developer tools or the console (`F12`, `Ctrl+Shift+I`)
- JavaScript execution in the browser (`document.querySelector`, `window.location`, etc.)
- DOM inspection or scraping via CLI tools (`curl`, `wget` for HTML parsing, `lynx`, `w3m`)
- Browser extension APIs or CDP/Playwright/Selenium automation

The entire point is to interact the way a human would — looking at the screen and clicking. If you find yourself wanting to run JS or scrape HTML, stop and use `desktop_look` + `gui_do` instead.

**How to start a browser research session:**
```
→ gui_do("open Firefox", task_id=<id>)              # launch the browser
→ smart_wait(target="window:Firefox", wake_when="browser is open and ready", task_id=<id>)
→ desktop_look(task_id=<id>)                         # confirm it's open
→ gui_do("click the address bar", task_id=<id>)
→ desktop_action(action="type", text="https://google.com", task_id=<id>)
→ desktop_action(action="press_key", text="Return", task_id=<id>)
```

**Typical browser workflow:**
```
→ desktop_look(task_id=<id>)          # see the current browser state
→ gui_do("click the address bar", task_id=<id>)
→ desktop_action(action="type", text="https://youtube.com", task_id=<id>)
→ desktop_action(action="press_key", text="Return", task_id=<id>)
→ smart_wait(target="window:browser", wake_when="page has loaded", task_id=<id>)
→ desktop_look(task_id=<id>)          # see what loaded
→ gui_do("click the search bar", task_id=<id>)
→ desktop_action(action="type", text="fitness supplement creators", task_id=<id>)
→ desktop_action(action="press_key", text="Return", task_id=<id>)
→ smart_wait(target="window:browser", wake_when="search results have loaded", task_id=<id>)
→ desktop_look(task_id=<id>)          # read results from the screenshot
→ task_update(task_id=<id>, message="Search results show: ...")
```

**Scrolling to read more:**
```
→ gui_do("scroll down", task_id=<id>)
→ desktop_look(task_id=<id>)          # see what's now visible
```

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

## Revising the plan mid-task

If reality diverges from the original plan, scrap the stale items and append revised ones. Scrapped items appear struck-through in the dashboard — the human can see exactly what changed and why.

```
# Something unexpected happened — revise the plan
task_item_update(task_id=<id>, ordinal=2, status="scrapped", note="Export dialog missing — app not open yet")
task_item_update(task_id=<id>, ordinal=3, status="scrapped", note="depends on ordinal 2")
task_plan_append(task_id=<id>, items=["Launch DaVinci Resolve", "Wait for project to load", "Open export dialog", "Set 4K and export"], note="Revised: app was not running")
task_item_update(task_id=<id>, ordinal=4, status="active")
```

Use `scrapped` (not `skipped`) when you are abandoning an item because the approach changed. Use `skipped` only when an item is intentionally bypassed but the plan remains valid.

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
desktop_action(action="click", x=847, y=523, task_id=<id>)
task_update(task_id=<id>, message="Export button clicked. Render dialog appeared with codec options.")
```

**Typing into a text field — ALWAYS click first, then type:**
```
task_log_action(task_id=<id>, action_type="gui", summary="Entering filename in the Save dialog", status="started")
desktop_action(action="click", x=640, y=400, task_id=<id>)   # click the input field
desktop_action(action="type", text="my_project_v2", task_id=<id>)   # type the text
desktop_action(action="press_key", text="Return", task_id=<id>)    # confirm
task_update(task_id=<id>, message="Typed filename and pressed Enter. Dialog closed.")
```

**Key combos (select all, copy, paste, undo):**
```
desktop_action(action="press_key", text="ctrl+a", task_id=<id>)   # select all
desktop_action(action="press_key", text="ctrl+c", task_id=<id>)   # copy
desktop_action(action="press_key", text="ctrl+v", task_id=<id>)   # paste
desktop_action(action="press_key", text="ctrl+z", task_id=<id>)   # undo
desktop_action(action="press_key", text="Tab", task_id=<id>)       # tab
desktop_action(action="press_key", text="Escape", task_id=<id>)    # escape
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
- `task_item_update` — update plan item status (pending/active/completed/failed/skipped/**scrapped**)
- `task_plan_append` — append new plan items to an existing task (use after scrapping to replace with revised steps)
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
- `gui_do` — execute NL action ("click the Export button", "double-click the timeline clip"). ALWAYS natural language — never raw coordinates. Uses iterative narrowing for precision.
- `gui_find` — locate element by description, return coords without acting

### Desktop Control
- `desktop_action` — explicit pixel coordinates: click(x,y), drag(x1,y1,x2,y2), type, press_key, window management. Use this when you have exact coords from `desktop_look` or `gui_find`.
- `desktop_look` — returns a screenshot **image** directly to you. Use this to observe the current screen state before deciding your next action. You interpret the image yourself — no local vision model is involved.
- `video_record` — record screen/window clip

### Video & Live Vision
- `mavi_understand` — record screen, upload to MAVI, ask a question, get an answer. Use for video-heavy UIs where motion or audio is the key information (TikTok sounds, animation text, live feeds). Pass a specific, concrete prompt. Not for static page reading — use `desktop_look` for that.
- `live_ui` — delegate a complete multi-step UI workflow to a live AI model that watches the screen and acts autonomously. See section below.

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

---

## live_ui — autonomous multi-step UI navigation

`live_ui` delegates a complete UI workflow to a vision AI model. The model watches screenshots and executes actions autonomously until the task is done or it needs to escalate. The active provider is configurable (Gemini Live streaming, or near-realtime OpenRouter VLM loop).

### When to use

| Situation | Use |
|---|---|
| Multi-step flow: login → navigate → fill form → submit | `live_ui` |
| Deep menu navigation (Settings > Network > Proxy > Manual) | `live_ui` |
| You'd write 8+ `desktop_look` + `gui_do` pairs in sequence | `live_ui` |
| Single click or single action | `desktop_action` or `gui_do` |
| Just need to see the screen | `desktop_look` |
| Waiting for something to appear | `smart_wait` |

### Basic usage

```python
result = live_ui(
    task_id=task_id,
    instruction="Click the Settings gear, go to Export, and click Export as PDF",
    timeout=60,
)
```

### With credentials / context

```python
result = live_ui(
    task_id=task_id,
    instruction="Log in with email user@example.com and the password from context. Confirm the inbox loads.",
    context="password: MySecurePass123",
    timeout=60,
)
```

### Longer workflow

```python
result = live_ui(
    task_id=task_id,
    instruction=(
        "Open the contact form at the bottom of the page. "
        "Fill in: Name='Surge Energy', Email='hello@surgeenergy.co.uk', "
        "Message='Partnership inquiry — we are a UK energy drink brand'. "
        "Click Submit and confirm the success message appears."
    ),
    timeout=90,
)
```

### Handling the result

```python
result = live_ui(task_id=task_id, instruction="...", timeout=60)

if result.get("error"):
    # Hard error: API key missing, timeout, WebSocket failure
    task_update(task_id=task_id, message=f"live_ui error: {result['error']}")

elif result.get("escalated"):
    # Model hit a blocker and needs human input
    task_update(task_id=task_id, message=(
        f"live_ui escalated: {result['escalation_reason']}. "
        "Please resolve in the browser, then reply to continue."
    ))
    # Poll task_update(query="status") until user confirms, then retry

elif result.get("success"):
    task_update(task_id=task_id, message=f"live_ui done: {result['summary']}")

else:
    task_update(task_id=task_id, message=f"live_ui failed: {result['summary']}")
```

### Common escalation scenarios

- **Login / auth wall** — site is asking for credentials you don't have in context
- **CAPTCHA** — solver required before proceeding
- **2FA prompt** — needs SMS/email code from user
- **Unexpected page** — navigation led somewhere unexpected, human needs to reorient

When escalated, tell the user what the model reported, ask them to resolve it in the browser (VNC on display :99), and poll `task_update(query="status")` until they confirm.

---

## mavi_understand — video content intelligence

Record the screen for a few seconds and ask a question about what's visible and audible.

### When to use vs desktop_look

| Information type | Tool |
|---|---|
| Static UI: buttons, text, layouts, search results | `desktop_look` |
| Playing video: sound name, audio content, song | `mavi_understand` |
| Animation text, captions on moving content | `mavi_understand` |
| Live feed, video player, TikTok video | `mavi_understand` |

### Examples

```python
# What sound is playing in a TikTok video?
mavi_understand(
    task_id=task_id,
    prompt="What is the name of the sound or song playing in the TikTok video on screen?",
    duration_seconds=10,
)

# Hashtags in video captions
mavi_understand(
    task_id=task_id,
    prompt="What hashtags are shown in the captions of the videos currently on screen?",
    duration_seconds=8,
)

# Video style analysis
mavi_understand(
    task_id=task_id,
    prompt="Describe the visual format, content style, and any text overlays in the video playing.",
    duration_seconds=12,
)
```

Write a specific, concrete prompt — vague prompts give vague answers.
