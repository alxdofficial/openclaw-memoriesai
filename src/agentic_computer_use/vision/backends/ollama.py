"""Ollama vision backend — local inference via Ollama API."""
import base64
import time
import httpx
import logging
from ... import config, debug
from ..base import VisionBackend

log = logging.getLogger(__name__)


class OllamaBackend(VisionBackend):
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        model = model or config.VISION_MODEL
        encoded_images = [base64.b64encode(img).decode() for img in images]

        debug.log_vision_request(prompt, len(images), [len(img) for img in images], job_id=job_id)

        payload = {
            "model": model,
            "system": config.VISION_SYSTEM_INSTRUCTIONS,
            "prompt": prompt,
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {
                "num_predict": 450,
                "temperature": 0.1,
            },
        }
        if encoded_images:
            payload["images"] = encoded_images

        start = time.time()
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{config.OLLAMA_URL}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "").strip()
            elapsed_ms = (time.time() - start) * 1000

            debug.log_vision_response(response_text, elapsed_ms, job_id=job_id)
            log.debug(f"Vision response ({data.get('total_duration', 0)/1e9:.1f}s): {response_text}")
            return response_text

    async def check_health(self) -> dict:
        model = config.VISION_MODEL
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                has_model = any(model in m for m in models)
                return {"ok": True, "backend": "ollama", "models": models, "has_model": has_model, "target_model": model}
        except Exception as e:
            return {"ok": False, "backend": "ollama", "error": str(e)}
