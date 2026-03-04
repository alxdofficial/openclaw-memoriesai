"""Live UI session recorder — writes events + frames to disk for dashboard replay."""
import io
import json
import logging
import time
import wave
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

    # Gemini Live sends PCM: 16-bit signed, mono, 24 kHz
    _AUDIO_SAMPLE_RATE = 24000
    _AUDIO_SAMPLE_WIDTH = 2  # bytes (16-bit)
    _AUDIO_CHANNELS = 1

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
        self._audio_chunks: list[bytes] = []

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

    def record_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Buffer a raw PCM audio chunk from the model's audio response."""
        if pcm_bytes:
            self._audio_chunks.append(pcm_bytes)

    def finalize_audio(self) -> Path | None:
        """Write buffered PCM chunks to audio.wav. Returns path or None if no audio."""
        if not self._audio_chunks:
            return None
        wav_path = self._dir / "audio.wav"
        try:
            data = b"".join(self._audio_chunks)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(self._AUDIO_CHANNELS)
                wf.setsampwidth(self._AUDIO_SAMPLE_WIDTH)
                wf.setframerate(self._AUDIO_SAMPLE_RATE)
                wf.writeframes(data)
            log.info(f"Audio saved: {wav_path} ({len(data)} bytes, {len(data)/self._AUDIO_SAMPLE_RATE/self._AUDIO_SAMPLE_WIDTH:.1f}s)")
            return wav_path
        except Exception as e:
            log.warning(f"Audio WAV write failed: {e}")
            return None

    def transcribe_audio(self, wav_path: Path, model_name: str = "tiny") -> None:
        """
        Run Whisper on wav_path and append model_text events with timestamps.
        Uses the 'tiny' model by default — fast enough for post-session transcription.
        """
        try:
            import whisper  # type: ignore
        except ImportError:
            log.debug("Whisper not installed — skipping audio transcription")
            return
        try:
            log.info(f"Transcribing audio with Whisper ({model_name})…")
            model = whisper.load_model(model_name)
            result = model.transcribe(str(wav_path), language="en", fp16=False)
            session_start = self.started_at
            for seg in result.get("segments", []):
                text = seg.get("text", "").strip()
                if not text:
                    continue
                # Offset segment timestamps by session start time
                ts = session_start + float(seg.get("start", 0))
                self._append({"type": "model_text", "text": text, "ts": ts})
            log.info(f"Whisper transcription complete: {len(result.get('segments', []))} segments")
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
    has_audio = (session_dir / "audio.wav").exists()

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
