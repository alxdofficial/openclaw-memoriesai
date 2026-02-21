"""OpenRouter vision backend — cloud routing to Claude Haiku, Gemini Flash, etc."""
import base64
import time
import httpx
import logging
from ... import config, debug
from ..base import VisionBackend

log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Persistent client with auth headers baked in
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://github.com/openclaw/agentic-computer-use",
                "X-Title": "OpenClaw SmartWait",
                "Content-Type": "application/json",
            },
        )
    return _client


class OpenRouterBackend(VisionBackend):
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        model = model or config.OPENROUTER_VISION_MODEL
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY not set — cannot use OpenRouter vision backend")

        debug.log_vision_request(prompt, len(images), [len(img) for img in images], job_id=job_id)

        # Build OpenAI-compatible messages with images
        content = []
        for img in images:
            b64 = base64.b64encode(img).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })
        content.append({"type": "text", "text": prompt})

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": config.VISION_SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": content},
            ],
            "max_tokens": 150,
            "temperature": 0.1,
        }

        start = time.time()
        client = _get_client()
        resp = await client.post(f"{_OPENROUTER_BASE}/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        response_text = data["choices"][0]["message"]["content"].strip()
        elapsed_ms = (time.time() - start) * 1000

        debug.log_vision_response(response_text, elapsed_ms, job_id=job_id)
        return response_text

    async def check_health(self) -> dict:
        if not config.OPENROUTER_API_KEY:
            return {"ok": False, "backend": "openrouter", "error": "OPENROUTER_API_KEY not set"}
        try:
            client = _get_client()
            resp = await client.get(f"{_OPENROUTER_BASE}/models", timeout=5.0)
            resp.raise_for_status()
            return {"ok": True, "backend": "openrouter", "model": config.OPENROUTER_VISION_MODEL}
        except Exception as e:
            return {"ok": False, "backend": "openrouter", "error": str(e)}
