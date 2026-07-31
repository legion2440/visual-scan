"""Decode, validate, orient, and preprocess uploaded images in memory."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.features.ocr.errors import (
    EmptyImageError,
    ImageTooLargeError,
    InvalidImageError,
    UnsupportedImageFormatError,
)
from app.features.ocr.schemas import PreprocessingMode

FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(slots=True)
class PreparedImage:
    """A validated Pillow image and the metadata needed by the response."""

    image: Image.Image
    width: int
    height: int
    format: str
    preprocessing: PreprocessingMode
    threshold: int | None


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").partition(";")[0].strip().lower()


def _validate_pixel_count(width: int, height: int, max_pixels: int) -> None:
    if width <= 0 or height <= 0:
        raise InvalidImageError()
    if width * height > max_pixels:
        raise ImageTooLargeError(f"The uploaded image exceeds the {max_pixels}-pixel limit.")


def transform_image(
    image: Image.Image,
    mode: PreprocessingMode,
    threshold: int | None,
) -> Image.Image:
    """Return an independent image transformed for Tesseract."""
    if mode is PreprocessingMode.NONE:
        return image.convert("RGB")
    if mode is PreprocessingMode.GRAYSCALE:
        return ImageOps.grayscale(image)

    grayscale = ImageOps.grayscale(image)
    try:
        return grayscale.point(
            lambda value: 255 if value >= int(threshold) else 0,
            mode="1",
        )
    finally:
        grayscale.close()


def preprocess_image(
    data: bytes,
    content_type: str | None,
    mode: PreprocessingMode,
    threshold: int | None,
    max_pixels: int,
) -> PreparedImage:
    """Validate and transform an uploaded image without persisting it."""
    if not data:
        raise EmptyImageError()

    declared_mime = _normalize_content_type(content_type)
    if declared_mime not in FORMAT_TO_MIME.values():
        raise UnsupportedImageFormatError()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                actual_format = probe.format
                if actual_format not in FORMAT_TO_MIME:
                    raise UnsupportedImageFormatError()
                if FORMAT_TO_MIME[actual_format] != declared_mime:
                    raise UnsupportedImageFormatError()

                width, height = probe.size
                _validate_pixel_count(width, height, max_pixels)
                probe.verify()

            with Image.open(BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                try:
                    output = transform_image(oriented, mode, threshold)
                    output.load()
                finally:
                    if oriented is not source:
                        oriented.close()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageTooLargeError() from error
    except (EmptyImageError, ImageTooLargeError, UnsupportedImageFormatError):
        raise
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise InvalidImageError() from error

    return PreparedImage(
        image=output,
        width=output.width,
        height=output.height,
        format=actual_format,
        preprocessing=mode,
        threshold=threshold,
    )
