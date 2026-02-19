## Desktop & GUI Automation — DETM Workflow (Always Use This)

Any task involving desktop apps, GUI interaction, visual monitoring, or Xvfb displays **must** use the `agentic-computer-use` (DETM) skill. Do not use raw shell X11 tricks or improvise — DETM handles per-task displays, vision, and dashboarding.

**Mandatory workflow for desktop tasks:**
1. `task_register(name="...", plan=[...])` — always start with a plan
2. `task_item_update(ordinal=0, status="active")` — activate the first item
3. Before each action: `task_log_action(action_type=<type>, summary="About to do X", status="started")`
4. Execute the tool (`desktop_look`, `gui_do`, `desktop_action`, `smart_wait`, etc.)
5. After each observation: `task_update(message="I see X, therefore I will do Y")`
6. `task_item_update(ordinal=N, status="completed")` when item is done
7. `task_update(status="completed")` to close the task

**Never skip narration.** The human watches the dashboard in real time. Every action must be announced before it happens.

**`desktop_look` returns the image directly to you** — you interpret it yourself. After calling it, always narrate what you see via `task_update(message=...)`.

**`smart_wait` is for long-running visual checks** (downloads, renders, builds). Use `window:<name>` not `screen` when possible. The local Ollama model does the vision polling — you just get woken up when done.

Dashboard: `http://127.0.0.1:18790/dashboard` — Alex monitors this.
