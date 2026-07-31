"""OpenAI-compatible provider protocol, timeout, and safety tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.features.analysis.errors import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.features.analysis.provider import OpenAICompatibleProvider

pytestmark = pytest.mark.anyio

VALID_RESULT = {
    "classification": "contract",
    "confidence": 0.93,
    "summary": "A concise summary.",
    "tags": ["legal"],
    "fields": [{"label": "Date", "value": "2026-07-30"}],
}
MESSAGES = [
    {"role": "system", "content": "system"},
    {"role": "user", "content": "document"},
]


def completion_response(
    result: dict[str, Any] | None = None,
    *,
    content: Any = None,
) -> httpx.Response:
    """Build one successful OpenAI-compatible completion envelope."""
    effective_result = VALID_RESULT if result is None else result
    effective_content = json.dumps(effective_result) if content is None else content
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": effective_content}}]},
    )


def build_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "",
    response_format: str = "json_object",
    timeout_seconds: float = 45,
) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        client=client,
        base_url="http://provider.test/v1/",
        api_key=SecretStr(api_key),
        model="document-model",
        timeout_seconds=timeout_seconds,
        max_output_tokens=1_200,
        response_format=response_format,
    )


@pytest.mark.parametrize(
    ("api_key", "expected_authorization"),
    [
        ("top-secret", "Bearer top-secret"),
        ("", None),
    ],
)
async def test_provider_sends_exact_request_contract(
    api_key: str,
    expected_authorization: str | None,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return completion_response()

    provider = build_provider(handler, api_key=api_key)
    try:
        result = await provider.analyze(MESSAGES)
    finally:
        await provider.close()

    assert result == VALID_RESULT
    assert captured == {
        "url": "http://provider.test/v1/chat/completions",
        "authorization": expected_authorization,
        "payload": {
            "model": "document-model",
            "messages": MESSAGES,
            "temperature": 0,
            "max_tokens": 1_200,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
    }


async def test_prompt_only_omits_response_format() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return completion_response()

    provider = build_provider(handler, response_format="prompt_only")
    try:
        await provider.analyze(MESSAGES)
    finally:
        await provider.close()

    assert "response_format" not in captured_payload


async def test_hard_timeout_covers_mock_transport_wait() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return completion_response()

    provider = build_provider(handler, timeout_seconds=0.001)
    try:
        with pytest.raises(ProviderTimeoutError):
            await provider.analyze(MESSAGES)
    finally:
        await provider.close()


async def test_httpx_timeout_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout details", request=request)

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderTimeoutError) as caught:
            await provider.analyze(MESSAGES)
    finally:
        await provider.close()

    assert str(caught.value) == ProviderTimeoutError.default_message


async def test_network_failure_is_safe_and_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "top-secret http://provider.test/v1",
            request=request,
        )

    provider = build_provider(handler, api_key="top-secret")
    try:
        with pytest.raises(ProviderUnavailableError) as caught:
            await provider.analyze(MESSAGES)
    finally:
        await provider.close()

    assert str(caught.value) == ProviderUnavailableError.default_message
    assert "top-secret" not in str(caught.value)
    assert "provider.test" not in str(caught.value)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (400, ProviderUnavailableError),
        (401, ProviderUnavailableError),
        (403, ProviderUnavailableError),
        (404, ProviderUnavailableError),
        (408, ProviderTimeoutError),
        (422, ProviderUnavailableError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (504, ProviderTimeoutError),
    ],
)
async def test_upstream_status_mapping(
    status_code: int,
    error_type: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            text="raw response with top-secret internals",
        )

    provider = build_provider(handler, api_key="top-secret")
    try:
        with pytest.raises(error_type) as caught:
            await provider.analyze(MESSAGES)
    finally:
        await provider.close()

    assert "raw response" not in str(caught.value)
    assert "top-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        completion_response(content="not JSON"),
        completion_response(content="```json\n{}\n```"),
        completion_response(content='{"classification":"other"} trailing'),
        completion_response(content="[]"),
        completion_response(content='{"confidence":NaN}'),
    ],
)
async def test_malformed_provider_responses_are_rejected(
    response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    provider = build_provider(handler)
    try:
        with pytest.raises(ProviderResponseError):
            await provider.analyze(MESSAGES)
    finally:
        await provider.close()


async def test_close_is_idempotent() -> None:
    provider = build_provider(lambda request: completion_response())

    await provider.close()
    await provider.close()
