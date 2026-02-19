"""vLLM vision backend — OpenAI-compatible API for UI-TARS, Qwen, etc."""
import base64
import time
import httpx
import logging
from ... import config, debug
from ..base import VisionBackend

log = logging.getLogger(__name__)


class VLLMBackend(VisionBackend):
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        model = model or config.VLLM_MODEL
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
            "max_tokens": 450,
            "temperature": 0.1,
        }

        start = time.time()
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{config.VLLM_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            response_text = data["choices"][0]["message"]["content"].strip()
            elapsed_ms = (time.time() - start) * 1000

            debug.log_vision_response(response_text, elapsed_ms, job_id=job_id)
            return response_text

    async def check_health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.VLLM_URL}/v1/models")
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", [])]
                return {"ok": True, "backend": "vllm", "models": models, "target_model": config.VLLM_MODEL}
        except Exception as e:
            return {"ok": False, "backend": "vllm", "error": str(e)}
