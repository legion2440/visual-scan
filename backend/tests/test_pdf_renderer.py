"""Tests for the serialized pypdfium2 rendering boundary."""

from __future__ import annotations

from io import BytesIO
from math import ceil
from typing import Any

import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.features.ocr.errors import (
    InvalidPdfError,
    InvalidPdfPasswordError,
    OcrTimeoutError,
    PdfRenderError,
    PdfTooLargeError,
    UnsupportedPdfSecurityError,
)
from app.features.ocr.pdf_renderer import PdfiumRenderer, PdfPageSpec


def make_pdf_bytes(
    pages: list[tuple[float, float, int]],
) -> bytes:
    """Create a small PDF with width, height, and clockwise rotation per page."""
    document = pdfium.PdfDocument.new()
    try:
        for width, height, rotation in pages:
            page = document.new_page(width, height)
            try:
                if rotation:
                    page.set_rotation(rotation)
            finally:
                page.close()
        output = BytesIO()
        document.save(output)
        return output.getvalue()
    finally:
        document.close()


def test_real_pdf_preflight_matches_fractional_and_rotated_render_sizes() -> None:
    data = make_pdf_bytes(
        [
            (72.5, 144.25, 0),
            (72.5, 144.25, 90),
        ]
    )
    renderer = PdfiumRenderer(render_dpi=300, clock=lambda: 0.0)

    with renderer.open_document(data, None, deadline=10) as document:
        specs = document.preflight(
            deadline=10,
            max_pages=2,
            max_page_pixels=1_000_000,
            max_total_pixels=2_000_000,
        )
        images = [document.render_page(spec, deadline=10) for spec in specs]

    try:
        assert [(spec.width, spec.height) for spec in specs] == [
            (
                ceil(72.5 * 300 / 72),
                ceil(144.25 * 300 / 72),
            ),
            (
                ceil(144.25 * 300 / 72),
                ceil(72.5 * 300 / 72),
            ),
        ]
        assert [image.size for image in images] == [(spec.width, spec.height) for spec in specs]
        assert all(image.mode == "RGB" for image in images)
        assert all(image.getpixel((0, 0)) == (255, 255, 255) for image in images)
    finally:
        for image in images:
            image.close()


class TrackingLock:
    """Deterministic lock that records ownership and acquisition timeouts."""

    def __init__(self, outcomes: list[bool] | None = None) -> None:
        self._outcomes = iter(outcomes or [])
        self.held = False
        self.acquire_calls: list[float | None] = []
        self.release_calls = 0

    def acquire(self, timeout: float | None = None) -> bool:
        self.acquire_calls.append(timeout)
        try:
            acquired = next(self._outcomes)
        except StopIteration:
            acquired = True
        if acquired:
            assert not self.held
            self.held = True
        return acquired

    def release(self) -> None:
        assert self.held
        self.held = False
        self.release_calls += 1


class FakePdfiumError(Exception):
    """Expose only the stable PDFium error code used for classification."""

    def __init__(self, message: str, err_code: int) -> None:
        super().__init__(message)
        self.err_code = err_code


class FakeBitmap:
    def __init__(
        self,
        lock: TrackingLock,
        size: tuple[int, int],
        events: list[str],
    ) -> None:
        self._lock = lock
        self._view = Image.new("RGBA", size, (10, 20, 30, 0))
        self._events = events
        self.closed = False

    def to_pil(self) -> Image.Image:
        assert self._lock.held
        self._events.append("to_pil")
        return self._view

    def close(self) -> None:
        assert self._lock.held
        self._events.append("bitmap.close")
        self.closed = True
        self._view.close()


class FakePage:
    def __init__(
        self,
        lock: TrackingLock,
        size_points: tuple[float, float],
        rendered_size: tuple[int, int],
        events: list[str],
    ) -> None:
        self._lock = lock
        self._size_points = size_points
        self._rendered_size = rendered_size
        self._events = events
        self.render_calls: list[dict[str, Any]] = []
        self.closed = False

    def get_size(self) -> tuple[float, float]:
        assert self._lock.held
        self._events.append("get_size")
        return self._size_points

    def render(self, **kwargs: Any) -> FakeBitmap:
        assert self._lock.held
        self._events.append("render")
        self.render_calls.append(kwargs)
        return FakeBitmap(self._lock, self._rendered_size, self._events)

    def close(self) -> None:
        assert self._lock.held
        self._events.append("page.close")
        self.closed = True


class FakeDocument:
    def __init__(self, lock: TrackingLock, pages: list[FakePage]) -> None:
        self._lock = lock
        self._pages = pages
        self.closed = False
        self.init_forms_calls = 0

    def __len__(self) -> int:
        assert self._lock.held
        return len(self._pages)

    def __getitem__(self, index: int) -> FakePage:
        assert self._lock.held
        return self._pages[index]

    def init_forms(self) -> None:
        self.init_forms_calls += 1
        raise AssertionError("init_forms() must not be called")

    def close(self) -> None:
        assert self._lock.held
        self.closed = True


class FakePdfiumModule:
    PdfiumError = FakePdfiumError

    def __init__(
        self,
        document: FakeDocument | None = None,
        error: Exception | None = None,
    ) -> None:
        self.document = document
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def PdfDocument(self, data: bytes, *, password: str | None) -> FakeDocument:
        self.calls.append({"data": data, "password": password})
        if self.error is not None:
            raise self.error
        assert self.document is not None
        return self.document


def build_fake_renderer(
    *,
    size_points: tuple[float, float] = (2, 2),
    rendered_size: tuple[int, int] = (2, 2),
    lock: TrackingLock | None = None,
    module_error: Exception | None = None,
) -> tuple[
    PdfiumRenderer,
    TrackingLock,
    FakePdfiumModule,
    FakeDocument,
    FakePage,
    list[str],
]:
    actual_lock = lock or TrackingLock()
    events: list[str] = []
    page = FakePage(actual_lock, size_points, rendered_size, events)
    document = FakeDocument(actual_lock, [page])
    module = FakePdfiumModule(document, module_error)
    renderer = PdfiumRenderer(
        render_dpi=72,
        pdfium_module=module,
        lock=actual_lock,
        clock=lambda: 0.0,
    )
    return renderer, actual_lock, module, document, page, events


def test_pdfium_calls_resources_and_annotations_stay_under_lock() -> None:
    renderer, lock, module, document, page, events = build_fake_renderer()

    with renderer.open_document(b"pdf", "secret", deadline=10) as session:
        specs = session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=4,
            max_total_pixels=4,
        )
        image = session.render_page(specs[0], deadline=10)
        assert not lock.held
        assert image.getpixel((0, 0)) == (10, 20, 30)

    try:
        assert module.calls == [{"data": b"pdf", "password": "secret"}]
        assert page.render_calls == [
            {
                "scale": 1.0,
                "fill_color": (255, 255, 255, 255),
                "draw_annots": True,
            }
        ]
        assert document.init_forms_calls == 0
        assert document.closed
        assert events == [
            "get_size",
            "page.close",
            "render",
            "to_pil",
            "bitmap.close",
            "page.close",
        ]
        assert lock.release_calls == 4
    finally:
        image.close()


@pytest.mark.parametrize(
    "size_points",
    [
        (float("nan"), 1),
        (float("inf"), 1),
        (1, float("-inf")),
        (0, 1),
        (-1, 1),
        (1, 0),
    ],
)
def test_invalid_page_dimensions_are_rejected_before_ceil(
    size_points: tuple[float, float],
) -> None:
    renderer, _, _, document, page, _ = build_fake_renderer(size_points=size_points)

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(InvalidPdfError),
    ):
        session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=4,
            max_total_pixels=4,
        )

    assert page.closed
    assert document.closed


def test_page_pixel_limit_is_inclusive_and_checked_during_preflight() -> None:
    renderer, _, _, _, _, _ = build_fake_renderer(
        size_points=(2, 2),
        rendered_size=(2, 2),
    )

    with renderer.open_document(b"pdf", None, deadline=10) as session:
        specs = session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=4,
            max_total_pixels=4,
        )

    assert specs[0].pixels == 4


def test_page_pixel_limit_rejects_before_render() -> None:
    renderer, _, _, _, page, _ = build_fake_renderer(
        size_points=(2, 2),
        rendered_size=(2, 2),
    )

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(PdfTooLargeError, match="page limit"),
    ):
        session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=3,
            max_total_pixels=4,
        )

    assert page.render_calls == []


def test_total_pixel_limit_rejects_individually_valid_pages() -> None:
    lock = TrackingLock()
    events: list[str] = []
    pages = [
        FakePage(lock, (2, 2), (2, 2), events),
        FakePage(lock, (2, 2), (2, 2), events),
    ]
    document = FakeDocument(lock, pages)
    renderer = PdfiumRenderer(
        72,
        pdfium_module=FakePdfiumModule(document),
        lock=lock,
        clock=lambda: 0.0,
    )

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(PdfTooLargeError, match="total limit"),
    ):
        session.preflight(
            deadline=10,
            max_pages=2,
            max_page_pixels=4,
            max_total_pixels=7,
        )

    assert all(page.render_calls == [] for page in pages)


def test_page_count_limit_rejects_before_page_access() -> None:
    lock = TrackingLock()
    events: list[str] = []
    pages = [
        FakePage(lock, (1, 1), (1, 1), events),
        FakePage(lock, (1, 1), (1, 1), events),
    ]
    document = FakeDocument(lock, pages)
    renderer = PdfiumRenderer(
        72,
        pdfium_module=FakePdfiumModule(document),
        lock=lock,
        clock=lambda: 0.0,
    )

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(PdfTooLargeError, match="1-page"),
    ):
        session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=1,
            max_total_pixels=2,
        )

    assert events == []


def test_zero_page_pdf_is_rejected_and_closed() -> None:
    lock = TrackingLock()
    document = FakeDocument(lock, [])
    renderer = PdfiumRenderer(
        72,
        pdfium_module=FakePdfiumModule(document),
        lock=lock,
        clock=lambda: 0.0,
    )

    with (
        pytest.raises(InvalidPdfError),
        renderer.open_document(b"pdf", None, deadline=10),
    ):
        raise AssertionError("zero-page PDF must not yield a session")

    assert document.closed


@pytest.mark.parametrize(
    ("err_code", "message", "expected_error"),
    [
        (4, "unrelated native text", InvalidPdfPasswordError),
        (5, "password text must not win", UnsupportedPdfSecurityError),
        (1, "password required", InvalidPdfError),
    ],
)
def test_pdfium_errors_are_classified_only_by_err_code(
    err_code: int,
    message: str,
    expected_error: type[Exception],
) -> None:
    renderer, _, _, _, _, _ = build_fake_renderer(module_error=FakePdfiumError(message, err_code))

    with (
        pytest.raises(expected_error) as captured,
        renderer.open_document(b"private bytes", None, deadline=10),
    ):
        raise AssertionError("failed open must not yield a session")

    assert message not in str(captured.value)


def test_lock_timeout_prevents_document_open() -> None:
    lock = TrackingLock(outcomes=[False])
    renderer, _, module, _, _, _ = build_fake_renderer(lock=lock)

    with (
        pytest.raises(OcrTimeoutError),
        renderer.open_document(b"pdf", None, deadline=2),
    ):
        raise AssertionError("timed out open must not yield a session")

    assert lock.acquire_calls == [2.0]
    assert module.calls == []


def test_lock_timeout_before_render_prevents_render_call() -> None:
    lock = TrackingLock(outcomes=[True, True, False, True])
    renderer, _, _, document, page, _ = build_fake_renderer(lock=lock)

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(OcrTimeoutError),
    ):
        specs = session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=4,
            max_total_pixels=4,
        )
        session.render_page(specs[0], deadline=10)

    assert page.render_calls == []
    assert document.closed


def test_deadline_expiry_during_preflight_stops_before_next_page() -> None:
    clock_values = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0])
    lock = TrackingLock()
    events: list[str] = []
    pages = [
        FakePage(lock, (1, 1), (1, 1), events),
        FakePage(lock, (1, 1), (1, 1), events),
    ]
    document = FakeDocument(lock, pages)
    renderer = PdfiumRenderer(
        72,
        pdfium_module=FakePdfiumModule(document),
        lock=lock,
        clock=lambda: next(clock_values),
    )

    with (
        renderer.open_document(b"pdf", None, deadline=5) as session,
        pytest.raises(OcrTimeoutError),
    ):
        session.preflight(
            deadline=5,
            max_pages=2,
            max_page_pixels=1,
            max_total_pixels=2,
        )

    assert events == ["get_size", "page.close"]
    assert document.closed


def test_render_size_mismatch_is_a_safe_render_error() -> None:
    renderer, _, _, _, page, _ = build_fake_renderer(
        size_points=(2, 2),
        rendered_size=(3, 2),
    )

    with (
        renderer.open_document(b"pdf", None, deadline=10) as session,
        pytest.raises(PdfRenderError) as captured,
    ):
        specs = session.preflight(
            deadline=10,
            max_pages=1,
            max_page_pixels=4,
            max_total_pixels=4,
        )
        session.render_page(specs[0], deadline=10)

    assert str(captured.value) == "PDF rendering failed unexpectedly."
    assert page.render_calls


def test_rendered_image_is_independent_after_bitmap_and_page_close() -> None:
    renderer, _, _, _, _, _ = build_fake_renderer()

    with renderer.open_document(b"pdf", None, deadline=10) as session:
        image = session.render_page(
            PdfPageSpec(index=0, width=2, height=2),
            deadline=10,
        )

    try:
        assert image.getpixel((0, 0)) == (10, 20, 30)
        grayscale = image.convert("L")
        try:
            assert grayscale.size == (2, 2)
            grayscale.load()
        finally:
            grayscale.close()
    finally:
        image.close()


def test_renderers_share_one_process_wide_default_lock() -> None:
    first = PdfiumRenderer(300)
    second = PdfiumRenderer(300)

    assert first._lock is second._lock
