"""
dob.db.schema
~~~~~~~~~~~~~
Pure DB-metadata dataclasses and schema loader.

Schema contains only structural information (tables, FK graph, column
names).  User preferences (sorts, filters) live in
dob.settings.preferences.UserPreferences and are NEVER stored here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


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


def load_schema(conn: sqlite3.Connection) -> Schema:
    """Load table list, FK graph and column names from *conn*."""
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
