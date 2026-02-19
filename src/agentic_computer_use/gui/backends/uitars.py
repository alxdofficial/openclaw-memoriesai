"""UI-TARS backend — best grounding accuracy via vLLM API."""
import base64
import re
import httpx
import logging
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult

log = logging.getLogger(__name__)


class UITARSBackend(GUIAgentBackend):
    """UI-TARS-1.5-7B grounding via vLLM OpenAI-compatible API."""

    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        b64 = base64.b64encode(screenshot).decode()

        payload = {
            "model": config.VLLM_MODEL,
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

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{config.VLLM_URL}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"].strip()

                # Parse coordinates — UI-TARS returns normalized (x, y) in various formats
                coords = _parse_coordinates(text)
                if coords:
                    return GroundingResult(
                        x=coords[0], y=coords[1],
                        confidence=0.8, description=description,
                        element_text=text,
                    )
                return None
        except Exception as e:
            log.error(f"UI-TARS grounding failed: {e}")
            return None

    async def check_health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{config.VLLM_URL}/v1/models")
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", [])]
                return {"ok": True, "backend": "uitars", "models": models}
        except Exception as e:
            return {"ok": False, "backend": "uitars", "error": str(e)}


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
