"""Integration tests for agentic-computer-use core logic.

Focused on deterministic components (task memory, wait linkage, diff/poller helpers).
"""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Isolate DB per test to avoid cross-test contamination."""
    from src.agentic_computer_use import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_data.db")
    return config.DB_PATH


def test_structured_verdict_parser_prefers_final_json():
    from src.agentic_computer_use.wait.engine import WaitEngine

    eng = WaitEngine()
    response = """I can see the terminal line indicating success.
Condition appears satisfied.
FINAL_JSON: {"decision":"resolved","confidence":0.93,"evidence":["PROCESS_COMPLETE"],"summary":"Completion token is visible"}"""

    verdict, detail = eng._parse_verdict(response)
    assert verdict == "resolved"
    assert "Completion token is visible" in detail
    assert "PROCESS_COMPLETE" in detail


def test_structured_verdict_parser_falls_back_to_legacy_text():
    from src.agentic_computer_use.wait.engine import WaitEngine

    eng = WaitEngine()
    response = "Reasoning text only\nYES: build output shows success"

    verdict, detail = eng._parse_verdict(response)
    assert verdict == "resolved"
    assert "build output shows success" in detail


def test_pixel_diff_gate():
    """Pixel diff should gate identical frames and pass changed frames."""
    import numpy as np
    from src.agentic_computer_use.capture.diff import PixelDiffGate

    gate = PixelDiffGate()
    frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
    frame2 = frame1.copy()
    frame3 = frame1.copy()
    frame3[10:50, 10:50] = 255  # 16% change

    assert gate.should_evaluate(frame1) is True   # first frame always
    assert gate.should_evaluate(frame2) is False  # identical
    assert gate.should_evaluate(frame3) is True   # changed


def test_adaptive_poller():
    """Poller should speed up on partial and slow down on static."""
    from src.agentic_computer_use.wait.poller import AdaptivePoller

    p = AdaptivePoller(base_interval=2.0)
    assert p.interval == 2.0

    p.on_partial()
    assert p.interval < 2.0

    p2 = AdaptivePoller(base_interval=2.0)
    for _ in range(10):
        p2.on_no_change()
    assert p2.interval > 2.0


def test_job_context_prompt():
    """Job context should build a valid prompt with frame history."""
    import time
    from src.agentic_computer_use.wait.context import JobContext

    ctx = JobContext()
    ctx.add_frame(b"jpeg1", b"thumb1", time.time() - 10)
    ctx.add_frame(b"jpeg2", b"thumb2", time.time())
    ctx.add_verdict("watching", "Nothing yet", time.time() - 5)

    prompt, images = ctx.build_prompt("A cat appears on screen")
    assert "A cat appears on screen" in prompt
    assert len(images) == 2  # 1 thumbnail + 1 full res
    assert images[0] == b"thumb1"
    assert images[1] == b"jpeg2"  # current = full res


@pytest.mark.asyncio
async def test_task_lifecycle_and_query(isolated_db):
    """Tasks should support register → update → query → complete."""
    from src.agentic_computer_use.task import manager

    result = await manager.register_task("Deploy", ["build", "test", "deploy"])
    tid = result["task_id"]
    assert result["status"] == "active"

    await manager.update_task(tid, message="Build started")
    await manager.update_plan_item(tid, ordinal=0, status="completed")

    summary = await manager.get_task_summary(tid)
    assert summary["items"][0]["status"] == "completed"

    await manager.update_task(tid, status="completed")
    tasks = await manager.list_tasks(status="completed")
    assert any(t["task_id"] == tid for t in tasks["tasks"])


@pytest.mark.asyncio
async def test_status_alias_canceled_normalizes_to_cancelled(isolated_db):
    from src.agentic_computer_use.task import manager

    task = await manager.register_task("Alias status", ["step1"])
    tid = task["task_id"]

    res = await manager.update_task(tid, status="canceled")
    assert res["status"] == "cancelled"


@pytest.mark.asyncio
async def test_hierarchical_task_model(isolated_db):
    """Test the full hierarchy: task → plan items → actions → logs."""
    from src.agentic_computer_use.task import manager

    # Register
    result = await manager.register_task("Video Export", ["Import clip", "Apply color grade", "Export"])
    tid = result["task_id"]

    # Update plan items
    await manager.update_plan_item(tid, ordinal=0, status="active")
    await manager.log_action(tid, "cli", "ffmpeg -i clip.mp4 timeline.mlt", status="completed")
    await manager.update_plan_item(tid, ordinal=0, status="completed")

    await manager.update_plan_item(tid, ordinal=1, status="active")
    await manager.log_action(tid, "gui", "Applied LUT via DaVinci Resolve", status="completed")

    # Get summary at item level
    summary = await manager.get_task_summary(tid, detail_level="items")
    assert len(summary["items"]) == 3
    assert summary["items"][0]["status"] == "completed"
    assert summary["items"][1]["status"] == "active"
    assert summary["items"][2]["status"] == "pending"

    # Drill down into item 0
    detail = await manager.get_task_detail(tid, ordinal=0)
    assert detail["title"] == "Import clip"
    assert len(detail["actions"]) >= 1

    # Get full detail
    full = await manager.get_task_summary(tid, detail_level="actions")
    assert "actions" in full["items"][0]


@pytest.mark.asyncio
async def test_stuck_detection_respects_active_wait_and_emits_resume_packet(isolated_db):
    from src.agentic_computer_use import db
    from src.agentic_computer_use.task import manager

    task = await manager.register_task("Long task", ["step1", "step2"])
    tid = task["task_id"]

    # Link active wait
    await manager.on_wait_created(tid, "wait123", "window:app", "process completes")

    # Mirror real runtime: wait_jobs has an active watching row
    conn = await db.get_db()
    try:
        await conn.execute(
            "INSERT INTO wait_jobs (id, task_id, target_type, target_id, criteria, timeout_seconds, poll_interval, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("wait123", tid, "window", "app", "process completes", 300, 2.0, "watching", datetime.now(timezone.utc).isoformat()),
        )
        await conn.commit()
    finally:
        await conn.close()

    # Artificially age the task
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=manager.STUCK_THRESHOLD_SECONDS + 30)).isoformat()
    conn = await db.get_db()
    try:
        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_ts, tid))
        await conn.commit()
    finally:
        await conn.close()

    # Should NOT alert while active wait exists
    alerts = await manager.check_stuck_tasks()
    assert alerts == []

    # Finish wait, then age again
    await manager.on_wait_finished(tid, "wait123", "resolved", "done")
    conn = await db.get_db()
    try:
        await conn.execute("UPDATE wait_jobs SET status = 'resolved', resolved_at = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), "wait123"))
        await conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_ts, tid))
        await conn.commit()
    finally:
        await conn.close()

    alerts = await manager.check_stuck_tasks()
    assert len(alerts) == 1
    packet = alerts[0]["packet"]
    assert packet["task_id"] == tid
    assert "progress" in packet
    assert packet["wait"]["active_wait_ids"] == []

    # Cooldown should suppress immediate duplicate alerts
    alerts_again = await manager.check_stuck_tasks()
    assert alerts_again == []
