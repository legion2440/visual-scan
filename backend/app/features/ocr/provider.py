"""Tesseract boundary for the OCR feature."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from typing import Any

import pytesseract
from PIL import Image
from pytesseract import Output, TesseractError, TesseractNotFoundError

from app.features.ocr.errors import (
    OcrEngineUnavailableError,
    OcrProcessingError,
    OcrTimeoutError,
)

_COMMAND_LOCK = Lock()
_configured_tesseract_command: str | None = None
_MISSING_LANGUAGE_MARKERS = (
    "error opening data file",
    "failed loading language",
    "could not initialize tesseract",
)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Text and aggregate word statistics returned by Tesseract."""

    text: str
    confidence: float | None
    words: int


def configure_tesseract_command(command: str) -> None:
    """Set pytesseract's process-global command at most once."""
    normalized = command.strip()
    if not normalized:
        return

    global _configured_tesseract_command
    with _COMMAND_LOCK:
        if _configured_tesseract_command is None:
            pytesseract.pytesseract.tesseract_cmd = normalized
            _configured_tesseract_command = normalized
            return
        if _configured_tesseract_command != normalized:
            raise RuntimeError("Tesseract command is already configured.")


def _value_at(data: dict[str, list[Any]], field: str, index: int) -> Any:
    values = data.get(field, [])
    return values[index] if index < len(values) else ""


def _normalize_token(value: Any) -> str:
    return " ".join(str(value or "").split())


def _line_key(data: dict[str, list[Any]], index: int) -> tuple[str, ...]:
    fields = ("page_num", "block_num", "par_num", "line_num")
    key = tuple(str(_value_at(data, field, index)) for field in fields)
    return key if any(key) else ("", "", "", str(index))


def _parse_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return confidence if isfinite(confidence) and confidence >= 0 else None


def _build_result(data: dict[str, list[Any]]) -> ProviderResult:
    lines: OrderedDict[tuple[str, ...], list[str]] = OrderedDict()
    confidences: list[float] = []
    word_count = 0

    for index, raw_text in enumerate(data.get("text", [])):
        token = _normalize_token(raw_text)
        if not token:
            continue

        lines.setdefault(_line_key(data, index), []).append(token)
        word_count += 1
        confidence = _parse_confidence(_value_at(data, "conf", index))
        if confidence is not None:
            confidences.append(confidence)

    text = "\n".join(" ".join(tokens) for tokens in lines.values())
    average = round(sum(confidences) / len(confidences), 2) if confidences else None
    return ProviderResult(text=text, confidence=average, words=word_count)


class TesseractProvider:
    """Run exactly one pytesseract recognition operation per image."""

    def __init__(self, timeout_seconds: int, tesseract_command: str = "") -> None:
        configure_tesseract_command(tesseract_command)
        self._timeout_seconds = timeout_seconds

    def recognize(
        self,
        image: Image.Image,
        language: str,
        timeout_seconds: float | None = None,
    ) -> ProviderResult:
        """Recognize an image with LSTM/OEM 1 and normalized failures."""
        effective_timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds
        if effective_timeout <= 0:
            raise OcrTimeoutError()

        try:
            data = pytesseract.image_to_data(
                image,
                lang=language,
                config="--oem 1",
                output_type=Output.DICT,
                timeout=effective_timeout,
            )
        except TesseractNotFoundError as error:
            raise OcrEngineUnavailableError() from error
        except SystemExit as error:
            raise OcrEngineUnavailableError() from error
        except TesseractError as error:
            message = " ".join(str(part) for part in error.args).lower()
            if any(marker in message for marker in _MISSING_LANGUAGE_MARKERS):
                raise OcrEngineUnavailableError() from error
            raise OcrProcessingError() from error
        except RuntimeError as error:
            if "timeout" in str(error).lower():
                raise OcrTimeoutError() from error
            raise OcrProcessingError() from error

        return _build_result(data)
