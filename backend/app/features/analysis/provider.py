"""OpenAI-compatible async HTTP provider for document analysis."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from app.features.analysis.errors import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.features.analysis.prompts import ChatMessage


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")


class OpenAICompatibleProvider:
    """Send one bounded chat completion request and validate its envelope."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        response_format: Literal["json_object", "prompt_only"],
    ) -> None:
        self._client = client
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._response_format = response_format
        self._close_lock = asyncio.Lock()
        self._closed = False

    async def analyze(self, messages: list[ChatMessage]) -> dict[str, Any]:
        """Return the single JSON object from provider message content."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }
        if self._response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        api_key = self._api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.post(
                    self._endpoint,
                    json=payload,
                    headers=headers,
                    timeout=httpx.Timeout(self._timeout_seconds),
                )
                self._raise_for_provider_status(response.status_code)

                try:
                    envelope = response.json()
                    choices = envelope["choices"]
                    content = choices[0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as error:
                    raise ProviderResponseError() from error

                if not isinstance(content, str):
                    raise ProviderResponseError()
                try:
                    result = json.loads(
                        content,
                        parse_constant=_reject_non_json_constant,
                    )
                except (ValueError, TypeError) as error:
                    raise ProviderResponseError() from error
                if not isinstance(result, dict):
                    raise ProviderResponseError()
                return result
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ProviderTimeoutError() from error
        except httpx.RequestError as error:
            raise ProviderUnavailableError() from error

    @staticmethod
    def _raise_for_provider_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 429:
            raise ProviderRateLimitError()
        if status_code in {408, 504}:
            raise ProviderTimeoutError()
        raise ProviderUnavailableError()

    async def close(self) -> None:
        """Close the owned HTTP client exactly once."""
        async with self._close_lock:
            if self._closed:
                return
            await self._client.aclose()
            self._closed = True
