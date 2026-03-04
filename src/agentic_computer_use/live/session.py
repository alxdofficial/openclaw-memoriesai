"""Live UI session recorder — writes events + frames to disk for dashboard replay."""
import json
import logging
import struct
import time
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

LIVE_SESSIONS_DIR = config.DATA_DIR / "live_sessions"

# Gemini Live sends PCM: 16-bit signed, mono, 24 kHz
AUDIO_SAMPLE_RATE = 24000
AUDIO_SAMPLE_WIDTH = 2   # bytes (16-bit)
AUDIO_CHANNELS = 1


def _ensure_dir():
    LIVE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def pcm_to_wav(pcm_data: bytes) -> bytes:
    """Wrap raw PCM bytes in a WAV header. Works on partial/growing data."""
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", data_size + 36, b"WAVE",
        b"fmt ", 16, 1, AUDIO_CHANNELS, AUDIO_SAMPLE_RATE,
        AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * AUDIO_SAMPLE_WIDTH,
        AUDIO_CHANNELS * AUDIO_SAMPLE_WIDTH, AUDIO_SAMPLE_WIDTH * 8,
        b"data", data_size,
    )
    return header + pcm_data


class LiveUISession:
    """
    Records a live_ui session to disk as JSONL events + JPEG frames + PCM audio.

    Dir layout:
        ~/.agentic-computer-use/live_sessions/<id>/
            events.jsonl    — newline-delimited JSON events, chronological
            frames/         — 00000.jpg, 00001.jpg, ...  (one per streamed frame)
            audio.pcm       — raw PCM chunks appended in real-time (served as WAV)
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
        self._pcm_path = self._dir / "audio.pcm"

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

    def record_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Append raw PCM bytes directly to audio.pcm — available for playback immediately."""
        if not pcm_bytes:
            return
        try:
            with open(self._pcm_path, "ab") as f:
                f.write(pcm_bytes)
        except Exception as e:
            log.debug(f"Audio chunk write error: {e}")

    def finalize_audio(self) -> Path | None:
        """Return pcm path if audio was recorded. PCM is already on disk — nothing to flush."""
        if self._pcm_path.exists() and self._pcm_path.stat().st_size > 0:
            size = self._pcm_path.stat().st_size
            duration = size / (AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH)
            log.info(f"Audio recorded: {size} bytes ({duration:.1f}s)")
            return self._pcm_path
        return None

    def transcribe_audio(self, pcm_path: Path, model_name: str = "tiny") -> None:
        """
        Run Whisper on the PCM file and append model_text events with timestamps.
        Uses the 'tiny' model by default — fast enough for post-session transcription.
        """
        try:
            import whisper  # type: ignore
        except ImportError:
            log.debug("Whisper not installed — skipping audio transcription")
            return
        try:
            log.info(f"Transcribing audio with Whisper ({model_name})…")
            # Whisper needs a WAV file
            wav_path = pcm_path.with_suffix(".wav")
            wav_path.write_bytes(pcm_to_wav(pcm_path.read_bytes()))
            model = whisper.load_model(model_name)
            result = model.transcribe(str(wav_path), language="en", fp16=False)
            for seg in result.get("segments", []):
                text = seg.get("text", "").strip()
                if not text:
                    continue
                ts = self.started_at + float(seg.get("start", 0))
                self._append({"type": "model_text", "text": text, "ts": ts})
            log.info(f"Whisper transcription: {len(result.get('segments', []))} segments")
        except Exception as e:
            log.warning(f"Whisper transcription failed: {e}")

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
    pcm_path = session_dir / "audio.pcm"
    has_audio = pcm_path.exists() and pcm_path.stat().st_size > 0

    return {
        "session_id": session_id,
        "instruction": instruction,
        "context": context,
        "timeout": timeout,
        "events": events,
        "frame_count": frame_count,
        "has_audio": has_audio,
    }


def get_frame(session_id: str, n: int) -> bytes | None:
    """Return raw JPEG bytes for frame n, or None."""
    path = LIVE_SESSIONS_DIR / session_id / "frames" / f"{n:05d}.jpg"
    if path.exists():
        return path.read_bytes()
    return None


def get_audio_wav(session_id: str) -> bytes | None:
    """Return current audio as WAV bytes (wraps live-growing PCM), or None."""
    pcm_path = LIVE_SESSIONS_DIR / session_id / "audio.pcm"
    if not pcm_path.exists() or pcm_path.stat().st_size == 0:
        return None
    return pcm_to_wav(pcm_path.read_bytes())
