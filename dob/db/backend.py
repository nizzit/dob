"""
dob.db.backend
~~~~~~~~~~~~~~
Thin protocol / wrapper that unifies SQLite and MySQL connections behind
a single interface consumed by the rest of dob.

Both backends expose:

  conn.cursor()              → DB-API 2 cursor
  conn.execute(sql, params)  → cursor (SQLite-style convenience)
  conn.close()
  conn.db_type               → "sqlite" | "mysql"

SQLite connections are the native sqlite3.Connection objects with two
shim attributes monkey-patched onto them.  MySQL connections are wrapped
in MysqlBackend.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol, runtime_checkable


# ── protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class DBConnection(Protocol):
    """Minimal interface all dob database backends must satisfy."""

    db_type: str  # "sqlite" | "mysql"

    def cursor(self) -> Any: ...
    def execute(self, sql: str, params: Any = ()) -> Any: ...
    def close(self) -> None: ...


# ── MySQL wrapper ─────────────────────────────────────────────────────────────


class MysqlBackend:
    """Wraps a PyMySQL connection to match the DBConnection interface."""

    db_type: str = "mysql"

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    def cursor(self) -> Any:
        return self._conn.cursor()

    def execute(self, sql: str, params: Any = ()) -> Any:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def close(self) -> None:
        self._conn.close()


# ── SQLite shim ───────────────────────────────────────────────────────────────


class SqliteBackend:
    """Thin wrapper around sqlite3.Connection that adds the db_type attribute."""

    db_type: str = "sqlite"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self) -> Any:
        return self._conn.cursor()

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()


def _wrap_sqlite(conn: sqlite3.Connection) -> "SqliteBackend":
    """Wrap a SQLite connection in SqliteBackend so it carries db_type."""
    return SqliteBackend(conn)
