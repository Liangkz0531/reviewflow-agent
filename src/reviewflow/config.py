"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the application."""

    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    llm_temperature: float
    llm_timeout_seconds: int
    llm_max_retries: int
    max_code_chars: int
    tool_timeout_seconds: int

    @property
    def api_key_configured(self) -> bool:
        """Return whether a usable API key appears to be configured."""
        value = self.deepseek_api_key.strip()
        return bool(value) and not value.lower().startswith(("sk-your", "your-"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once for the current process."""
    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.2),
        llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 60),
        llm_max_retries=_env_int("LLM_MAX_RETRIES", 3),
        max_code_chars=_env_int("MAX_CODE_CHARS", 20_000),
        tool_timeout_seconds=_env_int("TOOL_TIMEOUT_SECONDS", 15),
    )
