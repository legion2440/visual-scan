"""SQLite schema version two, strict validation, and v1 migration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

SCHEMA_VERSION = 2

V1_SCANS_TABLE = """
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
"""

V1_STATEMENTS = (
    V1_SCANS_TABLE,
    "CREATE INDEX idx_scans_scanned_at ON scans (scanned_at DESC)",
    "CREATE INDEX idx_scans_classification ON scans (classification)",
)


def create_schema_v1(connection: sqlite3.Connection) -> None:
    """Create the historical schema for migration fixtures and compatibility tests."""
    for statement in V1_STATEMENTS:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")


CREATE_TABLE_STATEMENTS = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY NOT NULL,
        username TEXT NOT NULL UNIQUE
            CHECK (length(username) BETWEEN 3 AND 32)
            CHECK (username = lower(username))
            CHECK (username NOT GLOB '*[^a-z0-9._-]*'),
        password_hash TEXT NOT NULL CHECK (length(password_hash) > 0),
        created_at TEXT NOT NULL,
        is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
        is_initial_user INTEGER NOT NULL CHECK (is_initial_user IN (0, 1))
    )
    """,
    """
    CREATE TABLE auth_sessions (
        token_hash BLOB PRIMARY KEY NOT NULL CHECK (length(token_hash) = 32),
        user_id TEXT NOT NULL,
        csrf_hash BLOB NOT NULL CHECK (length(csrf_hash) = 32),
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE auth_rate_limits (
        scope TEXT NOT NULL,
        key_hash BLOB NOT NULL CHECK (length(key_hash) = 32),
        window_started_at TEXT NOT NULL,
        failure_count INTEGER NOT NULL CHECK (failure_count >= 0),
        blocked_until TEXT,
        PRIMARY KEY (scope, key_hash)
    )
    """,
    """
    CREATE TABLE scans (
        id TEXT PRIMARY KEY NOT NULL,
        owner_id TEXT NOT NULL,
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
        FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
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
    V1_SCANS_TABLE.replace("CREATE TABLE scans", "CREATE TABLE legacy_scans", 1),
)

CREATE_INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX idx_users_initial ON users (is_initial_user) WHERE is_initial_user = 1",
    "CREATE INDEX idx_auth_sessions_user_id ON auth_sessions (user_id)",
    "CREATE INDEX idx_auth_sessions_expires_at ON auth_sessions (expires_at)",
    "CREATE INDEX idx_scans_owner_scanned_at ON scans (owner_id, scanned_at DESC)",
    "CREATE INDEX idx_scans_owner_classification ON scans (owner_id, classification)",
)

V1_EXPECTED_COLUMNS = (
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

EXPECTED_TABLES = {"users", "auth_sessions", "auth_rate_limits", "scans", "legacy_scans"}
EXPECTED_INDEXES = {
    "idx_users_initial": ("users", True, True, (("is_initial_user", 0),)),
    "idx_auth_sessions_user_id": ("auth_sessions", False, False, (("user_id", 0),)),
    "idx_auth_sessions_expires_at": ("auth_sessions", False, False, (("expires_at", 0),)),
    "idx_scans_owner_scanned_at": (
        "scans",
        False,
        False,
        (("owner_id", 0), ("scanned_at", 1)),
    ),
    "idx_scans_owner_classification": (
        "scans",
        False,
        False,
        (("owner_id", 0), ("classification", 0)),
    ),
}


def _normalize_sql(value: str) -> str:
    return " ".join(value.split())


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _user_schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (row["name"], row["type"].upper(), row["notnull"], row["dflt_value"], row["pk"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    )


def validate_schema_v1(connection: sqlite3.Connection) -> None:
    """Strictly validate the only schema accepted for migration."""
    if _user_tables(connection) != {"scans"}:
        raise ValueError("Schema version 1 has unexpected tables.")
    if _user_schema_objects(connection) != {
        ("table", "scans"),
        ("index", "idx_scans_scanned_at"),
        ("index", "idx_scans_classification"),
    }:
        raise ValueError("Schema version 1 has unexpected objects.")
    if _table_columns(connection, "scans") != V1_EXPECTED_COLUMNS:
        raise ValueError("The scans table does not match schema version 1.")
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scans'"
    ).fetchone()
    if table is None or _normalize_sql(table[0]) != _normalize_sql(V1_SCANS_TABLE):
        raise ValueError("The scans table definition does not match schema version 1.")

    indexes = {
        row["name"]: row
        for row in connection.execute("PRAGMA index_list(scans)").fetchall()
        if not row["name"].startswith("sqlite_autoindex_")
    }
    if set(indexes) != {"idx_scans_scanned_at", "idx_scans_classification"}:
        raise ValueError("The scans indexes do not match schema version 1.")
    for statement in V1_STATEMENTS[1:]:
        name = statement.split()[2]
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
        if row is None or _normalize_sql(row[0]) != _normalize_sql(statement):
            raise ValueError(f"Index {name} does not match schema version 1.")


def _create_schema_v2(connection: sqlite3.Connection) -> None:
    for statement in (*CREATE_TABLE_STATEMENTS, *CREATE_INDEX_STATEMENTS):
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate_v1(connection: sqlite3.Connection) -> None:
    legacy_statement = CREATE_TABLE_STATEMENTS[-1]
    for statement in CREATE_TABLE_STATEMENTS[:3]:
        connection.execute(statement)
    connection.execute(legacy_statement)
    connection.execute(
        """
        INSERT INTO legacy_scans (
            id, filename, scanned_at, text, classification, analysis_confidence,
            summary, provider, tags_json, fields_json, ocr_json
        )
        SELECT
            id, filename, scanned_at, text, classification, analysis_confidence,
            summary, provider, tags_json, fields_json, ocr_json
        FROM scans
        """
    )
    before = int(connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
    after = int(connection.execute("SELECT COUNT(*) FROM legacy_scans").fetchone()[0])
    if before != after:
        raise ValueError("The legacy scan migration did not preserve row count.")
    connection.execute("DROP TABLE scans")
    connection.execute(CREATE_TABLE_STATEMENTS[3])
    for statement in CREATE_INDEX_STATEMENTS:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def bootstrap_schema(
    connection: sqlite3.Connection,
    *,
    transaction: Callable[..., AbstractContextManager[None]],
) -> None:
    """Create v2 or migrate the exact version-one archive atomically."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == 0:
        if _user_tables(connection):
            raise ValueError("An unversioned database contains unexpected tables.")
        with transaction(connection, immediate=True):
            _create_schema_v2(connection)
        return
    if version == 1:
        validate_schema_v1(connection)
        with transaction(connection, immediate=True):
            _migrate_v1(connection)
        return
    if version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported SQLite schema version: {version}")


def validate_schema_v2(connection: sqlite3.Connection) -> None:
    """Reject any drift in version-two tables, indexes, or foreign keys."""
    if int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
        raise ValueError("SQLite schema version is not 2.")
    if _user_tables(connection) != EXPECTED_TABLES:
        raise ValueError("Schema version 2 has missing or unexpected tables.")
    expected_objects = {
        *(("table", table) for table in EXPECTED_TABLES),
        *(("index", index) for index in EXPECTED_INDEXES),
    }
    if _user_schema_objects(connection) != expected_objects:
        raise ValueError("Schema version 2 has missing or unexpected objects.")

    expected_sql = {
        statement.split("CREATE TABLE ", 1)[1].split(" ", 1)[0]: statement
        for statement in CREATE_TABLE_STATEMENTS
    }
    for table, statement in expected_sql.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if row is None or _normalize_sql(row[0]) != _normalize_sql(statement):
            raise ValueError(f"Table {table} does not match schema version 2.")

    indexes: dict[str, tuple[str, sqlite3.Row]] = {}
    for table in EXPECTED_TABLES:
        for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
            if not row["name"].startswith("sqlite_autoindex_"):
                indexes[row["name"]] = (table, row)
    if set(indexes) != set(EXPECTED_INDEXES):
        raise ValueError("Schema version 2 indexes do not match.")
    statement_by_name = {
        statement.split(" INDEX ", 1)[1].split(" ", 1)[0]: statement
        for statement in CREATE_INDEX_STATEMENTS
    }
    for name, (table, unique, partial, columns) in EXPECTED_INDEXES.items():
        actual_table, row = indexes[name]
        if actual_table != table:
            raise ValueError(f"Index {name} belongs to the wrong table.")
        if row["unique"] != int(unique) or row["partial"] != int(partial):
            raise ValueError(f"Index {name} flags do not match schema version 2.")
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
        if sql_row is None or _normalize_sql(sql_row[0]) != _normalize_sql(statement_by_name[name]):
            raise ValueError(f"Index {name} SQL does not match schema version 2.")
        actual_columns = tuple(
            (item["name"], item["desc"])
            for item in connection.execute(f'PRAGMA index_xinfo("{name}")').fetchall()
            if item["key"] == 1
        )
        if actual_columns != columns:
            raise ValueError(f"Index {name} columns do not match schema version 2.")
        if row["origin"] != "c" or row["name"] != name:
            raise ValueError(f"Index {name} origin does not match schema version 2.")

    expected_foreign_keys = {
        "users": set(),
        "auth_sessions": {("user_id", "users", "id", "CASCADE")},
        "auth_rate_limits": set(),
        "scans": {("owner_id", "users", "id", "CASCADE")},
        "legacy_scans": set(),
    }
    for table, expected in expected_foreign_keys.items():
        actual = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        }
        if actual != expected:
            raise ValueError(f"Table {table} foreign keys do not match schema version 2.")
