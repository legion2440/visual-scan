"""Public and provider contracts for document analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)


class AnalysisLanguage(StrEnum):
    """OCR language identifiers accepted by the analysis endpoint."""

    ENGLISH = "eng"
    RUSSIAN = "rus"
    ENGLISH_RUSSIAN = "eng+rus"
    GERMAN = "deu"
    FRENCH = "fra"
    SPANISH = "spa"


class DocumentClassification(StrEnum):
    """Stable taxonomy exposed to the frontend and stored results."""

    INVOICE = "invoice"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    LETTER = "letter"
    FORM = "form"
    REPORT = "report"
    STATEMENT = "statement"
    IDENTITY_DOCUMENT = "identity_document"
    CERTIFICATE = "certificate"
    BUSINESS_CARD = "business_card"
    NOTE = "note"
    OTHER = "other"


class AnalysisRequest(BaseModel):
    """JSON request accepted by POST /api/ai/analyze."""

    model_config = ConfigDict(extra="forbid")

    filename: StrictStr
    text: StrictStr
    language: AnalysisLanguage


class StructuredField(BaseModel):
    """One explicit label/value pair extracted from document text."""

    model_config = ConfigDict(extra="forbid")

    label: StrictStr
    value: StrictStr

    @field_validator("label", "value")
    @classmethod
    def normalize_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Structured field values must not be empty.")
        return normalized


Confidence = Annotated[
    float,
    Field(strict=True, ge=0, le=1, allow_inf_nan=False),
]


class ProviderAnalysisResult(BaseModel):
    """Strict JSON object expected inside provider message content."""

    model_config = ConfigDict(extra="forbid")

    classification: DocumentClassification
    confidence: Confidence
    summary: StrictStr
    tags: list[StrictStr]
    fields: list[StructuredField]

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Summary must not be empty.")
        if len(normalized) > 1_200:
            raise ValueError("Summary exceeds 1200 characters.")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        if len(values) > 10:
            raise ValueError("Analysis contains more than 10 tags.")

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = value.strip().lower()
            if not tag:
                raise ValueError("Tags must not be empty.")
            if tag not in seen:
                seen.add(tag)
                normalized.append(tag)
        return normalized

    @field_validator("fields")
    @classmethod
    def limit_fields(cls, values: list[StructuredField]) -> list[StructuredField]:
        if len(values) > 20:
            raise ValueError("Analysis contains more than 20 structured fields.")
        return values


class AnalysisResponse(ProviderAnalysisResult):
    """Validated analysis returned to the frontend."""

    filename: str
    provider: str
