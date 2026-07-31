"""PDF OCR orchestration and service tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from PIL import Image

from app.features.ocr import pdf_pipeline as pdf_pipeline_module
from app.features.ocr.errors import (
    EmptyPdfError,
    OcrTimeoutError,
    PdfTooLargeError,
    UnsupportedPdfFormatError,
)
from app.features.ocr.pdf_pipeline import (
    PdfOcrPipeline,
    PdfPagePipelineResult,
    PdfPipelineResult,
)
from app.features.ocr.pdf_renderer import PdfPageSpec
from app.features.ocr.provider import ProviderResult
from app.features.ocr.schemas import OcrLanguage, PreprocessingMode
from app.features.ocr.service import OcrService


class FakePdfDocument:
    """Return configured page images while recording pipeline order."""

    def __init__(
        self,
        specs: tuple[PdfPageSpec, ...],
        events: list[str],
        rendered_images: list[Image.Image],
        *,
        preflight_error: Exception | None = None,
    ) -> None:
        self._specs = specs
        self._events = events
        self._rendered_images = rendered_images
        self._preflight_error = preflight_error

    def preflight(self, **kwargs: Any) -> tuple[PdfPageSpec, ...]:
        self._events.append("preflight")
        if self._preflight_error is not None:
            raise self._preflight_error
        assert kwargs["max_pages"] > 0
        assert kwargs["max_page_pixels"] > 0
        assert kwargs["max_total_pixels"] > 0
        return self._specs

    def render_page(self, spec: PdfPageSpec, **kwargs: Any) -> Image.Image:
        self._events.append(f"render:{spec.index}")
        image = Image.new("RGB", (spec.width, spec.height), (spec.index, 10, 20))
        self._rendered_images.append(image)
        assert kwargs["deadline"] > 0
        return image


class FakePdfRenderer:
    """Expose a renderer-compatible context without calling PDFium."""

    render_dpi = 300

    def __init__(
        self,
        specs: tuple[PdfPageSpec, ...],
        events: list[str],
        *,
        preflight_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.rendered_images: list[Image.Image] = []
        self.document = FakePdfDocument(
            specs,
            events,
            self.rendered_images,
            preflight_error=preflight_error,
        )
        self.lock_held = False
        self.open_calls: list[dict[str, Any]] = []

    @contextmanager
    def open_document(
        self,
        data: bytes,
        password: str | None,
        *,
        deadline: float,
    ) -> Iterator[FakePdfDocument]:
        self.open_calls.append({"data": data, "password": password, "deadline": deadline})
        self.events.append("open")
        try:
            yield self.document
        finally:
            self.events.append("close")


class FakeProvider:
    """Return page-specific results and capture timeout and image metadata."""

    def __init__(
        self,
        events: list[str],
        renderer: FakePdfRenderer,
        results: list[ProviderResult],
    ) -> None:
        self._events = events
        self._renderer = renderer
        self._results = iter(results)
        self.calls: list[dict[str, Any]] = []

    def recognize(
        self,
        image: Image.Image,
        language: str,
        timeout_seconds: float | None = None,
    ) -> ProviderResult:
        assert not self._renderer.lock_held
        page_index = len(self.calls)
        self._events.append(f"provider:{page_index}")
        self.calls.append(
            {
                "mode": image.mode,
                "size": image.size,
                "language": language,
                "timeout_seconds": timeout_seconds,
            }
        )
        return next(self._results)


def build_pdf_pipeline(
    *,
    specs: tuple[PdfPageSpec, ...],
    results: list[ProviderResult],
    events: list[str],
    clock: Any = lambda: 0.0,
    pdf_timeout_seconds: int = 180,
    preflight_error: Exception | None = None,
) -> tuple[PdfOcrPipeline, FakePdfRenderer, FakeProvider]:
    renderer = FakePdfRenderer(
        specs,
        events,
        preflight_error=preflight_error,
    )
    provider = FakeProvider(events, renderer, results)
    pipeline = PdfOcrPipeline(
        renderer=renderer,
        provider=provider,
        max_pages=20,
        max_page_pixels=25_000_000,
        max_total_pixels=200_000_000,
        ocr_timeout_seconds=45,
        pdf_timeout_seconds=pdf_timeout_seconds,
        clock=clock,
    )
    return pipeline, renderer, provider


def test_pdf_pipeline_preflights_all_pages_then_recognizes_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    specs = (
        PdfPageSpec(index=0, width=4, height=3),
        PdfPageSpec(index=1, width=3, height=4),
    )
    pipeline, renderer, provider = build_pdf_pipeline(
        specs=specs,
        results=[
            ProviderResult(text="First page", confidence=90.0, words=2),
            ProviderResult(text="", confidence=None, words=0),
        ],
        events=events,
    )

    original_transform = pdf_pipeline_module.transform_image

    def transform(
        image: Image.Image,
        mode: PreprocessingMode,
        threshold: int | None,
    ) -> Image.Image:
        assert not renderer.lock_held
        events.append(f"transform:{len(provider.calls)}")
        return original_transform(image, mode, threshold)

    monkeypatch.setattr(pdf_pipeline_module, "transform_image", transform)

    result = pipeline.recognize(
        data=b"pdf",
        password="secret",
        language=OcrLanguage.ENGLISH_RUSSIAN,
        preprocessing=PreprocessingMode.GRAYSCALE,
        threshold=None,
    )

    assert events == [
        "open",
        "preflight",
        "render:0",
        "transform:0",
        "provider:0",
        "render:1",
        "transform:1",
        "provider:1",
        "close",
    ]
    assert result.text == "First page\n\n"
    assert result.page_count == 2
    assert result.render_dpi == 300
    assert [page.page for page in result.pages] == [1, 2]
    assert [(page.width, page.height) for page in result.pages] == [(4, 3), (3, 4)]
    assert [call["mode"] for call in provider.calls] == ["L", "L"]
    assert [call["language"] for call in provider.calls] == ["eng+rus", "eng+rus"]
    assert renderer.open_calls[0]["password"] == "secret"
    for image in renderer.rendered_images:
        with pytest.raises(ValueError):
            image.getpixel((0, 0))


def test_pdf_pipeline_does_not_render_or_recognize_when_preflight_fails() -> None:
    events: list[str] = []
    pipeline, renderer, provider = build_pdf_pipeline(
        specs=(),
        results=[],
        events=events,
        preflight_error=PdfTooLargeError(),
    )

    with pytest.raises(PdfTooLargeError):
        pipeline.recognize(
            data=b"pdf",
            password=None,
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert events == ["open", "preflight", "close"]
    assert renderer.rendered_images == []
    assert provider.calls == []


def test_pdf_pipeline_passes_remaining_deadline_to_tesseract() -> None:
    class StepClock:
        value = -0.5

        def __call__(self) -> float:
            self.value += 0.5
            return self.value

    events: list[str] = []
    pipeline, _, provider = build_pdf_pipeline(
        specs=(PdfPageSpec(index=0, width=2, height=2),),
        results=[ProviderResult(text="page", confidence=80.0, words=1)],
        events=events,
        clock=StepClock(),
        pdf_timeout_seconds=5,
    )

    pipeline.recognize(
        data=b"pdf",
        password=None,
        language=OcrLanguage.ENGLISH,
        preprocessing=PreprocessingMode.NONE,
        threshold=None,
    )

    assert provider.calls[0]["timeout_seconds"] == 3.0


def test_pdf_pipeline_checks_deadline_before_preprocessing() -> None:
    clock_values = iter([0.0, 0.5, 1.0])
    events: list[str] = []
    pipeline, renderer, provider = build_pdf_pipeline(
        specs=(PdfPageSpec(index=0, width=2, height=2),),
        results=[ProviderResult(text="page", confidence=80.0, words=1)],
        events=events,
        clock=lambda: next(clock_values),
        pdf_timeout_seconds=1,
    )

    with pytest.raises(OcrTimeoutError):
        pipeline.recognize(
            data=b"pdf",
            password=None,
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
        )

    assert events == ["open", "preflight", "render:0", "close"]
    assert provider.calls == []
    with pytest.raises(ValueError):
        renderer.rendered_images[0].getpixel((0, 0))


class StubImagePipeline:
    """Reject accidental image calls from PDF-only service tests."""

    def recognize(self, **kwargs: Any) -> None:
        raise AssertionError(f"Unexpected image OCR call: {kwargs}")


class StubPdfPipeline:
    """Return request metadata so PDF service behavior can be isolated."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def recognize(self, **kwargs: Any) -> PdfPipelineResult:
        self.calls.append(kwargs)
        return PdfPipelineResult(
            text="stub text",
            page_count=1,
            language=kwargs["language"],
            preprocessing=kwargs["preprocessing"],
            threshold=kwargs["threshold"],
            render_dpi=300,
            pages=(
                PdfPagePipelineResult(
                    page=1,
                    text="stub text",
                    confidence=None,
                    words=2,
                    width=10,
                    height=20,
                ),
            ),
        )


def make_pdf_service(
    pipeline: StubPdfPipeline,
    *,
    max_pdf_bytes: int = 100,
) -> OcrService:
    return OcrService(
        StubImagePipeline(),
        pipeline,
        max_image_bytes=100,
        max_pdf_bytes=max_pdf_bytes,
    )


def test_pdf_service_normalizes_mime_defaults_threshold_and_filename() -> None:
    pipeline = StubPdfPipeline()
    service = make_pdf_service(pipeline)

    result = service.recognize_pdf(
        filename=r"C:\private/uploads/document.pdf",
        data=b"pdf",
        content_type=" APPLICATION/PDF; charset=binary ",
        language=OcrLanguage.RUSSIAN,
        preprocessing=PreprocessingMode.THRESHOLD,
        threshold=None,
        password="secret",
    )

    assert result.filename == "document.pdf"
    assert result.format == "PDF"
    assert result.engine == "tesseract"
    assert result.threshold == 160
    assert pipeline.calls == [
        {
            "data": b"pdf",
            "password": "secret",
            "language": OcrLanguage.RUSSIAN,
            "preprocessing": PreprocessingMode.THRESHOLD,
            "threshold": 160,
        }
    ]


@pytest.mark.parametrize("content_type", [None, "", "application/octet-stream"])
def test_pdf_service_rejects_non_pdf_media_types(content_type: str | None) -> None:
    pipeline = StubPdfPipeline()
    service = make_pdf_service(pipeline)

    with pytest.raises(UnsupportedPdfFormatError):
        service.recognize_pdf(
            filename="document.pdf",
            data=b"pdf",
            content_type=content_type,
            language=OcrLanguage.ENGLISH,
            preprocessing=PreprocessingMode.NONE,
            threshold=None,
            password=None,
        )

    assert pipeline.calls == []


def test_pdf_service_rechecks_empty_and_byte_limits() -> None:
    pipeline = StubPdfPipeline()
    service = make_pdf_service(pipeline, max_pdf_bytes=4)
    kwargs = {
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "language": OcrLanguage.ENGLISH,
        "preprocessing": PreprocessingMode.NONE,
        "threshold": None,
        "password": None,
    }

    with pytest.raises(EmptyPdfError):
        service.recognize_pdf(data=b"", **kwargs)
    with pytest.raises(PdfTooLargeError, match="4-byte"):
        service.recognize_pdf(data=b"12345", **kwargs)

    assert pipeline.calls == []
