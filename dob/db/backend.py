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
    """Wraps a PyMySQL connection to match the DBConnection interface.

    PyMySQL connections are **not thread-safe**.  Never use the same
    MysqlBackend from multiple threads simultaneously.  Background workers
    should call ``clone()`` to obtain a private connection for the duration
    of the thread, then ``close()`` it when done.
    """

    db_type: str = "mysql"

    def __init__(self, raw_conn: Any, *, _connect_kwargs: dict | None = None) -> None:
        self._conn = raw_conn
        # Stored so clone() can open an identical fresh connection.
        self._connect_kwargs: dict = _connect_kwargs or {}

    def cursor(self) -> Any:
        return self._conn.cursor()

    def execute(self, sql: str, params: Any = ()) -> Any:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def close(self) -> None:
        self._conn.close()

    def clone(self) -> "MysqlBackend":
        """Open a **new** independent connection with identical credentials.

        Use this in background threads so each thread has its own socket
        and packet-sequence state.
        """
        if not self._connect_kwargs:
            raise RuntimeError(
                "Cannot clone a MysqlBackend that was not created via open_connection(). "
                "Pass the DSN down to the worker and call open_connection() directly."
            )
        import pymysql  # type: ignore[import]

        raw = pymysql.connect(**self._connect_kwargs)
        return MysqlBackend(raw, _connect_kwargs=self._connect_kwargs)


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


# ── thread-safe connection helpers ────────────────────────────────────────────


def thread_conn(conn: Any) -> Any:
    """Return a private connection safe for use in a background thread.

    MySQL (PyMySQL) is **not thread-safe** — sharing one connection across
    threads corrupts the packet-sequence state.  This function opens a fresh
    independent clone for MySQL.  SQLite with WAL + check_same_thread=False
    is safe for concurrent reads, so the same object is returned as-is.
    """
    if isinstance(conn, MysqlBackend):
        return conn.clone()
    return conn


def close_thread_conn(thread_conn_obj: Any, original_conn: Any) -> None:
    """Close *thread_conn_obj* only if it is a freshly cloned MySQL connection."""
    if thread_conn_obj is not original_conn:
        try:
            thread_conn_obj.close()
        except Exception:
            pass
