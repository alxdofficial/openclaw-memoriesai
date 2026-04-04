---
name: agentic-computer-use
description: Desktop Environment Task Manager (DETM) — hierarchical task tracking, smart visual waiting, GUI automation with NL grounding, and screen recording. Use for multi-step desktop workflows, visual app control, and long-running operations.
---

# agentic-computer-use — DETM

Local daemon on `127.0.0.1:18790` with hierarchical task persistence, async wait engine, GUI grounding, and pluggable vision.

## Architecture — who owns what

Three layers, each with a clear responsibility:

**You (OpenClaw LLM)** — own the plan and the user. You decide what needs to happen, break it into steps, create the DETM task, and talk to the user. You have access to everything: CLI, web search, file system, DETM tools, and any other OpenClaw tools. During a DETM task, you are free to mix GUI actions with CLI commands, web searches, file writes, or any other tool — DETM handles visual GUI interaction, but that is not the only way to get things done. Use whatever is fastest and most reliable.

**Supervisor (Gemini Flash)** — owns each action loop. When you call `gui_agent`, the supervisor takes over the screen. It sees screenshots, decides what to click/type/scroll, and uses the grounding model to place the cursor precisely. It verifies each action landed correctly before proceeding. It does NOT plan, does NOT know the broader task, and has NO access to CLI, files, or web search. It executes one concrete instruction and reports back.

**Grounding model (UI-TARS)** — owns cursor placement. Given a screenshot and a target description ("the Submit button"), it predicts pixel coordinates. The supervisor verifies the placement visually and decides whether to click or retry. UI-TARS does no reasoning — it just points.

```
You (plan, decide, talk to user)
  → gui_agent("click search bar, type hello, press Enter")
    → Supervisor (screenshot → decide action → verify)
      → UI-TARS (find element → return coordinates)
    → Supervisor returns {success, summary, actions_log}
  → You read result, take desktop_look if needed, decide next step
```

**Key principle:** DETM is one of your tools, not your only tool. If a website is broken and the fix is `pkill firefox && firefox <url> &`, do that via CLI. If you need to check whether a URL is up before navigating to it, use `curl`. The supervisor is fast at GUI manipulation but blind to everything else — you are the one with the full picture. All GUI interaction (clicking, typing, scrolling, keyboard shortcuts) goes through `gui_agent`. For CLI commands, use your native `Bash`/`exec` tools directly.

## When to use DETM vs. other tools

**Default to faster tools first.** DETM is powerful but slow. Always prefer built-in tools when they can do the job.

| Situation | Use instead of DETM |
|---|---|
| General web research, finding facts, news, documentation | `web_search` or `WebFetch` |
| Public APIs with known endpoints | Direct HTTP calls via `Bash` or `httpx` |
| File operations, data processing, code execution | `Bash`, Python, CLI tools |
| Publicly accessible URLs with no login | `WebFetch` or `curl` |
| Simple web page with no bot detection | OpenClaw `browser` tool (faster, DOM-level) |

**Use DETM when:**
- The site requires **authentication** (login, session cookies) — DETM uses a real Firefox session that the user can log into via VNC
- The site has **bot detection** (TikTok, LinkedIn, Instagram, most social media) — OpenClaw's `browser` tool uses headless Chrome with CDP, which many sites detect and block. DETM uses a real Firefox window on a real X11 display, which is much harder to fingerprint
- You need to interact with **non-browser apps** (DaVinci Resolve, LibreOffice, file manager, etc.)
- The user needs to **see what's happening** in real time (VNC + dashboard)
- You need the user to **take over** at any point (login, CAPTCHA, 2FA) — DETM runs on a visible desktop the user can VNC into

**DETM vs. OpenClaw browser tool:**
| | DETM (`gui_agent`) | OpenClaw `browser` |
|---|---|---|
| How it works | Screenshots + visual grounding (pixel-level) | CDP + accessibility tree (DOM-level) |
| Browser | Firefox on real X11 display | Headless Chromium |
| Bot detection | Hard to detect — real browser, real display | Often detected — headless Chrome fingerprint |
| Auth / login | User logs in via VNC, session persists | Requires cookies/credentials in config |
| Non-browser apps | Yes — any app on the desktop | No — Chrome only |
| Speed | Slower (screenshot per action) | Faster (DOM operations) |
| User takeover | Yes — VNC into display :99 | No |

**Rule of thumb:** If the site might block bots, or the user might need to log in or solve a CAPTCHA, use DETM. If it's a simple public page with no bot detection, use the `browser` tool or `WebFetch`.

**NEVER use the `browser` tool for these platforms — always use DETM `gui_agent`:**
- **TikTok** — aggressive bot detection, blocks headless Chrome
- **LinkedIn** — requires auth, blocks headless Chrome
- **Instagram** — requires auth, blocks headless Chrome
- **Twitter/X** — aggressive bot detection
- **Any site where the user says "use DETM"** — respect the explicit request

This is a hard rule. Even if the `browser` tool seems faster or easier, these platforms will block it or return incomplete data. Use DETM `gui_agent` which runs a real Firefox on a real display.

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
`task_register` MUST be your first call for any work that involves `desktop_look`, `gui_agent`, or `smart_wait`. Without a task, the human cannot cancel you and the dashboard shows nothing.

```
task_register(name="Find reporters on LinkedIn", plan=["Search LinkedIn", "Collect profiles", "Write to sheet"])
task_item_update(task_id=<id>, ordinal=0, status="active")
```

**2. Log desktop actions with `task_log_action`.**
Before each `gui_agent` or `desktop_look` call, log what you're about to do. After, report what you observed via `task_update`. This is how the dashboard shows your progress.

```
task_log_action(task_id=<id>, action_type="gui", summary="Searching LinkedIn for reporters")
gui_agent(instruction="Click the search bar, type TechCrunch AI reporters, press Enter", task_id=<id>)
task_update(task_id=<id>, message="Search submitted, results loading.")
```

You do NOT need to log non-desktop tool calls (web_search, file reads, bash commands). Only log DETM tool calls.

**3. Pass `task_id` to every desktop/GUI/wait call.**

**4. Verify visually — don't assume failure.**
`gui_agent` calls can take 30-120 seconds. If a call times out or returns an unclear result, **do NOT assume it failed.** Always take a `desktop_look` to see the actual screen state before deciding what to do next. The task often succeeded — the response just took too long to arrive.

```
# gui_agent timed out or returned empty:
# WRONG — blindly retry:
gui_agent(instruction="same thing again", task_id=<id>)  # wastes time, page already loaded

# RIGHT — look first, then decide:
desktop_look(task_id=<id>)  # see what actually happened
# If the page loaded → continue to next step
# If it genuinely failed → try a different approach
```

**`desktop_look` is your ground truth.** When in doubt about what happened, look at the screen. Don't reason from error messages alone — the screen shows the real state.

If a step genuinely fails, do NOT create a new task. Use `task_plan_append` to add corrective steps and `task_item_update(status="scrapped")` on stale items.

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
| Launch an app | CLI via `Bash` (`firefox &`, `thunar &`) |
| See the current screen state | `desktop_look` |
| Any GUI interaction (click, type, scroll, keyboard shortcuts) | `gui_agent` |
| Wait for something to appear/finish | `smart_wait` |
| Read content from a public URL | `curl` or `WebFetch` (faster than screenshotting) |
| File operations, shell commands | `Bash` / `exec` (OpenClaw native) |

### gui_agent — deterministic GUI steps

`gui_agent` is a fast vision-based executor. It handles the screenshot→action→verify loop using a vision model (Gemini Flash) and a grounding model (UI-TARS). It does NOT plan or reason about the broader task — that's your job.

**Give it concrete, deterministic instructions (3-8 GUI steps).** Don't dump a whole task on it — break your work into small, verifiable chunks and check in between each.

```
# Good — concrete, verifiable:
gui_agent(instruction="Click the search bar on Google, type 'flights NYC to London', press Enter", task_id=<id>)
# → check results with desktop_look → decide next step

gui_agent(instruction="Click the 'Nonstop' filter checkbox in the left sidebar", task_id=<id>)
# → check results with desktop_look → decide next step

# Bad — too broad, supervisor can't recover if anything goes wrong:
gui_agent(instruction="Search for flights, filter nonstop, select the cheapest, and proceed to booking", task_id=<id>)
```

**After each gui_agent call, take a `desktop_look`** to see what actually happened. The gui_agent result includes `{success, summary, actions_taken, actions_log}` — read the `actions_log` to understand what was tried, especially on failure.

**When gui_agent fails:** read the `actions_log` and `summary` carefully. It tells you what the supervisor tried and why it gave up. Use that to decide:
- Retry with a different instruction (e.g. keyboard shortcut instead of clicking)
- Fix the underlying problem via CLI (restart browser, navigate via URL bar)
- Take a `desktop_look` to see the current state and replan

**gui_agent cannot access CLI, files, or web search.** If the problem requires anything outside the GUI (checking a URL, clearing cache, restarting an app), you must do it yourself, then send gui_agent back in.

### desktop_look — observe and decide

Take a screenshot and reason about it yourself. No model is invoked — you interpret the image directly. Use this to read content from the screen, check results, or understand the current state before deciding what to do next.

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

### Desktop
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
