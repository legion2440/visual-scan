"""Prompt and strict response coordination for document analysis."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from app.features.analysis.errors import ProviderResponseError
from app.features.analysis.prompts import ChatMessage, build_analysis_messages
from app.features.analysis.schemas import (
    AnalysisLanguage,
    ProviderAnalysisResult,
)


class AnalysisProvider(Protocol):
    """Boundary used by the pipeline without depending on one HTTP adapter."""

    async def analyze(self, messages: list[ChatMessage]) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class AnalysisPipeline:
    """Build one prompt, call one provider, and validate its result."""

    def __init__(self, provider: AnalysisProvider) -> None:
        self._provider = provider

    async def analyze(
        self,
        *,
        filename: str,
        text: str,
        language: AnalysisLanguage,
    ) -> ProviderAnalysisResult:
        messages = build_analysis_messages(
            filename=filename,
            language=language,
            text=text,
        )
        raw_result = await self._provider.analyze(messages)
        try:
            return ProviderAnalysisResult.model_validate(raw_result)
        except ValidationError as error:
            raise ProviderResponseError() from error

    async def close(self) -> None:
        """Release the provider-owned HTTP client."""
        await self._provider.close()
