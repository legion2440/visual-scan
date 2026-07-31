"""Public FastAPI authentication, principal, Origin, and CSRF dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.features.auth.errors import (
    AuthenticationRequiredError,
    AuthError,
    AuthRateLimitError,
    CsrfValidationError,
    InactiveUserError,
    OriginValidationError,
)
from app.features.auth.schemas import AuthenticatedPrincipal
from app.features.auth.service import AuthenticatedSession, AuthService


def get_auth_service(request: Request) -> AuthService:
    """Return the auth service initialized by the application lifespan."""
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise RuntimeError("Application lifespan has not initialized authentication.")
    return service


def session_cookie_header(
    settings: Settings,
    *,
    token: str | None = None,
    clear: bool = False,
) -> str:
    """Build one cookie header with identical set/delete attributes."""
    response = Response()
    if clear:
        response.delete_cookie(
            settings.auth_cookie_name,
            path=settings.api_prefix,
            secure=settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
    elif token is not None:
        response.set_cookie(
            settings.auth_cookie_name,
            token,
            max_age=settings.auth_absolute_lifetime_seconds,
            path=settings.api_prefix,
            secure=settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
    else:
        raise ValueError("A session cookie requires a token or clear=True.")
    return next(
        value.decode("latin-1") for name, value in response.raw_headers if name == b"set-cookie"
    )


def apply_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.headers.append("Set-Cookie", session_cookie_header(settings, token=token))


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.headers.append("Set-Cookie", session_cookie_header(settings, clear=True))


def auth_http_exception(
    error: AuthError,
) -> HTTPException:
    headers: dict[str, str] = {}
    if isinstance(error, AuthRateLimitError):
        headers["Retry-After"] = str(error.retry_after)
    return HTTPException(status_code=error.status_code, detail=str(error), headers=headers or None)


def require_allowed_origin(request: Request) -> None:
    """Require an exact configured Origin for every unsafe browser request."""
    origin = request.headers.get("origin")
    if origin is None or origin not in request.app.state.settings.cors_origins:
        raise auth_http_exception(OriginValidationError())


async def _require_authenticated_session(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthenticatedSession:
    """Resolve internal session material without exposing it to features."""
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    try:
        resolution = await run_in_threadpool(service.resolve_session, raw_token)
    except AuthError as error:
        raise auth_http_exception(error) from error
    if resolution.inactive:
        raise auth_http_exception(InactiveUserError())
    if resolution.session is None:
        # A generic protected response must not delete a newer same-name cookie
        # that may have been issued while this request was in flight.
        raise auth_http_exception(AuthenticationRequiredError())
    return resolution.session


async def require_authenticated_principal(
    session: Annotated[AuthenticatedSession, Depends(_require_authenticated_session)],
) -> AuthenticatedPrincipal:
    """Expose only the minimal public identity to unrelated features."""
    return session.principal


async def require_csrf_principal(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(_require_authenticated_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedPrincipal:
    """Require exact Origin and constant-time CSRF validation for a mutation."""
    require_allowed_origin(request)
    try:
        service.verify_csrf(session, csrf_token)
    except CsrfValidationError as error:
        raise auth_http_exception(error) from error
    return session.principal
