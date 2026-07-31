"""Public entry point for the server-side scan archive."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.core.config import Settings
from app.features.scans.errors import (
    EmptyScanTextError,
    LegacyClaimForbiddenError,
    ScanNotFoundError,
    ScanTextTooLargeError,
)
from app.features.scans.repository import SQLiteScanRepository
from app.features.scans.schemas import (
    LegacyClaimResponse,
    LegacyScanStatus,
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
from app.storage.database import SQLiteDatabase


class ScanRepository(Protocol):
    """Persistence behavior required by the scan service."""

    def create(self, owner_id: UUID, record: ScanDetail) -> ScanDetail: ...

    def get(self, owner_id: UUID, scan_id: UUID) -> ScanDetail | None: ...

    def list(
        self,
        owner_id: UUID,
        *,
        limit: int,
        offset: int,
        query: str | None,
        classification: ScanClassificationFilter | None,
        sort: ScanSort,
        order: SortOrder,
    ) -> tuple[list[ScanDetail], int]: ...

    def delete(self, owner_id: UUID, scan_id: UUID) -> bool: ...

    def clear(self, owner_id: UUID) -> int: ...

    def legacy_count(self) -> int: ...

    def claim_legacy(self, owner_id: UUID) -> int: ...


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

    def create(self, owner_id: UUID, payload: ScanCreateRequest) -> ScanDetail:
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
        return self._repository.create(owner_id, record)

    def get(self, owner_id: UUID, scan_id: UUID) -> ScanDetail:
        """Return one scan or a safe not-found error."""
        record = self._repository.get(owner_id, scan_id)
        if record is None:
            raise ScanNotFoundError()
        return record

    def list(
        self,
        owner_id: UUID,
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
            owner_id,
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

    def delete(self, owner_id: UUID, scan_id: UUID) -> None:
        """Delete one record and reject missing identifiers."""
        if not self._repository.delete(owner_id, scan_id):
            raise ScanNotFoundError()

    def clear(self, owner_id: UUID) -> ScanClearResponse:
        """Delete all records and report the exact count."""
        return ScanClearResponse(deleted=self._repository.clear(owner_id))

    def legacy_status(self, *, is_initial_user: bool) -> LegacyScanStatus:
        """Expose only a count to the one user allowed to claim old records."""
        if not is_initial_user:
            raise LegacyClaimForbiddenError()
        count = self._repository.legacy_count()
        return LegacyScanStatus(count=count, claimable=count > 0)

    def claim_legacy(
        self,
        owner_id: UUID,
        *,
        is_initial_user: bool,
    ) -> LegacyClaimResponse:
        """Idempotently claim the complete pre-auth archive."""
        if not is_initial_user:
            raise LegacyClaimForbiddenError()
        return LegacyClaimResponse(claimed=self._repository.claim_legacy(owner_id))

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


def create_scans_service(database: SQLiteDatabase, settings: Settings) -> ScanService:
    """Build one resource-free app-local service from validated settings."""
    repository = SQLiteScanRepository(database)
    return ScanService(
        repository,
        max_text_chars=settings.scans_max_text_chars,
    )
