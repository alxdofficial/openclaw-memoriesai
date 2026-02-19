"""Direct backend — xdotool only, coordinates required (no grounding)."""
from ..base import GUIAgentBackend
from ..types import GroundingResult


class DirectBackend(GUIAgentBackend):
    """No visual grounding — only works with explicit coordinates."""

    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        """Direct backend cannot ground NL descriptions — returns None."""
        return None

    async def check_health(self) -> dict:
        return {"ok": True, "backend": "direct", "provider": "local", "note": "Coordinates required — no NL grounding"}
