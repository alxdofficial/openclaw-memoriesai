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
    """Answer a query about a task using its message history."""
    task_row = await conn.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = dict(task_row[0])
    msgs = await conn.execute_fetchall(
        "SELECT role, content, created_at FROM task_messages WHERE task_id = ? ORDER BY created_at",
        (task_id,)
    )
    plan = json.loads(task["plan"])

    # Build a simple summary from messages
    history = "\n".join(f"[{dict(m)['role']}] {dict(m)['content']}" for m in msgs)
    summary = f"Task: {task['name']}\nStatus: {task['status']}\nPlan: {json.dumps(plan)}\n\nHistory:\n{history}"

    return {
        "task_id": task_id,
        "status": task["status"],
        "summary": summary,
        "plan": plan,
        "message_count": len(msgs),
        "last_update": task["updated_at"],
    }


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
