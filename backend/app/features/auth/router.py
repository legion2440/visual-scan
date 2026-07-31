"""HTTP endpoints for registration, login, session restore, and logout."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.features.auth.dependencies import (
    apply_session_cookie,
    auth_http_exception,
    clear_session_cookie,
    get_auth_service,
    require_allowed_origin,
)
from app.features.auth.errors import AuthError, CsrfValidationError
from app.features.auth.schemas import CredentialsRequest, SessionResponse
from app.features.auth.service import AuthService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _remote_address(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _current_cookie(request: Request) -> str | None:
    settings = request.app.state.settings
    return request.cookies.get(settings.auth_cookie_name)


def _raise_auth_error(error: AuthError) -> None:
    raise auth_http_exception(error) from error


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    response: Response,
    payload: CredentialsRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    _origin: Annotated[None, Depends(require_allowed_origin)],
) -> SessionResponse:
    """Create one user and immediately establish a new opaque session."""
    try:
        outcome = await run_in_threadpool(
            service.register,
            payload,
            remote_address=_remote_address(request),
            current_session_token=_current_cookie(request),
        )
    except AuthError as error:
        _raise_auth_error(error)
    except Exception as error:
        logger.error("Unexpected registration failure (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail=AuthError.default_message) from error
    apply_session_cookie(response, request.app.state.settings, outcome.session_token)
    return outcome.to_response()


@router.post("/login", response_model=SessionResponse)
async def login(
    request: Request,
    response: Response,
    payload: CredentialsRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
    _origin: Annotated[None, Depends(require_allowed_origin)],
) -> SessionResponse:
    """Verify credentials and rotate the session presented by this client."""
    try:
        outcome = await run_in_threadpool(
            service.login,
            payload,
            remote_address=_remote_address(request),
            current_session_token=_current_cookie(request),
        )
    except AuthError as error:
        _raise_auth_error(error)
    except Exception as error:
        logger.error("Unexpected login failure (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail=AuthError.default_message) from error
    apply_session_cookie(response, request.app.state.settings, outcome.session_token)
    return outcome.to_response()


@router.get("/session", response_model=SessionResponse)
async def current_session(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> SessionResponse:
    """Restore auth state without mutating a possibly newer browser cookie."""
    try:
        resolution = await run_in_threadpool(service.resolve_session, _current_cookie(request))
    except AuthError as error:
        _raise_auth_error(error)
    except Exception as error:
        logger.error("Unexpected session lookup failure (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail=AuthError.default_message) from error
    if resolution.session is None:
        return SessionResponse.anonymous()
    return SessionResponse(
        authenticated=True,
        user=resolution.session.principal.to_user(),
        csrf_token=resolution.csrf_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
    _origin: Annotated[None, Depends(require_allowed_origin)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    """Revoke the current valid session; anonymous logout remains idempotent."""
    settings = request.app.state.settings
    raw_token = _current_cookie(request)
    try:
        resolution = await run_in_threadpool(service.resolve_session, raw_token)
        if resolution.session is not None:
            service.verify_csrf(resolution.session, csrf_token)
        await run_in_threadpool(service.logout, raw_token)
    except CsrfValidationError as error:
        # A bad CSRF token must not revoke or clear a valid session.
        raise auth_http_exception(error) from error
    except AuthError as error:
        _raise_auth_error(error)
    except Exception as error:
        logger.error("Unexpected logout failure (%s)", type(error).__name__)
        raise HTTPException(status_code=500, detail=AuthError.default_message) from error
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings)
    return response
