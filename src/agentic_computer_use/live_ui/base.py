"""Abstract base for live UI vision/control providers."""
from abc import ABC, abstractmethod


class LiveUIProvider(ABC):
    """
    A stateful live session that watches the screen and takes actions.

    Implementor: OpenRouterVLMProvider.
    """

    @abstractmethod
    async def run(
        self,
        instruction: str,
        timeout: int,
        task_id: str | None,
        display: str,
        context: str = "",
        session=None,  # LiveUISession | None
    ) -> dict:
        """
        Run a live UI automation session.

        Args:
            instruction: What to accomplish (natural language)
            timeout: Max seconds before giving up
            task_id: DETM task ID (for logging)
            display: X11 display string (e.g. ":99")
            context: Extra context for the model (credentials, brand info, etc.)

        Returns dict with:
            success: bool
            summary: str — what was accomplished or why it failed
            actions_taken: int — number of GUI actions executed
            error: str | None — set if a hard error occurred (API key missing, timeout, etc.)
            escalated: bool — True if the model requested human help
            escalation_reason: str — why it escalated (only if escalated=True)
        """
        ...
