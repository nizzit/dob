"""
tests.test_mysql_backend
~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the MySQL backend path using in-memory mock connections.

We avoid a real MySQL server by building lightweight fakes that mimic the
PyMySQL cursor / connection API and carry ``db_type = "mysql"``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dob.db.backend import MysqlBackend
from dob.db.connection import open_connection, _open_sqlite
from dob.db.lookup import LookupCache
from dob.db.queries import (
    fetch_all_rows,
    fetch_related_rows,
    fetch_related_rows_in,
    fetch_row_by_pk,
    get_pk_column,
    get_pk_columns,
    order_clause,
    sql_sort_rows,
)
from dob.db.schema import load_schema, Schema


# ── helpers ───────────────────────────────────────────────────────────────────


class FakeCursor:
    """Minimal DB-API 2 cursor stub."""

    def __init__(self, rows: list[tuple], description: list[tuple] | None = None) -> None:
        self._rows = rows
        self.description = description or []
        self._executed: list[tuple] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        self._executed.append((sql, params))

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeMysqlRaw:
    """Minimal PyMySQL-like raw connection."""

    def __init__(self, cursors: list[FakeCursor]) -> None:
        self._cursors = iter(cursors)

    def cursor(self) -> FakeCursor:
        return next(self._cursors)

    def close(self) -> None:
        pass


def mysql_conn(*cursors: FakeCursor) -> MysqlBackend:
    return MysqlBackend(FakeMysqlRaw(list(cursors)))


# ── backend.py ────────────────────────────────────────────────────────────────


def test_mysql_backend_db_type() -> None:
    raw = MagicMock()
    be = MysqlBackend(raw)
    assert be.db_type == "mysql"


def test_mysql_backend_cursor_delegates() -> None:
    raw = MagicMock()
    be = MysqlBackend(raw)
    be.cursor()
    raw.cursor.assert_called_once()


def test_mysql_backend_execute_delegates() -> None:
    cur = MagicMock()
    raw = MagicMock()
    raw.cursor.return_value = cur
    be = MysqlBackend(raw)
    be.execute("SELECT 1")
    cur.execute.assert_called_once_with("SELECT 1", ())


def test_mysql_backend_close_delegates() -> None:
    raw = MagicMock()
    be = MysqlBackend(raw)
    be.close()
    raw.close.assert_called_once()


# ── connection.py DSN parsing ─────────────────────────────────────────────────


def test_open_connection_returns_sqlite_for_path(tmp_path) -> None:
    from dob.db.backend import SqliteBackend
    db = tmp_path / "x.db"
    db.touch()
    conn = open_connection(str(db))
    assert conn.db_type == "sqlite"
    assert isinstance(conn, SqliteBackend)
    conn.close()


def test_open_connection_mysql_calls_pymysql() -> None:
    """open_connection("mysql://...") must call pymysql.connect with correct args."""
    fake_conn = MagicMock()
    with patch("pymysql.connect", return_value=fake_conn) as mock_connect:
        conn = open_connection("mysql://alice:secret@db.example.com:3307/mydb")
    mock_connect.assert_called_once()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["host"] == "db.example.com"
    assert kwargs["port"] == 3307
    assert kwargs["user"] == "alice"
    assert kwargs["password"] == "secret"
    assert kwargs["database"] == "mydb"
    assert isinstance(conn, MysqlBackend)


def test_open_connection_mysql_default_port() -> None:
    fake_conn = MagicMock()
    with patch("pymysql.connect", return_value=fake_conn) as mock_connect:
        open_connection("mysql://root:pw@localhost/testdb")
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["port"] == 3306
    assert kwargs["host"] == "localhost"


def test_open_connection_mysql_no_password() -> None:
    fake_conn = MagicMock()
    with patch("pymysql.connect", return_value=fake_conn) as mock_connect:
        open_connection("mysql://admin@localhost/testdb")
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["user"] == "admin"
    assert kwargs["password"] == ""


# ── schema.py MySQL path ──────────────────────────────────────────────────────


def _make_schema_conn(
    db_name: str = "testdb",
    tables: list[str] | None = None,
    col_info: dict[str, list[str]] | None = None,
    fk_rows: list[tuple] | None = None,
) -> MysqlBackend:
    """Build a fake MySQL backend pre-loaded with schema query responses."""
    if tables is None:
        tables = ["authors", "books"]
    if col_info is None:
        col_info = {
            "authors": ["id", "name"],
            "books": ["id", "author_id", "title"],
        }
    if fk_rows is None:
        fk_rows = [("books", "author_id", "authors", "id")]

    cursors: list[FakeCursor] = []

    # 1. SELECT DATABASE()
    cursors.append(FakeCursor([(db_name,)]))

    # 2. SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    cursors.append(FakeCursor([(t,) for t in tables]))

    # 3. One cursor per table for column names
    for table in tables:
        cols = col_info.get(table, ["id"])
        cursors.append(FakeCursor([(c,) for c in cols]))

    # 4. FK query
    cursors.append(FakeCursor(fk_rows))

    return mysql_conn(*cursors)


def test_load_schema_mysql_tables() -> None:
    conn = _make_schema_conn()
    schema = load_schema(conn)
    assert "authors" in schema.tables
    assert "books" in schema.tables


def test_load_schema_mysql_col_cache() -> None:
    conn = _make_schema_conn()
    schema = load_schema(conn)
    assert schema.col_cache["authors"] == ["id", "name"]
    assert schema.col_cache["books"] == ["id", "author_id", "title"]


def test_load_schema_mysql_fk_from() -> None:
    conn = _make_schema_conn()
    schema = load_schema(conn)
    fks = schema.fk_from["books"]
    assert len(fks) == 1
    fk = fks[0]
    assert fk.from_table == "books"
    assert fk.from_col == "author_id"
    assert fk.to_table == "authors"
    assert fk.to_col == "id"


def test_load_schema_mysql_fk_to() -> None:
    conn = _make_schema_conn()
    schema = load_schema(conn)
    assert len(schema.fk_to["authors"]) == 1
    assert schema.fk_to["authors"][0].from_table == "books"


def test_load_schema_mysql_no_fks() -> None:
    conn = _make_schema_conn(fk_rows=[])
    schema = load_schema(conn)
    assert schema.fk_from["books"] == []
    assert schema.fk_to["authors"] == []


# ── queries.py MySQL path ─────────────────────────────────────────────────────


def _qconn(rows: list[tuple], description: list[tuple]) -> MysqlBackend:
    """Single-shot query backend."""
    cur = FakeCursor(rows, description)
    raw = MagicMock()
    raw.cursor.return_value = cur
    be = MysqlBackend(raw)
    return be


def test_get_pk_columns_mysql() -> None:
    # Two calls to cursor(): DATABASE() + KEY_COLUMN_USAGE query
    cur_db = FakeCursor([("testdb",)])
    cur_pk = FakeCursor([("id",)])
    raw = FakeMysqlRaw([cur_db, cur_pk])
    conn = MysqlBackend(raw)
    pks = get_pk_columns(conn, "authors")
    assert pks == {"id"}


def test_get_pk_column_mysql_single() -> None:
    cur_db = FakeCursor([("testdb",)])
    cur_pk = FakeCursor([("id",)])
    raw = FakeMysqlRaw([cur_db, cur_pk])
    conn = MysqlBackend(raw)
    pk = get_pk_column(conn, "authors")
    assert pk == "id"


def test_get_pk_column_mysql_composite_returns_none() -> None:
    cur_db = FakeCursor([("testdb",)])
    cur_pk = FakeCursor([("col_a",), ("col_b",)])
    raw = FakeMysqlRaw([cur_db, cur_pk])
    conn = MysqlBackend(raw)
    pk = get_pk_column(conn, "composite_table")
    assert pk is None


def test_fetch_all_rows_mysql_uses_percent_placeholder() -> None:
    """fetch_all_rows with a filter should use %s not ? for MySQL."""
    rows = [(1, "Alice")]
    desc = [("id",), ("name",)]
    cur = FakeCursor(rows, desc)
    raw = MagicMock()
    raw.cursor.return_value = cur
    conn = MysqlBackend(raw)
    cols, fetched = fetch_all_rows(conn, "authors", filter_info=("id", 1))
    assert cols == ["id", "name"]
    assert fetched == rows
    # Verify the SQL used %s
    sql, params = cur._executed[0]
    assert "%s" in sql
    assert "?" not in sql


def test_fetch_row_by_pk_mysql() -> None:
    rows = [(1, "Alice")]
    desc = [("id",), ("name",)]
    cur = FakeCursor(rows, desc)
    raw = MagicMock()
    raw.cursor.return_value = cur
    conn = MysqlBackend(raw)
    cols, row = fetch_row_by_pk(conn, "authors", "id", 1)
    assert row == (1, "Alice")
    sql, params = cur._executed[0]
    assert "%s" in sql


def test_fetch_related_rows_mysql() -> None:
    rows = [(1, 1, "Alpha"), (2, 1, "Beta")]
    desc = [("id",), ("author_id",), ("title",)]
    cur = FakeCursor(rows, desc)
    raw = MagicMock()
    raw.cursor.return_value = cur
    conn = MysqlBackend(raw)
    cols, fetched = fetch_related_rows(conn, "books", "author_id", 1)
    assert len(fetched) == 2
    sql, _ = cur._executed[0]
    assert "%s" in sql


def test_fetch_related_rows_in_mysql() -> None:
    rows = [(1, 1, "Alpha")]
    desc = [("id",), ("author_id",), ("title",)]
    cur = FakeCursor(rows, desc)
    raw = MagicMock()
    raw.cursor.return_value = cur
    conn = MysqlBackend(raw)
    cols, fetched = fetch_related_rows_in(conn, "books", "author_id", [1])
    assert len(fetched) == 1
    sql, _ = cur._executed[0]
    assert "%s" in sql


# ── lookup.py MySQL path ──────────────────────────────────────────────────────


def _lookup_conn(info_rows: list[tuple], value_rows: list[tuple] | None = None) -> MysqlBackend:
    """Build a backend for LookupCache tests."""
    cursors: list[FakeCursor] = []
    # DATABASE() call
    cursors.append(FakeCursor([("testdb",)]))
    # INFORMATION_SCHEMA.COLUMNS
    cursors.append(FakeCursor(info_rows))
    if value_rows is not None:
        # DATABASE() call for _load_value is not needed (no extra DB() call)
        cursors.append(FakeCursor(value_rows))
    return mysql_conn(*cursors)


def test_lookup_cache_mysql_detects_lookup_table() -> None:
    # Two-column table: id(PRI), name(non-PRI)
    conn = _lookup_conn([("id", "PRI"), ("name", "")])
    cache = LookupCache(conn)
    assert cache.value_column("statuses") == "name"


def test_lookup_cache_mysql_non_lookup_three_cols() -> None:
    # Three-column table — not a lookup
    conn = _lookup_conn([("id", "PRI"), ("name", ""), ("extra", "")])
    cache = LookupCache(conn)
    assert cache.value_column("big_table") is None


def test_lookup_cache_mysql_pk_not_named_id() -> None:
    conn = _lookup_conn([("uuid", "PRI"), ("name", "")])
    cache = LookupCache(conn)
    assert cache.value_column("odd_table") is None


def test_lookup_cache_mysql_fetch_value() -> None:
    # First detection call, then fetch
    cur_db1 = FakeCursor([("testdb",)])
    cur_info = FakeCursor([("id", "PRI"), ("name", "")])
    cur_val = FakeCursor([("active",)])
    conn = MysqlBackend(FakeMysqlRaw([cur_db1, cur_info, cur_val]))
    cache = LookupCache(conn)
    val = cache.fetch_value("statuses", 1)
    assert val == "active"
