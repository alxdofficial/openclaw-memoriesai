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

**Launch applications via CLI, not gui_agent.** DETM runs on a bare Linux desktop (typically XFCE on a headless VM). Unlike a user's personal machine, the desktop is minimal — no curated dock, no pinned apps, possibly no desktop icons. gui_agent trying to visually find and launch applications is slow (30-60s) and unreliable because there's nothing well-organized to click on. But it's just Linux — every app can be launched instantly from the command line.

**Rule: always launch apps via CLI. Use gui_agent only after the app is open and ready for UI interaction.**

```
# WRONG — gui_agent spends 30-60s hunting for browser icons/menus on a bare desktop:
gui_agent(instruction="Open Firefox and go to google.com", task_id=<id>)

# RIGHT — launch via CLI (instant), then gui_agent for UI interaction:
desktop_action(action="press_key", text="ctrl+alt+t", task_id=<id>)  # open terminal
desktop_action(action="type", text="firefox https://google.com &\n", task_id=<id>)
# ... wait a few seconds for browser to load, then use gui_agent for clicks/typing
```

Common launch commands:
- **Firefox**: `firefox <url> &` or `firefox --new-window <url> &`
- **File manager**: `thunar &` or `thunar <path> &`
- **Terminal**: `xfce4-terminal &` or keyboard shortcut `Ctrl+Alt+T`
- **LibreOffice**: `libreoffice --calc <file> &`, `libreoffice --writer <file> &`
- **Text editor**: `mousepad <file> &` or `xdg-open <file> &`
- **Any app**: use `which <app>` to check if it exists, then run it directly

This principle applies broadly: anything that can be done via CLI should be done via CLI. Only use gui_agent for tasks that require visual interaction (clicking buttons, filling forms, reading UI state).

## HARD RULES — no exceptions

These are not guidelines. Violating them breaks observability, cancellation, and debugging.

**0. When the prompt says "DETM ONLY" — web_search and WebFetch are banned for that task.**
If the human's request contains the phrase **DETM ONLY**, you must do ALL research by opening a real browser visually on the desktop. You may not call `web_search`, `WebFetch`, or any equivalent built-in fetch tool for the duration of that task — not even once, not even "just to verify". The only permitted research method is: use `gui_agent` to navigate the browser, and read content via `desktop_look`. CLI tools (bash, python, ffmpeg, etc.) are still allowed for non-research steps like writing files.

```
# WRONG — even if faster, even if just one call:
web_search("TechCrunch AI reporters")

# RIGHT — launch browser via CLI, then gui_agent for search:
task_log_action(task_id=<id>, action_type="cli", summary="Launching Firefox to google.com", status="started")
desktop_action(action="press_key", text="ctrl+alt+t", task_id=<id>)
desktop_action(action="type", text="firefox https://google.com &\n", task_id=<id>)
gui_agent(instruction="Search for TechCrunch AI reporters", task_id=<id>)
desktop_look(task_id=<id>)
```

**1. Always create a task before touching the desktop.**
`task_register` MUST be your first call for any work that involves `desktop_look`, `desktop_action`, `gui_agent`, or `smart_wait`. No exceptions. Even a single screenshot requires a task.

```
# WRONG — never do this:
desktop_look()
gui_agent(instruction="click the search bar")

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
gui_agent(instruction="click the search bar", task_id=<id>)

# RIGHT — log every tool call first:
task_log_action(task_id=<id>, action_type="cli", summary="Searching web for TechCrunch AI reporters")
web_search("TechCrunch AI reporters 2025")
task_update(task_id=<id>, message="Found 8 results. Top result is techcrunch.com/author/...")

task_log_action(task_id=<id>, action_type="gui", summary="Searching LinkedIn for reporters")
gui_agent(instruction="Click the search bar, type TechCrunch AI reporters, and press Enter", task_id=<id>)
task_update(task_id=<id>, message="Search submitted. Will check results.")

task_log_action(task_id=<id>, action_type="vision", summary="Taking screenshot to read search results")
desktop_look(task_id=<id>)
task_update(task_id=<id>, message="Can see 8 reporter profiles in results: ...")
```

Every tool call = one `task_log_action` before + one `task_update` after. No exceptions.

**3. Pass `task_id` to every desktop/GUI/wait call.**
Every `desktop_look`, `desktop_action`, `gui_agent`, and `smart_wait` call must include `task_id=<id>`. This is how DETM tracks what you're doing and links screenshots to your plan.

**4. Verify each step — but stay decisive.**
After executing a plan item, verify it worked before marking it complete. But be efficient about it:
- **After `gui_agent`**: trust the `{success, summary}` result. `gui_agent` already verified the outcome visually. Only take `desktop_look` if you need to read specific screen content that gui_agent didn't report (e.g., reading search results, checking a number in a table). Do NOT take a screenshot just to "confirm" what gui_agent already told you.
- **After `desktop_action`**: one `desktop_look` to verify is enough. Never take 2+ screenshots back-to-back without acting between them.
- **General rule**: if you catch yourself taking multiple screenshots without making progress, STOP and act. Decide based on what you already know.

If the step failed or partially succeeded:

- Do NOT mark it completed and move to the next step.
- Do NOT create a new task to redo the failed step.
- Instead: use `task_plan_append` to add corrective steps and `task_item_update(status="scrapped")` on items that no longer apply. Keep working within the same task.

```
# WRONG — blindly advancing:
gui_agent(instruction="click Submit", task_id=<id>)
task_item_update(task_id=<id>, ordinal=2, status="completed")  # didn't verify!
task_item_update(task_id=<id>, ordinal=3, status="active")

# RIGHT — verify, then decide:
gui_agent(instruction="click Submit", task_id=<id>)
desktop_look(task_id=<id>)
task_update(task_id=<id>, message="Submit clicked but form shows validation error on email field")
# Don't mark completed — fix the issue first:
task_plan_append(task_id=<id>, items=["Fix email field", "Re-submit form"], note="Form validation failed")
task_item_update(task_id=<id>, ordinal=4, status="active")
```

**5. Move fast — don't stare at the screen.**
The human is watching in real time. Long pauses between actions look broken. Follow these pacing rules:
- After `gui_agent` returns success: immediately proceed to the next step. Do NOT take a `desktop_look` unless you need to read specific content.
- Never take 2+ `desktop_look` calls in a row without an action in between. One screenshot is enough to understand the screen.
- Never log 2+ `reasoning` entries in a row. Make one decision and act on it.
- Batch your work: `task_log_action` → tool call → `task_update` → next action. Don't insert extra screenshots or reasoning between these.
- If you already know what to do next, do it. Don't take a screenshot "just to be safe."

**6. Check task status after each plan item.**
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
| **Launching any application** | **CLI** (`firefox &`, `thunar &`, etc.) | **Instant and reliable. gui_agent wastes 30-60s hunting for icons.** |
| Visual apps (DaVinci Resolve, Blender, VS Code GUI) | `gui_agent` / `desktop_action` | No CLI equivalent for UI interaction |
| File operations, git, builds | CLI/exec | Faster, deterministic |
| Web forms, dialogs, popups | `gui_agent` | Visual interaction required |
| Status monitoring | `smart_wait` with window target | Vision handles any app |
| Scripting, automation | CLI/exec | Structured output |
| Web browsing and research | `gui_agent` / `desktop_look` | See below |

## Choosing between desktop_look, gui_agent, and desktop_action

Two tiers of GUI interaction. Choose based on how much of the sequence you know ahead of time.

### Tier 1 — `desktop_look` (orientation)
Take a screenshot and reason about it yourself. Use this whenever you need to understand the current state of the screen before deciding what to do next. No model is invoked — you interpret the image directly.

```
-> desktop_look(task_id=<id>)   # see what's on screen
-> task_update(...)             # describe what you observe
-> decide your next action
```

### Tier 2 — `gui_agent` (autonomous GUI subtask execution)
AI-powered GUI agent that uses Gemini Flash for reasoning and UI-TARS for precise cursor placement. Handles complex multi-step workflows autonomously — it thinks before each action, grounds the cursor precisely, verifies completion, and recovers from errors on its own.

**Delegate whole subtasks, not individual clicks.** The gui_agent is designed to handle 10-30 step workflows autonomously. Don't micromanage it with one-action instructions — describe the goal and let it figure out the steps. You should check in between subtasks to read results and decide what's next.

**Good — launch via CLI, then delegate subtask to gui_agent:**
```
# Launch browser first (instant):
desktop_action(action="press_key", text="ctrl+alt+t", task_id=<id>)
desktop_action(action="type", text="firefox https://www.google.com/travel/flights &\n", task_id=<id>)
# Then gui_agent for the interaction:
gui_agent(instruction="Search for flights from NYC to London on March 28 and filter for nonstop only", task_id=<id>, timeout=120)
```

**Bad — micromanage individual clicks:**
```
gui_agent(instruction="Click the search bar", task_id=<id>)
desktop_look(task_id=<id>)
gui_agent(instruction="Type NYC to London", task_id=<id>)
desktop_look(task_id=<id>)
gui_agent(instruction="Click the date field", task_id=<id>)
# ... this is 5x slower and less reliable
```

**How to chunk work:** Break the user's overall task into 2-5 subtasks. Each subtask should be a coherent workflow that gui_agent can complete autonomously (~10-30 actions). Check in between subtasks with `desktop_look` when YOU need to read results, make decisions, or report to the user.

**Do not use this when:**
- The next step depends on complex reasoning that only you can do (e.g. reading a report and deciding whether a number is acceptable) — use `desktop_look` instead
- You need explicit pixel-level control — use `desktop_action` with coordinates from `desktop_look`

```
-> gui_agent(task_id=<id>, instruction="Navigate to Settings, find the Export section, set format to PDF with A4 paper size, and click Export", timeout=90)
-> # agent handles the full chain autonomously: thinks, navigates, types, verifies
-> # Trust the result — gui_agent verified the outcome with an independent checker
-> # Only take desktop_look if YOU need to read specific content from the screen
```

### Decision guide

| Situation | Tool |
|---|---|
| "I need to see where I am before deciding anything" | `desktop_look` |
| "Complete this subtask (search, fill form, navigate to page)" | `gui_agent` with full subtask instruction |
| "I need to read results from the screen to decide what's next" | `desktop_look` |
| "One explicit pixel-precise click from known coordinates" | `desktop_action` |

**Rule of thumb:** If you're about to call `gui_agent` 3+ times in a row with `desktop_look` between each, combine them into one `gui_agent` call with a single instruction describing the full sequence.

## Browser interaction

**Prefer visual interaction for browser tasks** — `gui_agent` for clicking/typing/navigating, `desktop_look` for reading screen content. This is the primary workflow for authenticated sites and interactive web apps.

**But be pragmatic.** If gui_agent is struggling or a CLI approach is faster and more reliable, use it:
- `curl`/`wget` to fetch a public URL and extract text — faster than screenshotting and reading
- CLI tools to process downloaded data (grep, jq, python scripts)
- `xdg-open` or `firefox <url>` to navigate directly instead of clicking through menus

**The goal is task completion, not visual purity.** Use GUI when the task requires it (forms, logins, interactive apps). Use CLI when it's faster and the page is publicly accessible.

**How to start a browser research session:**
```
-> # Launch browser via CLI (instant — no gui_agent overhead):
-> desktop_action(action="press_key", text="ctrl+alt+t", task_id=<id>)
-> desktop_action(action="type", text="firefox https://google.com &\n", task_id=<id>)
-> # Wait for browser to load:
-> smart_wait(target="screen", wake_when="Firefox browser window is open and page has loaded", task_id=<id>, timeout=15)
-> # Now use gui_agent for UI interaction:
-> gui_agent(instruction="Click the search bar, type 'fitness supplement creators', and press Enter", task_id=<id>, timeout=90)
```

**Typical browser workflow:**
```
-> # Browser already open from CLI launch above
-> gui_agent(instruction="Search for fitness supplement creators on youtube.com", task_id=<id>, timeout=90)
-> # gui_agent succeeded — now I need to READ the results, so desktop_look is justified:
-> desktop_look(task_id=<id>)          # read results from the screenshot
-> task_update(task_id=<id>, message="Search results show: ...")
```

**Scrolling to read more:**
```
-> gui_agent(instruction="Scroll down on the current page", task_id=<id>)
-> desktop_look(task_id=<id>)          # see what's now visible
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
2. EXECUTE   →  call the tool (desktop_look, gui_agent, desktop_action, etc.)
3. REPORT    →  task_update(task_id=<id>, message="<what I observed or what happened>")
```

Do not skip steps. The announce creates a visible entry in the dashboard. The report records your observation.

### action_type values

| action_type | Use for |
|---|---|
| `vision` | desktop_look, observing the screen |
| `gui` | gui_agent, desktop_action (click/type/key) |
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
- `gui_agent` — AI-powered GUI agent for any desktop interaction. Uses Gemini Flash for reasoning/monitoring and UI-TARS for precise cursor placement. Handles single clicks to complex multi-step workflows autonomously. Unified replacement for legacy gui_do, gui_find, and live_ui tools.

### Desktop Control
- `desktop_action` — explicit pixel coordinates: click(x,y), drag(x1,y1,x2,y2), type, press_key, window management. Use this when you have exact coords from `desktop_look`.
- `desktop_look` — returns a screenshot **image** directly to you. Use this to observe the current screen state before deciding your next action. You interpret the image yourself — no local vision model is involved.
- `video_record` — record screen/window clip

### Video Intelligence
- `mavi_understand` — record screen, upload to MAVI, ask a question, get an answer. Use for video-heavy UIs where motion or audio is the key information (TikTok sounds, animation text, live feeds). Pass a specific, concrete prompt. Not for static page reading — use `desktop_look` for that.

### System
- `health_check` — daemon + vision backend + system status
- `memory_search`, `memory_read`, `memory_append` — workspace memory files

## Examples

### Using desktop_look — observe, describe, act
```
→ desktop_look(task_id=<id>)
  # You receive an image — look at it carefully
→ task_update(task_id=<id>, message="Screen shows DaVinci Resolve with the timeline open. The Export button is visible in the top-right toolbar. I'll click it now.")
→ gui_agent(instruction="click the Export button", task_id=<id>)
→ task_update(task_id=<id>, message="Clicked Export. A render dialog appeared with format options.")
→ desktop_look(task_id=<id>)
  # Observe the dialog
→ task_update(task_id=<id>, message="Export dialog shows H.264 selected. Output path is ~/Desktop/output.mp4. Setting 4K resolution before confirming.")
```

### Multi-step video export with GUI
```
→ task_register(name="Export 4K video", plan=["Open project", "Set export settings", "Start export", "Verify output"])
→ task_item_update(task_id=<id>, ordinal=0, status="active")
→ gui_agent(instruction="Click the Export button in DaVinci Resolve", task_id=<id>)
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
- Task name, status, progress percentage, **agent_id** (which agent owns the task)
- Completed/current/remaining plan items
- The active item expanded with full action details and logs
- Last 5 messages from the task thread
- Wait state summary

When you receive this event, use the resume packet to orient yourself and continue the task. You do NOT need to call `task_summary` — the packet already contains everything.

### Sub-agent routing on resume

If the resume packet contains `agent_id` (e.g., `"linkedin-test"`), you MUST spawn that agent to continue the task — do not resume it yourself. Use:
```
/subagents spawn <agent_id> "Resume DETM task <task_id>. <paste the resume packet or a summary of it>"
```
The sub-agent has its own SOUL.md and AGENTS.md with specialized instructions for that task type. It will pick up where it left off using the resume context.

### Human handoff resumption

Some tasks pause for human intervention (e.g., completing a login, CAPTCHA, or 2FA). When the human says they're done and you should continue:
1. Call `task_summary(task_id=<id>)` or check the resume packet to see current state.
2. If the task has an `agent_id`, spawn that sub-agent with the resume context.
3. If no `agent_id`, resume the task yourself using the resume packet.

## Logs & debugging
- Debug log: `~/.agentic-computer-use/logs/debug.log`
- Set `ACU_DEBUG=1` for verbose colored logging
- Both the human and Claude Code can tail the log file
- Use `./dev.sh logs` for live log tail

## Storage
- Data: `~/.agentic-computer-use/`
- Database: `~/.agentic-computer-use/data.db`

---

## gui_agent — AI-Powered GUI Agent

Autonomous GUI agent for any desktop interaction. Uses Gemini Flash for reasoning/monitoring and UI-TARS for precise cursor placement via iterative refinement.

### When to use

| Situation | Use |
|---|---|
| Any GUI interaction (click, type, scroll, navigate) | `gui_agent` |
| Multi-step flow: login, navigate, fill form, submit | `gui_agent` |
| Deep menu navigation (Settings > Network > Proxy > Manual) | `gui_agent` |
| Single click or single action | `gui_agent` |
| Just need to see the screen | `desktop_look` |
| Waiting for something to appear | `smart_wait` |
| Explicit pixel-precise control from known coordinates | `desktop_action` |

### Parameters

- `instruction` (required): What to accomplish
- `task_id`: Link to active task for logging
- `timeout`: Max seconds (default 180, max 300)
- `context`: Extra context (credentials, URLs, prior state)

### Returns

`{success, summary, escalated, escalation_reason, actions_taken, session_id, elapsed_s}`

### Subtask delegation (recommended)

Give gui_agent a whole subtask with a clear goal. It will plan and execute autonomously. **Always launch the app via CLI first** — gui_agent should only handle UI interaction within an already-open app.

```python
# Launch browser via CLI first (instant)
desktop_action(action="press_key", text="ctrl+alt+t", task_id=task_id)
desktop_action(action="type", text="firefox https://www.google.com/travel/flights &\n", task_id=task_id)

# Subtask 1: Search for flights (app already open)
result = gui_agent(
    task_id=task_id,
    instruction="In the Google Flights page, search for nonstop flights from NYC JFK to London Heathrow on March 28 for 1 adult, and wait for results to load",
    timeout=120,
)

# Check in: read results and decide
desktop_look(task_id=task_id)
task_update(task_id=task_id, message="Flight results loaded. Cheapest is $450 on BA. Will select it.")

# Subtask 2: Select and proceed
result = gui_agent(
    task_id=task_id,
    instruction="Select the cheapest nonstop flight and proceed to the booking page",
    timeout=90,
)
```

### With credentials / context

```python
result = gui_agent(
    task_id=task_id,
    instruction="Log in with email user@example.com and the password from context. Wait for the inbox to load and confirm you can see emails.",
    context="password: MySecurePass123",
    timeout=90,
)
```

### Complex workflow as a single subtask

```python
result = gui_agent(
    task_id=task_id,
    instruction=(
        "Navigate to the Contact page, fill in the form with: "
        "Name='Surge Energy', Email='hello@surgeenergy.co.uk', "
        "Message='Partnership inquiry -- we are a UK energy drink brand'. "
        "Click Submit and confirm the success message appears."
    ),
    timeout=90,
)
```

### Handling the result

```python
result = gui_agent(task_id=task_id, instruction="...", timeout=60)

if result.get("error"):
    task_update(task_id=task_id, message=f"gui_agent error: {result['error']}")

elif result.get("escalated"):
    task_update(task_id=task_id, message=(
        f"gui_agent escalated: {result['escalation_reason']}. "
        "Please resolve in the browser, then reply to continue."
    ))

elif result.get("success"):
    task_update(task_id=task_id, message=f"gui_agent done: {result['summary']}")

else:
    task_update(task_id=task_id, message=f"gui_agent failed: {result['summary']}")
```

### Common escalation scenarios

- **Login / auth wall** -- site is asking for credentials you don't have in context
- **CAPTCHA** -- solver required before proceeding
- **2FA prompt** -- needs SMS/email code from user
- **Unexpected page** -- navigation led somewhere unexpected, human needs to reorient

When escalated, tell the user what the agent reported, ask them to resolve it in the browser (VNC on display :99), and poll `task_update(query="status")` until they confirm.

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
