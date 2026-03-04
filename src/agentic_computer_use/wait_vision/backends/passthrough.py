"""Passthrough backend — raw screenshot capture, no vision evaluation."""
import logging
from ..base import VisionBackend

log = logging.getLogger(__name__)


class PassthroughBackend(VisionBackend):
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        """Returns a static watching response — no actual model evaluation."""
        return 'FINAL_JSON: {"decision":"watching","confidence":0.0,"evidence":[],"summary":"passthrough backend — no vision evaluation"}'

    async def check_health(self) -> dict:
        return {"ok": True, "backend": "passthrough", "note": "No vision model — screenshots only"}
