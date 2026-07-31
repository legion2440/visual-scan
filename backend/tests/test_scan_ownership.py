"""End-to-end ownership isolation and explicit legacy claim contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.factory import create_app
from app.storage.schema import create_schema_v1

pytestmark = pytest.mark.anyio

ORIGIN = "http://localhost:5500"
PASSWORD = "correct horse battery staple"


async def register(client: AsyncClient, username: str) -> dict:
    response = await client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 201
    body = response.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


def scan_payload(name: str) -> dict:
    return {"filename": name, "text": f"Text for {name}", "analysis": None, "ocr": None}


async def test_two_users_have_strictly_isolated_archives(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Origin": ORIGIN},
            ) as first,
            AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Origin": ORIGIN},
            ) as second,
        ):
            first_auth = await register(first, "first-user")
            second_auth = await register(second, "second-user")
            assert first_auth["user"]["is_initial_user"] is True
            assert second_auth["user"]["is_initial_user"] is False

            first_id = (await first.post("/api/scans", json=scan_payload("first.txt"))).json()["id"]
            second_id = (await second.post("/api/scans", json=scan_payload("second.txt"))).json()[
                "id"
            ]

            assert (await first.get("/api/scans")).json()["total"] == 1
            assert (await second.get("/api/scans")).json()["total"] == 1
            assert (await second.get(f"/api/scans/{first_id}")).status_code == 404
            assert (await second.delete(f"/api/scans/{first_id}")).status_code == 404
            assert (await second.delete("/api/scans")).json() == {"deleted": 1}
            assert (await first.get(f"/api/scans/{first_id}")).status_code == 200
            assert (await first.get(f"/api/scans/{second_id}")).status_code == 404


def create_v1_archive(path: Path, scan_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        create_schema_v1(connection)
        connection.execute(
            """
            INSERT INTO scans (
                id, filename, scanned_at, text, classification, analysis_confidence,
                summary, provider, tags_json, fields_json, ocr_json
            ) VALUES (?, 'legacy.txt', '2026-07-31T10:30:00.000000Z', 'legacy text',
                      NULL, NULL, NULL, NULL, NULL, NULL, NULL)
            """,
            (scan_id,),
        )
        connection.commit()


async def test_legacy_archive_requires_explicit_initial_user_claim(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    scan_id = str(uuid4())
    create_v1_archive(database_path, scan_id)
    application = create_app(
        test_settings.model_copy(update={"scans_database_path": database_path})
    )

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with (
            AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Origin": ORIGIN},
            ) as initial,
            AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Origin": ORIGIN},
            ) as other,
        ):
            await register(initial, "initial-user")
            await register(other, "other-user")
            assert (await initial.get("/api/scans")).json()["total"] == 0
            assert (await initial.get("/api/scans/legacy")).json() == {
                "count": 1,
                "claimable": True,
            }
            denied = await other.get("/api/scans/legacy")
            assert denied.status_code == 403
            assert "legacy text" not in denied.text

            claimed = await initial.post("/api/scans/legacy/claim")
            repeated = await initial.post("/api/scans/legacy/claim")
            assert claimed.json() == {"claimed": 1}
            assert repeated.json() == {"claimed": 0}
            assert (await initial.get(f"/api/scans/{scan_id}")).json()["text"] == "legacy text"
            assert (await initial.get("/api/scans/legacy")).json() == {
                "count": 0,
                "claimable": False,
            }
            assert (await other.get("/api/scans")).json()["total"] == 0
