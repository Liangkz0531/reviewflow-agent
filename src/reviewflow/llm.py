"""DeepSeek client and retry handling."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from openai import OpenAI

from reviewflow.config import Settings


class LLMError(RuntimeError):
    """Raised when an LLM request cannot be completed."""


class LLMNotConfiguredError(LLMError):
    """Raised when the API key has not been configured."""


class LLMClient(Protocol):
    """Minimal interface used by the review agent."""

    @property
    def available(self) -> bool:
        """Return whether the client can make requests."""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return model text for the supplied prompts."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from plain text or a fenced model response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("模型没有返回可解析的 JSON 对象。")

    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型返回的 JSON 无法解析: {exc}") from exc

    if not isinstance(payload, dict):
        raise LLMError("模型返回的 JSON 顶层必须是对象。")
    return payload


class DeepSeekClient:
    """OpenAI-compatible client configured for DeepSeek."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: OpenAI | None = None

    @property
    def available(self) -> bool:
        return self._settings.api_key_configured

    def _get_client(self) -> OpenAI:
        if not self.available:
            raise LLMNotConfiguredError(
                "DeepSeek API Key 尚未配置，请编辑项目根目录下的 .env 文件。"
            )
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.deepseek_api_key,
                base_url=self._settings.deepseek_base_url,
                timeout=self._settings.llm_timeout_seconds,
                max_retries=0,
            )
        return self._client

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(self._settings.llm_max_retries):
            try:
                response = client.chat.completions.create(
                    model=self._settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self._settings.llm_temperature,
                    max_tokens=3500,
                )
                content = response.choices[0].message.content
                if not content:
                    raise LLMError("模型返回了空内容。")
                return content
            except Exception as exc:
                last_error = exc
                if attempt < self._settings.llm_max_retries - 1:
                    time.sleep(2**attempt)

        raise LLMError(f"LLM 调用失败: {last_error}") from last_error
