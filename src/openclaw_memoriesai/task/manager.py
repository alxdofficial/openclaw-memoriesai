"""Task manager — persistent task tracking with chat-style history.

Tasks are organized as conversation threads:
  - agent: messages from the LLM ("Starting step 2", "Build failed, retrying")
  - system: auto-generated events (task created, status change)
  - wait: smart_wait results (resolved, timeout, progress)
  - stuck: stuck detection alerts from the daemon

The daemon monitors active tasks and proactively wakes the agent if
a task appears stuck (no progress for too long).
"""
import json
import time
import logging
from .. import db, debug

log = logging.getLogger(__name__)

# Stuck detection config
STUCK_THRESHOLD_SECONDS = 300   # 5 min with no updates → potentially stuck
STUCK_CHECK_INTERVAL = 60       # check every 60s
STUCK_WAIT_FAILURES = 3         # 3 consecutive wait failures → stuck


async def register_task(name: str, plan: list[str], metadata: dict = None) -> dict:
    """Register a new task. Returns task info with the thread started."""
    conn = await db.get_db()
    try:
        task_id = db.new_id()
        now = db.now_iso()
        await conn.execute(
            "INSERT INTO tasks (id, name, status, plan, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (task_id, name, "active", json.dumps(plan), json.dumps(metadata or {}), now, now)
        )
        # Initial system message with the plan
        plan_text = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(plan))
        await _append_msg(conn, task_id, "system", f"Task registered: {name}\nPlan:\n{plan_text}", "lifecycle", now)
        await conn.commit()

        debug.log_task(task_id, "REGISTERED", f"{name} ({len(plan)} steps)")
        log.info(f"Registered task {task_id}: {name}")
        return {"task_id": task_id, "name": name, "status": "active", "plan": plan, "created_at": now}
    finally:
        await conn.close()


async def post_message(task_id: str, role: str, content: str, msg_type: str = "text") -> dict:
    """Append a message to the task thread. This is the core operation."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        now = db.now_iso()
        await _append_msg(conn, task_id, role, content, msg_type, now)
        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        await conn.commit()

        debug.log_task(task_id, f"MSG [{role}]", content[:200])
        return {"task_id": task_id, "role": role, "acknowledged": True}
    finally:
        await conn.close()


async def update_task(task_id: str, message: str = None, query: str = None, status: str = None,
                      step_done: int = None) -> dict:
    """Update task status, post a message, mark step done, or query state."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}
        now = db.now_iso()

        # Status change
        if status and status != task["status"]:
            old = task["status"]
            await conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id))
            await _append_msg(conn, task_id, "system", f"Status changed: {old} → {status}", "lifecycle", now)
            debug.log_task(task_id, f"STATUS {old} → {status}")

        # Mark a plan step as done
        if step_done is not None:
            plan = json.loads(task["plan"])
            if 0 <= step_done < len(plan):
                meta = json.loads(task["metadata"])
                completed = meta.get("completed_steps", [])
                if step_done not in completed:
                    completed.append(step_done)
                    meta["completed_steps"] = sorted(completed)
                    await conn.execute("UPDATE tasks SET metadata = ?, updated_at = ? WHERE id = ?",
                                       (json.dumps(meta), now, task_id))
                    await _append_msg(conn, task_id, "system",
                                      f"✓ Step {step_done + 1} complete: {plan[step_done]}", "progress", now)
                    debug.log_task(task_id, f"STEP {step_done + 1} DONE", plan[step_done])

        # Agent message
        if message:
            await _append_msg(conn, task_id, "agent", message, "text", now)
            await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
            debug.log_task(task_id, "MSG [agent]", message[:200])

        await conn.commit()

        # Query mode — return summary
        if query:
            return await _query_task(conn, task_id, query)

        return await _get_task_summary(conn, task_id)
    finally:
        await conn.close()


async def get_thread(task_id: str, limit: int = 50) -> dict:
    """Get the full chat thread for a task."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        msgs = await conn.execute_fetchall(
            "SELECT role, content, msg_type, created_at FROM task_messages WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
            (task_id, limit)
        )
        messages = [dict(m) for m in reversed(msgs)]
        plan = json.loads(task["plan"])
        progress = _get_plan_progress(task)

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": task["status"],
            "plan": plan,
            "progress": progress,
            "messages": messages,
            "message_count": len(messages),
        }
    finally:
        await conn.close()


async def check_stuck_tasks() -> list[dict]:
    """Check for stuck tasks. Called periodically by the daemon.
    
    A task is considered stuck if:
    - It's active and hasn't been updated in STUCK_THRESHOLD_SECONDS
    - OR it has N consecutive wait failures/timeouts
    
    Returns list of stuck task alerts to inject as system events.
    """
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall("SELECT * FROM tasks WHERE status = 'active'")
        alerts = []
        now = time.time()

        for row in rows:
            task = dict(row)
            task_id = task["id"]

            # Check time since last update
            from datetime import datetime, timezone
            try:
                last_update = datetime.fromisoformat(task["updated_at"]).timestamp()
            except (ValueError, TypeError):
                continue

            idle_seconds = now - last_update

            if idle_seconds < STUCK_THRESHOLD_SECONDS:
                continue

            # Check recent messages for wait failures
            recent_msgs = await conn.execute_fetchall(
                "SELECT content, msg_type FROM task_messages WHERE task_id = ? ORDER BY created_at DESC LIMIT 5",
                (task_id,)
            )
            recent = [dict(m) for m in recent_msgs]

            # Count consecutive wait timeouts/failures
            consecutive_failures = 0
            for msg in recent:
                if msg["msg_type"] == "wait" and ("timeout" in msg["content"].lower() or "failed" in msg["content"].lower()):
                    consecutive_failures += 1
                else:
                    break

            is_stuck = idle_seconds > STUCK_THRESHOLD_SECONDS
            reason_parts = []
            if idle_seconds > STUCK_THRESHOLD_SECONDS:
                reason_parts.append(f"no updates for {idle_seconds/60:.0f} minutes")
            if consecutive_failures >= STUCK_WAIT_FAILURES:
                reason_parts.append(f"{consecutive_failures} consecutive wait failures")
                is_stuck = True

            if is_stuck:
                reason = "; ".join(reason_parts)
                # Get last meaningful message for context
                last_msg = ""
                for msg in recent:
                    if msg["msg_type"] != "lifecycle":
                        last_msg = msg["content"][:200]
                        break

                alert = {
                    "task_id": task_id,
                    "name": task["name"],
                    "reason": reason,
                    "last_message": last_msg,
                    "idle_seconds": idle_seconds,
                }
                alerts.append(alert)

                # Mark that we alerted (avoid spamming)
                meta = json.loads(task["metadata"])
                last_alert = meta.get("last_stuck_alert", 0)
                if now - last_alert > STUCK_THRESHOLD_SECONDS:
                    meta["last_stuck_alert"] = now
                    await conn.execute("UPDATE tasks SET metadata = ? WHERE id = ?",
                                       (json.dumps(meta), task_id))
                    await _append_msg(conn, task_id, "system",
                                      f"⚠️ Task appears stuck: {reason}", "stuck", db.now_iso())
                    await conn.commit()
                    debug.log_task(task_id, "⚠️ STUCK", reason)
                else:
                    # Already alerted recently, don't spam
                    alerts.pop()

        return alerts
    finally:
        await conn.close()


async def list_tasks(status: str = "active", limit: int = 10) -> dict:
    conn = await db.get_db()
    try:
        if status == "all":
            rows = await conn.execute_fetchall(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = await conn.execute_fetchall(
                "SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?", (status, limit)
            )
        tasks = []
        for row in rows:
            t = dict(row)
            tasks.append(await _get_task_summary_from_dict(conn, t))
        return {"tasks": tasks}
    finally:
        await conn.close()


# ─── Internal helpers ───────────────────────────────────────────

async def _append_msg(conn, task_id: str, role: str, content: str, msg_type: str, created_at: str):
    await conn.execute(
        "INSERT INTO task_messages (id, task_id, role, content, msg_type, created_at) VALUES (?,?,?,?,?,?)",
        (db.new_id(), task_id, role, content, msg_type, created_at)
    )


async def _get_task(conn, task_id: str) -> dict | None:
    rows = await conn.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return dict(rows[0]) if rows else None


def _get_plan_progress(task: dict) -> dict:
    """Get plan progress from metadata."""
    plan = json.loads(task["plan"])
    meta = json.loads(task["metadata"])
    completed = meta.get("completed_steps", [])
    total = len(plan)
    current = None
    for i in range(total):
        if i not in completed:
            current = i
            break
    remaining = [i for i in range(total) if i not in completed and i != current]
    return {
        "total": total,
        "completed": completed,
        "completed_names": [plan[i] for i in completed if i < total],
        "current": current,
        "current_name": plan[current] if current is not None and current < total else None,
        "remaining": remaining,
        "remaining_names": [plan[i] for i in remaining if i < total],
        "pct": round(len(completed) / total * 100) if total > 0 else 0,
    }


async def _get_task_summary(conn, task_id: str) -> dict:
    task = await _get_task(conn, task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}
    return await _get_task_summary_from_dict(conn, task)


async def _get_task_summary_from_dict(conn, task: dict) -> dict:
    msgs = await conn.execute_fetchall(
        "SELECT COUNT(*) as cnt FROM task_messages WHERE task_id = ?", (task["id"],)
    )
    # Get last 3 messages for quick context
    recent = await conn.execute_fetchall(
        "SELECT role, content, msg_type, created_at FROM task_messages WHERE task_id = ? ORDER BY created_at DESC LIMIT 3",
        (task["id"],)
    )
    progress = _get_plan_progress(task)

    return {
        "task_id": task["id"],
        "name": task["name"],
        "status": task["status"],
        "progress": progress,
        "message_count": dict(msgs[0])["cnt"],
        "recent_messages": [dict(m) for m in reversed(recent)],
        "last_update": task["updated_at"],
    }


async def _query_task(conn, task_id: str, query: str) -> dict:
    """Answer a query about a task using its message history + AI distillation."""
    task = await _get_task(conn, task_id)
    msgs = await conn.execute_fetchall(
        "SELECT role, content, msg_type, created_at FROM task_messages WHERE task_id = ? ORDER BY created_at",
        (task_id,)
    )
    plan = json.loads(task["plan"])
    progress = _get_plan_progress(task)

    # Build thread text
    thread = "\n".join(
        f"[{dict(m)['created_at']}] ({dict(m)['role']}/{dict(m)['msg_type']}) {dict(m)['content']}"
        for m in msgs
    )

    # AI distillation for long threads
    distilled = None
    if len(msgs) > 5:
        distilled = await _distill_task(task["name"], plan, progress, thread, query)

    return {
        "task_id": task_id,
        "name": task["name"],
        "status": task["status"],
        "plan": plan,
        "progress": progress,
        "answer": distilled or f"Task '{task['name']}' — {task['status']}. {len(msgs)} messages. Query: {query}",
        "message_count": len(msgs),
        "last_update": task["updated_at"],
    }


async def _distill_task(name: str, plan: list[str], progress: dict, thread: str, query: str) -> str | None:
    """Use MiniCPM-o (text mode) to answer a query about a task."""
    try:
        from ..vision import evaluate_condition
        prompt = f"""You are a task memory system. Answer the query about this task.

Task: {name}
Plan: {json.dumps(plan)}
Progress: {progress['pct']}% — completed: {progress['completed_names']}, current: {progress['current_name']}, remaining: {progress['remaining_names']}

Thread (oldest first):
{thread[-3000:]}

Query: {query}

Answer concisely (2-4 sentences). Focus on what's done, what's next, and any problems."""

        result = await evaluate_condition(prompt, [], job_id=f"task-{name[:20]}")
        return result if result else None
    except Exception as e:
        log.warning(f"Distillation failed: {e}")
        return None
