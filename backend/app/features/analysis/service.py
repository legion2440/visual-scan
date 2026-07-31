"""Public entry point for AI document analysis."""

from __future__ import annotations

from app.features.analysis.errors import (
    AnalysisInputTooLargeError,
    EmptyAnalysisTextError,
)
from app.features.analysis.pipeline import AnalysisPipeline
from app.features.analysis.schemas import (
    AnalysisLanguage,
    AnalysisResponse,
)


def sanitize_filename(filename: str) -> str:
    """Return a safe basename without separators or control characters."""
    candidate = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    return candidate or "untitled"


class AnalysisService:
    """Enforce request limits and expose the async analysis pipeline."""

    def __init__(
        self,
        pipeline: AnalysisPipeline,
        *,
        max_input_chars: int,
        provider_name: str,
    ) -> None:
        self._pipeline = pipeline
        self._max_input_chars = max_input_chars
        self._provider_name = provider_name

    async def analyze(
        self,
        *,
        filename: str,
        text: str,
        language: AnalysisLanguage,
    ) -> AnalysisResponse:
        if len(text) > self._max_input_chars:
            raise AnalysisInputTooLargeError(
                f"The OCR text exceeds the {self._max_input_chars}-character analysis limit."
            )

        normalized_text = text.strip()
        if not normalized_text:
            raise EmptyAnalysisTextError()
        normalized_filename = sanitize_filename(filename)
        result = await self._pipeline.analyze(
            filename=normalized_filename,
            text=normalized_text,
            language=language,
        )
        return AnalysisResponse(
            filename=normalized_filename,
            classification=result.classification,
            confidence=result.confidence,
            summary=result.summary,
            tags=result.tags,
            fields=result.fields,
            provider=self._provider_name,
        )

    async def close(self) -> None:
        """Release lazy provider resources."""
        await self._pipeline.close()
