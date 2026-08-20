"""Feature flags for ChillMind. Everything defaults to OFF."""
import os

_TRUTHY = {"1", "true", "yes", "on"}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def ai_answers_enabled() -> bool:
    """When False, ChillMind behaves exactly as it did before this feature."""
    return _bool_env("AI_ANSWERS_ENABLED", False)
