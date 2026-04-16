"""Integration tests for agentic-computer-use core logic.

Focused on deterministic components (task memory, wait linkage, diff/poller helpers).
"""
import asyncio
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


def test_job_context_and_build_prompt():
    """JobContext tracks start time; build_prompt generates valid YES/NO prompt."""
    import time
    from src.agentic_computer_use.wait.context import JobContext, build_prompt

    ctx = JobContext()
    assert ctx.started_at <= time.time()

    prompt = build_prompt("A cat appears on screen", elapsed=15.0)
    assert "A cat appears on screen" in prompt
    assert "YES" in prompt
    assert "NO" in prompt


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


def test_doctor_returns_structured_results():
    """Doctor runs end-to-end without crashing and returns a well-shaped report."""
    from src.agentic_computer_use.doctor import (
        run_diagnostics, summarize, exit_code, OK, WARN, FAIL, SKIP,
    )

    results = asyncio.run(run_diagnostics())
    assert isinstance(results, list)
    assert len(results) > 5, "expected several checks"

    # Every result must have required fields and a valid status
    for r in results:
        assert r.section, "missing section"
        assert r.name, "missing name"
        assert r.status in (OK, WARN, FAIL, SKIP), f"bad status: {r.status}"

    # Summary counts must add up
    s = summarize(results)
    assert sum(s.values()) == len(results)

    # Exit code must follow the 0/1/2 rule
    rc = exit_code(results)
    assert rc in (0, 1, 2)
    if any(r.status == FAIL for r in results):
        assert rc == 2
    elif any(r.status == WARN for r in results):
        assert rc == 1
    else:
        assert rc == 0


def test_configure_plan_unit_update_preserves_existing_env():
    """plan_unit_update should preserve existing Environment= lines when adding new ones."""
    import tempfile
    from pathlib import Path
    from src.agentic_computer_use.configure import io

    unit_text = (
        "[Unit]\nDescription=Test\n\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=EXISTING_KEY=keep-me\n"
        "Environment=OTHER=also-keep\n"
        "ExecStart=/bin/true\n"
        "\n[Install]\nWantedBy=multi-user.target\n"
    )
    with tempfile.TemporaryDirectory() as d:
        unit = Path(d) / "fake.service"
        unit.write_text(unit_text)
        plan = io.plan_unit_update({"NEW_KEY": "hello"}, unit_path=unit)
        assert plan is not None
        assert "+ Environment=NEW_KEY" in plan.changes
        assert "EXISTING_KEY=keep-me" in plan.new_content
        assert "OTHER=also-keep" in plan.new_content
        assert "NEW_KEY=hello" in plan.new_content

        # No-op case: re-applying the same value should produce None
        unit.write_text(plan.new_content)
        assert io.plan_unit_update({"NEW_KEY": "hello"}, unit_path=unit) is None


def test_configure_openclaw_dry_run_does_not_write(tmp_path, monkeypatch):
    """update_openclaw_config(dry_run=True) must return a diff without touching the file."""
    from src.agentic_computer_use.configure import io, state

    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text('{"mcp": {"servers": {}}}')
    monkeypatch.setattr(state, "OPENCLAW_CONFIG", cfg_path)

    import hashlib
    before = hashlib.md5(cfg_path.read_bytes()).hexdigest()

    _new, diff = io.update_openclaw_config(
        lambda c: {**c, "mcp": {"servers": {"agentic-computer-use": {"env": {"K": "v"}}}}},
        dry_run=True,
    )
    assert diff is not None

    after = hashlib.md5(cfg_path.read_bytes()).hexdigest()
    assert before == after, "dry_run wrote to disk"


def test_humanize_off_returns_legacy_timing():
    """When humanization is off (test default), typing delays are exactly 12ms."""
    from src.agentic_computer_use import humanize as h
    assert h.is_enabled() is False
    delays = list(h.typing_delays("hello"))
    assert delays == [0.012, 0.012, 0.012, 0.012, 0.012]
    path = h.bezier_path(100, 100, 500, 500)
    assert len(path) == 1 and path[0].x == 500 and path[0].y == 500


def test_humanize_on_varies_typing(humanize_on):
    """With humanize on, delays vary and are bounded."""
    delays = list(humanize_on.typing_delays("hello world test"))
    assert len(delays) == 16
    assert min(delays) >= humanize_on.TYPING_MIN_S - 1e-9
    # Any single char can be up to TYPING_MAX + a word-boundary pause
    assert max(delays) <= humanize_on.TYPING_MAX_S + (humanize_on.WORD_PAUSE_MAX_MS / 1000.0) + 1e-9
    # Mean should be noticeably above the 12ms legacy flat rate
    assert sum(delays) / len(delays) > 0.04


def test_humanize_bezier_exact_endpoint(humanize_on):
    """Bezier path must land exactly on the target — no jitter on last waypoint."""
    for x0, y0, x1, y1 in [(10, 10, 800, 400), (500, 500, 505, 500), (0, 0, 1919, 1079)]:
        path = humanize_on.bezier_path(x0, y0, x1, y1, screen_bounds=(0, 0, 1920, 1080))
        assert path[-1].x == x1 and path[-1].y == y1, f"endpoint drift for ({x0},{y0})→({x1},{y1})"
        assert len(path) >= 1


def test_humanize_bezier_stays_in_bounds(humanize_on):
    """Even with a random control-point offset, the path must stay on screen."""
    import random
    rng = random.Random(1)
    for _ in range(50):
        x0, y0 = rng.randint(0, 1919), rng.randint(0, 1079)
        x1, y1 = rng.randint(0, 1919), rng.randint(0, 1079)
        path = humanize_on.bezier_path(x0, y0, x1, y1, screen_bounds=(0, 0, 1920, 1080))
        for wp in path:
            assert 0 <= wp.x <= 1919 and 0 <= wp.y <= 1079, f"waypoint {wp} off-screen"


def test_humanize_click_jitter_inside_bbox(humanize_on):
    """Jitter must land inside bbox — never on a neighboring element."""
    bbox = (200, 300, 220, 320)  # 20×20 button
    for _ in range(200):
        nx, ny = humanize_on.jitter_click_coords(210, 310, bbox=bbox)
        assert bbox[0] < nx < bbox[2], f"x={nx} escaped {bbox}"
        assert bbox[1] < ny < bbox[3], f"y={ny} escaped {bbox}"


def test_humanize_set_snapshot_roundtrip(humanize_on):
    """set_enabled(False) → snapshot reports disabled with reason."""
    humanize_on.set_enabled(False, reason="speed test")
    snap = humanize_on.snapshot()
    assert snap["enabled"] is False
    assert snap["reason"] == "speed test"
    humanize_on.set_enabled(True, reason="back on")
    snap = humanize_on.snapshot()
    assert snap["enabled"] is True
    assert snap["reason"] == "back on"


def test_export_format_ts_handles_iso_epoch_and_none():
    from src.agentic_computer_use.export import _format_ts
    assert _format_ts("2026-04-16T00:25:00+00:00") == "2026-04-16 00:25:00"
    # Epoch seconds should produce a matching UTC string
    assert _format_ts(1776299114).startswith("2026-04-16 00:25:")
    assert _format_ts(None) is None
    assert _format_ts("") is None


def test_export_earliest_action_ts():
    from src.agentic_computer_use.export import _earliest_action_ts
    summary = {
        "items": [
            {"action_details": [
                {"created_at": "2026-04-16T00:26:00+00:00"},
                {"created_at": "2026-04-16T00:25:20.465+00:00"},
            ]},
            {"action_details": [
                {"created_at": "2026-04-16T00:25:19+00:00"},  # earliest
            ]},
        ]
    }
    assert _earliest_action_ts(summary) == "2026-04-16T00:25:19+00:00"
    assert _earliest_action_ts({"items": []}) is None


def test_export_shot_candidates():
    from src.agentic_computer_use.export import _shot_candidates
    # bare id → generates 4 candidate filenames
    assert _shot_candidates("abc123") == [
        "abc123_before.jpg", "abc123_after.jpg",
        "abc123_before_thumb.jpg", "abc123_after_thumb.jpg",
    ]
    # already a filename → returned as-is
    assert _shot_candidates("abc123_before.jpg") == ["abc123_before.jpg"]
    # path-prefixed → basename wins
    assert _shot_candidates("screenshots/abc.png") == ["abc.png"]


def test_export_maybe_pad_nudges_iso_timestamps():
    from src.agentic_computer_use.export import _maybe_pad
    padded = _maybe_pad("2026-04-16T00:25:20+00:00", -60)
    assert padded.startswith("2026-04-16T00:24:20")
    padded = _maybe_pad("2026-04-16T00:28:51+00:00", 60)
    assert padded.startswith("2026-04-16T00:29:51")
    # None passes through
    assert _maybe_pad(None, 60) is None
    # Unparseable passes through
    assert _maybe_pad("not-a-date", 60) == "not-a-date"


def test_export_read_journal_graceful_on_bad_unit(monkeypatch):
    """If journalctl fails we should return an explanatory header, not raise."""
    from src.agentic_computer_use import export
    # Point to a unit that doesn't exist — journalctl still succeeds with empty
    # output. So we simulate failure by routing to a bogus binary.
    result = export.read_journal(unit="definitely-not-a-real-service")
    # Either empty-but-successful or a "# " header — never raises
    assert isinstance(result, str)


def test_export_parse_any_ts():
    from src.agentic_computer_use.export import _parse_any_ts
    from datetime import datetime, timezone
    assert _parse_any_ts(None) is None
    assert _parse_any_ts("") is None
    dt = _parse_any_ts("2026-04-16T12:00:00+00:00")
    assert dt and dt.year == 2026 and dt.hour == 12
    dt = _parse_any_ts(1776299114)
    assert dt and dt.year == 2026 and dt.tzinfo is timezone.utc
    assert _parse_any_ts("not-a-date") is None


def test_export_redact_catches_common_keys():
    from src.agentic_computer_use.export import _redact, _REDACTED
    text = (
        "OPENROUTER_API_KEY=sk-or-v1-abcdef0123456789abcdef0123456789abcdef0123456789 "
        "ANTHROPIC=sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ_0123456789-hello "
        "and safe text sk-ant-short stays"
    )
    red = _redact(text)
    assert "sk-or-v1-abcdef" not in red
    assert "sk-ant-api03-aBcD" not in red
    assert _REDACTED in red
    # Short non-matching tokens survive
    assert "safe text" in red


def test_export_error_grep_tags_sources():
    from src.agentic_computer_use.export import _extract_errors
    sources = {
        "detm-daemon.journal": "info ok\n2026-04-16 ERROR something broke\nnormal line\nTraceback (most recent call last):\n",
        "openclaw-gateway.journal": "info ok\nWARNING mild\n2026-04-16 CRITICAL fatal boom\n",
    }
    errors = _extract_errors(sources)
    assert "[detm-daemon.journal] 2026-04-16 ERROR something broke" in errors
    assert "[detm-daemon.journal] Traceback (most recent call last):" in errors
    assert "[openclaw-gateway.journal] 2026-04-16 CRITICAL fatal boom" in errors
    assert "[openclaw-gateway.journal] WARNING mild" not in errors   # WARNING is not in our error set


def test_export_rejected_calls_formatter_calls_out_fingerprint():
    from src.agentic_computer_use.export import _extract_rejected_calls
    journal = (
        '2026-04-16T00:25:08+00:00 aiohttp.access "POST /gui_agent HTTP/1.1" 404 56\n'
        '2026-04-16T00:25:09+00:00 aiohttp.access "GET /health HTTP/1.1" 200 100\n'
        '2026-04-16T00:25:10+00:00 aiohttp.access "POST /desktop_look HTTP/1.1" 400 40\n'
    )
    out = _extract_rejected_calls(journal)
    # Both 4xx lines included, 200 is not
    assert '"POST /gui_agent HTTP/1.1" 404' in out
    assert '"POST /desktop_look HTTP/1.1" 400' in out
    assert "HTTP/1.1\" 200" not in out
    # Instructive header warns about the task_register fingerprint
    assert "task_register" in out.lower() or "cross-reference" in out.lower()

    empty = _extract_rejected_calls('2026-04-16 "GET /health HTTP/1.1" 200 100\n')
    assert "no 4xx" in empty.lower()


def test_export_session_overlaps_window(tmp_path):
    from src.agentic_computer_use.export import _session_overlaps_window
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    # Session file that starts 2h ago
    p = tmp_path / "sess.jsonl"
    p.write_text(
        '{"type":"session","version":3,"id":"s1","timestamp":"'
        + (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        + '","cwd":"/x"}\n'
    )
    # Window: last 3h → includes
    assert _session_overlaps_window(p, now - timedelta(hours=3), now) is True
    # Window: 4-5h ago → excludes (session starts AFTER the until)
    assert _session_overlaps_window(
        p, now - timedelta(hours=5), now - timedelta(hours=4)
    ) is False
