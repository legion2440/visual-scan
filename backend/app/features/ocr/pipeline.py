"""Coordinate image preprocessing and the external OCR provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from app.features.ocr.preprocessing import preprocess_image
from app.features.ocr.provider import ProviderResult
from app.features.ocr.schemas import OcrLanguage, PreprocessingMode


class OcrProvider(Protocol):
    """Provider behavior required by the OCR pipeline."""

    def recognize(self, image: Image.Image, language: str) -> ProviderResult:
        """Recognize text in one prepared image."""


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Provider output combined with normalized image metadata."""

    text: str
    confidence: float | None
    words: int
    language: OcrLanguage
    preprocessing: PreprocessingMode
    threshold: int | None
    width: int
    height: int
    format: str


class OcrPipeline:
    """Prepare one image and send it to one OCR provider call."""

    def __init__(self, provider: OcrProvider, max_image_pixels: int) -> None:
        self._provider = provider
        self._max_image_pixels = max_image_pixels

    def recognize(
        self,
        data: bytes,
        content_type: str | None,
        language: OcrLanguage,
        preprocessing: PreprocessingMode,
        threshold: int | None,
    ) -> PipelineResult:
        """Run the in-memory preprocessing and recognition pipeline."""
        prepared = preprocess_image(
            data=data,
            content_type=content_type,
            mode=preprocessing,
            threshold=threshold,
            max_pixels=self._max_image_pixels,
        )
        try:
            result = self._provider.recognize(prepared.image, language.value)
        finally:
            prepared.image.close()

        return PipelineResult(
            text=result.text,
            confidence=result.confidence,
            words=result.words,
            language=language,
            preprocessing=prepared.preprocessing,
            threshold=prepared.threshold,
            width=prepared.width,
            height=prepared.height,
            format=prepared.format,
        )
