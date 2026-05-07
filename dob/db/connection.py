"""
dob.db.connection
~~~~~~~~~~~~~~~~~
Single entry-point for opening a database connection.

Accepts either:
  • A filesystem path (str or Path) → opens SQLite in WAL + multi-thread mode.
  • A MySQL DSN of the form ``mysql://user:pass@host[:port]/dbname``
    → opens a PyMySQL connection.

Returns a :class:`dob.db.backend.DBConnection`-compatible object.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from dob.db.backend import MysqlBackend, _wrap_sqlite


def open_connection(dsn_or_path: Union[str, Path]) -> object:
    """
    Return an open database connection.

    Parameters
    ----------
    dsn_or_path:
        Either a path to a SQLite ``.db`` file **or** a MySQL DSN string
        starting with ``mysql://``.

    Returns
    -------
    A :class:`dob.db.backend.DBConnection`-compatible object.
    """
    s = str(dsn_or_path)
    if s.startswith("mysql://"):
        return _open_mysql(s)
    return _open_sqlite(dsn_or_path)


# ── SQLite ────────────────────────────────────────────────────────────────────


def _open_sqlite(path: Union[str, Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return _wrap_sqlite(conn)


# ── MySQL ─────────────────────────────────────────────────────────────────────


def _open_mysql(dsn: str) -> MysqlBackend:
    """Parse ``mysql://user:pass@host[:port]/dbname`` and open a PyMySQL conn."""
    try:
        import pymysql  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pymysql is required for MySQL support. "
            "Install it with: pip install pymysql"
        ) from exc

    # ── parse DSN ─────────────────────────────────────────────────────────────
    # Strip scheme
    rest = dsn[len("mysql://"):]

    # Split user:pass from host/db
    if "@" in rest:
        userinfo, hostpart = rest.rsplit("@", 1)
    else:
        userinfo, hostpart = "", rest

    user, password = "", ""
    if userinfo:
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
        else:
            user = userinfo

    # Split host:port from /dbname
    if "/" in hostpart:
        hostport, database = hostpart.split("/", 1)
    else:
        hostport, database = hostpart, ""

    host, port = "127.0.0.1", 3306
    if ":" in hostport:
        host, port_str = hostport.rsplit(":", 1)
        port = int(port_str)
    elif hostport:
        host = hostport

    raw = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        autocommit=True,
    )
    return MysqlBackend(raw)
