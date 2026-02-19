"""Abstract base class for GUI agent backends."""
from abc import ABC, abstractmethod
from .types import GroundingResult


class GUIAgentBackend(ABC):
    """Interface for GUI grounding backends (UI-TARS, Claude CU, direct)."""

    @abstractmethod
    async def ground(self, description: str, screenshot: bytes) -> GroundingResult | None:
        """Locate a UI element by natural language description.

        Args:
            description: What to find (e.g., "the Export button")
            screenshot: JPEG bytes of the current screen/window

        Returns:
            GroundingResult with coordinates, or None if not found.
        """
        ...

    @abstractmethod
    async def check_health(self) -> dict:
        """Check if the backend is ready."""
        ...
