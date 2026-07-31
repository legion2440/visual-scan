"""SQLite schema definition and strict version-one validation."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1
TABLE_NAME = "scans"

CREATE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE scans (
        id TEXT PRIMARY KEY NOT NULL,
        filename TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
        scanned_at TEXT NOT NULL,
        text TEXT NOT NULL CHECK (length(text) > 0),
        classification TEXT,
        analysis_confidence REAL,
        summary TEXT,
        provider TEXT,
        tags_json TEXT,
        fields_json TEXT,
        ocr_json TEXT,
        CHECK (
            (
                classification IS NULL
                AND analysis_confidence IS NULL
                AND summary IS NULL
                AND provider IS NULL
                AND tags_json IS NULL
                AND fields_json IS NULL
            )
            OR
            (
                classification IS NOT NULL
                AND analysis_confidence IS NOT NULL
                AND classification IN (
                    'invoice',
                    'receipt',
                    'contract',
                    'letter',
                    'form',
                    'report',
                    'statement',
                    'identity_document',
                    'certificate',
                    'business_card',
                    'note',
                    'other'
                )
                AND analysis_confidence BETWEEN 0 AND 1
                AND summary IS NOT NULL
                AND provider IS NOT NULL
                AND tags_json IS NOT NULL
                AND fields_json IS NOT NULL
            )
        )
    )
    """,
    "CREATE INDEX idx_scans_scanned_at ON scans (scanned_at DESC)",
    "CREATE INDEX idx_scans_classification ON scans (classification)",
)

EXPECTED_COLUMNS = (
    ("id", "TEXT", 1, None, 1),
    ("filename", "TEXT", 1, None, 0),
    ("scanned_at", "TEXT", 1, None, 0),
    ("text", "TEXT", 1, None, 0),
    ("classification", "TEXT", 0, None, 0),
    ("analysis_confidence", "REAL", 0, None, 0),
    ("summary", "TEXT", 0, None, 0),
    ("provider", "TEXT", 0, None, 0),
    ("tags_json", "TEXT", 0, None, 0),
    ("fields_json", "TEXT", 0, None, 0),
    ("ocr_json", "TEXT", 0, None, 0),
)

EXPECTED_INDEXES = {
    "idx_scans_scanned_at": (("scanned_at", 1),),
    "idx_scans_classification": (("classification", 0),),
}


def _normalize_sql(value: str) -> str:
    return " ".join(value.split())


def scans_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether a user table named scans already exists."""
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (TABLE_NAME,),
    ).fetchone()
    return row is not None


def create_schema_v1(connection: sqlite3.Connection) -> None:
    """Create schema version one inside the caller's explicit transaction."""
    for statement in CREATE_SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def validate_schema_v1(connection: sqlite3.Connection) -> None:
    """Reject a version-one database whose table or indexes differ."""
    if not scans_table_exists(connection):
        raise ValueError("Schema version 1 is missing the scans table.")

    columns = tuple(
        (row["name"], row["type"].upper(), row["notnull"], row["dflt_value"], row["pk"])
        for row in connection.execute("PRAGMA table_info(scans)").fetchall()
    )
    if columns != EXPECTED_COLUMNS:
        raise ValueError("The scans table does not match schema version 1.")

    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE_NAME,),
    ).fetchone()
    if table_sql is None or _normalize_sql(table_sql["sql"]) != _normalize_sql(
        CREATE_SCHEMA_STATEMENTS[0]
    ):
        raise ValueError("The scans table definition does not match schema version 1.")

    index_rows = connection.execute("PRAGMA index_list(scans)").fetchall()
    named_indexes = {
        row["name"]: row for row in index_rows if not row["name"].startswith("sqlite_autoindex_")
    }
    if set(named_indexes) != set(EXPECTED_INDEXES):
        raise ValueError("The scans indexes do not match schema version 1.")

    for index_name, expected_columns in EXPECTED_INDEXES.items():
        index = named_indexes[index_name]
        if index["unique"] != 0 or index["partial"] != 0 or index["origin"] != "c":
            raise ValueError(f"Index {index_name} does not match schema version 1.")
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        expected_sql = next(
            statement
            for statement in CREATE_SCHEMA_STATEMENTS
            if statement.startswith(f"CREATE INDEX {index_name} ")
        )
        if index_sql is None or _normalize_sql(index_sql["sql"]) != _normalize_sql(expected_sql):
            raise ValueError(f"Index {index_name} does not match schema version 1.")
        actual_columns = tuple(
            (row["name"], row["desc"])
            for row in connection.execute(f'PRAGMA index_xinfo("{index_name}")').fetchall()
            if row["key"] == 1
        )
        if actual_columns != expected_columns:
            raise ValueError(f"Index {index_name} does not match schema version 1.")
