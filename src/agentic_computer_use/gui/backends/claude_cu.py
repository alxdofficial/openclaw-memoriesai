"""Claude computer_use backend — Anthropic API with built-in grounding."""
import base64
import re
import httpx
import logging
from ... import config
from ..base import GUIAgentBackend
from ..types import GroundingResult

log = logging.getLogger(__name__)


class ClaudeCUBackend(GUIAgentBackend):
    """Claude computer_use tool for grounding — lower accuracy but zero-GPU."""

    @property
    def provider(self) -> str:
        return "anthropic"

    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        api_key = config.CLAUDE_API_KEY
        if not api_key:
            log.error("ANTHROPIC_API_KEY not set for Claude CU backend")
            return None

        b64 = base64.b64encode(screenshot).decode()

        payload = {
            "model": config.CLAUDE_VISION_MODEL,
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": f"Find the UI element described as: {description}\nReturn ONLY the pixel coordinates as (x, y). Nothing else."},
                    ],
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=payload,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["content"][0]["text"].strip()

                # Parse (x, y)
                match = re.search(r'\(\s*(\d+)\s*,\s*(\d+)\s*\)', text)
                if match:
                    return GroundingResult(
                        x=int(match.group(1)), y=int(match.group(2)),
                        confidence=0.5, description=description,
                        element_text=text,
                    )
                return None
        except Exception as e:
            log.error(f"Claude CU grounding failed: {e}")
            return None

    async def check_health(self) -> dict:
        if not config.CLAUDE_API_KEY:
            return {"ok": False, "backend": "claude_cu", "provider": "anthropic", "error": "ANTHROPIC_API_KEY not set"}
        return {"ok": True, "backend": "claude_cu", "provider": "anthropic", "model": config.CLAUDE_VISION_MODEL}
