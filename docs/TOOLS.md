# Tool Specifications

## Tool 1: `task_register`

Register a new long-running task with a plan.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | yes | Human-readable task name |
| `plan` | string[] | yes | Ordered checklist of steps |
| `metadata` | object | no | Arbitrary key-value pairs (e.g., `{ repo: "coursefolio", branch: "main" }`) |

### Returns

```json
{
  "task_id": "abc123",
  "name": "Deploy coursefolio to production",
  "status": "active",
  "plan": ["Build Docker image", "Push to registry", "SSH into server", "Pull and run", "Verify"],
  "created_at": "2026-02-13T17:00:00Z"
}
```

### Example usage by the LLM

```
I need to deploy coursefolio. Let me register this as a task so I don't lose track.

→ task_register({
    name: "Deploy coursefolio to production",
    plan: [
      "1. Build Docker image",
      "2. Push to ghcr.io",
      "3. SSH into server",
      "4. Pull image and run container",
      "5. Verify site is live at coursefolio.com"
    ]
  })
```

---

## Tool 2: `task_update`

Report progress on a task OR query current state.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `task_id` | string | yes | The task to update/query |
| `message` | string | no* | Progress update (e.g., "Step 2 done — pushed to ghcr.io") |
| `query` | string | no* | Question about the task (e.g., "what have I done so far?") |
| `status` | string | no | Set task status: "active", "paused", "completed", "failed" |

*At least one of `message` or `query` is required.

### Returns (for updates)

```json
{
  "task_id": "abc123",
  "message_count": 4,
  "status": "active",
  "acknowledged": true
}
```

### Returns (for queries)

```json
{
  "task_id": "abc123",
  "status": "active",
  "summary": "Completed steps 1-3 (build image, push to registry, SSH into server). Currently on step 4. Remaining: pull and run container, verify site. No blockers noted.",
  "plan_progress": {
    "completed": [1, 2, 3],
    "current": 4,
    "remaining": [5]
  },
  "last_update": "2026-02-13T17:15:00Z"
}
```

### Example usage by the LLM

```
# Reporting progress
→ task_update({ task_id: "abc123", message: "Step 1 done — built image, tagged v1.2.3" })
→ task_update({ task_id: "abc123", message: "Step 2 done — pushed to ghcr.io/alex/coursefolio:v1.2.3" })

# After context compaction, the LLM lost track
→ task_update({ task_id: "abc123", query: "where am I on this task?" })
← "Completed steps 1-2. Next: SSH into server."

# Marking complete
→ task_update({ task_id: "abc123", message: "All done — site is live", status: "completed" })
```

---

## Tool 3: `task_list`

List active (or all) tasks.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `status` | string | no | Filter by status. Default: "active" |
| `limit` | int | no | Max results. Default: 10 |

### Returns

```json
{
  "tasks": [
    {
      "task_id": "abc123",
      "name": "Deploy coursefolio to production",
      "status": "active",
      "plan_steps": 5,
      "messages": 3,
      "last_update": "2026-02-13T17:15:00Z"
    }
  ]
}
```

---

## Tool 4: `smart_wait`

Delegate waiting to the vision daemon. The agent's current run ends; the daemon monitors the target and wakes the agent when the condition is met.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `target` | string | yes | What to watch. Format: `window:<name_or_id>`, `pty:<session_id>`, or `screen` |
| `wake_when` | string | yes | Natural language description of the condition to watch for |
| `timeout` | int | no | Seconds before giving up. Default: 300 |
| `task_id` | string | no | Link this wait to a task (auto-posts update on resolution) |
| `poll_interval` | float | no | Base polling interval in seconds. Default: 2.0 |

### Returns (immediately)

```json
{
  "wait_id": "wait-789",
  "status": "watching",
  "target": "window:Firefox",
  "message": "Monitoring. I'll wake you when: download completes or error dialog appears. Timeout: 300s."
}
```

### Wake event (injected into OpenClaw session when condition met)

```
[system] smart_wait resolved (wait-789): Download completed — file "report.pdf" 
saved to ~/Downloads. Elapsed: 47s.
```

### Wake event (on timeout)

```
[system] smart_wait timeout (wait-789): Condition not met after 300s. 
Last observation: Download progress bar at ~85%, appears stalled.
```

### Example usage by the LLM

```
I've clicked the download button in Firefox. Now I need to wait for it to finish.

→ smart_wait({
    target: "window:Firefox",
    wake_when: "File download completes (progress bar disappears or 'Download complete' notification appears), or an error dialog/message appears",
    timeout: 120,
    task_id: "abc123"  // links to the deploy task
  })

← { wait_id: "wait-789", status: "watching", ... }

# Agent's run ends here. 47 seconds later:
# [system event injected] smart_wait resolved (wait-789): Download completed...
# Agent wakes up and continues.
```

### Target types

**`window:<name_or_id>`** — Watch a specific X11/Wayland window
- By title substring: `window:Firefox`, `window:VS Code`
- By X11 window ID: `window:0x4200003`
- The daemon resolves the name to a window ID using `xdotool search --name`

**`pty:<session_id>`** — Watch a terminal session
- References an OpenClaw exec session (from `process` tool)
- Uses text matching first (fast path), vision fallback for complex criteria
- Example: `pty:crisp-ridge` (the exec session ID)

**`screen`** — Watch the full desktop
- Captures the entire screen
- Useful for OS-level events (notification popups, dialog boxes)

---

## Tool 5: `memory_recall` *(Phase 2)*

Search the user's screen recording history for procedural knowledge.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | yes | What to search for (e.g., "how to add an SSH key on GitHub") |
| `limit` | int | no | Max video segments to return. Default: 3 |
| `recency_bias` | float | no | 0.0-1.0, how much to prefer recent recordings. Default: 0.5 |

### Returns

```json
{
  "results": [
    {
      "timestamp": "2026-02-10T15:42:00Z",
      "duration_seconds": 45,
      "confidence": 0.92,
      "summary": "User added an SSH key to GitHub",
      "steps": [
        "Opened Firefox, navigated to github.com/settings/keys",
        "Clicked 'New SSH key'",
        "Pasted key content from clipboard into the Key field",
        "Set title to 'alxdws2'",
        "Clicked 'Add SSH key'",
        "Confirmed with password"
      ],
      "video_url": "memories://recordings/2026-02-10/segment-447.mp4",
      "video_start_ms": 142000,
      "video_end_ms": 187000
    }
  ]
}
```

This tool depends on:
1. The screen recording extension being active
2. Memories AI having indexed the recordings
3. Memories AI API credentials being configured

---

## Tool 6: `wait_update`

Resume or modify an existing wait job. Use after being woken — if the condition wasn't fully met, send the job back to waiting with updated criteria or a longer timeout.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `wait_id` | string | yes | The wait job to update |
| `wake_when` | string | no | Updated condition (replaces the original) |
| `timeout` | int | no | New timeout in seconds (resets the clock) |
| `message` | string | no | Note to attach (logged in job history, e.g., "Got woken but page still loading, extending wait") |

### Returns

```json
{
  "wait_id": "wait-789",
  "status": "watching",
  "message": "Resumed. Watching for: page fully loaded and data table visible. New timeout: 60s."
}
```

### Example usage by the LLM

```
# Agent got woken: "smart_wait resolved: Page started loading"
# But it checks the screenshot and the data table hasn't rendered yet.

→ wait_update({
    wait_id: "wait-789",
    wake_when: "Data table is fully rendered with rows visible, not just a loading spinner",
    timeout: 60,
    message: "Page loaded but table still rendering — refining condition"
  })

# Agent goes back to sleep. Woken again when the table appears.
```

---

## Tool 7: `wait_cancel`

Cancel an active wait job. Use when the wait is no longer needed — the agent found another way, the task was abandoned, or the agent is satisfied and doesn't need to wait anymore.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `wait_id` | string | yes | The wait job to cancel |
| `reason` | string | no | Why it's being cancelled (logged in job history) |

### Returns

```json
{
  "wait_id": "wait-789",
  "status": "cancelled",
  "message": "Wait cancelled. Reason: Task completed via alternative approach."
}
```

### Example usage by the LLM

```
# Agent was waiting for a download, but the user said "never mind, I found it elsewhere"

→ wait_cancel({
    wait_id: "wait-789",
    reason: "User provided the file directly, download no longer needed"
  })
```

---

## Tool Design Principles

The tools are designed so the main LLM **wants** to use them and can use them intuitively:

### 1. Natural language everywhere
All conditions (`wake_when`), queries (`query`), and reasons (`reason`) are free-form natural language. No regex, no structured conditions. The LLM writes what it would say to a human: "wake me when the download finishes or an error pops up."

### 2. Clear lifecycle verbs
- **`task_register`** → start tracking something
- **`task_update`** → report progress or ask a question
- **`smart_wait`** → delegate waiting
- **`wait_update`** → refine a wait (woken too early, adjust criteria)
- **`wait_cancel`** → stop waiting (satisfied or changed mind)
- **`task_list`** → what am I working on?
- **`memory_recall`** → have I seen this before?

### 3. Everything returns actionable info
No opaque IDs without context. Every response includes a human-readable `message` field that tells the LLM what just happened and what to expect next.

### 4. Forgiving defaults
- `timeout` defaults to 300s (not infinity — never hang forever)
- `task_update` without `status` keeps the task active
- `wait_cancel` without `reason` still works (reason is optional context)
- Queries return useful answers even if the task history is sparse

### 5. Cross-tool linking
- `smart_wait` accepts `task_id` — resolution auto-posts to the task
- `wait_update` preserves the task link
- The LLM doesn't have to manually coordinate between tools

### 6. The LLM should think of these tools like delegation
The system prompt framing matters. These tools should feel like:
- "I'm starting a complex task → let me write it down (task_register)"
- "Step done → jot it in my notes (task_update)"
- "This will take a while → hand it to my assistant to watch (smart_wait)"
- "My assistant woke me but it's not ready → send them back (wait_update)"
- "Never mind, I don't need to wait anymore (wait_cancel)"
- "What was I doing again? (task_update with query)"
