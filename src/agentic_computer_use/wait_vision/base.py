"""Abstract base class for vision backends."""
from abc import ABC, abstractmethod


class VisionBackend(ABC):
    """Interface for all vision backends (ollama, vllm, claude, passthrough)."""

    @abstractmethod
    async def evaluate_condition(
        self,
        prompt: str,
        images: list[bytes],
        model: str = None,
        job_id: str = None,
    ) -> str:
        """Send prompt + images to vision model, return raw text response."""
        ...

    @abstractmethod
    async def check_health(self) -> dict:
        """Check if the backend is healthy and ready."""
        ...
