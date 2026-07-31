"""Public entry point for registration, login, and session lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.config import Settings
from app.features.auth.errors import (
    AuthRateLimitError,
    CsrfValidationError,
    InactiveUserError,
    InvalidCredentialsError,
)
from app.features.auth.repository import SQLiteAuthRepository, StoredUser
from app.features.auth.schemas import (
    AuthenticatedPrincipal,
    CredentialsRequest,
    SessionResponse,
)
from app.features.auth.security import AuthSecurity
from app.storage.database import SQLiteDatabase

LOGIN_ACCOUNT_SCOPE = "login_account"
LOGIN_IP_SCOPE = "login_ip"
LOGIN_ACCOUNT_LIMIT = 5
LOGIN_IP_LIMIT = 20
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BLOCK_SECONDS = 15 * 60
REGISTER_LIMIT = 5
REGISTER_WINDOW_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Auth-internal session material paired with the public identity."""

    principal: AuthenticatedPrincipal
    token_hash: bytes
    csrf_hash: bytes


@dataclass(frozen=True, slots=True)
class AuthOutcome:
    """Successful auth result including raw browser-only response tokens."""

    principal: AuthenticatedPrincipal
    session_token: str
    csrf_token: str

    def to_response(self) -> SessionResponse:
        return SessionResponse(
            authenticated=True,
            user=self.principal.to_user(),
            csrf_token=self.csrf_token,
        )


@dataclass(frozen=True, slots=True)
class SessionResolution:
    """Internal resolution state never imported by unrelated features."""

    session: AuthenticatedSession | None
    csrf_token: str | None = None
    inactive: bool = False


class AuthService:
    """Enforce authentication invariants over repository and crypto adapters."""

    def __init__(
        self,
        repository: SQLiteAuthRepository,
        security: AuthSecurity,
        *,
        absolute_lifetime_seconds: int,
        idle_lifetime_seconds: int,
        touch_interval_seconds: int,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._security = security
        self._absolute_lifetime = timedelta(seconds=absolute_lifetime_seconds)
        self._idle_lifetime = timedelta(seconds=idle_lifetime_seconds)
        self._touch_interval = timedelta(seconds=touch_interval_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    def register(
        self,
        credentials: CredentialsRequest,
        *,
        remote_address: str,
        current_session_token: str | None,
    ) -> AuthOutcome:
        now = self._now()
        rate_key = self._security.rate_limit_key("register_ip", remote_address)
        retry_after = self._repository.consume_registration_attempt(
            key_hash=rate_key,
            now=now,
            limit=REGISTER_LIMIT,
            window_seconds=REGISTER_WINDOW_SECONDS,
        )
        if retry_after:
            raise AuthRateLimitError(retry_after)

        password_hash = self._security.hash_password(credentials.password)
        session_token, csrf_token, token_hash, csrf_hash = self._new_session_credentials()
        user_id = self._uuid_factory()
        user, _ = self._repository.create_user_with_session(
            user_id=user_id,
            username=credentials.username,
            password_hash=password_hash,
            created_at=now,
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            expires_at=now + self._absolute_lifetime,
            replaced_token_hash=self._optional_token_hash(current_session_token),
        )
        principal = self._principal(user)
        return AuthOutcome(principal, session_token, csrf_token)

    def login(
        self,
        credentials: CredentialsRequest,
        *,
        remote_address: str,
        current_session_token: str | None,
    ) -> AuthOutcome:
        now = self._now()
        account_key = self._security.rate_limit_key(LOGIN_ACCOUNT_SCOPE, credentials.username)
        ip_key = self._security.rate_limit_key(LOGIN_IP_SCOPE, remote_address)
        retry_after = max(
            self._repository.rate_limit_remaining(
                scope=LOGIN_ACCOUNT_SCOPE,
                key_hash=account_key,
                now=now,
                window_seconds=LOGIN_WINDOW_SECONDS,
            ),
            self._repository.rate_limit_remaining(
                scope=LOGIN_IP_SCOPE,
                key_hash=ip_key,
                now=now,
                window_seconds=LOGIN_WINDOW_SECONDS,
            ),
        )
        if retry_after:
            raise AuthRateLimitError(retry_after)

        user = self._repository.get_user_by_username(credentials.username)
        updated_hash: str | None = None
        if user is None:
            self._security.verify_dummy_password(credentials.password)
            valid = False
        else:
            valid, updated_hash = self._security.verify_password(
                credentials.password,
                user.password_hash,
            )
        if not valid or user is None:
            self._record_login_failure(account_key=account_key, ip_key=ip_key, now=now)
            raise InvalidCredentialsError()
        if not user.is_active:
            raise InactiveUserError()
        if updated_hash is not None:
            self._repository.update_password_hash(user.id, updated_hash)

        session_token, csrf_token, token_hash, csrf_hash = self._new_session_credentials()
        self._repository.rotate_login_session(
            user_id=user.id,
            token_hash=token_hash,
            csrf_hash=csrf_hash,
            created_at=now,
            expires_at=now + self._absolute_lifetime,
            replaced_token_hash=self._optional_token_hash(current_session_token),
            account_rate_limit_scope=LOGIN_ACCOUNT_SCOPE,
            account_rate_limit_key=account_key,
        )
        principal = self._principal(user)
        return AuthOutcome(principal, session_token, csrf_token)

    def resolve_session(self, session_token: str | None) -> SessionResolution:
        if not session_token:
            return SessionResolution(session=None)
        token_hash = self._security.token_digest(session_token)
        session = self._repository.get_session(token_hash)
        if session is None:
            return SessionResolution(session=None)

        now = self._now()
        if session.expires_at <= now or session.last_seen_at + self._idle_lifetime <= now:
            self._repository.delete_session(token_hash)
            return SessionResolution(session=None)
        if not session.user.is_active:
            return SessionResolution(session=None, inactive=True)
        if session.last_seen_at + self._touch_interval <= now:
            self._repository.touch_session(token_hash, now)

        csrf_token = self._security.csrf_token(session_token)
        if not self._security.verify_digest(csrf_token, session.csrf_hash):
            self._repository.delete_session(token_hash)
            return SessionResolution(session=None)
        return SessionResolution(
            session=AuthenticatedSession(
                principal=self._principal(session.user),
                token_hash=token_hash,
                csrf_hash=session.csrf_hash,
            ),
            csrf_token=csrf_token,
        )

    def verify_csrf(self, session: AuthenticatedSession, csrf_token: str | None) -> None:
        if not csrf_token or not self._security.verify_digest(csrf_token, session.csrf_hash):
            raise CsrfValidationError()

    def logout(self, session_token: str | None) -> None:
        if session_token:
            self._repository.delete_session(self._security.token_digest(session_token))

    def _record_login_failure(self, *, account_key: bytes, ip_key: bytes, now: datetime) -> None:
        for scope, key, limit in (
            (LOGIN_ACCOUNT_SCOPE, account_key, LOGIN_ACCOUNT_LIMIT),
            (LOGIN_IP_SCOPE, ip_key, LOGIN_IP_LIMIT),
        ):
            self._repository.record_failure(
                scope=scope,
                key_hash=key,
                now=now,
                window_seconds=LOGIN_WINDOW_SECONDS,
                limit=limit,
                block_seconds=LOGIN_BLOCK_SECONDS,
            )

    def _new_session_credentials(self) -> tuple[str, str, bytes, bytes]:
        session_token = self._security.new_session_token()
        csrf_token = self._security.csrf_token(session_token)
        return (
            session_token,
            csrf_token,
            self._security.token_digest(session_token),
            self._security.token_digest(csrf_token),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("The auth clock must return a timezone-aware datetime.")
        return value.astimezone(UTC)

    def _optional_token_hash(self, token: str | None) -> bytes | None:
        return self._security.token_digest(token) if token else None

    @staticmethod
    def _principal(user: StoredUser) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=user.id,
            username=user.username,
            created_at=user.created_at,
            is_initial_user=user.is_initial_user,
        )


def create_auth_service(database: SQLiteDatabase, settings: Settings) -> AuthService:
    """Build one resource-free app-local authentication service."""
    return AuthService(
        SQLiteAuthRepository(database),
        AuthSecurity(settings.auth_hmac_secret.get_secret_value()),
        absolute_lifetime_seconds=settings.auth_absolute_lifetime_seconds,
        idle_lifetime_seconds=settings.auth_idle_lifetime_seconds,
        touch_interval_seconds=settings.auth_touch_interval_seconds,
    )
