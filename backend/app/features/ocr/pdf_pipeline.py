"""Coordinate PDF preflight, rendering, preprocessing, and OCR."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.features.ocr.errors import OcrTimeoutError
from app.features.ocr.pdf_renderer import PdfiumRenderer
from app.features.ocr.pipeline import OcrProvider
from app.features.ocr.preprocessing import transform_image
from app.features.ocr.schemas import OcrLanguage, PreprocessingMode


@dataclass(frozen=True, slots=True)
class PdfPagePipelineResult:
    """OCR output and render metadata for one PDF page."""

    page: int
    text: str
    confidence: float | None
    words: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PdfPipelineResult:
    """Complete sequential OCR output for one PDF document."""

    text: str
    page_count: int
    language: OcrLanguage
    preprocessing: PreprocessingMode
    threshold: int | None
    render_dpi: int
    pages: tuple[PdfPagePipelineResult, ...]


class PdfOcrPipeline:
    """Render and recognize every PDF page in deterministic order."""

    def __init__(
        self,
        *,
        renderer: PdfiumRenderer,
        provider: OcrProvider,
        max_pages: int,
        max_page_pixels: int,
        max_total_pixels: int,
        ocr_timeout_seconds: int,
        pdf_timeout_seconds: int,
        clock: Any = monotonic,
    ) -> None:
        self._renderer = renderer
        self._provider = provider
        self._max_pages = max_pages
        self._max_page_pixels = max_page_pixels
        self._max_total_pixels = max_total_pixels
        self._ocr_timeout_seconds = ocr_timeout_seconds
        self._pdf_timeout_seconds = pdf_timeout_seconds
        self._clock = clock

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise OcrTimeoutError()
        return remaining

    def recognize(
        self,
        *,
        data: bytes,
        password: str | None,
        language: OcrLanguage,
        preprocessing: PreprocessingMode,
        threshold: int | None,
    ) -> PdfPipelineResult:
        """Run preflight before making one Tesseract call per page."""
        deadline = self._clock() + self._pdf_timeout_seconds
        pages: list[PdfPagePipelineResult] = []

        specs = self._renderer.preflight(
            data,
            password,
            deadline=deadline,
            max_pages=self._max_pages,
            max_page_pixels=self._max_page_pixels,
            max_total_pixels=self._max_total_pixels,
        )
        self._remaining(deadline)

        for spec in specs:
            rendered = self._renderer.render_page(
                data,
                password,
                spec,
                deadline=deadline,
            )
            prepared = None
            try:
                self._remaining(deadline)
                prepared = transform_image(rendered, preprocessing, threshold)
                prepared.load()
                self._remaining(deadline)

                timeout = min(
                    float(self._ocr_timeout_seconds),
                    self._remaining(deadline),
                )
                result = self._provider.recognize(
                    prepared,
                    language.value,
                    timeout_seconds=timeout,
                )
                self._remaining(deadline)
            finally:
                if prepared is not None:
                    prepared.close()
                rendered.close()

            pages.append(
                PdfPagePipelineResult(
                    page=spec.index + 1,
                    text=result.text,
                    confidence=result.confidence,
                    words=result.words,
                    width=spec.width,
                    height=spec.height,
                )
            )

        return PdfPipelineResult(
            text="\n\n".join(page.text for page in pages),
            page_count=len(pages),
            language=language,
            preprocessing=preprocessing,
            threshold=threshold,
            render_dpi=self._renderer.render_dpi,
            pages=tuple(pages),
        )
