"""
tests.test_mysql_integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests against a real MySQL instance.

Skipped automatically when the MySQL container is not reachable.
Run explicitly with the full suite via:

    pytest -m integration

Or together with unit tests:

    pytest

The DSN is read from the environment variable MYSQL_TEST_DSN, falling back
to the Makefile default (root:secret@127.0.0.1:3307/testshop).
"""

from __future__ import annotations

import os
import pytest

DSN = os.environ.get(
    "MYSQL_TEST_DSN",
    "mysql://root:secret@127.0.0.1:3307/testshop",
)


def _try_connect():
    """Return a live MysqlBackend or None if the server is unreachable."""
    try:
        from dob.db.connection import open_connection
        conn = open_connection(DSN)
        conn.cursor().execute("SELECT 1")
        return conn
    except Exception:
        return None


# Skip the entire module if MySQL is not available
pytestmark = pytest.mark.skipif(
    _try_connect() is None,
    reason="MySQL not reachable — skipping integration tests",
)


@pytest.fixture(scope="module")
def live_conn():
    """Module-scoped live MySQL connection with a throwaway schema."""
    from dob.db.connection import open_connection
    conn = open_connection(DSN)

    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS `_test_items`")
    cur.execute("DROP TABLE IF EXISTS `_test_orders`")
    cur.execute("DROP TABLE IF EXISTS `_test_users`")
    cur.execute("""
        CREATE TABLE `_test_users` (
            id   INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL
        ) ENGINE=InnoDB
    """)
    cur.execute("""
        CREATE TABLE `_test_orders` (
            id      INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            total   DECIMAL(10,2) NOT NULL DEFAULT 0,
            CONSTRAINT fk_to_user FOREIGN KEY (user_id) REFERENCES `_test_users`(id)
        ) ENGINE=InnoDB
    """)
    cur.execute("""
        CREATE TABLE `_test_items` (
            id       INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            order_id INT NOT NULL,
            name     VARCHAR(100) NOT NULL,
            CONSTRAINT fk_to_order FOREIGN KEY (order_id) REFERENCES `_test_orders`(id)
        ) ENGINE=InnoDB
    """)
    # seed rows
    cur.execute("INSERT INTO `_test_users` (name) VALUES ('Alice'), ('Bob')")
    cur.execute("INSERT INTO `_test_orders` (user_id, total) VALUES (1, 99.99), (1, 19.50), (2, 5.00)")
    cur.execute("INSERT INTO `_test_items` (order_id, name) VALUES (1,'Widget'),(1,'Gadget'),(2,'Doohickey')")
    conn._conn.commit()

    yield conn

    # teardown
    cur.execute("DROP TABLE IF EXISTS `_test_items`")
    cur.execute("DROP TABLE IF EXISTS `_test_orders`")
    cur.execute("DROP TABLE IF EXISTS `_test_users`")
    conn._conn.commit()
    conn.close()


# ── schema ────────────────────────────────────────────────────────────────────

def test_schema_loads_tables(live_conn):
    from dob.db.schema import load_schema
    schema = load_schema(live_conn)
    assert "_test_users" in schema.tables
    assert "_test_orders" in schema.tables
    assert "_test_items" in schema.tables


def test_schema_col_cache(live_conn):
    from dob.db.schema import load_schema
    schema = load_schema(live_conn)
    assert schema.col_cache["_test_users"] == ["id", "name"]
    assert schema.col_cache["_test_orders"] == ["id", "user_id", "total"]


def test_schema_fk_from(live_conn):
    from dob.db.schema import load_schema
    schema = load_schema(live_conn)
    fks = schema.fk_from["_test_orders"]
    assert len(fks) == 1
    assert fks[0].from_col == "user_id"
    assert fks[0].to_table == "_test_users"


def test_schema_fk_to(live_conn):
    from dob.db.schema import load_schema
    schema = load_schema(live_conn)
    assert any(fk.from_table == "_test_orders" for fk in schema.fk_to["_test_users"])


# ── queries: identifier quoting ───────────────────────────────────────────────

def test_fetch_all_rows_backtick_quoting(live_conn):
    """MySQL must receive backtick-quoted identifiers — this was the original bug."""
    from dob.db.queries import fetch_all_rows
    cols, rows = fetch_all_rows(live_conn, "_test_users")
    assert cols == ["id", "name"]
    assert len(rows) == 2


def test_fetch_all_rows_with_filter(live_conn):
    from dob.db.queries import fetch_all_rows
    cols, rows = fetch_all_rows(live_conn, "_test_users", filter_info=("name", "Alice"))
    assert len(rows) == 1
    assert rows[0][1] == "Alice"


def test_fetch_all_rows_with_sort(live_conn):
    from dob.db.queries import fetch_all_rows
    _, rows_asc  = fetch_all_rows(live_conn, "_test_orders", sort_info=("total", False))
    _, rows_desc = fetch_all_rows(live_conn, "_test_orders", sort_info=("total", True))
    assert rows_asc[0][2] <= rows_asc[-1][2]
    assert rows_desc[0][2] >= rows_desc[-1][2]


def test_fetch_row_by_pk(live_conn):
    from dob.db.queries import fetch_row_by_pk
    cols, row = fetch_row_by_pk(live_conn, "_test_users", "id", 1)
    assert row is not None
    assert row[1] == "Alice"


def test_fetch_row_by_pk_missing(live_conn):
    from dob.db.queries import fetch_row_by_pk
    _, row = fetch_row_by_pk(live_conn, "_test_users", "id", 9999)
    assert row is None


def test_fetch_related_rows(live_conn):
    from dob.db.queries import fetch_related_rows
    cols, rows = fetch_related_rows(live_conn, "_test_orders", "user_id", 1)
    assert len(rows) == 2
    assert all(r[1] == 1 for r in rows)


def test_fetch_related_rows_in(live_conn):
    from dob.db.queries import fetch_related_rows_in
    cols, rows = fetch_related_rows_in(live_conn, "_test_items", "order_id", [1, 2])
    assert len(rows) == 3


def test_fetch_related_rows_in_empty(live_conn):
    from dob.db.queries import fetch_related_rows_in
    cols, rows = fetch_related_rows_in(live_conn, "_test_items", "order_id", [])
    assert rows == []


def test_get_pk_column(live_conn):
    from dob.db.queries import get_pk_column
    assert get_pk_column(live_conn, "_test_users") == "id"
    assert get_pk_column(live_conn, "_test_orders") == "id"


def test_sql_sort_rows(live_conn):
    from dob.db.queries import fetch_all_rows, sql_sort_rows
    cols, rows = fetch_all_rows(live_conn, "_test_orders")
    sorted_rows = sql_sort_rows(live_conn, "_test_orders", cols, rows, ("total", True))
    totals = [r[2] for r in sorted_rows]
    assert totals == sorted(totals, reverse=True)


# ── lookup cache ──────────────────────────────────────────────────────────────

def test_lookup_not_detected_for_three_col_table(live_conn):
    from dob.db.lookup import LookupCache
    cache = LookupCache(live_conn)
    # _test_orders has 3 cols — not a lookup table
    assert cache.value_column("_test_orders") is None
