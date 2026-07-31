"""HTTP and persistence contracts for the server-side scan archive."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
)

from app.features.analysis.schemas import (
    AnalysisData,
    Confidence,
    DocumentClassification,
)
from app.features.ocr.schemas import OcrLanguage


class OcrSource(StrEnum):
    """Origin of the OCR result saved with a scan."""

    BROWSER = "browser"
    SERVER = "server"


class ScanSort(StrEnum):
    """Allowed deterministic list sort keys."""

    SCANNED_AT = "scanned_at"
    FILENAME = "filename"
    CLASSIFICATION = "classification"
    CONFIDENCE = "confidence"


class SortOrder(StrEnum):
    """Allowed list sort directions."""

    ASCENDING = "asc"
    DESCENDING = "desc"


ScanClassificationFilter = DocumentClassification | Literal["unclassified"]
OcrConfidence = Annotated[
    float,
    Field(strict=True, ge=0, le=100, allow_inf_nan=False),
]


class ScanAnalysisSnapshot(AnalysisData):
    """Complete immutable analysis metadata saved with a scan."""

    provider: StrictStr

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Analysis provider must not be empty.")
        if len(normalized) > 100:
            raise ValueError("Analysis provider exceeds 100 characters.")
        return normalized


class ScanListAnalysisSnapshot(BaseModel):
    """Analysis metadata returned in lists without structured fields."""

    model_config = ConfigDict(extra="forbid")

    classification: DocumentClassification
    confidence: Confidence
    summary: str
    tags: list[str]
    provider: str


class ScanOcrSnapshot(BaseModel):
    """OCR metadata saved without display-only frontend labels."""

    model_config = ConfigDict(extra="forbid")

    source: OcrSource
    engine: StrictStr
    language: OcrLanguage
    profile: StrictStr | None = None
    confidence: OcrConfidence | None = None
    words: StrictInt | None = Field(default=None, ge=0)

    @field_validator("engine")
    @classmethod
    def normalize_engine(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("OCR engine must not be empty.")
        if len(normalized) > 100:
            raise ValueError("OCR engine exceeds 100 characters.")
        return normalized

    @field_validator("profile")
    @classmethod
    def normalize_profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("OCR profile must not be empty.")
        if len(normalized) > 50:
            raise ValueError("OCR profile exceeds 50 characters.")
        return normalized


class ScanCreateRequest(BaseModel):
    """Client-supplied data accepted by POST /api/scans."""

    model_config = ConfigDict(extra="forbid")

    filename: StrictStr = Field(max_length=255)
    text: StrictStr
    analysis: ScanAnalysisSnapshot | None = None
    ocr: ScanOcrSnapshot | None = None


class ScanDetail(BaseModel):
    """Complete scan record returned after create or by identifier."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    filename: str
    scanned_at: datetime
    text: str
    analysis: ScanAnalysisSnapshot | None
    ocr: ScanOcrSnapshot | None


class ScanListItem(BaseModel):
    """Compact scan record returned by the archive list."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    filename: str
    scanned_at: datetime
    snippet: str
    analysis: ScanListAnalysisSnapshot | None
    ocr: ScanOcrSnapshot | None


class ScanListResponse(BaseModel):
    """Paginated archive response."""

    model_config = ConfigDict(extra="forbid")

    items: list[ScanListItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ScanClearResponse(BaseModel):
    """Number of records deleted by archive cleanup."""

    deleted: int = Field(ge=0)
