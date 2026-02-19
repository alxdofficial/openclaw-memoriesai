"""UI-TARS backend — dual-mode grounding via OpenRouter (cloud) or Ollama (local)."""
import base64
import re
import httpx
import logging
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "bytedance/ui-tars-1.5-7b"


class UITARSBackend(GUIAgentBackend):
    """UI-TARS-1.5-7B grounding via OpenRouter (cloud) or Ollama (local)."""

    @property
    def _use_openrouter(self) -> bool:
        return bool(config.OPENROUTER_API_KEY)

    @property
    def provider(self) -> str:
        return "openrouter" if self._use_openrouter else "ollama"

    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        if self._use_openrouter:
            return await self._ground_openrouter(description, screenshot)
        return await self._ground_ollama(description, screenshot)

    async def _ground_openrouter(self, description: str, screenshot: bytes) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": f"Find the UI element: {description}\nReturn the click coordinates as (x, y) normalized to [0, 1]."},
                    ],
                }
            ],
            "max_tokens": 100,
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"].strip()
                log.debug(f"OpenRouter UI-TARS response: {text}")

                coords = _parse_coordinates(text)
                if coords:
                    return GroundingResult(
                        x=coords[0], y=coords[1],
                        confidence=0.8, description=description,
                        element_text=text,
                    )
                return None
        except Exception as e:
            log.error(f"UI-TARS grounding failed (openrouter): {e}")
            return None

    async def _ground_ollama(self, description: str, screenshot: bytes) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()
        model = config.UITARS_OLLAMA_MODEL
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Find the UI element: {description}\nReturn the click coordinates as (x, y) normalized to [0, 1].",
                    "images": [b64],
                }
            ],
            "stream": False,
            "keep_alive": config.UITARS_KEEP_ALIVE,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{config.OLLAMA_URL}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["message"]["content"].strip()
                log.debug(f"Ollama UI-TARS response: {text}")

                coords = _parse_coordinates(text)
                if coords:
                    return GroundingResult(
                        x=coords[0], y=coords[1],
                        confidence=0.8, description=description,
                        element_text=text,
                    )
                return None
        except httpx.HTTPStatusError as e:
            body = e.response.text if e.response else ""
            if "not found" in body.lower() or e.response.status_code == 404:
                log.error(f"UI-TARS model not found. Run: ollama pull {model}")
            else:
                log.error(f"UI-TARS grounding failed (ollama): {e}")
            return None
        except Exception as e:
            log.error(f"UI-TARS grounding failed (ollama): {e}")
            return None

    async def check_health(self) -> dict:
        if self._use_openrouter:
            return await self._check_health_openrouter()
        return await self._check_health_ollama()

    async def _check_health_openrouter(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                )
                resp.raise_for_status()
                return {"ok": True, "backend": "uitars", "provider": "openrouter", "model": OPENROUTER_MODEL}
        except Exception as e:
            return {"ok": False, "backend": "uitars", "provider": "openrouter", "error": str(e)}

    async def _check_health_ollama(self) -> dict:
        model = config.UITARS_OLLAMA_MODEL
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", [])]
                has_model = any(model in m for m in models)
                result = {
                    "ok": has_model, "backend": "uitars", "provider": "ollama",
                    "model": model, "has_model": has_model,
                }
                if not has_model:
                    result["hint"] = f"Run: ollama pull {model}"
                return result
        except Exception as e:
            return {"ok": False, "backend": "uitars", "provider": "ollama", "error": str(e)}


def _parse_coordinates(text: str) -> tuple[int, int] | None:
    """Parse coordinates from model output. Handles (x, y) and <point>x, y</point> formats."""
    # Try <point>x, y</point> format
    point_match = re.search(r'<point>\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*</point>', text)
    if point_match:
        x, y = float(point_match.group(1)), float(point_match.group(2))
        # If normalized (0-1), scale to common resolution
        if x <= 1.0 and y <= 1.0:
            x, y = int(x * 1920), int(y * 1080)
        return (int(x), int(y))

    # Try (x, y) format
    paren_match = re.search(r'\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)', text)
    if paren_match:
        x, y = float(paren_match.group(1)), float(paren_match.group(2))
        if x <= 1.0 and y <= 1.0:
            x, y = int(x * 1920), int(y * 1080)
        return (int(x), int(y))

    return None
