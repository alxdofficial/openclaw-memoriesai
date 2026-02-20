"""Claude vision backend — Anthropic API for zero-GPU fallback."""
import base64
import time
import httpx
import logging
from ... import config, debug
from ..base import VisionBackend

log = logging.getLogger(__name__)

# Persistent client — reused across calls to avoid TLS handshake overhead
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "x-api-key": config.CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
    return _client


class ClaudeBackend(VisionBackend):
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        model = model or config.CLAUDE_VISION_MODEL
        api_key = config.CLAUDE_API_KEY
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — cannot use Claude vision backend")

        debug.log_vision_request(prompt, len(images), [len(img) for img in images], job_id=job_id)

        # Build Anthropic messages format
        content = []
        for img in images:
            b64 = base64.b64encode(img).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
            })
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": model,
            "max_tokens": 450,
            "system": config.VISION_SYSTEM_INSTRUCTIONS,
            "messages": [{"role": "user", "content": content}],
        }

        start = time.time()
        client = _get_client()
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        response_text = data["content"][0]["text"].strip()
        elapsed_ms = (time.time() - start) * 1000

        debug.log_vision_response(response_text, elapsed_ms, job_id=job_id)
        return response_text

    async def check_health(self) -> dict:
        if not config.CLAUDE_API_KEY:
            return {"ok": False, "backend": "claude", "error": "ANTHROPIC_API_KEY not set"}
        return {"ok": True, "backend": "claude", "model": config.CLAUDE_VISION_MODEL}
