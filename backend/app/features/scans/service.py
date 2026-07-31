"""Public entry point for the server-side scan archive."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.config import Settings
from app.features.scans.errors import (
    EmptyScanTextError,
    ScanNotFoundError,
    ScanTextTooLargeError,
)
from app.features.scans.repository import SQLiteScanRepository
from app.features.scans.schemas import (
    ScanClassificationFilter,
    ScanClearResponse,
    ScanCreateRequest,
    ScanDetail,
    ScanListAnalysisSnapshot,
    ScanListItem,
    ScanListResponse,
    ScanSort,
    SortOrder,
)


class ScanRepository(Protocol):
    """Persistence behavior required by the scan service."""

    def bootstrap(self) -> None: ...

    def create(self, record: ScanDetail) -> ScanDetail: ...

    def get(self, scan_id: UUID) -> ScanDetail | None: ...

    def list(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None,
        classification: ScanClassificationFilter | None,
        sort: ScanSort,
        order: SortOrder,
    ) -> tuple[list[ScanDetail], int]: ...

    def delete(self, scan_id: UUID) -> bool: ...

    def clear(self) -> int: ...


def sanitize_filename(filename: str) -> str:
    """Return a printable basename suitable for archive metadata."""
    candidate = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    return candidate or "untitled"


def create_snippet(text: str) -> str:
    """Collapse whitespace and return at most 160 visible characters."""
    normalized = " ".join(text.split())
    if len(normalized) <= 160:
        return normalized
    return f"{normalized[:159]}…"


class ScanService:
    """Enforce archive invariants and delegate persistence to SQLite."""

    def __init__(
        self,
        repository: ScanRepository,
        *,
        max_text_chars: int,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._max_text_chars = max_text_chars
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    def bootstrap(self) -> None:
        """Prepare storage during the application lifespan."""
        self._repository.bootstrap()

    def create(self, payload: ScanCreateRequest) -> ScanDetail:
        """Create one server-identified immutable scan record."""
        if len(payload.text) > self._max_text_chars:
            raise ScanTextTooLargeError(
                f"Scan text exceeds the {self._max_text_chars}-character storage limit."
            )
        if not payload.text.strip():
            raise EmptyScanTextError()

        scanned_at = self._clock()
        if scanned_at.tzinfo is None or scanned_at.utcoffset() is None:
            raise ValueError("The scan clock must return a timezone-aware datetime.")

        record = ScanDetail(
            id=self._uuid_factory(),
            filename=sanitize_filename(payload.filename),
            scanned_at=scanned_at.astimezone(UTC),
            text=payload.text,
            analysis=payload.analysis,
            ocr=payload.ocr,
        )
        return self._repository.create(record)

    def get(self, scan_id: UUID) -> ScanDetail:
        """Return one scan or a safe not-found error."""
        record = self._repository.get(scan_id)
        if record is None:
            raise ScanNotFoundError()
        return record

    def list(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None,
        classification: ScanClassificationFilter | None,
        sort: ScanSort,
        order: SortOrder,
    ) -> ScanListResponse:
        """Return compact list items without full text or structured fields."""
        records, total = self._repository.list(
            limit=limit,
            offset=offset,
            query=query,
            classification=classification,
            sort=sort,
            order=order,
        )
        return ScanListResponse(
            items=[self._to_list_item(record) for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    def delete(self, scan_id: UUID) -> None:
        """Delete one record and reject missing identifiers."""
        if not self._repository.delete(scan_id):
            raise ScanNotFoundError()

    def clear(self) -> ScanClearResponse:
        """Delete all records and report the exact count."""
        return ScanClearResponse(deleted=self._repository.clear())

    @staticmethod
    def _to_list_item(record: ScanDetail) -> ScanListItem:
        analysis = None
        if record.analysis is not None:
            analysis = ScanListAnalysisSnapshot(
                classification=record.analysis.classification,
                confidence=record.analysis.confidence,
                summary=record.analysis.summary,
                tags=record.analysis.tags,
                provider=record.analysis.provider,
            )
        return ScanListItem(
            id=record.id,
            filename=record.filename,
            scanned_at=record.scanned_at,
            snippet=create_snippet(record.text),
            analysis=analysis,
            ocr=record.ocr,
        )


def create_scans_service(settings: Settings) -> ScanService:
    """Build one resource-free app-local service from validated settings."""
    repository = SQLiteScanRepository(
        database_path=settings.scans_database_path,
        busy_timeout_ms=settings.scans_database_busy_timeout_ms,
    )
    return ScanService(
        repository,
        max_text_chars=settings.scans_max_text_chars,
    )
