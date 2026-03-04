"""Live UI vision/control — OpenRouter-backed implementation."""
from .base import LiveUIProvider
from .openrouter import OpenRouterVLMProvider


def get_provider() -> LiveUIProvider:
    """Return the single supported live UI provider."""
    return OpenRouterVLMProvider()
