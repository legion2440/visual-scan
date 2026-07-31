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
    """Raised when OCR exceeds a configured call or document deadline."""

    status_code = 504
    default_message = "OCR processing timed out."


class OcrProcessingError(OcrError):
    """Raised for an unexpected provider failure with a safe response."""


class EmptyPdfError(OcrError):
    """Raised when the uploaded PDF contains no bytes."""

    status_code = 400
    default_message = "The uploaded PDF is empty."


class InvalidPdfError(OcrError):
    """Raised when PDFium cannot decode a usable PDF document."""

    status_code = 400
    default_message = "The uploaded file is not a valid PDF."


class PdfTooLargeError(OcrError):
    """Raised when a PDF exceeds a configured byte, page, or pixel limit."""

    status_code = 413
    default_message = "The uploaded PDF exceeds the configured size limit."


class UnsupportedPdfFormatError(OcrError):
    """Raised when the upload does not declare the PDF media type."""

    status_code = 415
    default_message = "Only application/pdf uploads are supported."


class InvalidPdfPasswordError(OcrError):
    """Raised when an encrypted PDF needs another password."""

    status_code = 422
    default_message = "The PDF password is required or incorrect."


class UnsupportedPdfSecurityError(OcrError):
    """Raised when PDFium cannot open the document's security scheme."""

    status_code = 422
    default_message = "The PDF security scheme is not supported."


class PdfRenderError(OcrError):
    """Raised when a PDF page cannot be rendered safely."""

    default_message = "PDF rendering failed unexpectedly."
