"""SQLite persistence adapter for saved scan results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.features.scans.errors import ScanStorageUnavailableError
from app.features.scans.schema import (
    SCHEMA_VERSION,
    create_schema_v1,
    scans_table_exists,
    validate_schema_v1,
)
from app.features.scans.schemas import (
    ScanAnalysisSnapshot,
    ScanClassificationFilter,
    ScanDetail,
    ScanOcrSnapshot,
    ScanSort,
    SortOrder,
)

ConnectionFactory = Callable[..., sqlite3.Connection]

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


def _casefold(value: object) -> str:
    """Return a Unicode-aware search value for a SQLite scalar."""
    if value is None:
        return ""
    return str(value).casefold()


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
    """Open one configured SQLite connection for each archive operation."""

    def __init__(
        self,
        *,
        database_path: Path,
        busy_timeout_ms: int,
        connection_factory: ConnectionFactory = sqlite3.connect,
    ) -> None:
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._connection_factory = connection_factory

    @property
    def database_path(self) -> Path:
        """Expose the resolved path for diagnostics and lifecycle tests."""
        return self._database_path

    def bootstrap(self) -> None:
        """Create or strictly validate schema version one."""
        try:
            if self._database_path.exists() and self._database_path.is_dir():
                raise ScanStorageUnavailableError()
            self._database_path.parent.mkdir(parents=True, exist_ok=True)

            with self._connection() as connection:
                journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if journal_mode is None or str(journal_mode[0]).casefold() != "wal":
                    raise ScanStorageUnavailableError()
                self._run_quick_check(connection)

                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                table_exists = scans_table_exists(connection)
                if version == 0:
                    if table_exists:
                        raise ScanStorageUnavailableError()
                    with self._transaction(connection, immediate=True):
                        create_schema_v1(connection)
                elif version != SCHEMA_VERSION:
                    raise ScanStorageUnavailableError()

                try:
                    validate_schema_v1(connection)
                except ValueError as error:
                    raise ScanStorageUnavailableError() from error
        except ScanStorageUnavailableError:
            raise
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def create(self, record: ScanDetail) -> ScanDetail:
        """Persist one complete record in an explicit write transaction."""
        try:
            with (
                self._connection() as connection,
                self._transaction(
                    connection,
                    immediate=True,
                ),
            ):
                analysis = record.analysis
                connection.execute(
                    """
                        INSERT INTO scans (
                            id,
                            filename,
                            scanned_at,
                            text,
                            classification,
                            analysis_confidence,
                            summary,
                            provider,
                            tags_json,
                            fields_json,
                            ocr_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                    (
                        str(record.id),
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
                        (_json_dump(record.ocr.model_dump(mode="json")) if record.ocr else None),
                    ),
                )
            return record
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def get(self, scan_id: UUID) -> ScanDetail | None:
        """Return one scan from a consistent read transaction."""
        try:
            with self._connection() as connection, self._transaction(connection):
                row = connection.execute(
                    "SELECT * FROM scans WHERE id = ?",
                    (str(scan_id),),
                ).fetchone()
            return self._map_row(row) if row is not None else None
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (json.JSONDecodeError, ValidationError, ValueError, OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def list(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None,
        classification: ScanClassificationFilter | None,
        sort: ScanSort,
        order: SortOrder,
    ) -> tuple[list[ScanDetail], int]:
        """Return total and page from the same SQLite read snapshot."""
        where_sql, parameters = self._build_filters(
            query=query,
            classification=classification,
        )
        sort_expression = _SORT_EXPRESSIONS[sort]
        order_expression = _ORDER_EXPRESSIONS[order]
        select_parameters = {**parameters, "limit": limit, "offset": offset}

        try:
            with self._connection() as connection, self._transaction(connection):
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM scans{where_sql}",
                        parameters,
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                        SELECT *
                        FROM scans
                        {where_sql}
                        ORDER BY {sort_expression} {order_expression}, id ASC
                        LIMIT :limit OFFSET :offset
                        """,
                    select_parameters,
                ).fetchall()
            return [self._map_row(row) for row in rows], total
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (json.JSONDecodeError, ValidationError, ValueError, OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def delete(self, scan_id: UUID) -> bool:
        """Delete one scan and report whether a row existed."""
        try:
            with (
                self._connection() as connection,
                self._transaction(
                    connection,
                    immediate=True,
                ),
            ):
                cursor = connection.execute(
                    "DELETE FROM scans WHERE id = ?",
                    (str(scan_id),),
                )
                deleted = cursor.rowcount
            return deleted == 1
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    def clear(self) -> int:
        """Delete every saved scan in one write transaction."""
        try:
            with (
                self._connection() as connection,
                self._transaction(
                    connection,
                    immediate=True,
                ),
            ):
                total = int(connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
                connection.execute("DELETE FROM scans")
            return total
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            raise
        except (OSError, sqlite3.Error) as error:
            raise ScanStorageUnavailableError() from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection_factory(
            str(self._database_path),
            timeout=self._busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ScanStorageUnavailableError()
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise ScanStorageUnavailableError()
            connection.create_function("casefold", 1, _casefold, deterministic=True)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(
        self,
        connection: sqlite3.Connection,
        *,
        immediate: bool = False,
    ) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def _run_quick_check(connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA quick_check").fetchall()
        if len(rows) != 1 or str(rows[0][0]).casefold() != "ok":
            raise ScanStorageUnavailableError()

    @staticmethod
    def _build_filters(
        *,
        query: str | None,
        classification: ScanClassificationFilter | None,
    ) -> tuple[str, dict[str, Any]]:
        filters: list[str] = []
        parameters: dict[str, Any] = {}

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

        return (" WHERE " + " AND ".join(filters) if filters else ""), parameters

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

        ocr = None
        if row["ocr_json"] is not None:
            ocr = ScanOcrSnapshot.model_validate(json.loads(row["ocr_json"]))

        return ScanDetail(
            id=UUID(row["id"]),
            filename=row["filename"],
            scanned_at=_timestamp_from_storage(row["scanned_at"]),
            text=row["text"],
            analysis=analysis,
            ocr=ocr,
        )
