"""In-memory preprocessing, pipeline, and service tests."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from app.features.ocr.errors import (
    EmptyImageError,
    ImageTooLargeError,
    InvalidImageError,
    InvalidOcrParametersError,
    UnsupportedImageFormatError,
)
from app.features.ocr.pipeline import OcrPipeline, PipelineResult
from app.features.ocr.provider import ProviderResult
from app.features.ocr.schemas import OcrLanguage, PreprocessingMode
from app.features.ocr.service import OcrService


def make_image_bytes(
    image_format: str = "PNG",
    size: tuple[int, int] = (4, 3),
    color: tuple[int, int, int] = (120, 80, 40),
    *,
    exif: Image.Exif | None = None,
) -> bytes:
    output = BytesIO()
    with Image.new("RGB", size, color) as image:
        save_options: dict[str, Any] = {}
        if exif is not None:
            save_options["exif"] = exif
        if image_format == "WEBP":
            save_options["lossless"] = True
        image.save(output, format=image_format, **save_options)
    return output.getvalue()


class FakeProvider:
    """Capture the prepared image before the pipeline closes it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def recognize(self, image: Image.Image, language: str) -> ProviderResult:
        self.calls.append(
            {
                "mode": image.mode,
                "size": image.size,
                "pixels": list(image.get_flattened_data()),
                "language": language,
            }
        )
        return ProviderResult(text="recognized", confidence=88.5, words=1)


def build_pipeline(
    provider: FakeProvider | None = None,
    max_pixels: int = 1_000,
) -> tuple[OcrPipeline, FakeProvider]:
    actual_provider = provider or FakeProvider()
    return OcrPipeline(actual_provider, max_image_pixels=max_pixels), actual_provider


@pytest.mark.parametrize(
    ("image_format", "mime_type", "language", "preprocessing", "expected_mode"),
    [
        (
            "PNG",
            "image/png",
            OcrLanguage.ENGLISH,
            PreprocessingMode.NONE,
            "RGB",
        ),
        (
            "JPEG",
            "image/jpeg",
            OcrLanguage.RUSSIAN,
            PreprocessingMode.GRAYSCALE,
            "L",
        ),
        (
            "WEBP",
            "image/webp",
            OcrLanguage.SPANISH,
            PreprocessingMode.NONE,
            "RGB",
        ),
    ],
)
def test_pipeline_accepts_supported_matching_formats(
    image_format: str,
    mime_type: str,
    language: OcrLanguage,
    preprocessing: PreprocessingMode,
    expected_mode: str,
) -> None:
    pipeline, provider = build_pipeline()

    result = pipeline.recognize(
        data=make_image_bytes(image_format),
        content_type=mime_type,
        language=language,
        preprocessing=preprocessing,
        threshold=None,
    )

    assert result.format == image_format
    assert result.width == 4
    assert result.height == 3
    assert result.text == "recognized"
    assert result.confidence == 88.5
    assert result.words == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["mode"] == expected_mode
    assert provider.calls[0]["size"] == (4, 3)
    assert provider.calls[0]["language"] == language.value


def test_grayscale_preprocessing_passes_l_image_to_provider() -> None:
    pipeline, provider = build_pipeline()

    result = pipeline.recognize(
        data=make_image_bytes(),
        content_type="image/png",
        language=OcrLanguage.RUSSIAN,
        preprocessing=PreprocessingMode.GRAYSCALE,
        threshold=None,
    )

    assert result.preprocessing is PreprocessingMode.GRAYSCALE
    assert result.threshold is None
    assert provider.calls[0]["mode"] == "L"
    assert provider.calls[0]["language"] == "rus"


def test_threshold_preprocessing_creates_binary_image() -> None:
    output = BytesIO()
    with Image.new("L", (2, 1)) as image:
        image.putdata([50, 200])
        image.save(output, format="PNG")
    pipeline, provider = build_pipeline()

    result = pipeline.recognize(
        data=output.getvalue(),
        content_type="image/png",
        language=OcrLanguage.ENGLISH_RUSSIAN,
        preprocessing=PreprocessingMode.THRESHOLD,
        threshold=100,
    )

    assert result.threshold == 100
    assert provider.calls[0]["mode"] == "1"
    assert provider.calls[0]["pixels"] == [0, 255]
    assert provider.calls[0]["language"] == "eng+rus"


def test_exif_orientation_is_applied_before_response_dimensions() -> None:
    exif = Image.Exif()
    exif[274] = 6
    pipeline, provider = build_pipeline()

    result = pipeline.recognize(
        data=make_image_bytes("JPEG", size=(4, 2), exif=exif),
        content_type="image/jpeg",
        language=OcrLanguage.ENGLISH,
        preprocessing=PreprocessingMode.NONE,
        threshold=None,
    )

    assert result.width == 2
    assert result.height == 4
    assert provider.calls[0]["size"] == (2, 4)


@pytest.mark.parametrize(
    ("data", "content_type", "error_type"),
    [
        (b"", "image/png", EmptyImageError),
        (b"not an image", "image/png", InvalidImageError),
        (make_image_bytes("PNG"), "image/jpeg", UnsupportedImageFormatError),
        (make_image_bytes("PNG"), "image/gif", UnsupportedImageFormatError),
        (make_image_bytes("GIF"), "image/png", UnsupportedImageFormatError),
    ],
)
def test_invalid_images_are_normalized(
    data: bytes,
    content_type: str,
    error_type: type[Exception],
) -> None:
    pipeline, provider = build_pipeline()

    with pytest.raises(error_type):
        pipeline.recognize(
            data=data,
            content_type=content_type,
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert provider.calls == []


def test_truncated_image_is_rejected_before_provider() -> None:
    data = make_image_bytes("PNG")
    pipeline, provider = build_pipeline()

    with pytest.raises(InvalidImageError):
        pipeline.recognize(
            data=data[: len(data) // 2],
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert provider.calls == []


def test_project_pixel_limit_is_checked_before_recognition() -> None:
    pipeline, provider = build_pipeline(max_pixels=99)

    with pytest.raises(ImageTooLargeError, match="99-pixel"):
        pipeline.recognize(
            data=make_image_bytes(size=(10, 10)),
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert provider.calls == []


@pytest.mark.parametrize("pillow_limit", [15, 10])
def test_pillow_decompression_bomb_warning_and_error_become_413(
    monkeypatch: pytest.MonkeyPatch,
    pillow_limit: int,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", pillow_limit)
    pipeline, provider = build_pipeline(max_pixels=1_000)

    with pytest.raises(ImageTooLargeError):
        pipeline.recognize(
            data=make_image_bytes(size=(5, 5)),
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert provider.calls == []


class StubPipeline:
    """Return request metadata so service behavior can be isolated."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def recognize(self, **kwargs: Any) -> PipelineResult:
        self.calls.append(kwargs)
        return PipelineResult(
            text="stub text",
            confidence=None,
            words=2,
            language=kwargs["language"],
            preprocessing=kwargs["preprocessing"],
            threshold=kwargs["threshold"],
            width=10,
            height=20,
            format="PNG",
        )


def test_service_defaults_threshold_and_sanitizes_both_path_styles() -> None:
    pipeline = StubPipeline()
    service = OcrService(pipeline, max_image_bytes=100)

    result = service.recognize(
        filename=r"C:\private/uploads/scan.png",
        data=b"image",
        content_type="image/png",
        language=OcrLanguage.ENGLISH,
        preprocessing=PreprocessingMode.THRESHOLD,
        threshold=None,
    )

    assert result.filename == "scan.png"
    assert result.threshold == 160
    assert pipeline.calls[0]["threshold"] == 160


@pytest.mark.parametrize(
    ("preprocessing", "threshold"),
    [
        (PreprocessingMode.NONE, 160),
        (PreprocessingMode.GRAYSCALE, 160),
        (PreprocessingMode.THRESHOLD, -1),
        (PreprocessingMode.THRESHOLD, 256),
    ],
)
def test_service_rejects_invalid_threshold_combinations(
    preprocessing: PreprocessingMode,
    threshold: int,
) -> None:
    service = OcrService(StubPipeline(), max_image_bytes=100)

    with pytest.raises(InvalidOcrParametersError):
        service.recognize(
            filename="scan.png",
            data=b"image",
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=preprocessing,
            threshold=threshold,
        )


def test_service_rechecks_byte_and_empty_limits() -> None:
    pipeline = StubPipeline()
    service = OcrService(pipeline, max_image_bytes=4)

    with pytest.raises(EmptyImageError):
        service.recognize(
            filename=None,
            data=b"",
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )
    with pytest.raises(ImageTooLargeError, match="4-byte"):
        service.recognize(
            filename=None,
            data=b"12345",
            content_type="image/png",
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert pipeline.calls == []
