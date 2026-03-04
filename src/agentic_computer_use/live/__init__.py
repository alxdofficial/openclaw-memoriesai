"""Live UI vision/control — multi-provider factory."""
from .. import config
from .base import LiveUIProvider


def get_provider() -> LiveUIProvider:
    """Return the configured live UI provider."""
    name = config.LIVE_UI_PROVIDER.lower()
    if name == "gemini":
        from .gemini import GeminiLiveProvider
        return GeminiLiveProvider()
    elif name == "qwen":
        from .qwen import QwenOmniProvider
        return QwenOmniProvider()
    # Future:
    # elif name == "openai":
    #     from .openai import OpenAIRealtimeProvider
    #     return OpenAIRealtimeProvider()
    else:
        raise ValueError(
            f"Unknown ACU_LIVE_UI_PROVIDER={name!r}. Supported: gemini, qwen"
        )
