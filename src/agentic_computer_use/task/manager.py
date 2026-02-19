"""Task manager — hierarchical task tracking.

Hierarchy: Task → Plan Items → Actions → Logs

Tasks contain plan items (ordered checklist). Each plan item tracks
actions (discrete operations) and their logs. This replaces the flat
message thread with structured, drillable state.
"""
import json
import time
import logging
from datetime import datetime, timezone

from .. import db, debug, config
from ..display.manager import allocate_display, release_display

log = logging.getLogger(__name__)

VALID_STATUSES = {"active", "paused", "completed", "failed", "cancelled"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VALID_ITEM_STATUSES = {"pending", "active", "completed", "failed", "skipped"}
VALID_MSG_TYPES = {"text", "lifecycle", "progress", "wait", "stuck", "plan"}
VALID_WAIT_STATES = {"watching", "resolved", "timeout", "cancelled", "error"}

# Stuck detection config
STUCK_THRESHOLD_SECONDS = 300
STUCK_CHECK_INTERVAL = 60
STUCK_ALERT_COOLDOWN_SECONDS = 300
STUCK_WAIT_FAILURES = 3


def _normalize_status(status: str | None) -> str | None:
    if status is None:
        return None
    s = str(status).strip().lower()
    if s == "canceled":
        s = "cancelled"
    if s not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Expected one of {sorted(VALID_STATUSES)}")
    return s


def _load_json(text_value: str, fallback):
    try:
        return json.loads(text_value) if text_value else fallback
    except Exception:
        return fallback


def _load_metadata(task: dict) -> dict:
    meta = _load_json(task.get("metadata") or "{}", {})
    active_wait_ids = meta.get("active_wait_ids", [])
    if not isinstance(active_wait_ids, list):
        active_wait_ids = []
    meta["active_wait_ids"] = [str(x) for x in active_wait_ids if str(x).strip()]
    state = str(meta.get("last_wait_state", "")).strip().lower()
    if state not in VALID_WAIT_STATES:
        state = "watching" if meta["active_wait_ids"] else "resolved"
    meta["last_wait_state"] = state
    ts = meta.get("last_wait_event_at")
    try:
        meta["last_wait_event_at"] = float(ts) if ts is not None else None
    except Exception:
        meta["last_wait_event_at"] = None
    alert_ts = meta.get("last_stuck_alert_at", 0)
    try:
        meta["last_stuck_alert_at"] = float(alert_ts or 0)
    except Exception:
        meta["last_stuck_alert_at"] = 0.0
    return meta


def _dump_metadata(meta: dict) -> str:
    return json.dumps(meta, ensure_ascii=False)


def _parse_iso(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


# ─── Public API ──────────────────────────────────────────────────

async def task_exists(task_id: str) -> bool:
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall("SELECT 1 FROM tasks WHERE id = ? LIMIT 1", (task_id,))
        return bool(rows)
    finally:
        await conn.close()


async def register_task(name: str, plan: list[str], metadata: dict = None) -> dict:
    """Register a new task with plan items."""
    conn = await db.get_db()
    try:
        if not isinstance(plan, list) or not plan:
            return {"error": "Plan must be a non-empty array of step strings"}

        plan = [str(s).strip() for s in plan if str(s).strip()]
        task_id = db.new_id()
        now = db.now_iso()

        initial_meta = dict(metadata or {})
        initial_meta.setdefault("active_wait_ids", [])
        initial_meta.setdefault("last_wait_state", "resolved")
        initial_meta.setdefault("last_wait_event_at", None)
        initial_meta.setdefault("last_stuck_alert_at", 0)

        # Allocate a per-task virtual display
        display_width = initial_meta.pop("display_width", None)
        display_height = initial_meta.pop("display_height", None)
        try:
            display_info = allocate_display(
                task_id,
                width=int(display_width) if display_width else None,
                height=int(display_height) if display_height else None,
            )
            initial_meta["display"] = display_info.display_str
            initial_meta["display_num"] = display_info.display_num
            initial_meta["display_resolution"] = f"{display_info.width}x{display_info.height}"
        except Exception as e:
            log.warning(f"Failed to allocate display for task {task_id}: {e}")
            initial_meta["display"] = config.DISPLAY

        await conn.execute(
            "INSERT INTO tasks (id, name, status, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (task_id, name, "active", json.dumps(initial_meta), now, now)
        )

        # Create plan items
        items = []
        for i, title in enumerate(plan):
            item_id = db.new_id()
            await conn.execute(
                "INSERT INTO plan_items (id, task_id, ordinal, title, status) VALUES (?,?,?,?,?)",
                (item_id, task_id, i, title, "pending")
            )
            items.append({"ordinal": i, "title": title, "status": "pending"})

        # Log registration
        await _append_msg(conn, task_id, "system",
            f"Task registered: {name}\nPlan:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan)),
            "lifecycle", now)
        await conn.commit()

        debug.log_task(task_id, "REGISTERED", f"{name} ({len(plan)} items)")
        return {"task_id": task_id, "name": name, "status": "active", "items": items, "created_at": now}
    finally:
        await conn.close()


async def update_task(task_id: str, message: str = None, query: str = None, status: str = None) -> dict:
    """Update task status or post a message."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        now = db.now_iso()

        if status is not None:
            try:
                normalized = _normalize_status(status)
            except ValueError as e:
                return {"error": str(e)}
            if normalized != task["status"]:
                old = task["status"]
                await conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (normalized, now, task_id))
                await _append_msg(conn, task_id, "system", f"Status changed: {old} → {normalized}", "lifecycle", now)
                debug.log_task(task_id, f"STATUS {old} → {normalized}")
                if normalized in TERMINAL_STATUSES:
                    try:
                        release_display(task_id)
                    except Exception as e:
                        log.warning(f"Failed to release display for task {task_id}: {e}")

        if message:
            # Log message as an action under the active plan item
            active_item = await _get_active_plan_item(conn, task_id)
            if active_item:
                action_id = db.new_id()
                await conn.execute(
                    "INSERT INTO actions (id, plan_item_id, task_id, action_type, summary, status, created_at) VALUES (?,?,?,?,?,?,?)",
                    (action_id, active_item["id"], task_id, "reasoning", message, "completed", now)
                )
            await _append_msg(conn, task_id, "agent", message, "text", now)
            await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
            debug.log_task(task_id, "MSG [agent]", message[:200])

        await conn.commit()

        if query:
            return await _query_task(conn, task_id, query)

        return await _build_item_summary(conn, task_id)
    finally:
        await conn.close()


async def update_plan_item(task_id: str, ordinal: int, status: str, note: str = None) -> dict:
    """Update a plan item's status."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        rows = await conn.execute_fetchall(
            "SELECT * FROM plan_items WHERE task_id = ? AND ordinal = ?", (task_id, ordinal)
        )
        if not rows:
            return {"error": f"Plan item {ordinal} not found"}

        item = dict(rows[0])
        now = db.now_iso()

        if status not in VALID_ITEM_STATUSES:
            return {"error": f"Invalid item status: {status}"}

        updates = {"status": status}
        if status == "active" and not item.get("started_at"):
            updates["started_at"] = now
        elif status in ("completed", "failed", "skipped"):
            updates["completed_at"] = now
            if item.get("started_at"):
                duration = _parse_iso(now) - _parse_iso(item["started_at"])
                updates["duration_seconds"] = round(duration, 1)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [item["id"]]
        await conn.execute(f"UPDATE plan_items SET {set_clause} WHERE id = ?", values)

        if note:
            action_id = db.new_id()
            await conn.execute(
                "INSERT INTO actions (id, plan_item_id, task_id, action_type, summary, status, created_at) VALUES (?,?,?,?,?,?,?)",
                (action_id, item["id"], task_id, "reasoning", note, "completed", now)
            )

        symbol = {"completed": "✓", "failed": "✗", "skipped": "⊘", "active": "▶", "pending": "○"}.get(status, "•")
        await _append_msg(conn, task_id, "system", f"{symbol} Item {ordinal}: {item['title']} → {status}", "progress", now)
        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        await conn.commit()

        debug.log_task(task_id, f"ITEM {ordinal} → {status}", item["title"])
        return await _build_item_summary(conn, task_id)
    finally:
        await conn.close()


async def log_action(
    task_id: str,
    action_type: str,
    summary: str,
    input_data: str = None,
    output_data: str = None,
    status: str = "completed",
    ordinal: int = None,
) -> dict:
    """Log a discrete action under a plan item."""
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        now = db.now_iso()

        if ordinal is not None:
            rows = await conn.execute_fetchall(
                "SELECT * FROM plan_items WHERE task_id = ? AND ordinal = ?", (task_id, ordinal)
            )
            item = dict(rows[0]) if rows else None
        else:
            item = await _get_active_plan_item(conn, task_id)

        plan_item_id = item["id"] if item else None
        action_id = db.new_id()

        await conn.execute(
            "INSERT INTO actions (id, plan_item_id, task_id, action_type, summary, status, input_data, output_data, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (action_id, plan_item_id, task_id, action_type, summary, status, input_data, output_data, now)
        )

        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        await conn.commit()

        debug.log_task(task_id, f"ACTION [{action_type}]", summary[:200])
        return {"ok": True, "action_id": action_id, "plan_item_ordinal": item["ordinal"] if item else None}
    finally:
        await conn.close()


async def get_task_summary(task_id: str, detail_level: str = "items") -> dict:
    """Get task summary at specified detail level."""
    conn = await db.get_db()
    try:
        return await _build_item_summary(conn, task_id, detail_level=detail_level)
    finally:
        await conn.close()


async def get_task_detail(task_id: str, ordinal: int) -> dict:
    """Drill down into a specific plan item."""
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall(
            "SELECT * FROM plan_items WHERE task_id = ? AND ordinal = ?", (task_id, ordinal)
        )
        if not rows:
            return {"error": f"Plan item {ordinal} not found for task {task_id}"}

        item = dict(rows[0])

        action_rows = await conn.execute_fetchall(
            "SELECT * FROM actions WHERE plan_item_id = ? ORDER BY created_at", (item["id"],)
        )
        actions = []
        for ar in action_rows:
            a = dict(ar)
            log_rows = await conn.execute_fetchall(
                "SELECT * FROM action_logs WHERE action_id = ? ORDER BY created_at", (a["id"],)
            )
            a["logs"] = [dict(lr) for lr in log_rows]
            actions.append(a)

        return {
            "task_id": task_id,
            "ordinal": item["ordinal"],
            "title": item["title"],
            "status": item["status"],
            "started_at": item.get("started_at"),
            "completed_at": item.get("completed_at"),
            "duration_seconds": item.get("duration_seconds"),
            "actions": actions,
            "action_count": len(actions),
        }
    finally:
        await conn.close()


async def get_thread(task_id: str, limit: int = 50) -> dict:
    """Legacy chat thread view."""
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

        items = await conn.execute_fetchall(
            "SELECT ordinal, title, status FROM plan_items WHERE task_id = ? ORDER BY ordinal", (task_id,)
        )
        item_list = [dict(i) for i in items]

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": task["status"],
            "items": item_list,
            "messages": messages,
            "message_count": len(messages),
        }
    finally:
        await conn.close()


async def post_message(task_id: str, role: str, content: str, msg_type: str = "text") -> dict:
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}
        now = db.now_iso()
        await _append_msg(conn, task_id, role, content, msg_type, now)
        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        await conn.commit()
        return {"task_id": task_id, "role": role, "acknowledged": True}
    finally:
        await conn.close()


async def on_wait_created(task_id: str, wait_id: str, target: str, criteria: str,
                          screenshot_refs: dict = None, timeout: int = None) -> dict:
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        now_iso = db.now_iso()
        now_epoch = time.time()
        meta = _load_metadata(task)

        active_wait_ids = meta.get("active_wait_ids", [])
        if wait_id not in active_wait_ids:
            active_wait_ids.append(wait_id)
        meta["active_wait_ids"] = active_wait_ids
        meta["last_wait_state"] = "watching"
        meta["last_wait_event_at"] = now_epoch

        await conn.execute(
            "UPDATE tasks SET metadata = ?, updated_at = ? WHERE id = ?",
            (_dump_metadata(meta), now_iso, task_id),
        )

        active_item = await _get_active_plan_item(conn, task_id)
        if active_item:
            action_id = db.new_id()
            input_data = {"wait_id": wait_id, "target": target, "criteria": criteria}
            if timeout is not None:
                input_data["timeout"] = timeout
            if screenshot_refs:
                input_data["screenshot"] = screenshot_refs
            await conn.execute(
                "INSERT INTO actions (id, plan_item_id, task_id, action_type, summary, status, input_data, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (action_id, active_item["id"], task_id, "wait", f"Started wait {wait_id} on {target}: {criteria}",
                 "started", json.dumps(input_data), now_iso)
            )

        await _append_msg(conn, task_id, "system", f"[smart_wait] Started wait {wait_id} on {target}: {criteria}", "wait", now_iso)
        await conn.commit()

        debug.log_task(task_id, "WAIT LINKED", f"{wait_id} ({target})")
        return {"ok": True, "task_id": task_id, "wait_id": wait_id}
    finally:
        await conn.close()


async def on_wait_finished(task_id: str, wait_id: str, state: str, detail: str,
                           screenshot_refs: dict = None, elapsed_seconds: float = None) -> dict:
    conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        normalized_state = str(state or "error").strip().lower()
        if normalized_state == "canceled":
            normalized_state = "cancelled"
        if normalized_state not in VALID_WAIT_STATES:
            normalized_state = "error"

        now_iso = db.now_iso()
        now_epoch = time.time()
        meta = _load_metadata(task)

        active_wait_ids = [wid for wid in meta.get("active_wait_ids", []) if wid != wait_id]
        meta["active_wait_ids"] = active_wait_ids
        meta["last_wait_state"] = normalized_state
        meta["last_wait_event_at"] = now_epoch

        await conn.execute(
            "UPDATE tasks SET metadata = ?, updated_at = ? WHERE id = ?",
            (_dump_metadata(meta), now_iso, task_id),
        )

        # Update the wait action's output_data and status
        output_data = {"state": normalized_state, "detail": detail}
        if elapsed_seconds is not None:
            output_data["elapsed_seconds"] = round(elapsed_seconds, 1)
        if screenshot_refs:
            output_data["screenshot"] = screenshot_refs
        action_status = "completed" if normalized_state == "resolved" else "failed"
        await conn.execute(
            "UPDATE actions SET output_data = ?, status = ? WHERE task_id = ? AND action_type = 'wait' AND summary LIKE ?",
            (json.dumps(output_data), action_status, task_id, f"%{wait_id}%"),
        )

        await _append_msg(conn, task_id, "system", f"[smart_wait] Wait {wait_id} {normalized_state}: {detail}", "wait", now_iso)
        await conn.commit()

        debug.log_task(task_id, f"WAIT {normalized_state.upper()}", f"{wait_id}: {detail[:180]}")
        return {"ok": True, "task_id": task_id, "wait_id": wait_id, "state": normalized_state}
    finally:
        await conn.close()


async def log_wait_verdict(task_id: str, wait_id: str, verdict: str, description: str) -> None:
    """Append a vision poll result to the wait action's logs."""
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall(
            "SELECT id FROM actions WHERE task_id = ? AND action_type = 'wait' "
            "AND json_extract(input_data, '$.wait_id') = ?",
            (task_id, wait_id)
        )
        if not rows:
            return
        action_id = dict(rows[0])["id"]
        await conn.execute(
            "INSERT INTO action_logs (id, action_id, log_type, content, created_at) VALUES (?,?,?,?,?)",
            (db.new_id(), action_id, "verdict", f"[{verdict}] {description}", db.now_iso())
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_task_display(task_id: str) -> str | None:
    """Return the display string from a task's metadata, or None."""
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall("SELECT metadata FROM tasks WHERE id = ?", (task_id,))
        if rows:
            meta = _load_json(dict(rows[0]).get("metadata", "{}"), {})
            return meta.get("display")
        return None
    finally:
        await conn.close()


async def list_tasks(status: str = "active", limit: int = 10) -> dict:
    conn = await db.get_db()
    try:
        normalized = str(status or "active").strip().lower()
        if normalized == "canceled":
            normalized = "cancelled"

        if normalized == "all":
            rows = await conn.execute_fetchall("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,))
        else:
            rows = await conn.execute_fetchall(
                "SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (normalized, limit),
            )

        tasks = []
        for row in rows:
            t = dict(row)
            summary = await _build_item_summary(conn, t["id"])
            tasks.append(summary)
        return {"tasks": tasks}
    finally:
        await conn.close()


async def build_resume_packet(task_id: str, reason: str | None = None, conn=None) -> dict:
    owns_conn = conn is None
    if owns_conn:
        conn = await db.get_db()
    try:
        task = await _get_task(conn, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        meta = _load_metadata(task)
        items = await conn.execute_fetchall(
            "SELECT ordinal, title, status FROM plan_items WHERE task_id = ? ORDER BY ordinal", (task_id,)
        )
        item_list = [dict(i) for i in items]
        completed = [i for i in item_list if i["status"] == "completed"]
        active = [i for i in item_list if i["status"] == "active"]
        remaining = [i for i in item_list if i["status"] == "pending"]

        recent_rows = await conn.execute_fetchall(
            "SELECT role, content, msg_type, created_at FROM task_messages "
            "WHERE task_id = ? ORDER BY created_at DESC LIMIT 5",
            (task_id,),
        )
        recent_messages = [dict(m) for m in reversed(recent_rows)]

        pct = round((len(completed) / len(item_list)) * 100) if item_list else 0
        current = active[0] if active else (remaining[0] if remaining else None)

        # Expand action details for the current (active/next pending) item
        if current:
            pi_rows = await conn.execute_fetchall(
                "SELECT id FROM plan_items WHERE task_id = ? AND ordinal = ?",
                (task_id, current["ordinal"]))
            if pi_rows:
                plan_item_id = dict(pi_rows[0])["id"]
                action_rows = await conn.execute_fetchall(
                    "SELECT * FROM actions WHERE plan_item_id = ? ORDER BY created_at",
                    (plan_item_id,))
                expanded_actions = []
                for ar in action_rows:
                    a = dict(ar)
                    log_rows = await conn.execute_fetchall(
                        "SELECT * FROM action_logs WHERE action_id = ? ORDER BY created_at",
                        (a["id"],))
                    a["logs"] = [dict(lr) for lr in log_rows]
                    expanded_actions.append(a)
                current["action_details"] = expanded_actions

        return {
            "task_id": task_id,
            "name": task["name"],
            "status": task["status"],
            "progress": {
                "completed": [i["ordinal"] for i in completed],
                "current": current["ordinal"] if current else None,
                "current_name": current["title"] if current else None,
                "remaining": [i["ordinal"] for i in remaining],
                "pct": pct,
            },
            "items": item_list,
            "recent_messages": recent_messages,
            "wait": {
                "active_wait_ids": meta.get("active_wait_ids", []),
                "last_wait_state": meta.get("last_wait_state"),
                "last_wait_event_at": meta.get("last_wait_event_at"),
            },
            "reason": reason or "task appears stuck",
        }
    finally:
        if owns_conn:
            await conn.close()


async def check_stuck_tasks() -> list[dict]:
    conn = await db.get_db()
    try:
        rows = await conn.execute_fetchall("SELECT * FROM tasks WHERE status = 'active'")
        alerts = []
        now_epoch = time.time()

        for row in rows:
            task = dict(row)
            task_id = task["id"]
            meta = _load_metadata(task)

            active_wait_ids, reconciled = await _reconcile_active_wait_ids(conn, meta.get("active_wait_ids", []))
            if reconciled:
                meta["active_wait_ids"] = active_wait_ids
                await conn.execute("UPDATE tasks SET metadata = ? WHERE id = ?", (_dump_metadata(meta), task_id))
                await conn.commit()

            if active_wait_ids:
                continue

            idle_seconds = now_epoch - _parse_iso(task.get("updated_at"))
            if idle_seconds < STUCK_THRESHOLD_SECONDS:
                continue

            last_alert_at = float(meta.get("last_stuck_alert_at", 0) or 0)
            if now_epoch - last_alert_at < STUCK_ALERT_COOLDOWN_SECONDS:
                continue

            reason = f"no updates for {idle_seconds/60:.0f} minutes and no active smart wait"

            packet = await build_resume_packet(task_id, reason=reason, conn=conn)
            alerts.append({
                "task_id": task_id,
                "name": task["name"],
                "reason": reason,
                "packet": packet,
                "idle_seconds": idle_seconds,
            })

            meta["last_stuck_alert_at"] = now_epoch
            await conn.execute("UPDATE tasks SET metadata = ? WHERE id = ?", (_dump_metadata(meta), task_id))
            await _append_msg(conn, task_id, "system", f"Task appears stuck: {reason}", "stuck", db.now_iso())
            await conn.commit()
            debug.log_task(task_id, "STUCK", reason)

        return alerts
    finally:
        await conn.close()


# ─── Internal helpers ────────────────────────────────────────────

async def _append_msg(conn, task_id: str, role: str, content: str, msg_type: str, created_at: str):
    await conn.execute(
        "INSERT INTO task_messages (id, task_id, role, content, msg_type, created_at) VALUES (?,?,?,?,?,?)",
        (db.new_id(), task_id, role, content, msg_type, created_at)
    )


async def _get_task(conn, task_id: str) -> dict | None:
    rows = await conn.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return dict(rows[0]) if rows else None


async def _get_active_plan_item(conn, task_id: str) -> dict | None:
    rows = await conn.execute_fetchall(
        "SELECT * FROM plan_items WHERE task_id = ? AND status = 'active' ORDER BY ordinal LIMIT 1",
        (task_id,)
    )
    if rows:
        return dict(rows[0])
    rows = await conn.execute_fetchall(
        "SELECT * FROM plan_items WHERE task_id = ? AND status = 'pending' ORDER BY ordinal LIMIT 1",
        (task_id,)
    )
    return dict(rows[0]) if rows else None


async def _build_item_summary(conn, task_id: str, detail_level: str = "items") -> dict:
    task = await _get_task(conn, task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    items_rows = await conn.execute_fetchall(
        "SELECT * FROM plan_items WHERE task_id = ? ORDER BY ordinal", (task_id,)
    )

    # For "focused" mode, find the ordinal to expand (last active, fallback first pending)
    focused_ordinal = None
    if detail_level == "focused":
        for ir in items_rows:
            it = dict(ir)
            if it["status"] == "active":
                focused_ordinal = it["ordinal"]
        if focused_ordinal is None:
            for ir in items_rows:
                it = dict(ir)
                if it["status"] == "pending":
                    focused_ordinal = it["ordinal"]
                    break

    items = []
    for ir in items_rows:
        item = dict(ir)
        entry = {
            "ordinal": item["ordinal"],
            "title": item["title"],
            "status": item["status"],
            "duration_s": item.get("duration_seconds"),
        }

        action_count_rows = await conn.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM actions WHERE plan_item_id = ?", (item["id"],)
        )
        entry["actions"] = dict(action_count_rows[0])["cnt"]

        expand_this = (
            detail_level in ("actions", "full")
            or (detail_level == "focused" and item["ordinal"] == focused_ordinal)
        )

        if expand_this:
            action_rows = await conn.execute_fetchall(
                "SELECT * FROM actions WHERE plan_item_id = ? ORDER BY created_at", (item["id"],)
            )
            entry["action_details"] = []
            for ar in action_rows:
                a = dict(ar)
                action_entry = {
                    "id": a["id"],
                    "action_type": a["action_type"],
                    "summary": a["summary"],
                    "status": a["status"],
                    "created_at": a["created_at"],
                }
                action_entry["input_data"] = a.get("input_data")
                action_entry["output_data"] = a.get("output_data")
                log_rows = await conn.execute_fetchall(
                    "SELECT * FROM action_logs WHERE action_id = ? ORDER BY created_at", (a["id"],)
                )
                action_entry["logs"] = [dict(lr) for lr in log_rows]
                entry["action_details"].append(action_entry)

        items.append(entry)

    completed = [i for i in items if i["status"] == "completed"]
    total = len(items)
    pct = round((len(completed) / total) * 100) if total > 0 else 0

    return {
        "task_id": task_id,
        "name": task["name"],
        "status": task["status"],
        "items": items,
        "progress_pct": pct,
        "last_update": task["updated_at"],
    }


async def _query_task(conn, task_id: str, query: str) -> dict:
    task = await _get_task(conn, task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    items = await conn.execute_fetchall(
        "SELECT ordinal, title, status FROM plan_items WHERE task_id = ? ORDER BY ordinal", (task_id,)
    )
    item_list = [dict(i) for i in items]
    completed = [i for i in item_list if i["status"] == "completed"]
    active = [i for i in item_list if i["status"] == "active"]
    remaining = [i for i in item_list if i["status"] == "pending"]
    pct = round((len(completed) / len(item_list)) * 100) if item_list else 0

    current = active[0] if active else (remaining[0] if remaining else None)
    answer = (
        f"Task '{task['name']}' is {task['status']} at {pct}%. "
        f"Completed: {[i['title'] for i in completed]}. "
        f"Current: {current['title'] if current else 'none'}. "
        f"Remaining: {[i['title'] for i in remaining]}."
    )

    return {
        "task_id": task_id,
        "name": task["name"],
        "status": task["status"],
        "items": item_list,
        "summary": answer,
        "progress_pct": pct,
    }


async def _reconcile_active_wait_ids(conn, active_wait_ids: list[str]) -> tuple[list[str], bool]:
    ids = [str(x) for x in active_wait_ids if str(x).strip()]
    if not ids:
        return [], False
    placeholders = ",".join("?" for _ in ids)
    rows = await conn.execute_fetchall(
        f"SELECT id FROM wait_jobs WHERE id IN ({placeholders}) AND status = 'watching'",
        tuple(ids),
    )
    alive = {dict(r)["id"] for r in rows}
    filtered = [wid for wid in ids if wid in alive]
    changed = filtered != ids
    return filtered, changed
