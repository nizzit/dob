"""
dob.db.schema
~~~~~~~~~~~~~
Pure DB-metadata dataclasses and schema loader.

Schema contains only structural information (tables, FK graph, column
names).  User preferences (sorts, filters) live in
dob.settings.preferences.UserPreferences and are NEVER stored here.

Supports both SQLite (via PRAGMA) and MySQL (via INFORMATION_SCHEMA).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FKInfo:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    virtual: bool = False  # True = user-defined link


@dataclass
class Schema:
    tables: list[str]
    fk_from: dict[str, list[FKInfo]] = field(default_factory=dict)
    fk_to: dict[str, list[FKInfo]] = field(default_factory=dict)
    col_cache: dict[str, list[str]] = field(default_factory=dict)  # table→cols
    # db_path is set by the application layer after loading so that
    # VirtualLinks can locate the settings file without passing it separately.
    # It is intentionally NOT used in domain/traversal logic.
    db_path: str = ""


def load_schema(conn: Any) -> Schema:
    """Load table list, FK graph and column names from *conn*.

    Dispatches to the correct implementation based on ``conn.db_type``.
    If *conn* has no ``db_type`` attribute it is treated as SQLite (backwards
    compatibility with raw ``sqlite3.Connection`` objects in tests).
    """
    db_type = getattr(conn, "db_type", "sqlite")
    if db_type == "mysql":
        return _load_schema_mysql(conn)
    return _load_schema_sqlite(conn)


# ── SQLite ────────────────────────────────────────────────────────────────────


def _load_schema_sqlite(conn: Any) -> Schema:
    """Load schema from a SQLite connection using PRAGMA calls."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    schema = Schema(tables=tables)

    # cache column names for each table
    for table in tables:
        cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
        schema.col_cache[table] = [d[0] for d in cur.description]

    for table in tables:
        schema.fk_from[table] = []
        cur.execute(f"PRAGMA foreign_key_list('{table}')")
        for row in cur.fetchall():
            fk = FKInfo(
                from_table=table, from_col=row[3], to_table=row[2], to_col=row[4]
            )
            schema.fk_from[table].append(fk)

    for table in tables:
        schema.fk_to[table] = []
    for table in tables:
        for fk in schema.fk_from[table]:
            schema.fk_to[fk.to_table].append(fk)

    return schema


# ── MySQL ─────────────────────────────────────────────────────────────────────


def _load_schema_mysql(conn: Any) -> Schema:
    """Load schema from a MySQL connection using INFORMATION_SCHEMA."""

    # Determine the current database name
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    row = cur.fetchone()
    db_name: str = row[0] if row and row[0] else ""

    # Tables
    cur = conn.cursor()
    cur.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
        "ORDER BY TABLE_NAME",
        (db_name,),
    )
    tables = [r[0] for r in cur.fetchall()]

    schema = Schema(tables=tables)

    # Column names
    for table in tables:
        cur = conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (db_name, table),
        )
        schema.col_cache[table] = [r[0] for r in cur.fetchall()]

    # Foreign keys via INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    for table in tables:
        schema.fk_from[table] = []

    cur = conn.cursor()
    cur.execute(
        "SELECT kcu.TABLE_NAME, kcu.COLUMN_NAME, "
        "       kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
        "JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
        "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
        "  AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA "
        "  AND tc.TABLE_NAME = kcu.TABLE_NAME "
        "WHERE kcu.TABLE_SCHEMA = %s "
        "  AND tc.CONSTRAINT_TYPE = 'FOREIGN KEY' "
        "  AND kcu.REFERENCED_TABLE_NAME IS NOT NULL",
        (db_name,),
    )
    for row in cur.fetchall():
        from_table, from_col, to_table, to_col = row
        if from_table in schema.fk_from:
            fk = FKInfo(
                from_table=from_table,
                from_col=from_col,
                to_table=to_table,
                to_col=to_col,
            )
            schema.fk_from[from_table].append(fk)

    for table in tables:
        schema.fk_to[table] = []
    for table in tables:
        for fk in schema.fk_from[table]:
            if fk.to_table in schema.fk_to:
                schema.fk_to[fk.to_table].append(fk)

    return schema
