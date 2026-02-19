# Task Memory + Smart Wait Recovery Spec (Alex-aligned)

**Status:** Updated 2026-02-16 (UTC) and aligned to Alex's described behavior.

## Goal
Make runtime behavior match the intended model:
1. Main LLM registers long-running tasks with a checklist/plan.
2. Task memory stores a thread-like history and progress state.
3. Main LLM continuously narrates updates, marks steps done, and can revise the plan.
4. Main LLM can delegate waiting to smart wait, linked to the task.
5. If task execution stalls, task memory proactively wakes the main LLM with enough context to resume immediately.

---

## Required Behavior

### 1) Task lifecycle model
A task has explicit lifecycle states:
- `active`
- `paused`
- `completed`
- `failed`
- `cancelled` (new; required; accept `canceled` as input alias and normalize to `cancelled`)

Terminal states are: `completed`, `failed`, `cancelled`.
Only `active` tasks are eligible for stuck detection.

#### API requirements
- `task_register(name, plan, metadata?)` creates `active` task.
- `task_update(task_id, ...)` can set any valid status, including `cancelled`.
- Any invalid status returns a validation error.

---

### 2) Plan/checklist behavior
Plan must be mutable with history.

#### Data model additions
- `tasks.plan` remains canonical current checklist.
- Add `task_plan_revisions` table:
  - `id`
  - `task_id`
  - `old_plan_json`
  - `new_plan_json`
  - `reason`
  - `created_at`
  - `author` (`agent` | `system`)

#### API additions
Add either:
- **Option A (preferred):** `task_plan_update(task_id, new_plan, reason)`

or
- **Option B:** extend `task_update` with `plan` + `plan_reason` fields.

#### Behavior
- Plan update writes revision record and updates `tasks.plan`.
- Auto-post a system message in task thread: "Plan revised: <reason>".
- Completed steps are remapped safely:
  - If step identity preserved, keep completion.
  - Otherwise drop ambiguous completion marks and report in response.

---

### 3) Thread/message semantics
Task memory is message-thread-first.

#### Message type normalization
Allowed `msg_type` values:
- `text`
- `lifecycle`
- `progress`
- `wait`
- `stuck`
- `plan`

All smart wait auto-posts MUST use `msg_type='wait'`.

---

### 4) Smart wait task linkage
When a wait is launched with `task_id`:
- wait job stores task linkage
- resolution/timeout/failure writes `wait` message into task thread
- task metadata records active wait IDs for that task

#### Required task metadata keys
- `active_wait_ids: string[]`
- `last_wait_event_at: epoch_seconds`
- `last_wait_state: "watching" | "resolved" | "timeout" | "cancelled" | "error"`

On wait completion/cancel/timeout, remove wait id from `active_wait_ids`.

---

### 5) Stuck-task detection logic (authoritative)
Run periodic loop (current 60s interval acceptable).

A task is stuck iff ALL are true:
1. `status == active`
2. `status` is not terminal (implied)
3. `active_wait_ids` is empty (no ongoing smart wait)
4. `now - task.updated_at >= STUCK_THRESHOLD_SECONDS`

Optional secondary signal (non-blocking):
- N consecutive wait failures/timeouts can raise urgency level but should not replace rule #3.

#### Anti-spam
- Keep cooldown per task (`last_stuck_alert_at`).
- Do not emit repeated alerts inside cooldown window.

---

### 6) Wake payload must include resumable context
When stuck task alert is emitted, wake event must include compact resume packet:
- task id + name + status
- current plan
- completed/current/remaining steps
- last 3–5 meaningful messages
- last wait state summary
- suggested next action (simple heuristic)

#### Format
Emit machine-parseable JSON inside wake text envelope, e.g.:

```text
[task_stuck_resume] { ...json... }
```

Where payload schema is:
```json
{
  "task_id": "...",
  "name": "...",
  "status": "active",
  "progress": {
    "completed": [0,1],
    "current": 2,
    "remaining": [3,4],
    "pct": 40
  },
  "plan": ["..."],
  "recent_messages": [
    {"role":"agent","msg_type":"text","content":"...","created_at":"..."}
  ],
  "wait": {
    "active_wait_ids": [],
    "last_wait_state": "timeout",
    "last_wait_event_at": 1771220000
  },
  "reason": "no updates for 9 minutes and no active wait",
  "suggested_next_action": "Re-open terminal session and check deployment logs"
}
```

---

## Non-functional requirements
- Backward compatible migrations for existing DB.
- No duplicate wake events for same stuck condition in cooldown window.
- All new paths have tests.
- Fail-safe behavior: if enrichment payload building fails, still emit minimal wake.

---

## Implementation Plan (incremental)

### Phase A — Data + API parity
1. Add `cancelled` status support.
2. Add plan revision mechanism (`task_plan_update` or `task_update(plan=...)`).
3. Add `task_plan_revisions` migration.
4. Normalize `msg_type` enum usage and ensure wait writes `msg_type='wait'`.

### Phase B — Wait/task coupling
5. Track `active_wait_ids` in task metadata.
6. Update on wait create/resolve/timeout/cancel.
7. Add last wait state metadata updates.

### Phase C — Stuck engine correctness
8. Replace stuck predicate with authoritative rule set.
9. Keep cooldown + idempotent alerting.

### Phase D — Resume packet wake
10. Build `build_resume_packet(task_id)` helper.
11. Emit `[task_stuck_resume] {json}` via system event.
12. Add tests for payload shape and key fields.

---

## Acceptance Tests

1. **No false stuck while waiting**
   - Create active task, launch smart wait linked to task.
   - Advance time > threshold.
   - Assert no stuck alert while wait remains active.

2. **Stuck after idle + no waits**
   - Active task with no active waits, no updates past threshold.
   - Assert exactly one wake alert emitted with resume packet.

3. **Terminal tasks are ignored**
   - Mark task completed/failed/cancelled.
   - Assert no stuck alerts.

4. **Plan revision recorded**
   - Update plan with reason.
   - Assert revision row exists and system plan message posted.

5. **Wait messages typed correctly**
   - Resolve/timeout smart wait on linked task.
   - Assert thread message has `msg_type='wait'`.

6. **Cooldown prevents spam**
   - Keep task stuck over multiple checks.
   - Assert no duplicate alerts within cooldown.

---

## Notes for OpenClaw integration
- Wake delivery still uses `openclaw system event --mode now`.
- The main LLM should call task recall tools after wake, but resume packet is designed to reduce that extra step.
- Keep wake message concise enough for system-event channels while retaining JSON parseability.

---

## Out of scope (for this spec)
- Full autonomous task resumption execution policy.
- Multi-agent task ownership.
- Cross-session distributed locking.
- UI/dashboard for task state.
