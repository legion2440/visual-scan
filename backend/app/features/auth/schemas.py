"""Public authentication, session, user, and principal contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator


def _validate_password(value: str) -> str:
    if not 12 <= len(value) <= 256:
        raise ValueError("Password must contain between 12 and 256 characters.")
    if "\x00" in value:
        raise ValueError("Password must not contain null characters.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("Password must contain valid Unicode characters.") from error
    return value


class CredentialsRequest(BaseModel):
    """Registration and login credentials accepted only in request bodies."""

    model_config = ConfigDict(extra="forbid")

    username: StrictStr
    password: StrictStr

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        try:
            value.encode("ascii", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("Username must contain only allowed ASCII characters.") from error
        normalized = value.lower()
        if not 3 <= len(normalized) <= 32:
            raise ValueError("Username must contain between 3 and 32 characters.")
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
        if any(character not in allowed for character in normalized):
            raise ValueError("Username contains unsupported characters.")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)


class AuthUser(BaseModel):
    """Safe user identity returned to the browser."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    username: str
    created_at: datetime
    is_initial_user: bool


class SessionResponse(BaseModel):
    """Authenticated or anonymous session state."""

    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    user: AuthUser | None
    csrf_token: str | None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.authenticated != (self.user is not None and self.csrf_token is not None):
            raise ValueError("Session response fields are inconsistent.")
        if not self.authenticated and (self.user is not None or self.csrf_token is not None):
            raise ValueError("Anonymous session responses must not contain credentials.")
        return self

    @classmethod
    def anonymous(cls) -> Self:
        return cls(authenticated=False, user=None, csrf_token=None)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Public server-side identity passed from auth dependencies to features."""

    user_id: UUID
    username: str
    created_at: datetime
    is_initial_user: bool

    def to_user(self) -> AuthUser:
        return AuthUser(
            id=self.user_id,
            username=self.username,
            created_at=self.created_at,
            is_initial_user=self.is_initial_user,
        )
