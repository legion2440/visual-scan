"""Unit tests for the pytesseract provider boundary."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from packaging.version import Version
from PIL import Image
from pytesseract import TesseractError, TesseractNotFoundError

from app.features.ocr import provider as provider_module
from app.features.ocr.errors import (
    OcrEngineUnavailableError,
    OcrProcessingError,
    OcrTimeoutError,
)
from app.features.ocr.provider import TesseractProvider


@pytest.fixture(autouse=True)
def cached_tesseract_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ordinary provider tests independent of the system binary."""
    command = provider_module.pytesseract.pytesseract.tesseract_cmd
    monkeypatch.setattr(provider_module, "_version_command", command)
    monkeypatch.setattr(
        provider_module.pytesseract.pytesseract.get_tesseract_version,
        "_result",
        Version("5.0.0"),
    )


def sample_tesseract_data() -> dict[str, list[Any]]:
    return {
        "text": ["Hello", "world", "", "Again"],
        "conf": ["90.0", "80.0", "99.0", "70.0"],
        "page_num": [1, 1, 1, 1],
        "block_num": [1, 1, 1, 1],
        "par_num": [1, 1, 1, 1],
        "line_num": [1, 1, 1, 2],
    }


def test_provider_calls_image_to_data_once_and_aggregates_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def image_to_data(image: Image.Image, **kwargs: Any) -> dict[str, list[Any]]:
        calls.append({"image": image, **kwargs})
        return sample_tesseract_data()

    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    provider = TesseractProvider(timeout_seconds=45)

    with Image.new("RGB", (3, 2), "white") as image:
        result = provider.recognize(image, "eng+rus")

    assert len(calls) == 1
    assert calls[0]["lang"] == "eng+rus"
    assert calls[0]["config"] == "--oem 1"
    assert calls[0]["output_type"] == provider_module.Output.DICT
    assert calls[0]["timeout"] == pytest.approx(45, abs=0.01)
    assert result.text == "Hello world\nAgain"
    assert result.words == 3
    assert result.confidence == 80.0


def test_provider_uses_per_call_timeout_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def image_to_data(*args: Any, **kwargs: Any) -> dict[str, list[Any]]:
        calls.append(kwargs)
        return sample_tesseract_data()

    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    provider = TesseractProvider(timeout_seconds=45)

    with Image.new("RGB", (1, 1)) as image:
        provider.recognize(image, "eng", timeout_seconds=2.5)

    assert calls[0]["timeout"] == pytest.approx(2.5, abs=0.01)


def test_provider_rejects_exhausted_timeout_before_tesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_call(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", unexpected_call)
    provider = TesseractProvider(timeout_seconds=45)

    with Image.new("RGB", (1, 1)) as image, pytest.raises(OcrTimeoutError):
        provider.recognize(image, "eng", timeout_seconds=0)

    assert calls == 0


def test_first_version_probe_and_ocr_share_one_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StepClock:
        values = iter([0.0, 0.25, 0.5, 0.75])

        def __call__(self) -> float:
            return next(self.values)

    probe_calls: list[dict[str, Any]] = []
    ocr_calls: list[dict[str, Any]] = []

    def check_output(*args: Any, **kwargs: Any) -> bytes:
        probe_calls.append({"args": args, **kwargs})
        return b"tesseract 5.5.0\n"

    def image_to_data(*args: Any, **kwargs: Any) -> dict[str, list[Any]]:
        ocr_calls.append(kwargs)
        return sample_tesseract_data()

    monkeypatch.setattr(provider_module, "_version_command", None)
    monkeypatch.setattr(provider_module.subprocess, "check_output", check_output)
    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    provider = TesseractProvider(timeout_seconds=45, clock=StepClock())

    with Image.new("RGB", (1, 1)) as image:
        provider.recognize(image, "eng", timeout_seconds=2.0)

    assert len(probe_calls) == 1
    assert probe_calls[0]["args"][0] == ["tesseract", "--version"]
    assert probe_calls[0]["timeout"] == 1.5
    assert ocr_calls[0]["timeout"] == 1.25
    assert provider_module._version_command == "tesseract"
    assert provider_module.pytesseract.pytesseract.get_tesseract_version._result == Version("5.5.0")


def test_first_version_probe_timeout_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_calls = 0

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="tesseract --version", timeout=1)

    def image_to_data(*args: Any, **kwargs: Any) -> None:
        nonlocal ocr_calls
        ocr_calls += 1

    monkeypatch.setattr(provider_module, "_version_command", None)
    monkeypatch.setattr(provider_module.subprocess, "check_output", timeout)
    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    provider = TesseractProvider(timeout_seconds=1, clock=lambda: 0.0)

    with Image.new("RGB", (1, 1)) as image, pytest.raises(OcrTimeoutError):
        provider.recognize(image, "eng")

    assert ocr_calls == 0


def test_invalid_bounded_version_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_calls = 0

    def image_to_data(*args: Any, **kwargs: Any) -> None:
        nonlocal ocr_calls
        ocr_calls += 1

    monkeypatch.setattr(provider_module, "_version_command", None)
    monkeypatch.setattr(
        provider_module.subprocess,
        "check_output",
        lambda *args, **kwargs: b"private invalid version output",
    )
    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    provider = TesseractProvider(timeout_seconds=1, clock=lambda: 0.0)

    with (
        Image.new("RGB", (1, 1)) as image,
        pytest.raises(OcrEngineUnavailableError) as captured,
    ):
        provider.recognize(image, "eng")

    assert str(captured.value) == ("Tesseract or the selected language data is not available.")
    assert "private invalid version output" not in str(captured.value)
    assert ocr_calls == 0


def test_version_cache_lock_wait_respects_call_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BusyLock:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def acquire(self, timeout: float) -> bool:
            self.timeouts.append(timeout)
            return False

        def release(self) -> None:
            raise AssertionError("An unacquired version lock must not be released.")

    lock = BusyLock()
    monkeypatch.setattr(provider_module, "_version_command", None)
    monkeypatch.setattr(provider_module, "_VERSION_LOCK", lock)
    provider = TesseractProvider(timeout_seconds=1, clock=lambda: 0.0)

    with Image.new("RGB", (1, 1)) as image, pytest.raises(OcrTimeoutError):
        provider.recognize(image, "eng")

    assert lock.timeouts == [1.0]


def test_confidence_is_none_when_no_nonnegative_word_confidence_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = sample_tesseract_data()
    data["conf"] = ["-1", "invalid", "98", "inf"]
    monkeypatch.setattr(
        provider_module.pytesseract,
        "image_to_data",
        lambda *args, **kwargs: data,
    )
    provider = TesseractProvider(timeout_seconds=1)

    with Image.new("L", (1, 1)) as image:
        result = provider.recognize(image, "eng")

    assert result.words == 3
    assert result.confidence is None


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (TesseractNotFoundError(), OcrEngineUnavailableError),
        (
            TesseractError(
                1,
                r"Error opening data file C:\private\tessdata\rus.traineddata",
            ),
            OcrEngineUnavailableError,
        ),
        (RuntimeError("Tesseract process timeout"), OcrTimeoutError),
        (TesseractError(1, "unexpected native failure"), OcrProcessingError),
        (RuntimeError("unexpected wrapper failure"), OcrProcessingError),
    ],
)
def test_provider_normalizes_tesseract_failures(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
    expected_error: type[Exception],
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise provider_error

    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", fail)
    provider = TesseractProvider(timeout_seconds=45)

    with Image.new("RGB", (1, 1)) as image, pytest.raises(expected_error) as captured:
        provider.recognize(image, "rus")

    assert "private" not in str(captured.value).lower()
    assert "native failure" not in str(captured.value).lower()


def test_invalid_tesseract_version_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_version_check(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(r'Invalid tesseract version: "private build output"')

    monkeypatch.setattr(
        provider_module.pytesseract,
        "image_to_data",
        fail_version_check,
    )
    provider = TesseractProvider(timeout_seconds=45)

    with (
        Image.new("RGB", (1, 1)) as image,
        pytest.raises(OcrEngineUnavailableError) as captured,
    ):
        provider.recognize(image, "eng")

    expected_message = "Tesseract or the selected language data is not available."
    assert str(captured.value) == expected_message
    assert "private build output" not in str(captured.value)


def test_custom_tesseract_command_is_configured_once_without_launching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches = 0

    def image_to_data(*args: Any, **kwargs: Any) -> None:
        nonlocal launches
        launches += 1

    monkeypatch.setattr(provider_module, "_configured_tesseract_command", None)
    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", image_to_data)
    monkeypatch.setattr(
        provider_module.pytesseract.pytesseract,
        "tesseract_cmd",
        "tesseract",
    )

    TesseractProvider(timeout_seconds=5, tesseract_command=r"C:\Tesseract\tesseract.exe")
    TesseractProvider(timeout_seconds=10, tesseract_command=r"C:\Tesseract\tesseract.exe")

    assert provider_module.pytesseract.pytesseract.tesseract_cmd == r"C:\Tesseract\tesseract.exe"
    assert launches == 0

    with pytest.raises(RuntimeError, match="already configured"):
        TesseractProvider(
            timeout_seconds=5,
            tesseract_command=r"D:\Other\tesseract.exe",
        )
