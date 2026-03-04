"""MAVI video intelligence integration.

Flow:
  1. record_screen()  — capture N seconds from the DETM display
  2. upload()         — POST multipart to /v1/upload, get videoNo
  3. poll_until_parse() — wait for MAVI to index the video
  4. chat()           — POST /v1/chat, parse SSE stream for answer
  5. delete()         — POST /v1/delete_videos to clean up
"""
import asyncio
import logging
import time
from pathlib import Path

import httpx

from . import config
from .video.recorder import record_screen

log = logging.getLogger(__name__)

MAVI_BASE = "https://api.memories.ai/serve/api/v1"
MAX_DURATION = 60
MIN_DURATION = 3
RECORD_FPS = 10
POLL_INTERVAL = 3.0   # seconds between status checks
POLL_TIMEOUT = 120.0  # max seconds to wait for PARSE status


async def record_and_understand(
    prompt: str,
    duration_seconds: int,
    task_id: str = None,
    api_key: str = None,
) -> dict:
    """
    Record the screen for duration_seconds, upload to MAVI, ask prompt,
    return the answer. Cleans up the local recording and MAVI asset afterwards.

    Returns:
        {"answer": str, "duration_recorded": int, "video_no": str}
        or {"error": str}
    """
    api_key = api_key or config.MAVI_API_KEY
    if not api_key:
        return {"error": "MAVI_API_KEY not set — add it to your .env file"}

    duration = max(MIN_DURATION, min(int(duration_seconds), MAX_DURATION))

    # ── 1. Record ────────────────────────────────────────────────
    output_name = f"mavi_{task_id or 'tmp'}_{int(time.time())}.mp4"
    log.info(f"MAVI: recording {duration}s at {RECORD_FPS}fps → {output_name}")
    path = record_screen(duration=duration, output_name=output_name, fps=RECORD_FPS)
    if not path or not Path(path).exists():
        return {"error": "Screen recording failed — is ffmpeg installed and is the display running?"}

    size_mb = Path(path).stat().st_size / 1024 / 1024
    log.info(f"MAVI: recorded {size_mb:.1f}MB")

    video_no = None
    try:
        # ── 2. Upload ────────────────────────────────────────────
        video_no = await _upload(path, api_key)
        log.info(f"MAVI: uploaded → video_no={video_no}")

        # ── 3. Poll until parsed ─────────────────────────────────
        await _poll_until_parse(video_no, api_key)
        log.info(f"MAVI: video {video_no} is parsed and ready")

        # ── 4. Chat ──────────────────────────────────────────────
        answer = await _chat(video_no, prompt, api_key)
        log.info(f"MAVI: answer received ({len(answer)} chars)")

        return {"answer": answer, "duration_recorded": duration, "video_no": video_no}

    except httpx.HTTPStatusError as e:
        body = e.response.text[:400]
        log.error(f"MAVI HTTP {e.response.status_code}: {body}")
        return {"error": f"MAVI API error {e.response.status_code}: {body}"}
    except asyncio.TimeoutError:
        return {"error": f"MAVI video did not finish parsing within {POLL_TIMEOUT}s"}
    except Exception as e:
        log.error(f"MAVI error: {e}")
        return {"error": str(e)}
    finally:
        # ── 5. Cleanup ───────────────────────────────────────────
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
        if video_no:
            try:
                await _delete(video_no, api_key)
                log.info(f"MAVI: deleted video {video_no}")
            except Exception as e:
                log.warning(f"MAVI: failed to delete {video_no}: {e}")


async def _upload(file_path: str, api_key: str) -> str:
    """Upload a video file to MAVI. Returns videoNo."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                f"{MAVI_BASE}/upload",
                headers={"Authorization": api_key},
                files={"file": (Path(file_path).name, f, "video/mp4")},
            )
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["videoNo"]


async def _poll_until_parse(video_no: str, api_key: str) -> None:
    """Poll list_videos until the video status is PARSE. Raises asyncio.TimeoutError on timeout."""
    deadline = time.time() + POLL_TIMEOUT
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() < deadline:
            resp = await client.post(
                f"{MAVI_BASE}/list_videos",
                headers={"Authorization": api_key},
                json={"video_no": video_no},
            )
            resp.raise_for_status()
            videos = resp.json().get("data", {}).get("videos", [])
            if videos and videos[0].get("status") == "PARSE":
                return
            status = videos[0].get("status", "unknown") if videos else "not found"
            log.debug(f"MAVI: waiting for PARSE, current status={status}")
            await asyncio.sleep(POLL_INTERVAL)
    raise asyncio.TimeoutError()


async def _chat(video_no: str, prompt: str, api_key: str) -> str:
    """Send a prompt about a video. Parses the SSE stream and returns the answer text."""
    payload = {
        "video_nos": [video_no],
        "prompt": prompt,
        "unique_id": "detm",
    }
    answer_parts = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{MAVI_BASE}/chat",
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == '"done"' or raw.lower() == '"done"':
                    break
                # The stream sends JSON objects; extract content field
                try:
                    import json
                    obj = json.loads(raw)
                    # Top-level content response
                    content = (
                        obj.get("data", {}).get("content")
                        or obj.get("content")
                    )
                    if content:
                        answer_parts.append(content)
                    # Stop on done signal
                    if obj.get("code") == "SUCCESS" and obj.get("data") == "Done":
                        break
                except Exception:
                    # Plain text chunk
                    answer_parts.append(raw.strip('"'))

    return "".join(answer_parts).strip()


async def _delete(video_no: str, api_key: str) -> None:
    """Delete a MAVI video."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{MAVI_BASE}/delete_videos",
            headers={"Authorization": api_key},
            json=[video_no],
        )
        resp.raise_for_status()
