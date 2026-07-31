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


class _PdfiumDocumentSession:
    """Keep one PDFium document alive while serializing every native call."""

    def __init__(
        self,
        renderer: PdfiumRenderer,
        document: Any,
        page_count: int,
    ) -> None:
        self._renderer = renderer
        self._document = document
        self.page_count = page_count
        self._closed = False

    def preflight(
        self,
        *,
        deadline: float,
        max_pages: int,
        max_page_pixels: int,
        max_total_pixels: int,
    ) -> tuple[PdfPageSpec, ...]:
        """Validate every page before the first OCR provider call."""
        if self.page_count > max_pages:
            raise PdfTooLargeError(f"The uploaded PDF exceeds the {max_pages}-page limit.")

        specs: list[PdfPageSpec] = []
        total_pixels = 0

        with self._renderer._pdfium_guard(deadline):
            for index in range(self.page_count):
                self._renderer.check_deadline(deadline)
                page = None
                error: Exception | None = None
                try:
                    page = self._document[index]
                    width_points, height_points = page.get_size()
                    width, height = self._renderer.pixel_dimensions(
                        width_points,
                        height_points,
                    )
                except OcrError:
                    raise
                except Exception as caught:
                    error = caught
                finally:
                    if page is not None:
                        try:
                            page.close()
                        except Exception as caught:
                            error = error or caught

                if error is not None:
                    raise InvalidPdfError() from error
                self._renderer.check_deadline(deadline)

                spec = PdfPageSpec(index=index, width=width, height=height)
                if spec.pixels > max_page_pixels:
                    raise PdfTooLargeError(
                        f"A rendered PDF page exceeds the {max_page_pixels}-pixel page limit."
                    )

                total_pixels += spec.pixels
                if total_pixels > max_total_pixels:
                    raise PdfTooLargeError(
                        f"The rendered PDF pages exceed the {max_total_pixels}-pixel total limit."
                    )
                specs.append(spec)

        return tuple(specs)

    def render_page(self, spec: PdfPageSpec, *, deadline: float) -> Image.Image:
        """Render one page and detach an RGB image from PDFium memory."""
        page = None
        bitmap = None
        pil_view = None
        image = None
        error: Exception | None = None

        with self._renderer._pdfium_guard(deadline):
            try:
                page = self._document[spec.index]
                bitmap = page.render(
                    scale=self._renderer.scale,
                    fill_color=(255, 255, 255, 255),
                    draw_annots=True,
                )
                pil_view = bitmap.to_pil()
                image = pil_view.convert("RGB")
                image.load()
            except Exception as caught:
                error = caught
            finally:
                for resource in (pil_view, bitmap, page):
                    if resource is None:
                        continue
                    try:
                        resource.close()
                    except Exception as caught:
                        error = error or caught
            if error is None:
                try:
                    self._renderer.check_deadline(deadline)
                except Exception as caught:
                    error = caught

        if error is not None:
            if image is not None:
                image.close()
            if isinstance(error, OcrError):
                raise error
            raise PdfRenderError() from error
        if image is None:
            raise PdfRenderError()
        if image.size != (spec.width, spec.height):
            image.close()
            raise PdfRenderError()
        return image

    def close(self) -> None:
        """Close the native document under the process-wide lock."""
        if self._closed:
            return

        error: Exception | None = None
        with self._renderer._pdfium_guard(deadline=None):
            try:
                self._document.close()
            except Exception as caught:
                error = caught
            finally:
                self._closed = True

        if error is not None:
            raise PdfRenderError() from error


class PdfiumRenderer:
    """Open and render PDFs without allowing concurrent PDFium calls."""

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

        width = max(1, ceil(width_points * self.render_dpi / 72))
        height = max(1, ceil(height_points * self.render_dpi / 72))
        return width, height

    def check_deadline(self, deadline: float) -> float:
        """Return remaining seconds or raise the shared OCR timeout error."""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise OcrTimeoutError()
        return remaining

    @contextmanager
    def _pdfium_guard(self, deadline: float | None) -> Iterator[None]:
        if deadline is None:
            self._lock.acquire()
        else:
            remaining = self.check_deadline(deadline)
            if not self._lock.acquire(timeout=remaining):
                raise OcrTimeoutError()

        try:
            if deadline is not None:
                self.check_deadline(deadline)
            yield
        finally:
            self._lock.release()

    @contextmanager
    def open_document(
        self,
        data: bytes,
        password: str | None,
        *,
        deadline: float,
    ) -> Iterator[_PdfiumDocumentSession]:
        """Open a document from bytes and close it safely after pipeline use."""
        document = None
        page_count = 0

        with self._pdfium_guard(deadline):
            try:
                document = self._pdfium.PdfDocument(data, password=password or None)
                page_count = len(document)
                self.check_deadline(deadline)
                if page_count <= 0:
                    raise InvalidPdfError()
            except self._pdfium.PdfiumError as error:
                if getattr(error, "err_code", None) == _INCORRECT_PASSWORD_ERROR:
                    raise InvalidPdfPasswordError() from error
                if getattr(error, "err_code", None) == _UNSUPPORTED_SECURITY_ERROR:
                    raise UnsupportedPdfSecurityError() from error
                raise InvalidPdfError() from error
            except OcrError:
                if document is not None:
                    try:
                        document.close()
                    except Exception:
                        logger.exception("Failed to close an invalid PDFium document")
                raise
            except Exception as error:
                if document is not None:
                    try:
                        document.close()
                    except Exception:
                        logger.exception("Failed to close an invalid PDFium document")
                raise InvalidPdfError() from error

        session = _PdfiumDocumentSession(self, document, page_count)
        try:
            yield session
        except BaseException:
            try:
                session.close()
            except Exception:
                logger.exception("Failed to close a PDFium document after an OCR error")
            raise
        else:
            session.close()
