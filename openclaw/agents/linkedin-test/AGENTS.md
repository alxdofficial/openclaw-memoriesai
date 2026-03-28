# AGENTS.md — linkedin-test

## Hard Rules

1. **Use DETM for LinkedIn interaction.** LinkedIn requires authentication and visual interaction — use `gui_agent` for navigating profiles, filling forms, and clicking buttons. CLI tools (bash, python, curl for public URLs) are fine for supporting tasks like data processing.

2. **DETM always.** Every task starts with `task_register`. Log every action with `task_log_action` before executing it. Narrate what you see after every `desktop_look`.

3. **No memory persistence.** Do NOT read `MEMORY.md`, `memory/*.md`, or any prior session context. Do NOT write memory files. Every run is independent.

5. **Human handoff for auth gates.** If you encounter a login page, 2FA prompt, CAPTCHA, or any robot-check blocker, do NOT give up or try workarounds. Instead:
   - Take a `desktop_look` screenshot so the human can see what's blocking you.
   - Post `task_update(message="Auth gate: <description>. Please complete this manually and tell me when to continue.")`.
   - Wait for the human to respond before resuming. Do not retry, skip, or abandon the task.

## Tool Preferences (in order)

1. **`gui_agent`** — preferred for all GUI interactions: logging in, navigating profiles, filling forms, scrolling through feeds, clicking buttons, typing text, multi-step sequences. Give it a clear instruction and let it handle the workflow. It uses Gemini Flash for reasoning and UI-TARS for precise cursor placement.

2. **`desktop_look`** — use for observation only. Take a screenshot, interpret what you see, narrate it via `task_update`, then decide your next action.

3. **`desktop_action`** — use for raw keyboard shortcuts (e.g., Ctrl+T for new tab, Page Down to scroll) or when you already have exact coordinates from `desktop_look`.

4. **`smart_wait`** — use when waiting for page loads, spinners to disappear, or content to render. Target `window:<browser_name>` not `screen`.

## Launching apps

Always launch applications via CLI (e.g. `firefox https://linkedin.com &`), not by asking gui_agent to find and open them. The desktop is minimal — no curated dock or app menus. See the base DETM skill for common launch commands.

## LinkedIn-Specific Tips

- LinkedIn has aggressive cookie/login banners — handle them visually (click Accept, dismiss overlays) before proceeding.
- Pages load progressively — use `smart_wait` after navigation to ensure content is rendered before reading.
- Profile pages, search results, and feed all have different layouts — take a `desktop_look` after each navigation to orient yourself.
- If you get a CAPTCHA or rate-limit wall, escalate immediately via `task_update(status="blocked", message="...")` — do not retry blindly.
- Scroll with Page Down via `desktop_action` rather than trying to click scroll bars.
- If a button (Send, Connect, Message) is at the bottom edge of the screen, use `key_press("Return")` or scroll the page first — clicking near the taskbar will miss.

## Task Workflow

```
1. task_register(name="...", plan=[...], agent_id="linkedin-test")
2. task_item_update(ordinal=0, status="active")
3. For each plan item:
   a. task_log_action(action_type="...", summary="About to do X", status="started")
   b. Execute tool (gui_agent / desktop_look / desktop_action)
   c. task_update(message="I see X, next I will do Y")
   d. task_item_update(ordinal=N, status="completed")
4. task_update(status="completed", message="Done: <summary>")
```

## Never create a new task for unfinished work

If a plan item fails, do NOT create a second task to redo it. Stay in the current task:
- Use `task_plan_append` to add new steps addressing the failure.
- Use `task_item_update(status="scrapped")` on items that no longer apply.
- Continue working within the same task until all items are done or you escalate.
