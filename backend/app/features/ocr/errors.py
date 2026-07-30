"""Normalized errors exposed by the OCR feature."""


class OcrError(Exception):
    """Base class for safe, client-facing OCR failures."""

    status_code = 500
    default_message = "OCR processing failed unexpectedly."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class EmptyImageError(OcrError):
    """Raised when the uploaded file contains no bytes."""

    status_code = 400
    default_message = "The uploaded image is empty."


class InvalidImageError(OcrError):
    """Raised when uploaded bytes cannot be decoded as a complete image."""

    status_code = 400
    default_message = "The uploaded file is not a valid image."


class ImageTooLargeError(OcrError):
    """Raised when an upload exceeds a configured byte or pixel limit."""

    status_code = 413
    default_message = "The uploaded image exceeds the configured size limit."


class UnsupportedImageFormatError(OcrError):
    """Raised for unsupported or mismatched MIME and image formats."""

    status_code = 415
    default_message = "Only matching JPEG, PNG, and WebP images are supported."


class InvalidOcrParametersError(OcrError):
    """Raised when otherwise valid parameters form an invalid combination."""

    status_code = 422
    default_message = "The OCR parameters are invalid."


class OcrEngineUnavailableError(OcrError):
    """Raised when Tesseract or the selected language data is unavailable."""

    status_code = 503
    default_message = "Tesseract or the selected language data is not available."


class OcrTimeoutError(OcrError):
    """Raised when Tesseract exceeds its configured execution timeout."""

    status_code = 504
    default_message = "OCR processing timed out."


class OcrProcessingError(OcrError):
    """Raised for an unexpected provider failure with a safe response."""
