"""Vision model client via Ollama API."""
import base64
import time
import httpx
import logging
from . import config
from . import debug

log = logging.getLogger(__name__)


async def evaluate_condition(
    prompt: str,
    images: list[bytes],
    model: str = None,
) -> str:
    """Send prompt + images to Ollama vision model, return raw text response."""
    model = model or config.VISION_MODEL
    encoded_images = [base64.b64encode(img).decode() for img in images]

    debug.log_vision_request(prompt, len(images), [len(img) for img in images])

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": 200,  # short responses
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

        debug.log_vision_response(response_text, elapsed_ms)
        log.debug(f"Vision response ({data.get('total_duration', 0)/1e9:.1f}s): {response_text}")
        return response_text


async def check_ollama_health(model: str = None) -> dict:
    """Check if Ollama is running and model is available."""
    model = model or config.VISION_MODEL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check server
            resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            has_model = any(model in m for m in models)
            return {"ok": True, "models": models, "has_model": has_model, "target_model": model}
    except Exception as e:
        return {"ok": False, "error": str(e)}
