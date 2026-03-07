"""Abstract base class for GUI agent backends."""
from abc import ABC, abstractmethod
from .types import GroundingResult


class GUIAgentBackend(ABC):
    """Interface for GUI grounding backends (UI-TARS, Claude CU, direct)."""

    @property
    def provider(self) -> str:
        """Short label for the active provider (e.g. 'ollama', 'openrouter', 'local')."""
        return "local"

    @abstractmethod
    async def ground(
        self,
        description: str,
        screenshot: bytes,
        image_size: tuple[int, int] = (1920, 1080),
        cursor_pos: tuple[int, int] | None = None,
        hint: str | None = None,
    ) -> GroundingResult | None:
        """Locate a UI element by natural language description.

        Args:
            description: What to find (e.g., "the Export button")
            screenshot: JPEG bytes of the current screen/window
            image_size: (width, height) in pixels of the screenshot. Used to
                scale normalized [0,1] coordinates returned by the model back
                to pixel space. Pass the actual screen or crop dimensions —
                do not assume 1920×1080.
            cursor_pos: If provided, (x, y) pixel coordinates of the current
                cursor overlay visible in the screenshot. Used during refinement
                rounds so the model knows what the cursor marker is.

        Returns:
            GroundingResult with pixel coordinates, or None if not found.
        """
        ...

    @abstractmethod
    async def check_health(self) -> dict:
        """Check if the backend is ready."""
        ...
