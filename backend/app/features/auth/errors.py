"""Safe authentication feature errors."""


class AuthError(RuntimeError):
    status_code = 500
    default_message = "Authentication could not be completed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class AuthenticationRequiredError(AuthError):
    status_code = 401
    default_message = "Authentication is required."


class InvalidCredentialsError(AuthError):
    status_code = 401
    default_message = "Invalid username or password."


class InactiveUserError(AuthError):
    status_code = 403
    default_message = "This user account is inactive."


class CsrfValidationError(AuthError):
    status_code = 403
    default_message = "The request security token is invalid."


class OriginValidationError(AuthError):
    status_code = 403
    default_message = "The request origin is not allowed."


class UsernameAlreadyExistsError(AuthError):
    status_code = 409
    default_message = "This username is unavailable."


class AuthRateLimitError(AuthError):
    status_code = 429
    default_message = "Too many authentication attempts. Try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__()
        self.retry_after = max(1, retry_after)


class AuthStorageUnavailableError(AuthError):
    status_code = 503
    default_message = "Authentication storage is temporarily unavailable."
