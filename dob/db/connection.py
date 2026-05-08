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


_MYSQL_SCHEME = "mysql://"


class MysqlCredentials:
    """Parsed MySQL DSN components."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    # rebuild DSN from components --------------------------------

    def to_dsn(self, *, database: str | None = None) -> str:
        """Return a ``mysql://`` DSN string.

        If *database* is given, override the stored database name.
        """
        db = database if database is not None else self.database

        userinfo = self.user
        if self.password:
            userinfo += f":{self.password}"

        hostport = self.host
        if self.port != 3306:
            hostport += f":{self.port}"

        return f"mysql://{userinfo}@{hostport}/{db}"


def parse_mysql_dsn(dsn: str) -> MysqlCredentials:
    """Parse ``mysql://user:pass@host[:port]/dbname`` into components.

    The *database* part may be empty (``mysql://...host/`` or
    ``mysql://...host`` without trailing slash).
    """
    if not dsn.startswith(_MYSQL_SCHEME):
        raise ValueError(f"DSN must start with {_MYSQL_SCHEME}")

    rest = dsn[len(_MYSQL_SCHEME):]

    # user:pass @ hostpart
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

    # host:port / database
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

    return MysqlCredentials(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


def _open_mysql(dsn: str) -> MysqlBackend:
    """Parse DSN and open a PyMySQL connection."""
    creds = parse_mysql_dsn(dsn)

    try:
        import pymysql  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pymysql is required for MySQL support. "
            "Install it with: pip install pymysql"
        ) from exc

    raw = pymysql.connect(
        host=creds.host,
        port=creds.port,
        user=creds.user,
        password=creds.password,
        database=creds.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        autocommit=True,
    )
    return MysqlBackend(raw)


def open_mysql_bare(dsn: str) -> MysqlBackend:
    """Open a MySQL connection **without** selecting a database.

    Returns a :class:`MysqlBackend` connected only to the server,
    useful for running ``SHOW DATABASES`` so the user can pick one.
    """
    creds = parse_mysql_dsn(dsn)

    try:
        import pymysql  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pymysql is required for MySQL support. "
            "Install it with: pip install pymysql"
        ) from exc

    raw = pymysql.connect(
        host=creds.host,
        port=creds.port,
        user=creds.user,
        password=creds.password,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        autocommit=True,
    )
    return MysqlBackend(raw)
