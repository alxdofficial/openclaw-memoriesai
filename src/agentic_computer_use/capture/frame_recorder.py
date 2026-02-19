"""Per-task background frame recorder — captures screenshots at a fixed interval."""
import asyncio
import logging
from pathlib import Path
from .. import config

log = logging.getLogger(__name__)

FRAME_INTERVAL = 0.5   # seconds between frames
FRAME_QUALITY  = 50    # lower quality to save disk
FRAME_MAX_DIM  = 1280

_tasks: dict[str, asyncio.Task] = {}  # task_id → asyncio capture loop


def frames_dir(task_id: str) -> Path:
    return config.DATA_DIR / "recordings" / task_id


def start(task_id: str, display: str) -> None:
    """Start background frame capture for a task (idempotent)."""
    if task_id in _tasks:
        return
    d = frames_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    loop_task = asyncio.ensure_future(_loop(task_id, display, d))
    _tasks[task_id] = loop_task
    log.debug(f"Frame recorder started: task={task_id} display={display}")


def stop(task_id: str) -> None:
    """Stop frame capture for a task."""
    t = _tasks.pop(task_id, None)
    if t:
        t.cancel()
        log.debug(f"Frame recorder stopped: task={task_id}")


def list_frames(task_id: str) -> list[int]:
    d = frames_dir(task_id)
    if not d.exists():
        return []
    return sorted(int(f.stem) for f in d.glob("*.jpg"))


def get_frame(task_id: str, frame_n: int) -> bytes | None:
    path = frames_dir(task_id) / f"{frame_n:06d}.jpg"
    return path.read_bytes() if path.exists() else None


def frame_count(task_id: str) -> int:
    d = frames_dir(task_id)
    return len(list(d.glob("*.jpg"))) if d.exists() else 0


async def _loop(task_id: str, display: str, d: Path) -> None:
    from .screen import capture_screen, frame_to_jpeg
    n = frame_count(task_id)   # resume numbering if restarted
    try:
        while True:
            frame = capture_screen(display=display)
            if frame is not None:
                jpeg = frame_to_jpeg(frame, max_dim=FRAME_MAX_DIM, quality=FRAME_QUALITY)
                (d / f"{n:06d}.jpg").write_bytes(jpeg)
                n += 1
            await asyncio.sleep(FRAME_INTERVAL)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning(f"Frame recorder error task={task_id}: {e}")
