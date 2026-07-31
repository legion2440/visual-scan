"""SQLite scan repository bootstrap, transactions, and query behavior."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.features.analysis.schemas import DocumentClassification
from app.features.scans.errors import ScanStorageUnavailableError
from app.features.scans.repository import SQLiteScanRepository
from app.features.scans.schemas import ScanDetail, ScanSort, SortOrder

BASE_TIME = datetime(2026, 7, 31, 6, 30, tzinfo=UTC)


def build_repository(
    database_path: Path,
    *,
    busy_timeout_ms: int = 5_000,
    connection_factory=sqlite3.connect,
) -> SQLiteScanRepository:
    return SQLiteScanRepository(
        database_path=database_path,
        busy_timeout_ms=busy_timeout_ms,
        connection_factory=connection_factory,
    )


def build_record(
    *,
    scan_id: UUID | None = None,
    filename: str = "contract.jpg",
    text: str = "Full edited OCR text.",
    scanned_at: datetime = BASE_TIME,
    classification: str | None = "contract",
    confidence: float = 0.93,
) -> ScanDetail:
    analysis = None
    if classification is not None:
        analysis = {
            "classification": classification,
            "confidence": confidence,
            "summary": f"Summary for {filename}",
            "tags": ["legal", "Straße"],
            "fields": [{"label": "Date", "value": "2026-07-31"}],
            "provider": "local-llm",
        }
    return ScanDetail.model_validate(
        {
            "id": scan_id or uuid4(),
            "filename": filename,
            "scanned_at": scanned_at,
            "text": text,
            "analysis": analysis,
            "ocr": {
                "source": "server",
                "engine": "tesseract",
                "language": "eng",
                "profile": None,
                "confidence": 88.5,
                "words": 10,
            },
        }
    )


def test_first_and_repeated_bootstrap_create_strict_wal_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "visual-scan.db"
    repository = build_repository(database_path)

    repository.bootstrap()
    repository.bootstrap()

    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        index_names = {row[1] for row in connection.execute("PRAGMA index_list(scans)").fetchall()}
    assert {"idx_scans_scanned_at", "idx_scans_classification"} <= index_names


def test_bootstrap_rejects_directory_path(tmp_path: Path) -> None:
    with pytest.raises(ScanStorageUnavailableError):
        build_repository(tmp_path).bootstrap()


@pytest.mark.parametrize("version", [2, 99])
def test_bootstrap_rejects_unknown_schema_versions(tmp_path: Path, version: int) -> None:
    database_path = tmp_path / "unknown.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(f"PRAGMA user_version = {version}")

    with pytest.raises(ScanStorageUnavailableError):
        build_repository(database_path).bootstrap()


def test_bootstrap_rejects_version_zero_database_with_existing_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unversioned.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE scans (id TEXT PRIMARY KEY)")

    with pytest.raises(ScanStorageUnavailableError):
        build_repository(database_path).bootstrap()


def test_bootstrap_strictly_rejects_missing_index(tmp_path: Path) -> None:
    database_path = tmp_path / "invalid-index.db"
    repository = build_repository(database_path)
    repository.bootstrap()
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX idx_scans_classification")

    with pytest.raises(ScanStorageUnavailableError):
        repository.bootstrap()


def test_records_persist_across_repository_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent.db"
    first = build_repository(database_path)
    first.bootstrap()
    record = build_record()

    first.create(record)
    second = build_repository(database_path)
    second.bootstrap()

    assert second.get(record.id) == record


def test_create_get_list_delete_and_clear(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "operations.db")
    repository.bootstrap()
    first = build_record(filename="first.jpg", classification=None)
    second = build_record(filename="second.jpg")
    repository.create(first)
    repository.create(second)

    page, total = repository.list(
        limit=1,
        offset=1,
        query=None,
        classification=None,
        sort=ScanSort.FILENAME,
        order=SortOrder.ASCENDING,
    )

    assert repository.get(first.id) == first
    assert total == 2
    assert [record.id for record in page] == [second.id]
    assert repository.delete(first.id) is True
    assert repository.delete(first.id) is False
    assert repository.clear() == 1
    assert repository.get(second.id) is None


def test_failed_write_rolls_back_without_losing_existing_record(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "rollback.db")
    repository.bootstrap()
    record = build_record()
    repository.create(record)

    with pytest.raises(sqlite3.IntegrityError):
        repository.create(record)

    page, total = repository.list(
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.DESCENDING,
    )
    assert total == 1
    assert page == [record]


def test_database_rejects_partial_analysis_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "analysis-invariant.db"
    repository = build_repository(database_path)
    repository.bootstrap()

    with sqlite3.connect(database_path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
                INSERT INTO scans (
                    id,
                    filename,
                    scanned_at,
                    text,
                    classification,
                    summary,
                    provider,
                    tags_json,
                    fields_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                str(uuid4()),
                "partial.jpg",
                "2026-07-31T06:30:00.000000Z",
                "text",
                "contract",
                "summary",
                "provider",
                "[]",
                "[]",
            ),
        )


def test_concurrent_writes_do_not_lose_records(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.db"
    repository = build_repository(database_path)
    repository.bootstrap()
    records = [
        build_record(
            filename=f"scan-{index}.jpg",
            scanned_at=BASE_TIME + timedelta(seconds=index),
        )
        for index in range(24)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(repository.create, records))

    page, total = repository.list(
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.ASCENDING,
    )
    assert total == len(records)
    assert {record.id for record in page} == {record.id for record in records}


def test_busy_timeout_maps_locked_database_to_storage_unavailable(tmp_path: Path) -> None:
    database_path = tmp_path / "locked.db"
    repository = build_repository(database_path, busy_timeout_ms=5)
    repository.bootstrap()
    blocker = sqlite3.connect(database_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ScanStorageUnavailableError):
            repository.create(build_record())
    finally:
        blocker.rollback()
        blocker.close()


def test_corrupted_json_row_maps_to_storage_unavailable(tmp_path: Path) -> None:
    database_path = tmp_path / "corrupt-json.db"
    repository = build_repository(database_path)
    repository.bootstrap()
    record = build_record()
    repository.create(record)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE scans SET fields_json = ? WHERE id = ?",
            ("not-json", str(record.id)),
        )

    with pytest.raises(ScanStorageUnavailableError):
        repository.get(record.id)


def test_non_utc_storage_timestamp_maps_to_storage_unavailable(tmp_path: Path) -> None:
    database_path = tmp_path / "corrupt-timestamp.db"
    repository = build_repository(database_path)
    repository.bootstrap()
    record = build_record()
    repository.create(record)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE scans SET scanned_at = ? WHERE id = ?",
            ("2026-07-31T06:30:00", str(record.id)),
        )

    with pytest.raises(ScanStorageUnavailableError):
        repository.get(record.id)


def test_unicode_casefold_search_and_literal_wildcards(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "search.db")
    repository.bootstrap()
    unicode_record = build_record(
        filename="Straße.jpg",
        text="Заявление о работе",
    )
    wildcard_record = build_record(
        filename="100%_complete.jpg",
        text="Literal wildcard test",
        classification=None,
    )
    repository.create(unicode_record)
    repository.create(wildcard_record)

    unicode_page, unicode_total = repository.list(
        limit=50,
        offset=0,
        query="STRASSE",
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.DESCENDING,
    )
    wildcard_page, wildcard_total = repository.list(
        limit=50,
        offset=0,
        query="%_",
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.DESCENDING,
    )

    assert unicode_total == 1
    assert unicode_page[0].id == unicode_record.id
    assert wildcard_total == 1
    assert wildcard_page[0].id == wildcard_record.id


def test_filters_and_normalized_deterministic_sorting(tmp_path: Path) -> None:
    repository = build_repository(tmp_path / "sorting.db")
    repository.bootstrap()
    identifiers = [
        UUID("00000000-0000-4000-8000-000000000003"),
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
    ]
    repository.create(
        build_record(
            scan_id=identifiers[0],
            filename="zeta.jpg",
            classification=None,
        )
    )
    repository.create(
        build_record(
            scan_id=identifiers[1],
            filename="Alpha.jpg",
            classification="contract",
            confidence=0.5,
        )
    )
    repository.create(
        build_record(
            scan_id=identifiers[2],
            filename="alpha.jpg",
            classification="contract",
            confidence=0.5,
        )
    )

    classified, classified_total = repository.list(
        limit=50,
        offset=0,
        query=None,
        classification=DocumentClassification.CONTRACT,
        sort=ScanSort.CONFIDENCE,
        order=SortOrder.ASCENDING,
    )
    unclassified, unclassified_total = repository.list(
        limit=50,
        offset=0,
        query=None,
        classification="unclassified",
        sort=ScanSort.CLASSIFICATION,
        order=SortOrder.ASCENDING,
    )
    all_records, _ = repository.list(
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.FILENAME,
        order=SortOrder.ASCENDING,
    )

    assert classified_total == 2
    assert [record.id for record in classified] == identifiers[1:]
    assert unclassified_total == 1
    assert unclassified[0].id == identifiers[0]
    assert [record.id for record in all_records] == [
        identifiers[1],
        identifiers[2],
        identifiers[0],
    ]


def test_count_and_page_share_one_read_snapshot(tmp_path: Path) -> None:
    database_path = tmp_path / "snapshot.db"
    writer = build_repository(database_path)
    writer.bootstrap()
    initial = build_record(filename="initial.jpg")
    late = build_record(filename="late.jpg")
    writer.create(initial)
    inserted = False

    class HookedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal inserted
            if "SELECT *" in sql and not inserted:
                inserted = True
                writer.create(late)
            return super().execute(sql, parameters)

    def connection_factory(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=HookedConnection)

    reader = build_repository(
        database_path,
        connection_factory=connection_factory,
    )
    page, total = reader.list(
        limit=50,
        offset=0,
        query=None,
        classification=None,
        sort=ScanSort.SCANNED_AT,
        order=SortOrder.ASCENDING,
    )

    assert inserted is True
    assert total == 1
    assert [record.id for record in page] == [initial.id]
    assert writer.get(late.id) == late
