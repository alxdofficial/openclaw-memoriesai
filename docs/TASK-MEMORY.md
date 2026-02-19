# Task Memory — Detailed Design

## Overview

Task Memory gives an LLM agent a persistent, queryable record of what it's doing on long-running tasks. Think of it as the agent's notepad — it survives context compaction, session restarts, and interruptions.

## The Problem in Detail

### Context compaction kills task state

OpenClaw compacts conversation history when the context window fills. The compaction summary is good for general conversation flow but loses the granular step-by-step detail needed for task tracking:

**Before compaction (in context):**
```
Agent: Starting deployment. Step 1: Building Docker image...
Tool: exec → docker build -t coursefolio:v1.2.3 . → exit 0
Agent: Image built. Step 2: Pushing to registry...
Tool: exec → docker push ghcr.io/alex/coursefolio:v1.2.3 → exit 0
Agent: Pushed. Step 3: SSHing into server...
Tool: exec → ssh deploy@server → connected
Agent: Connected. Step 4: Pulling image...
```

**After compaction:**
```
Summary: The agent was deploying coursefolio. Several Docker commands were run.
```

The agent no longer knows it completed steps 1-3 and is on step 4.

### The Task Memory solution

With Task Memory, the agent writes progress to an external store as it goes:

```
→ task_register({ name: "Deploy coursefolio", plan: [...] })
→ task_update({ task_id: "abc", message: "Step 1 done — image built as v1.2.3" })
→ task_update({ task_id: "abc", message: "Step 2 done — pushed to ghcr.io" })
→ task_update({ task_id: "abc", message: "Step 3 done — SSH connected to server" })
[... context compaction happens ...]
→ task_update({ task_id: "abc", query: "where was I?" })
← "Completed steps 1-3. Next: Step 4 — pull image and run container."
```

## Data Model

### SQLite Schema (Hierarchical)

The current implementation uses a hierarchical model: Task → Plan Items → Actions → Logs, replacing the earlier flat plan/message design.

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active, paused, completed, failed, cancelled
    metadata TEXT,      -- JSON: active_wait_ids, last_wait_state, display, display_num, etc.
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE plan_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    ordinal INTEGER NOT NULL,           -- 0-based index
    title TEXT NOT NULL,
    status TEXT DEFAULT 'pending',      -- pending, active, completed, failed, skipped
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL
);

CREATE TABLE actions (
    id TEXT PRIMARY KEY,
    plan_item_id TEXT REFERENCES plan_items(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    action_type TEXT NOT NULL,          -- cli, gui, wait, vision, reasoning, other
    summary TEXT NOT NULL,
    status TEXT DEFAULT 'completed',    -- started, completed, failed
    input_data TEXT,                    -- JSON: command, coordinates, screenshots, etc.
    output_data TEXT,                   -- JSON: result, screenshots, elapsed time, etc.
    created_at TEXT NOT NULL
);

CREATE TABLE action_logs (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES actions(id),
    log_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE task_messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL,      -- 'agent', 'system', 'user'
    content TEXT NOT NULL,
    msg_type TEXT NOT NULL,  -- text, lifecycle, progress, wait, stuck, plan
    created_at TEXT NOT NULL
);

CREATE TABLE wait_jobs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    criteria TEXT NOT NULL,
    timeout_seconds INTEGER,
    poll_interval REAL,
    status TEXT NOT NULL,
    result_message TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

### Task metadata keys

| Key | Type | Description |
|-----|------|-------------|
| `active_wait_ids` | string[] | Currently active smart wait job IDs |
| `last_wait_state` | string | watching, resolved, timeout, cancelled, error |
| `last_wait_event_at` | float | Epoch seconds of last wait event |
| `last_stuck_alert_at` | float | Epoch seconds of last stuck alert (cooldown) |
| `display` | string | Per-task Xvfb display string (e.g., ":100") |
| `display_num` | int | Display number |
| `display_resolution` | string | e.g., "1280x720" |

## Distillation Strategy

When the agent queries task state, we need to produce a concise, accurate answer from potentially dozens of update messages.

### Two-tier distillation

**Tier 1: Running Summary (lazy, periodic)**

Every 5 messages (configurable), the daemon regenerates a running summary:

```python
async def update_running_summary(task_id: str):
    task = db.get_task(task_id)
    messages = db.get_task_messages(task_id)
    
    if len(messages) - task.summary_message_count < 5:
        return  # Not enough new messages
    
    prompt = f"""Task: {task.name}
Plan: {json.dumps(task.plan)}

Message history:
{format_messages(messages)}

Write a concise summary of:
1. Which plan steps are completed
2. What step is currently in progress
3. Any blockers or issues noted
4. Key details (versions, URLs, file paths) worth remembering

Be concise — 3-5 sentences max."""

    summary = await model.generate(prompt)
    db.update_task(task_id, running_summary=summary, summary_message_count=len(messages))
```

**Tier 2: Live Query (on-demand)**

When the agent asks a question, we use the running summary + any messages since the last summary:

```python
async def query_task(task_id: str, query: str) -> str:
    task = db.get_task(task_id)
    recent_messages = db.get_messages_since(task_id, task.summary_message_count)
    
    context = f"""Task: {task.name}
Plan: {json.dumps(task.plan)}
Status: {task.status}

Summary (as of {task.summary_message_count} messages ago):
{task.running_summary or "No summary yet."}

Recent updates since summary:
{format_messages(recent_messages) if recent_messages else "None."}

Question: {query}

Answer concisely."""

    return await model.generate(context)
```

### Model selection for distillation

- **Simple queries** ("what step am I on?", "what's next?"): MiniCPM-o locally. Fast, free.
- **Complex queries** ("should I change the plan?", "analyze the errors"): Optional remote model via API (OpenRouter). User-configurable.
- **Default**: Always try local first. Only use remote if explicitly configured.

## Plan Progress Tracking

The daemon attempts to auto-track which plan steps are completed based on update messages:

```python
def infer_plan_progress(plan: list[str], messages: list[TaskMessage]) -> dict:
    """Heuristic: match update messages to plan steps."""
    completed = set()
    
    for msg in messages:
        content_lower = msg.content.lower()
        for i, step in enumerate(plan):
            step_lower = step.lower()
            # Check for completion signals
            if any(signal in content_lower for signal in ["done", "completed", "finished", "✓"]):
                # Check if the step content is referenced
                step_words = set(step_lower.split()) - {"the", "a", "an", "to", "and", "or"}
                content_words = set(content_lower.split())
                overlap = step_words & content_words
                if len(overlap) >= len(step_words) * 0.5:  # 50% word overlap
                    completed.add(i)
    
    completed_list = sorted(completed)
    current = max(completed_list) + 1 if completed_list else 0
    remaining = [i for i in range(len(plan)) if i not in completed and i > current]
    
    return {
        "completed": completed_list,
        "current": current if current < len(plan) else None,
        "remaining": remaining
    }
```

This is best-effort — the distillation model is the authoritative source for progress tracking.

## Integration with Smart Wait

When a `smart_wait` is linked to a task (`task_id` parameter), the wait resolution automatically posts to the task's message history:

```python
# In WaitScheduler.resolve_job():
if job.task_id:
    task_manager.add_message(
        task_id=job.task_id,
        role="system",
        content=f"[Wait resolved] {job.criteria} → {job.result_message}"
    )
```

This keeps the task history complete even for asynchronous events.

## Lifecycle

```
1. Agent calls task_register → task created (status: active)
2. Agent works, calling task_update with progress messages
3. Agent may call smart_wait (linked to task) → wait events auto-logged
4. Agent may lose context (compaction) → calls task_update with query
5. Agent finishes → calls task_update with status: "completed"
6. Old completed tasks auto-archived after 7 days (configurable)
```

## Edge Cases

- **Multiple active tasks**: Supported. The agent can register and track several concurrent tasks.
- **Task abandonment**: Tasks not updated for >24h get auto-paused. The agent (or user) can resume them.
- **Concurrent updates**: SQLite handles this with WAL mode. No complex locking needed.
- **Very long tasks**: Message history is append-only but the running summary keeps the queryable state bounded. Even 1000 messages compress to a ~500-token summary.
