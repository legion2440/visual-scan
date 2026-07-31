"""Scans HTTP contracts, lifecycle, validation, and safe error mapping."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import BACKEND_ROOT, Settings
from app.factory import create_app
from app.features.scans.errors import ScanStorageUnavailableError
from app.features.scans.router import get_scans_service

pytestmark = pytest.mark.anyio

FULL_PAYLOAD = {
    "filename": "contract.jpg",
    "text": "Full edited OCR text.",
    "analysis": {
        "classification": "contract",
        "confidence": 0.93,
        "summary": "Employment agreement between the parties.",
        "tags": ["legal", "employment"],
        "fields": [{"label": "Effective date", "value": "2026-07-30"}],
        "provider": "local-llm",
    },
    "ocr": {
        "source": "browser",
        "engine": "Tesseract.js 5.1.1 (browser)",
        "language": "eng",
        "profile": "fast",
        "confidence": 87,
        "words": 143,
    },
}


@asynccontextmanager
async def application_client(application: FastAPI) -> AsyncIterator[AsyncClient]:
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client


async def create_scan(
    client: AsyncClient,
    payload: dict[str, Any] | None = None,
):
    return await client.post("/api/scans", json=payload or FULL_PAYLOAD)


async def test_create_returns_exact_contract_and_relative_location(
    client: AsyncClient,
) -> None:
    response = await create_scan(client)

    assert response.status_code == 201
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    scan_id = UUID(body["id"])
    assert scan_id.version == 4
    assert body == {
        "id": str(scan_id),
        "filename": "contract.jpg",
        "scanned_at": body["scanned_at"],
        "text": "Full edited OCR text.",
        "analysis": FULL_PAYLOAD["analysis"],
        "ocr": FULL_PAYLOAD["ocr"],
    }
    assert body["scanned_at"].endswith("Z")
    assert response.headers["location"] == f"/api/scans/{scan_id}"


async def test_create_without_analysis_or_ocr_returns_nulls(
    client: AsyncClient,
) -> None:
    response = await create_scan(
        client,
        {
            "filename": "plain.txt",
            "text": "  User-edited text.\r\n",
            "analysis": None,
            "ocr": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["text"] == "  User-edited text.\r\n"
    assert response.json()["analysis"] is None
    assert response.json()["ocr"] is None


async def test_detail_keeps_fields_while_list_omits_text_and_fields(
    client: AsyncClient,
) -> None:
    created = (await create_scan(client)).json()
    scan_id = created["id"]

    detail = await client.get(f"/api/scans/{scan_id}")
    listing = await client.get("/api/scans")

    assert detail.status_code == 200
    assert detail.json() == created
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["id"] == scan_id
    assert item["snippet"] == "Full edited OCR text."
    assert "text" not in item
    assert "fields" not in item["analysis"]


async def test_list_pagination_search_filter_and_sort(client: AsyncClient) -> None:
    payloads = [
        {
            **FULL_PAYLOAD,
            "filename": "Zulu.jpg",
            "text": "Первый договор",
        },
        {
            **FULL_PAYLOAD,
            "filename": "alpha.jpg",
            "text": "Unicode Straße marker",
            "analysis": None,
        },
        {
            **FULL_PAYLOAD,
            "filename": "Beta.jpg",
            "text": "Third record",
            "analysis": {
                **FULL_PAYLOAD["analysis"],
                "classification": "invoice",
                "confidence": 0.2,
            },
        },
    ]
    for payload in payloads:
        assert (await create_scan(client, payload)).status_code == 201

    page = await client.get(
        "/api/scans",
        params={"limit": 1, "offset": 1, "sort": "filename", "order": "asc"},
    )
    search = await client.get("/api/scans", params={"q": "STRASSE"})
    unclassified = await client.get(
        "/api/scans",
        params={"classification": "unclassified"},
    )
    invoices = await client.get(
        "/api/scans",
        params={"classification": "invoice", "sort": "confidence", "order": "asc"},
    )

    assert page.json()["total"] == 3
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert page.json()["items"][0]["filename"] == "Beta.jpg"
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["filename"] == "alpha.jpg"
    assert unclassified.json()["total"] == 1
    assert unclassified.json()["items"][0]["analysis"] is None
    assert invoices.json()["total"] == 1
    assert invoices.json()["items"][0]["filename"] == "Beta.jpg"


async def test_offset_beyond_sqlite_integer_range_returns_422(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/api/scans",
        params={"offset": 10_000_000_000_000_000_000_000_000_000},
    )

    assert response.status_code == 422
    assert all("input" not in item for item in response.json()["detail"])


async def test_maximum_sqlite_offset_is_accepted(client: AsyncClient) -> None:
    response = await client.get(
        "/api/scans",
        params={"offset": 9_223_372_036_854_775_807},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["offset"] == 9_223_372_036_854_775_807


async def test_delete_one_and_clear_contracts(client: AsyncClient) -> None:
    first = (await create_scan(client)).json()["id"]
    second = (
        await create_scan(
            client,
            {**FULL_PAYLOAD, "filename": "second.jpg"},
        )
    ).json()["id"]

    deleted = await client.delete(f"/api/scans/{first}")
    missing = await client.delete(f"/api/scans/{first}")
    cleared = await client.delete("/api/scans")

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert missing.status_code == 404
    assert missing.json() == {"detail": "The requested scan was not found."}
    assert cleared.status_code == 200
    assert cleared.json() == {"deleted": 1}
    assert (await client.get(f"/api/scans/{second}")).status_code == 404


async def test_missing_and_malformed_identifiers_are_distinct(client: AsyncClient) -> None:
    missing = await client.get("/api/scans/00000000-0000-4000-8000-000000000001")
    malformed = await client.get("/api/scans/not-a-uuid")

    assert missing.status_code == 404
    assert malformed.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {**FULL_PAYLOAD, "id": "00000000-0000-4000-8000-000000000001"},
        {**FULL_PAYLOAD, "scanned_at": "2026-07-31T06:30:00Z"},
        {**FULL_PAYLOAD, "snippet": "client value"},
        {**FULL_PAYLOAD, "thumbnail": "data:image/jpeg;base64,abc"},
        {**FULL_PAYLOAD, "analysis": {**FULL_PAYLOAD["analysis"], "classification": "unknown"}},
        {**FULL_PAYLOAD, "analysis": {**FULL_PAYLOAD["analysis"], "tags": ["x" * 101]}},
        {
            **FULL_PAYLOAD,
            "analysis": {
                **FULL_PAYLOAD["analysis"],
                "tags": ["LEGAL", " legal "] * 6,
            },
        },
        {
            **FULL_PAYLOAD,
            "analysis": {
                **FULL_PAYLOAD["analysis"],
                "fields": [{"label": "x" * 201, "value": "value"}],
            },
        },
        {
            **FULL_PAYLOAD,
            "analysis": {
                **FULL_PAYLOAD["analysis"],
                "fields": [{"label": "label", "value": "x" * 5_001}],
            },
        },
        {
            **FULL_PAYLOAD,
            "analysis": {**FULL_PAYLOAD["analysis"], "provider": "x" * 101},
        },
        {**FULL_PAYLOAD, "ocr": {**FULL_PAYLOAD["ocr"], "engine": "x" * 101}},
        {**FULL_PAYLOAD, "ocr": {**FULL_PAYLOAD["ocr"], "profile": "x" * 51}},
        {**FULL_PAYLOAD, "filename": "x" * 256},
    ],
)
async def test_invalid_or_client_owned_fields_return_422(
    client: AsyncClient,
    payload: dict[str, Any],
) -> None:
    response = await create_scan(client, payload)

    assert response.status_code == 422


async def test_whitespace_text_returns_422(client: AsyncClient) -> None:
    response = await create_scan(client, {**FULL_PAYLOAD, "text": " \r\n\t "})

    assert response.status_code == 422
    assert response.json() == {"detail": "Scan text must not be empty."}


@pytest.mark.parametrize(
    "target",
    [
        "filename",
        "text",
        "summary",
        "tag",
        "field_label",
        "field_value",
        "provider",
        "ocr_engine",
        "ocr_profile",
    ],
)
async def test_lone_surrogates_in_stored_strings_return_422(
    client: AsyncClient,
    target: str,
) -> None:
    payload = deepcopy(FULL_PAYLOAD)
    surrogate = "\ud800"
    if target in {"filename", "text"}:
        payload[target] = surrogate
    elif target == "summary":
        payload["analysis"]["summary"] = surrogate
    elif target == "tag":
        payload["analysis"]["tags"] = [surrogate]
    elif target == "field_label":
        payload["analysis"]["fields"][0]["label"] = surrogate
    elif target == "field_value":
        payload["analysis"]["fields"][0]["value"] = surrogate
    elif target == "provider":
        payload["analysis"]["provider"] = surrogate
    elif target == "ocr_engine":
        payload["ocr"]["engine"] = surrogate
    else:
        payload["ocr"]["profile"] = surrogate

    raw_json = json.dumps(payload, ensure_ascii=True).encode("ascii")
    response = await client.post(
        "/api/scans",
        content=raw_json,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert all("input" not in item for item in response.json()["detail"])


async def test_valid_unicode_scalar_text_is_stored(client: AsyncClient) -> None:
    response = await create_scan(
        client,
        {
            "filename": "emoji.txt",
            "text": "Valid emoji: 😀",
            "analysis": None,
            "ocr": None,
        },
    )

    assert response.status_code == 201
    assert response.json()["text"] == "Valid emoji: 😀"


async def test_null_character_in_text_returns_422(client: AsyncClient) -> None:
    response = await create_scan(
        client,
        {
            "filename": "null.txt",
            "text": "\x00",
            "analysis": None,
            "ocr": None,
        },
    )

    assert response.status_code == 422


async def test_configured_text_limit_returns_413(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(
        update={
            "scans_database_path": tmp_path / "limit.db",
            "scans_max_text_chars": 5,
        }
    )
    application = create_app(settings)

    async with application_client(application) as test_client:
        response = await create_scan(
            test_client,
            {**FULL_PAYLOAD, "text": "123456"},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Scan text exceeds the 5-character storage limit."}
    assert "123456" not in response.text


class FailingService:
    """Raise one configured storage or unexpected exception."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def create(self, payload) -> None:
        raise self.error


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            ScanStorageUnavailableError(),
            503,
            "The scan archive is temporarily unavailable.",
        ),
        (
            RuntimeError("database path and full OCR text"),
            500,
            "The scan archive request failed unexpectedly.",
        ),
    ],
)
async def test_failures_return_safe_errors(
    app: FastAPI,
    client: AsyncClient,
    error: Exception,
    status_code: int,
    detail: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[get_scans_service] = lambda: FailingService(error)
    try:
        response = await create_scan(client)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "database path" not in response.text
    assert "full OCR text" not in response.text
    assert "database path" not in caplog.text
    assert "full OCR text" not in caplog.text


async def test_openapi_includes_scans_and_existing_routes(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/api/scans" in paths
    assert "/api/scans/{scan_id}" in paths
    assert "/api/health" in paths
    assert "/api/ocr/recognize" in paths
    assert "/api/ocr/pdf/recognize" in paths
    assert "/api/ai/analyze" in paths


async def test_database_is_created_only_during_lifespan(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "lifecycle" / "scans.db"
    application = create_app(
        test_settings.model_copy(update={"scans_database_path": database_path})
    )

    assert not database_path.exists()
    async with application.router.lifespan_context(application):
        assert database_path.is_file()


async def test_storage_bootstrap_failure_prevents_startup(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    application = create_app(test_settings.model_copy(update={"scans_database_path": tmp_path}))

    with pytest.raises(ScanStorageUnavailableError):
        async with application.router.lifespan_context(application):
            pass


async def test_custom_api_prefix_is_used_in_location(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    application = create_app(
        test_settings.model_copy(
            update={
                "api_prefix": "/custom-api",
                "scans_database_path": tmp_path / "custom.db",
            }
        )
    )

    async with application_client(application) as test_client:
        response = await test_client.post("/custom-api/scans", json=FULL_PAYLOAD)

    assert response.status_code == 201
    assert response.headers["location"].startswith("/custom-api/scans/")


async def test_relative_database_path_resolves_from_backend() -> None:
    settings = Settings(
        _env_file=None,
        scans_database_path="custom-data/scans.db",
    )

    assert settings.scans_database_path == (BACKEND_ROOT / "custom-data" / "scans.db").resolve()


@pytest.mark.parametrize("database_path", ["", ":memory:"])
async def test_non_file_database_paths_are_rejected_by_settings(
    database_path: str,
) -> None:
    with pytest.raises(ValueError, match="SCANS_DATABASE_PATH"):
        Settings(
            _env_file=None,
            scans_database_path=database_path,
        )


@pytest.mark.parametrize("busy_timeout_ms", [0, 60_001])
async def test_database_busy_timeout_is_bounded(busy_timeout_ms: int) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            scans_database_busy_timeout_ms=busy_timeout_ms,
        )
