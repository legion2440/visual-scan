"""Safe errors exposed by the document analysis feature."""


class AnalysisError(Exception):
    """Base class for safe, client-facing analysis failures."""

    status_code = 500
    default_message = "AI analysis failed unexpectedly."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class AnalysisDisabledError(AnalysisError):
    """Raised when analysis is requested while AI support is disabled."""

    status_code = 503
    default_message = "AI analysis is not enabled."


class AnalysisInputTooLargeError(AnalysisError):
    """Raised when OCR text exceeds the configured character limit."""

    status_code = 413
    default_message = "The OCR text exceeds the configured analysis limit."


class EmptyAnalysisTextError(AnalysisError):
    """Raised when OCR text contains no non-whitespace characters."""

    status_code = 422
    default_message = "OCR text must not be empty."


class ProviderRateLimitError(AnalysisError):
    """Raised when the configured provider rejects the request rate."""

    status_code = 429
    default_message = "The AI provider rate limit was reached."


class ProviderResponseError(AnalysisError):
    """Raised when a successful provider response violates its contract."""

    status_code = 502
    default_message = "The AI provider returned an invalid response."


class ProviderUnavailableError(AnalysisError):
    """Raised when the configured provider cannot serve the request."""

    status_code = 503
    default_message = "The AI provider is unavailable or rejected its configuration."


class ProviderTimeoutError(AnalysisError):
    """Raised when the provider call exceeds its whole-request deadline."""

    status_code = 504
    default_message = "The AI provider request timed out."
