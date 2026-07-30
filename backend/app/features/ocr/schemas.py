"""HTTP contracts for server-side OCR."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class OcrLanguage(StrEnum):
    """Languages supported by the server-side Tesseract feature."""

    ENGLISH = "eng"
    RUSSIAN = "rus"
    ENGLISH_RUSSIAN = "eng+rus"
    GERMAN = "deu"
    FRENCH = "fra"
    SPANISH = "spa"


class PreprocessingMode(StrEnum):
    """Supported server-side image preprocessing modes."""

    NONE = "none"
    GRAYSCALE = "grayscale"
    THRESHOLD = "threshold"


class OcrResponse(BaseModel):
    """Result returned by POST /api/ocr/recognize."""

    filename: str
    text: str
    confidence: float | None
    words: int = Field(ge=0)
    language: OcrLanguage
    preprocessing: PreprocessingMode
    threshold: int | None = Field(default=None, ge=0, le=255)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format: Literal["JPEG", "PNG", "WEBP"]
    engine: Literal["tesseract"] = "tesseract"
