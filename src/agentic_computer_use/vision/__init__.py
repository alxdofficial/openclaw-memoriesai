"""Pluggable vision backend — re-exports evaluate_condition() and check_health().

All existing callers (wait/engine.py, daemon.py) work unchanged.
Backend selected by ACU_VISION_BACKEND env var: ollama|vllm|claude|passthrough
"""
from .. import config
from .base import VisionBackend

_backend: VisionBackend | None = None


def _get_backend() -> VisionBackend:
    global _backend
    if _backend is not None:
        return _backend

    name = config.VISION_BACKEND.lower()
    if name == "ollama":
        from .backends.ollama import OllamaBackend
        _backend = OllamaBackend()
    elif name == "vllm":
        from .backends.vllm import VLLMBackend
        _backend = VLLMBackend()
    elif name == "claude":
        from .backends.claude import ClaudeBackend
        _backend = ClaudeBackend()
    elif name == "passthrough":
        from .backends.passthrough import PassthroughBackend
        _backend = PassthroughBackend()
    else:
        raise ValueError(f"Unknown vision backend: {name}. Use ollama|vllm|claude|passthrough")

    return _backend


async def evaluate_condition(
    prompt: str,
    images: list[bytes],
    model: str = None,
    job_id: str = None,
) -> str:
    """Send prompt + images to the configured vision backend."""
    return await _get_backend().evaluate_condition(prompt, images, model=model, job_id=job_id)


async def check_health(model: str = None) -> dict:
    """Check if the configured vision backend is healthy."""
    return await _get_backend().check_health()


# Legacy alias
check_ollama_health = check_health
