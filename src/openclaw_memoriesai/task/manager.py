"""Task manager — persistent task tracking across context boundaries."""
import json
import logging
from .. import db

log = logging.getLogger(__name__)


async def register_task(name: str, plan: list[str], metadata: dict = None) -> dict:
    conn = await db.get_db()
    try:
        task_id = db.new_id()
        now = db.now_iso()
        await conn.execute(
            "INSERT INTO tasks (id, name, status, plan, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (task_id, name, "active", json.dumps(plan), json.dumps(metadata or {}), now, now)
        )
        # Add initial message
        await conn.execute(
            "INSERT INTO task_messages (id, task_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (db.new_id(), task_id, "system", f"Task registered: {name}", now)
        )
        await conn.commit()
        log.info(f"Registered task {task_id}: {name}")
        return {"task_id": task_id, "name": name, "status": "active", "plan": plan, "created_at": now}
    finally:
        await conn.close()


async def update_task(task_id: str, message: str = None, query: str = None, status: str = None) -> dict:
    conn = await db.get_db()
    try:
        row = await conn.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return {"error": f"Task {task_id} not found"}
        task = dict(row[0])
        now = db.now_iso()

        if status:
            await conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id))
        if message:
            await conn.execute(
                "INSERT INTO task_messages (id, task_id, role, content, created_at) VALUES (?,?,?,?,?)",
                (db.new_id(), task_id, "agent", message, now)
            )
            await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))

        await conn.commit()

        if query:
            return await _query_task(conn, task_id, query)

        msgs = await conn.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM task_messages WHERE task_id = ?", (task_id,)
        )
        return {
            "task_id": task_id,
            "status": status or task["status"],
            "message_count": dict(msgs[0])["cnt"],
            "acknowledged": True,
        }
    finally:
        await conn.close()


async def _query_task(conn, task_id: str, query: str) -> dict:
    """Answer a query about a task using its message history, with optional AI distillation."""
    task_row = await conn.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = dict(task_row[0])
    msgs = await conn.execute_fetchall(
        "SELECT role, content, created_at FROM task_messages WHERE task_id = ? ORDER BY created_at",
        (task_id,)
    )
    plan = json.loads(task["plan"])

    history = "\n".join(f"[{dict(m)['role']}] {dict(m)['content']}" for m in msgs)

    # Try AI distillation if we have enough history
    distilled = None
    if len(msgs) > 3:
        distilled = await _distill_task(task["name"], plan, history, query)

    summary = distilled or f"Task: {task['name']}\nStatus: {task['status']}\nPlan: {json.dumps(plan)}\n\nHistory:\n{history}"

    # Infer plan progress from messages
    progress = _infer_plan_progress(plan, history)

    return {
        "task_id": task_id,
        "status": task["status"],
        "summary": summary,
        "plan": plan,
        "plan_progress": progress,
        "message_count": len(msgs),
        "last_update": task["updated_at"],
    }


async def _distill_task(name: str, plan: list[str], history: str, query: str) -> str | None:
    """Use local vision model (text mode) to distill task state."""
    try:
        from ..vision import evaluate_condition
        prompt = f"""You are a task memory system. Summarize the current state of this task concisely.

Task: {name}
Plan: {json.dumps(plan)}
Query: {query}

Message history:
{history}

Answer the query in 2-3 sentences. Focus on: what's done, what's next, any blockers."""

        # Use vision model in text-only mode (no images)
        result = await evaluate_condition(prompt, [])
        return result if result else None
    except Exception as e:
        log.warning(f"Distillation failed: {e}")
        return None


def _infer_plan_progress(plan: list[str], history: str) -> dict:
    """Heuristically infer which plan steps are done from message history."""
    history_lower = history.lower()
    completed = []
    current = None

    for i, step in enumerate(plan):
        step_lower = step.lower()
        # Extract key words from the step
        words = [w for w in step_lower.split() if len(w) > 3]
        # Check if any key words appear in "done"/"completed" context
        step_mentioned = any(w in history_lower for w in words[:3])
        done_indicators = ["done", "completed", "finished", "✓", "checked"]
        step_done = step_mentioned and any(d in history_lower for d in done_indicators)

        if step_done:
            completed.append(i)
        elif current is None and not step_done:
            current = i

    remaining = [i for i in range(len(plan)) if i not in completed and i != current]
    return {"completed": completed, "current": current, "remaining": remaining}


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
            msgs = await conn.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM task_messages WHERE task_id = ?", (t["id"],)
            )
            tasks.append({
                "task_id": t["id"],
                "name": t["name"],
                "status": t["status"],
                "plan_steps": len(json.loads(t["plan"])),
                "messages": dict(msgs[0])["cnt"],
                "last_update": t["updated_at"],
            })
        return {"tasks": tasks}
    finally:
        await conn.close()
