"""Owner-scoped SQLite persistence adapter for saved scan results."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.features.scans.errors import ScanStorageUnavailableError
from app.features.scans.schemas import (
    ScanAnalysisSnapshot,
    ScanClassificationFilter,
    ScanDetail,
    ScanOcrSnapshot,
    ScanSort,
    SortOrder,
)
from app.storage.database import SQLiteDatabase

_SORT_EXPRESSIONS = {
    ScanSort.SCANNED_AT: "scanned_at",
    ScanSort.FILENAME: "casefold(filename)",
    ScanSort.CLASSIFICATION: "COALESCE(classification, 'unclassified')",
    ScanSort.CONFIDENCE: "COALESCE(analysis_confidence, 0)",
}
_ORDER_EXPRESSIONS = {
    SortOrder.ASCENDING: "ASC",
    SortOrder.DESCENDING: "DESC",
}


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _timestamp_to_storage(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_from_storage(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Stored scan timestamp is not timezone-aware.")
    return timestamp.astimezone(UTC)


class SQLiteScanRepository:
    """Use shared database policy while enforcing owner filters in every query."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @property
    def database_path(self):
        return self._database.database_path

    def create(self, owner_id: UUID, record: ScanDetail) -> ScanDetail:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                analysis = record.analysis
                connection.execute(
                    """
                    INSERT INTO scans (
                        id, owner_id, filename, scanned_at, text, classification,
                        analysis_confidence, summary, provider, tags_json,
                        fields_json, ocr_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.id),
                        str(owner_id),
                        record.filename,
                        _timestamp_to_storage(record.scanned_at),
                        record.text,
                        analysis.classification.value if analysis else None,
                        analysis.confidence if analysis else None,
                        analysis.summary if analysis else None,
                        analysis.provider if analysis else None,
                        _json_dump(analysis.tags) if analysis else None,
                        (
                            _json_dump([field.model_dump(mode="json") for field in analysis.fields])
                            if analysis
                            else None
                        ),
                        _json_dump(record.ocr.model_dump(mode="json")) if record.ocr else None,
                    ),
                )
            return record
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def get(self, owner_id: UUID, scan_id: UUID) -> ScanDetail | None:
        try:
            with self._database.connection() as connection, self._database.transaction(connection):
                row = connection.execute(
                    "SELECT * FROM scans WHERE owner_id = ? AND id = ?",
                    (str(owner_id), str(scan_id)),
                ).fetchone()
            return self._map_row(row) if row is not None else None
        except (json.JSONDecodeError, ValidationError, ValueError, OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

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
    ) -> tuple[list[ScanDetail], int]:
        where_sql, parameters = self._build_filters(
            owner_id=owner_id,
            query=query,
            classification=classification,
        )
        sort_expression = _SORT_EXPRESSIONS[sort]
        order_expression = _ORDER_EXPRESSIONS[order]
        select_parameters = {**parameters, "limit": limit, "offset": offset}
        try:
            with self._database.connection() as connection, self._database.transaction(connection):
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM scans{where_sql}",
                        parameters,
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                    SELECT * FROM scans
                    {where_sql}
                    ORDER BY {sort_expression} {order_expression}, id ASC
                    LIMIT :limit OFFSET :offset
                    """,
                    select_parameters,
                ).fetchall()
            return [self._map_row(row) for row in rows], total
        except (json.JSONDecodeError, ValidationError, ValueError, OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def delete(self, owner_id: UUID, scan_id: UUID) -> bool:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                cursor = connection.execute(
                    "DELETE FROM scans WHERE owner_id = ? AND id = ?",
                    (str(owner_id), str(scan_id)),
                )
            return cursor.rowcount == 1
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def clear(self, owner_id: UUID) -> int:
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM scans WHERE owner_id = ?",
                        (str(owner_id),),
                    ).fetchone()[0]
                )
                connection.execute("DELETE FROM scans WHERE owner_id = ?", (str(owner_id),))
            return total
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def legacy_count(self) -> int:
        try:
            with self._database.connection() as connection, self._database.transaction(connection):
                return int(connection.execute("SELECT COUNT(*) FROM legacy_scans").fetchone()[0])
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def claim_legacy(self, owner_id: UUID) -> int:
        """Copy and delete all legacy rows in one BEGIN IMMEDIATE transaction."""
        try:
            with (
                self._database.connection() as connection,
                self._database.transaction(connection, immediate=True),
            ):
                total = int(connection.execute("SELECT COUNT(*) FROM legacy_scans").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO scans (
                        id, owner_id, filename, scanned_at, text, classification,
                        analysis_confidence, summary, provider, tags_json,
                        fields_json, ocr_json
                    )
                    SELECT
                        id, ?, filename, scanned_at, text, classification,
                        analysis_confidence, summary, provider, tags_json,
                        fields_json, ocr_json
                    FROM legacy_scans
                    """,
                    (str(owner_id),),
                )
                connection.execute("DELETE FROM legacy_scans")
                remaining = int(
                    connection.execute("SELECT COUNT(*) FROM legacy_scans").fetchone()[0]
                )
                if remaining != 0:
                    raise ScanStorageUnavailableError()
            return total
        except ScanStorageUnavailableError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    @staticmethod
    def _build_filters(
        *,
        owner_id: UUID,
        query: str | None,
        classification: ScanClassificationFilter | None,
    ) -> tuple[str, dict[str, Any]]:
        filters = ["owner_id = :owner_id"]
        parameters: dict[str, Any] = {"owner_id": str(owner_id)}
        if query is not None:
            filters.append(
                """
                (
                    instr(casefold(filename), casefold(:query)) > 0
                    OR instr(casefold(text), casefold(:query)) > 0
                    OR instr(casefold(summary), casefold(:query)) > 0
                    OR instr(casefold(tags_json), casefold(:query)) > 0
                )
                """
            )
            parameters["query"] = query
        if classification == "unclassified":
            filters.append("classification IS NULL")
        elif classification is not None:
            filters.append("classification = :classification")
            parameters["classification"] = classification.value
        return " WHERE " + " AND ".join(filters), parameters

    @staticmethod
    def _map_row(row: sqlite3.Row) -> ScanDetail:
        analysis = None
        if row["classification"] is not None:
            analysis = ScanAnalysisSnapshot(
                classification=row["classification"],
                confidence=row["analysis_confidence"],
                summary=row["summary"],
                provider=row["provider"],
                tags=json.loads(row["tags_json"]),
                fields=json.loads(row["fields_json"]),
            )
        ocr = (
            ScanOcrSnapshot.model_validate(json.loads(row["ocr_json"]))
            if row["ocr_json"] is not None
            else None
        )
        return ScanDetail(
            id=UUID(row["id"]),
            filename=row["filename"],
            scanned_at=_timestamp_from_storage(row["scanned_at"]),
            text=row["text"],
            analysis=analysis,
            ocr=ocr,
        )
