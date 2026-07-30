"""Public entry point for server-side OCR behavior."""

from __future__ import annotations

from app.features.ocr.errors import (
    EmptyImageError,
    ImageTooLargeError,
    InvalidOcrParametersError,
)
from app.features.ocr.pipeline import OcrPipeline
from app.features.ocr.schemas import OcrLanguage, OcrResponse, PreprocessingMode

DEFAULT_THRESHOLD = 160


def sanitize_filename(filename: str | None) -> str:
    """Return a basename that handles both POSIX and Windows separators."""
    candidate = (filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    return candidate or "upload"


def resolve_threshold(
    preprocessing: PreprocessingMode,
    threshold: int | None,
) -> int | None:
    """Validate the conditional threshold parameter and apply its default."""
    if preprocessing is PreprocessingMode.THRESHOLD:
        effective_threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        if not 0 <= effective_threshold <= 255:
            raise InvalidOcrParametersError("Threshold must be between 0 and 255.")
        return effective_threshold
    if threshold is not None:
        raise InvalidOcrParametersError(
            "Threshold is only valid when preprocessing is set to threshold."
        )
    return None


class OcrService:
    """Validate request-level invariants and expose the OCR pipeline."""

    def __init__(self, pipeline: OcrPipeline, max_image_bytes: int) -> None:
        self._pipeline = pipeline
        self._max_image_bytes = max_image_bytes

    def recognize(
        self,
        filename: str | None,
        data: bytes,
        content_type: str | None,
        language: OcrLanguage,
        preprocessing: PreprocessingMode,
        threshold: int | None,
    ) -> OcrResponse:
        """Recognize one uploaded image and return the public response."""
        if not data:
            raise EmptyImageError()
        if len(data) > self._max_image_bytes:
            raise ImageTooLargeError(
                f"The uploaded image exceeds the {self._max_image_bytes}-byte limit."
            )

        effective_threshold = resolve_threshold(preprocessing, threshold)
        result = self._pipeline.recognize(
            data=data,
            content_type=content_type,
            language=language,
            preprocessing=preprocessing,
            threshold=effective_threshold,
        )
        return OcrResponse(
            filename=sanitize_filename(filename),
            text=result.text,
            confidence=result.confidence,
            words=result.words,
            language=result.language,
            preprocessing=result.preprocessing,
            threshold=result.threshold,
            width=result.width,
            height=result.height,
            format=result.format,
        )
