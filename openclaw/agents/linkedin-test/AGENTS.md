# AGENTS.md — linkedin-test

## Hard Rules

1. **Vision only.** You MUST NOT use `web_search`, `web_fetch`, `browser.snapshot`, `browser.navigate`, or any DOM/API-based browser tool. All interaction with LinkedIn happens through the GUI on the desktop display.

2. **DETM always.** Every task starts with `task_register`. Log every action with `task_log_action` before executing it. Narrate what you see after every `desktop_look`.

3. **No memory persistence.** Do NOT read `MEMORY.md`, `memory/*.md`, or any prior session context. Do NOT write memory files. Every run is independent.

4. **No web search.** Treat every task as `DETM ONLY` — web_search and WebFetch are banned.

5. **Human handoff for auth gates.** If you encounter a login page, 2FA prompt, CAPTCHA, or any robot-check blocker, do NOT give up or try workarounds. Instead:
   - Take a `desktop_look` screenshot so the human can see what's blocking you.
   - Post `task_update(message="Auth gate: <description>. Please complete this manually and tell me when to continue.")`.
   - Wait for the human to respond before resuming. Do not retry, skip, or abandon the task.

## Tool Preferences (in order)

1. **`live_ui`** — preferred for multi-step flows: logging in, navigating profiles, filling forms, scrolling through feeds, multi-click sequences. Give it a clear instruction and let it handle the full workflow.

2. **`gui_do`** — use for single targeted actions when you need precise NL-grounded clicks or typing (e.g., "click the Search button", "type 'machine learning' in the search box"). Uses UITaRS vision grounding.

3. **`desktop_look`** — use for observation only. Take a screenshot, interpret what you see, narrate it via `task_update`, then decide your next action.

4. **`desktop_action`** — use for raw keyboard shortcuts (e.g., Ctrl+T for new tab, Page Down to scroll) or when you already have exact coordinates from `desktop_look`.

5. **`smart_wait`** — use when waiting for page loads, spinners to disappear, or content to render. Target `window:<browser_name>` not `screen`.

## Opening a Browser

Use `desktop_action` to launch Firefox on the shared display:
```
desktop_action(action="key", text="ctrl+t")        # new tab if Firefox is already open
desktop_action(action="shell", text="firefox https://www.linkedin.com &")  # or launch directly
```
Then use `smart_wait(condition="Firefox window is visible and loaded", target="window:Firefox")` before interacting.

## LinkedIn-Specific Tips

- LinkedIn has aggressive cookie/login banners — handle them visually (click Accept, dismiss overlays) before proceeding.
- Pages load progressively — use `smart_wait` after navigation to ensure content is rendered before reading.
- Profile pages, search results, and feed all have different layouts — take a `desktop_look` after each navigation to orient yourself.
- If you get a CAPTCHA or rate-limit wall, escalate immediately via `task_update(status="blocked", message="...")` — do not retry blindly.
- Scroll with Page Down via `desktop_action` rather than trying to click scroll bars.

## Task Workflow

```
1. task_register(name="...", plan=[...], agent_id="linkedin-test")
2. task_item_update(ordinal=0, status="active")
3. For each plan item:
   a. task_log_action(action_type="...", summary="About to do X", status="started")
   b. Execute tool (live_ui / gui_do / desktop_look / desktop_action)
   c. desktop_look to VERIFY the action succeeded
   d. task_update(message="I see X, next I will do Y")
   e. Only if verified: task_item_update(ordinal=N, status="completed")
   f. If step failed: do NOT advance. Fix it or use task_plan_append to add corrective steps.
4. task_update(status="completed", message="Done: <summary>")
```

## Never create a new task for unfinished work

If a plan item fails, do NOT create a second task to redo it. Stay in the current task:
- Use `task_plan_append` to add new steps addressing the failure.
- Use `task_item_update(status="scrapped")` on items that no longer apply.
- Continue working within the same task until all items are done or you escalate.
