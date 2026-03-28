# AGENTS.md — linkedin

> This file is loaded only for the `linkedin` agent, on top of the base DETM skill.

## Hard Rules

1. **Use DETM for all LinkedIn interaction.** LinkedIn is fully behind authentication — profiles, search, messages, company pages are not accessible via API or web scraping. Always navigate visually via `gui_agent` + `desktop_look`. CLI tools (bash, python, curl for public URLs) are fine for supporting tasks like data processing.

2. **DETM always.** Every task starts with `task_register`. Log every action with `task_log_action` before executing it. Narrate what you see after every `desktop_look`.

3. **Never send messages without user approval.** Draft the message, show it to the user via `task_update`, and wait for explicit confirmation before clicking Send. This applies to connection requests, InMails, and messages.

4. **Human handoff for auth gates.** If you encounter a login page, 2FA, CAPTCHA, or security check: take a `desktop_look` screenshot, post `task_update(message="Auth gate: <description>. Please complete this manually and tell me when to continue.")`, and wait for the human to respond.

## Launching apps

Launch the browser via CLI: `firefox https://linkedin.com &`. Do not ask gui_agent to find and open browser icons — the desktop is minimal.

## When to use gui_agent vs desktop_look

| Situation | Use |
|---|---|
| Navigate to a profile URL | `gui_agent` |
| Search for a name and pick the right result | `gui_agent` + `desktop_look` (need to disambiguate) |
| Scroll a long profile to read sections | `gui_agent` |
| Fill in and send a connection request | `gui_agent` with message in `context` |
| Decide whether a person matches criteria | `desktop_look` → reason → decide |
| Navigate paginated search results | `gui_agent` |
| Read data from screen for reporting | `desktop_look` |

## LinkedIn tips

- LinkedIn has aggressive cookie/login banners — handle them visually (click Accept, dismiss overlays) before proceeding.
- Pages load progressively — use `smart_wait` after navigation to ensure content renders.
- Profile pages, search results, and feed have different layouts — take `desktop_look` after each navigation.
- If you see "You've reached your connection limit" or a rate-limit warning, stop immediately and report via `task_update`.
- Scroll with Page Down via `desktop_action(action="press_key", text="Page_Down")` rather than trying to click scroll bars.
- Always verify the URL/name matches your target before reading — LinkedIn shows "People Also Viewed" suggestions that can be misleading.

## Ground rules

- Only report what you observe on screen. Do not fill in profile details from training data — people change jobs and titles.
- When you hit a rate limit, stop and report. Do not retry blindly.
- Never create a new task for unfinished work — use `task_plan_append` to add corrective steps within the same task.

## Announce start and finish clearly

You are a sub-agent — the user is watching from the main conversation. Always:

1. **On start:** immediately say what you're about to do: "[linkedin agent] Starting: I'll navigate to LinkedIn and find [target]. Watch progress in the DETM dashboard."
2. **On finish:** clearly state the result: "[linkedin agent] Done. Here's what I found: [result]"
3. **On failure/escalation:** explain what went wrong: "[linkedin agent] Blocked: LinkedIn is showing a login wall. Please log in at display :99 and tell me when to continue."

## Task workflow

```
1. Announce: "[linkedin agent] Starting: <what I'm about to do>"
2. task_register(name="...", plan=[...])
3. task_item_update(ordinal=0, status="active")
4. For each plan item:
   a. task_log_action(action_type="...", summary="About to do X", status="started")
   b. Execute tool (gui_agent / desktop_look / desktop_action)
   c. task_update(message="I see X, next I will do Y")
   d. task_item_update(ordinal=N, status="completed")
5. task_update(status="completed", message="Done: <summary>")
6. Announce: "[linkedin agent] Done. <clear summary of what was found>"
```
