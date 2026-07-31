"""Safe errors exposed by the scan archive feature."""


class ScanError(Exception):
    """Base class for safe, client-facing scan archive failures."""

    status_code = 500
    default_message = "The scan archive request failed unexpectedly."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class ScanNotFoundError(ScanError):
    """Raised when a requested scan does not exist."""

    status_code = 404
    default_message = "The requested scan was not found."


class EmptyScanTextError(ScanError):
    """Raised when a scan contains only whitespace."""

    status_code = 422
    default_message = "Scan text must not be empty."


class ScanTextTooLargeError(ScanError):
    """Raised when scan text exceeds the configured storage limit."""

    status_code = 413
    default_message = "Scan text exceeds the configured storage limit."


class ScanStorageUnavailableError(ScanError):
    """Raised when SQLite cannot safely serve the archive operation."""

    status_code = 503
    default_message = "The scan archive is temporarily unavailable."


class LegacyClaimForbiddenError(ScanError):
    """Raised when a non-initial user accesses the pre-auth archive."""

    status_code = 403
    default_message = "The legacy scan archive is not available to this user."
