"""Authentication HTTP contracts, cookies, CSRF, Origin, and protection."""

from __future__ import annotations

from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient

from app.factory import create_app

pytestmark = pytest.mark.anyio

ORIGIN = "http://localhost:5500"
PASSWORD = "correct horse battery staple"


async def register(client: AsyncClient, username: str = "nazar"):
    return await client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD},
    )


def session_cookie(response) -> tuple[str, str]:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    morsel = cookie["visual_scan_session"]
    return morsel.value, response.headers["set-cookie"]


async def test_anonymous_session_is_exact_and_health_remains_public(
    anonymous_client: AsyncClient,
) -> None:
    session = await anonymous_client.get("/api/auth/session")
    health = await anonymous_client.get("/api/health")

    assert session.status_code == 200
    assert session.json() == {"authenticated": False, "user": None, "csrf_token": None}
    assert health.status_code == 200


async def test_register_sets_cookie_and_restores_same_session(
    anonymous_client: AsyncClient,
) -> None:
    response = await register(anonymous_client, "Nazar")

    assert response.status_code == 201
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == "nazar"
    assert body["user"]["is_initial_user"] is True
    assert isinstance(body["csrf_token"], str) and body["csrf_token"]
    token, header = session_cookie(response)
    assert token not in response.text
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/api" in header
    assert "Secure" not in header

    restored = await anonymous_client.get("/api/auth/session")
    assert restored.status_code == 200
    assert restored.json() == body


async def test_secure_cookie_setting_adds_secure_attribute(
    test_settings,
) -> None:
    application = create_app(test_settings.model_copy(update={"auth_cookie_secure": True}))
    from httpx import ASGITransport

    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="https://testserver",
            headers={"Origin": ORIGIN},
        ) as secure_client,
    ):
        response = await register(secure_client)
    assert response.status_code == 201
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("origin", [None, "http://evil.test", "null"])
async def test_register_requires_exact_origin(
    anonymous_client: AsyncClient,
    origin: str | None,
) -> None:
    headers = {} if origin is None else {"Origin": origin}
    if origin is None:
        headers["Origin"] = ""
    response = await anonymous_client.post(
        "/api/auth/register",
        json={"username": "nazar", "password": PASSWORD},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "The request origin is not allowed."}


async def test_login_rotates_presented_session_and_rejects_bad_credentials(
    anonymous_client: AsyncClient,
) -> None:
    registered = await register(anonymous_client)
    old_token, _ = session_cookie(registered)

    bad = await anonymous_client.post(
        "/api/auth/login",
        json={"username": "nazar", "password": "incorrect password value"},
    )
    assert bad.status_code == 401
    assert bad.json() == {"detail": "Invalid username or password."}

    logged_in = await anonymous_client.post(
        "/api/auth/login",
        json={"username": "NAZAR", "password": PASSWORD},
    )
    assert logged_in.status_code == 200
    new_token, _ = session_cookie(logged_in)
    assert new_token != old_token

    anonymous_client.cookies.set("visual_scan_session", old_token, path="/api")
    stale = await anonymous_client.get("/api/auth/session")
    assert stale.json() == {"authenticated": False, "user": None, "csrf_token": None}
    assert "Max-Age=0" in stale.headers["set-cookie"]


async def test_duplicate_username_is_case_insensitive(
    anonymous_client: AsyncClient,
) -> None:
    assert (await register(anonymous_client, "nazar")).status_code == 201
    duplicate = await register(anonymous_client, "NAZAR")
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "This username is unavailable."}


async def test_login_rate_limit_returns_retry_after(
    anonymous_client: AsyncClient,
) -> None:
    assert (await register(anonymous_client)).status_code == 201
    payload = {"username": "nazar", "password": "incorrect password value"}

    for _ in range(5):
        response = await anonymous_client.post("/api/auth/login", json=payload)
        assert response.status_code == 401

    blocked = await anonymous_client.post("/api/auth/login", json=payload)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many authentication attempts. Try again later."}
    assert 899 <= int(blocked.headers["retry-after"]) <= 900


async def test_logout_requires_csrf_only_for_a_valid_session(
    anonymous_client: AsyncClient,
) -> None:
    registered = await register(anonymous_client)
    csrf = registered.json()["csrf_token"]

    missing = await anonymous_client.post("/api/auth/logout")
    assert missing.status_code == 403
    assert (await anonymous_client.get("/api/auth/session")).json()["authenticated"] is True

    success = await anonymous_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert success.status_code == 204
    assert "Max-Age=0" in success.headers["set-cookie"]
    assert (await anonymous_client.get("/api/auth/session")).json()["authenticated"] is False

    repeated = await anonymous_client.post("/api/auth/logout")
    assert repeated.status_code == 204


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/ocr/recognize"),
        ("POST", "/api/ocr/pdf/recognize"),
        ("POST", "/api/ai/analyze"),
        ("POST", "/api/scans"),
        ("GET", "/api/scans"),
        ("GET", "/api/scans/00000000-0000-4000-8000-000000000001"),
        ("DELETE", "/api/scans"),
    ],
)
async def test_server_features_require_authentication(
    anonymous_client: AsyncClient,
    method: str,
    path: str,
) -> None:
    response = await anonymous_client.request(method, path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication is required."}


async def test_protected_401_never_deletes_a_same_name_browser_cookie(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.get(
        "/api/scans",
        headers={"Cookie": "visual_scan_session=revoked-session-token"},
    )

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_mutation_requires_csrf_and_exact_origin(client: AsyncClient) -> None:
    without_csrf = await client.post(
        "/api/scans",
        json={"filename": "note.txt", "text": "hello", "analysis": None, "ocr": None},
        headers={"X-CSRF-Token": ""},
    )
    wrong_origin = await client.post(
        "/api/scans",
        json={"filename": "note.txt", "text": "hello", "analysis": None, "ocr": None},
        headers={"Origin": "http://evil.test"},
    )

    assert without_csrf.status_code == 403
    assert wrong_origin.status_code == 403


async def test_validation_does_not_echo_password_or_username(
    anonymous_client: AsyncClient,
) -> None:
    password = "short-secret"
    username = "unsafe user"
    response = await anonymous_client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    text = response.text
    assert response.status_code == 422
    assert username not in text
    assert password not in text
    assert "input" not in text
