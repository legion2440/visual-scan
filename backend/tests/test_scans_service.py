"""Scan service invariants independent from HTTP and SQLite."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.features.scans.errors import (
    EmptyScanTextError,
    ScanNotFoundError,
    ScanTextTooLargeError,
)
from app.features.scans.schemas import (
    ScanCreateRequest,
    ScanDetail,
    ScanSort,
    SortOrder,
)
from app.features.scans.service import ScanService, create_snippet

FIXED_ID = UUID("a11aa4fd-3354-4af1-81b5-740ef31afad2")
OWNER_ID = UUID("11111111-1111-4111-8111-111111111111")
FIXED_TIME = datetime(2026, 7, 31, 6, 30, tzinfo=UTC)


class MemoryRepository:
    """Small repository double that preserves complete scan contracts."""

    def __init__(self) -> None:
        self.records: dict[UUID, ScanDetail] = {}

    def create(self, owner_id: UUID, record: ScanDetail) -> ScanDetail:
        assert owner_id == OWNER_ID
        self.records[record.id] = record
        return record

    def get(self, owner_id: UUID, scan_id: UUID) -> ScanDetail | None:
        assert owner_id == OWNER_ID
        return self.records.get(scan_id)

    def list(self, owner_id: UUID, **kwargs) -> tuple[list[ScanDetail], int]:
        assert owner_id == OWNER_ID
        records = list(self.records.values())
        return records[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]], len(records)

    def delete(self, owner_id: UUID, scan_id: UUID) -> bool:
        assert owner_id == OWNER_ID
        return self.records.pop(scan_id, None) is not None

    def clear(self, owner_id: UUID) -> int:
        assert owner_id == OWNER_ID
        total = len(self.records)
        self.records.clear()
        return total

    def legacy_count(self) -> int:
        return 0

    def claim_legacy(self, owner_id: UUID) -> int:
        assert owner_id == OWNER_ID
        return 0


def build_service(
    repository: MemoryRepository | None = None,
    *,
    max_text_chars: int = 250_000,
) -> tuple[ScanService, MemoryRepository]:
    storage = repository or MemoryRepository()
    return (
        ScanService(
            storage,
            max_text_chars=max_text_chars,
            clock=lambda: FIXED_TIME,
            uuid_factory=lambda: FIXED_ID,
        ),
        storage,
    )


def full_payload(**updates: object) -> ScanCreateRequest:
    values = {
        "filename": "contract.jpg",
        "text": "Full edited OCR text.",
        "analysis": {
            "classification": "contract",
            "confidence": 0.93,
            "summary": "Employment agreement.",
            "tags": ["legal", "employment"],
            "fields": [{"label": "Effective date", "value": "2026-07-30"}],
            "provider": "local-llm",
        },
        "ocr": {
            "source": "browser",
            "engine": "Tesseract.js 5.1.1 (browser)",
            "language": "eng",
            "profile": "fast",
            "confidence": 87,
            "words": 143,
        },
        **updates,
    }
    return ScanCreateRequest.model_validate(values)


def test_create_preserves_text_and_generates_server_fields() -> None:
    service, repository = build_service()
    original_text = "  Edited OCR text.\r\n"

    result = service.create(
        OWNER_ID,
        full_payload(
            filename="../folder\\agreement\u0000.jpg",
            text=original_text,
        ),
    )

    assert result.id == FIXED_ID
    assert result.scanned_at == FIXED_TIME
    assert result.filename == "agreement.jpg"
    assert result.text == original_text
    assert repository.records[FIXED_ID] == result


def test_analysis_and_ocr_round_trip_without_synthetic_fields() -> None:
    service, _ = build_service()

    complete = service.create(OWNER_ID, full_payload())
    empty_metadata = service.create(
        OWNER_ID,
        full_payload(
            filename="plain.txt",
            text="Plain text",
            analysis=None,
            ocr=None,
        ),
    )

    assert complete.analysis is not None
    assert complete.analysis.fields[0].label == "Effective date"
    assert complete.ocr is not None
    assert complete.ocr.language == "eng"
    assert empty_metadata.analysis is None
    assert empty_metadata.ocr is None


def test_whitespace_only_text_is_rejected_without_writing() -> None:
    service, repository = build_service()

    with pytest.raises(EmptyScanTextError):
        service.create(OWNER_ID, full_payload(text=" \r\n\t "))

    assert repository.records == {}


def test_text_limit_is_checked_without_truncation() -> None:
    service, repository = build_service(max_text_chars=5)

    with pytest.raises(ScanTextTooLargeError, match="5-character"):
        service.create(OWNER_ID, full_payload(text="123456"))

    assert repository.records == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("one \r\n two\tthree", "one two three"),
        ("x" * 160, "x" * 160),
        ("x" * 161, ("x" * 159) + "…"),
    ],
)
def test_snippet_collapses_whitespace_and_limits_visible_length(
    text: str,
    expected: str,
) -> None:
    assert create_snippet(text) == expected
    assert len(create_snippet(text)) <= 160


def test_list_omits_full_text_and_structured_fields() -> None:
    service, _ = build_service()
    created = service.create(OWNER_ID, full_payload(text="one \n two"))

    result = service.list(
        OWNER_ID,
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.DESCENDING,
    )

    item = result.items[0]
    assert item.id == created.id
    assert item.snippet == "one two"
    assert item.analysis is not None
    assert "fields" not in item.analysis.model_dump()
    assert "text" not in item.model_dump()


def test_get_and_delete_missing_record_raise_not_found() -> None:
    service, _ = build_service()

    with pytest.raises(ScanNotFoundError):
        service.get(OWNER_ID, FIXED_ID)
    with pytest.raises(ScanNotFoundError):
        service.delete(OWNER_ID, FIXED_ID)


def test_clear_delegates_to_repository() -> None:
    service, repository = build_service()
    service.create(OWNER_ID, full_payload())

    result = service.clear(OWNER_ID)

    assert result.deleted == 1
    assert repository.records == {}
