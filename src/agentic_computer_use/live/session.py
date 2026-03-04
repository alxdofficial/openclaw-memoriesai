"""Live UI session recorder — writes events + frames to disk for dashboard replay."""
import json
import logging
import time
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

LIVE_SESSIONS_DIR = config.DATA_DIR / "live_sessions"


def _ensure_dir():
    LIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


class LiveUISession:
    """
    Records a live_ui session to disk as JSONL events + JPEG frames.

    Dir layout:
        ~/.agentic-computer-use/live_sessions/<id>/
            events.jsonl    — newline-delimited JSON events, chronological
            frames/         — 00000.jpg, 00001.jpg, ...  (one per streamed frame)
    """

    def __init__(
        self,
        session_id: str,
        task_id: str | None,
        instruction: str,
        context: str,
        timeout: int,
    ):
        _ensure_dir()
        self.id = session_id
        self.task_id = task_id
        self.instruction = instruction
        self.context = context
        self.timeout = timeout
        self.started_at = time.time()
        self._frame_count = 0

        self._dir = LIVE_SESSIONS_DIR / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "frames").mkdir(exist_ok=True)
        self._events_path = self._dir / "events.jsonl"

        # Write opening instruction event
        self._append({
            "type": "instruction",
            "instruction": instruction,
            "context": context,
            "timeout": timeout,
            "ts": self.started_at,
        })

    # ── Public API ───────────────────────────────────────────────

    def record_frame(self, jpeg_bytes: bytes) -> int:
        """Save a JPEG frame. Returns the frame index."""
        n = self._frame_count
        self._frame_count += 1
        try:
            (self._dir / "frames" / f"{n:05d}.jpg").write_bytes(jpeg_bytes)
            self._append({"type": "frame", "n": n, "ts": time.time()})
        except Exception as e:
            log.debug(f"Session frame write error: {e}")
        return n

    def record_model_text(self, text: str) -> None:
        if text.strip():
            self._append({"type": "model_text", "text": text.strip(), "ts": time.time()})

    def record_tool_call(self, name: str, args: dict, call_id: str) -> None:
        self._append({
            "type": "tool_call",
            "name": name,
            "args": args,
            "id": call_id,
            "ts": time.time(),
        })

    def record_tool_response(self, name: str, call_id: str, result: str) -> None:
        self._append({
            "type": "tool_response",
            "name": name,
            "id": call_id,
            "result": result,
            "ts": time.time(),
        })

    def record_done(self, success: bool, summary: str) -> None:
        self._append({
            "type": "done",
            "success": success,
            "summary": summary,
            "ts": time.time(),
        })

    def record_escalate(self, reason: str) -> None:
        self._append({"type": "escalate", "reason": reason, "ts": time.time()})

    def record_error(self, message: str) -> None:
        self._append({"type": "error", "message": message, "ts": time.time()})

    @property
    def frame_count(self) -> int:
        return self._frame_count

    # ── Internal ─────────────────────────────────────────────────

    def _append(self, event: dict) -> None:
        try:
            with open(self._events_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            log.debug(f"Session event write error: {e}")


def load_session(session_id: str) -> dict | None:
    """Load all events and metadata for a session. Returns None if not found."""
    session_dir = LIVE_SESSIONS_DIR / session_id
    events_path = session_dir / "events.jsonl"
    if not events_path.exists():
        return None

    events = []
    instruction = ""
    context = ""
    timeout = 0

    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                events.append(ev)
                if ev.get("type") == "instruction":
                    instruction = ev.get("instruction", "")
                    context = ev.get("context", "")
                    timeout = ev.get("timeout", 0)
            except Exception:
                pass

    frame_count = sum(1 for ev in events if ev.get("type") == "frame")

    return {
        "session_id": session_id,
        "instruction": instruction,
        "context": context,
        "timeout": timeout,
        "events": events,
        "frame_count": frame_count,
    }


def get_frame(session_id: str, n: int) -> bytes | None:
    """Return raw JPEG bytes for frame n, or None."""
    path = LIVE_SESSIONS_DIR / session_id / "frames" / f"{n:05d}.jpg"
    if path.exists():
        return path.read_bytes()
    return None
