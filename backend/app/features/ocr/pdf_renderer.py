"""Thread-safe PDFium boundary for in-memory PDF page rendering."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil, isfinite
from threading import Lock
from time import monotonic
from typing import Any

import pypdfium2 as pdfium
from PIL import Image

from app.features.ocr.errors import (
    InvalidPdfError,
    InvalidPdfPasswordError,
    OcrError,
    OcrTimeoutError,
    PdfRenderError,
    PdfTooLargeError,
    UnsupportedPdfSecurityError,
)

logger = logging.getLogger(__name__)

_PDFIUM_LOCK = Lock()
_INCORRECT_PASSWORD_ERROR = 4
_UNSUPPORTED_SECURITY_ERROR = 5


@dataclass(frozen=True, slots=True)
class PdfPageSpec:
    """Preflight dimensions for one zero-based PDF page."""

    index: int
    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


class PdfiumRenderer:
    """Preflight and render PDFs without concurrent PDFium calls."""

    def __init__(
        self,
        render_dpi: int,
        *,
        pdfium_module: Any = pdfium,
        lock: Any = None,
        clock: Any = monotonic,
    ) -> None:
        self.render_dpi = render_dpi
        self.scale = render_dpi / 72
        self._pdfium = pdfium_module
        self._lock = _PDFIUM_LOCK if lock is None else lock
        self._clock = clock

    def pixel_dimensions(
        self,
        width_points: float,
        height_points: float,
    ) -> tuple[int, int]:
        """Return deterministic render dimensions after validating PDF units."""
        if (
            not isfinite(width_points)
            or not isfinite(height_points)
            or width_points <= 0
            or height_points <= 0
        ):
            raise InvalidPdfError()

        width = max(1, ceil(width_points * self.scale))
        height = max(1, ceil(height_points * self.scale))
        return width, height

    def check_deadline(self, deadline: float) -> float:
        """Return remaining seconds or raise the shared OCR timeout error."""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise OcrTimeoutError()
        return remaining

    @contextmanager
    def _pdfium_guard(self, deadline: float) -> Iterator[None]:
        remaining = self.check_deadline(deadline)
        if not self._lock.acquire(timeout=remaining):
            raise OcrTimeoutError()

        try:
            self.check_deadline(deadline)
            yield
        finally:
            self._lock.release()

    def _open_native_document(
        self,
        data: bytes,
        password: str | None,
    ) -> Any:
        try:
            return self._pdfium.PdfDocument(data, password=password or None)
        except self._pdfium.PdfiumError as error:
            if getattr(error, "err_code", None) == _INCORRECT_PASSWORD_ERROR:
                raise InvalidPdfPasswordError() from error
            if getattr(error, "err_code", None) == _UNSUPPORTED_SECURITY_ERROR:
                raise UnsupportedPdfSecurityError() from error
            raise InvalidPdfError() from error
        except Exception as error:
            raise InvalidPdfError() from error

    @staticmethod
    def _close_resource(resource: Any) -> Exception | None:
        if resource is None:
            return None
        try:
            resource.close()
        except Exception as error:
            return error
        return None

    @staticmethod
    def _raise_operation_error(
        operation_error: BaseException | None,
        cleanup_error: Exception | None,
        default_error: type[OcrError],
    ) -> None:
        if operation_error is not None:
            if cleanup_error is not None:
                logger.error(
                    "PDFium cleanup also failed while handling another error",
                    exc_info=(
                        type(cleanup_error),
                        cleanup_error,
                        cleanup_error.__traceback__,
                    ),
                )
            if isinstance(operation_error, OcrError):
                raise operation_error
            if isinstance(operation_error, Exception):
                raise default_error() from operation_error
            raise operation_error
        if cleanup_error is not None:
            raise PdfRenderError() from cleanup_error

    def preflight(
        self,
        data: bytes,
        password: str | None,
        *,
        deadline: float,
        max_pages: int,
        max_page_pixels: int,
        max_total_pixels: int,
    ) -> tuple[PdfPageSpec, ...]:
        """Open, validate, and close a PDF inside one deadline-aware lock."""
        specs: list[PdfPageSpec] = []

        with self._pdfium_guard(deadline):
            document = None
            operation_error: BaseException | None = None
            cleanup_error: Exception | None = None
            try:
                document = self._open_native_document(data, password)
                page_count = len(document)
                if page_count <= 0:
                    raise InvalidPdfError()
                if page_count > max_pages:
                    raise PdfTooLargeError(f"The uploaded PDF exceeds the {max_pages}-page limit.")

                total_pixels = 0
                for index in range(page_count):
                    self.check_deadline(deadline)
                    page = None
                    page_error: BaseException | None = None
                    page_cleanup_error: Exception | None = None
                    try:
                        page = document[index]
                        width_points, height_points = page.get_size()
                        width, height = self.pixel_dimensions(
                            width_points,
                            height_points,
                        )
                    except BaseException as error:
                        page_error = error
                    finally:
                        page_cleanup_error = self._close_resource(page)

                    self._raise_operation_error(
                        page_error,
                        page_cleanup_error,
                        InvalidPdfError,
                    )
                    self.check_deadline(deadline)

                    spec = PdfPageSpec(index=index, width=width, height=height)
                    if spec.pixels > max_page_pixels:
                        raise PdfTooLargeError(
                            f"A rendered PDF page exceeds the {max_page_pixels}-pixel page limit."
                        )

                    total_pixels += spec.pixels
                    if total_pixels > max_total_pixels:
                        raise PdfTooLargeError(
                            "The rendered PDF pages exceed the "
                            f"{max_total_pixels}-pixel total limit."
                        )
                    specs.append(spec)
            except BaseException as error:
                operation_error = error
            finally:
                cleanup_error = self._close_resource(document)

            self._raise_operation_error(
                operation_error,
                cleanup_error,
                InvalidPdfError,
            )
            self.check_deadline(deadline)

        return tuple(specs)

    def render_page(
        self,
        data: bytes,
        password: str | None,
        spec: PdfPageSpec,
        *,
        deadline: float,
    ) -> Image.Image:
        """Open, render, detach, and close one page inside one lock."""
        image = None

        with self._pdfium_guard(deadline):
            document = None
            page = None
            bitmap = None
            pil_view = None
            operation_error: BaseException | None = None
            cleanup_error: Exception | None = None
            try:
                document = self._open_native_document(data, password)
                if spec.index >= len(document):
                    raise PdfRenderError()
                page = document[spec.index]
                bitmap = page.render(
                    scale=self.scale,
                    fill_color=(255, 255, 255, 255),
                    draw_annots=True,
                )
                pil_view = bitmap.to_pil()
                image = pil_view.convert("RGB")
                image.load()
            except BaseException as error:
                operation_error = error
            finally:
                for resource in (pil_view, bitmap, page, document):
                    caught = self._close_resource(resource)
                    cleanup_error = cleanup_error or caught

            try:
                self._raise_operation_error(
                    operation_error,
                    cleanup_error,
                    PdfRenderError,
                )
                self.check_deadline(deadline)
            except BaseException:
                if image is not None:
                    image.close()
                raise

        if image is None:
            raise PdfRenderError()
        if image.size != (spec.width, spec.height):
            image.close()
            raise PdfRenderError()
        return image
