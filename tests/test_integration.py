"""Integration tests for openclaw-memoriesai."""
import asyncio
import time
import pytest

# Reset display cache before tests
import src.openclaw_memoriesai.capture.screen as screen_mod
screen_mod._display = None


@pytest.fixture
def engine():
    from src.openclaw_memoriesai.wait.engine import WaitEngine
    e = WaitEngine()
    # Mock system event injection
    e._events = []
    async def mock_inject(msg):
        e._events.append(msg)
    e._inject_system_event = mock_inject
    return e


def test_pty_fast_path():
    """PTY text matching should catch common patterns without vision."""
    from src.openclaw_memoriesai.capture.pty import try_text_match

    assert try_text_match("Compiled successfully in 2.3s\n$ ", "build completes") is not None
    assert try_text_match("added 423 packages in 12s", "npm install done") is not None
    assert try_text_match("Downloading... 45%", "download completes") is None
    assert try_text_match("Process exited with code 1", "error occurs") is not None
    assert try_text_match("random text", "something weird") is None


def test_pixel_diff_gate():
    """Pixel diff should gate identical frames and pass different ones."""
    import numpy as np
    from src.openclaw_memoriesai.capture.diff import PixelDiffGate

    gate = PixelDiffGate()
    frame1 = np.zeros((100, 100, 3), dtype=np.uint8)
    frame2 = frame1.copy()
    frame3 = frame1.copy()
    frame3[10:50, 10:50] = 255  # 16% change

    assert gate.should_evaluate(frame1) == True   # first frame always
    assert gate.should_evaluate(frame2) == False   # identical
    assert gate.should_evaluate(frame3) == True    # changed


def test_adaptive_poller():
    """Poller should speed up on partial and slow down on static."""
    from src.openclaw_memoriesai.wait.poller import AdaptivePoller

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
    from src.openclaw_memoriesai.wait.context import JobContext

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
async def test_task_lifecycle():
    """Tasks should support register → update → query → complete."""
    from src.openclaw_memoriesai.task import manager
    # Clean DB
    from src.openclaw_memoriesai import db, config
    import os
    config.DB_PATH = config.DATA_DIR / "test_data.db"
    if config.DB_PATH.exists():
        os.remove(config.DB_PATH)

    result = await manager.register_task("Test", ["step1", "step2"])
    tid = result["task_id"]
    assert result["status"] == "active"

    await manager.update_task(tid, message="Did step 1")
    q = await manager.update_task(tid, query="what happened?")
    assert "Did step 1" in q["summary"]

    await manager.update_task(tid, status="completed")
    tasks = await manager.list_tasks(status="completed")
    assert any(t["task_id"] == tid for t in tasks["tasks"])

    # Cleanup
    os.remove(config.DB_PATH)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
