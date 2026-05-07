"""
dob.db.queries
~~~~~~~~~~~~~~
All SQL read helpers.  No business logic, no settings, no UI.

Supports both SQLite (PRAGMA-based PK detection) and MySQL
(INFORMATION_SCHEMA-based PK detection).
"""

from __future__ import annotations

from typing import Any


# ── ordering ──────────────────────────────────────────────────────────────────


def order_clause(sort_info: tuple[str, bool] | None, conn: Any = None) -> str:
    """Return an ORDER BY clause string (empty if *sort_info* is None)."""
    if not sort_info:
        return ""
    col = _q(conn, sort_info[0]) if conn is not None else f'"{sort_info[0]}"'
    return f' ORDER BY {col} {"DESC" if sort_info[1] else "ASC"}'


# ── PK helpers ────────────────────────────────────────────────────────────────


def get_pk_column(conn: Any, table: str) -> str | None:
    """Return the single PK column name, or None for composite / no PK."""
    pks = get_pk_columns(conn, table)
    if len(pks) == 1:
        return next(iter(pks))
    return None


def get_pk_columns(conn: Any, table: str) -> set[str]:
    """Return all PK column names (composite PK support)."""
    db_type = getattr(conn, "db_type", "sqlite")
    if db_type == "mysql":
        return _get_pk_columns_mysql(conn, table)
    return _get_pk_columns_sqlite(conn, table)


def _get_pk_columns_sqlite(conn: Any, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    return {row[1] for row in cur.fetchall() if row[5] > 0}


def _get_pk_columns_mysql(conn: Any, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    row = cur.fetchone()
    db_name: str = row[0] if row and row[0] else ""

    cur = conn.cursor()
    cur.execute(
        "SELECT COLUMN_NAME "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
        "  AND CONSTRAINT_NAME = 'PRIMARY'",
        (db_name, table),
    )
    return {r[0] for r in cur.fetchall()}


# ── row fetchers ──────────────────────────────────────────────────────────────


def _placeholder(conn: Any) -> str:
    """Return the parameter placeholder for this backend."""
    db_type = getattr(conn, "db_type", "sqlite")
    return "%s" if db_type == "mysql" else "?"


def _q(conn: Any, name: str) -> str:
    """Quote an identifier for the target backend.

    SQLite accepts both double-quotes and backticks; MySQL only backticks.
    We use backtick universally — SQLite supports it too.
    """
    escaped = name.replace("`", "``")
    return f"`{escaped}`"


def fetch_all_rows(
    conn: Any,
    table: str,
    sort_info: tuple[str, bool] | None = None,
    filter_info: tuple[str, Any] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    ph = _placeholder(conn)
    qt = _q(conn, table)
    where_sql = ""
    params: list[Any] = []
    if filter_info:
        col, val = filter_info
        qc = _q(conn, col)
        if val is None:
            where_sql = f' WHERE {qc} IS NULL'
        else:
            where_sql = f' WHERE {qc} = {ph}'
            params.append(val)
    limit_sql = ""
    if limit is not None:
        limit_sql = f' LIMIT {int(limit)} OFFSET {int(offset)}'
    cur.execute(
        f'SELECT * FROM {qt}{where_sql}{order_clause(sort_info, conn)}{limit_sql}', params
    )
    cols = [d[0] for d in cur.description]
    return cols, list(cur.fetchall())


def count_rows(
    conn: Any,
    table: str,
    filter_info: tuple[str, Any] | None = None,
) -> int:
    """Return total row count (with optional filter) without fetching data."""
    cur = conn.cursor()
    ph = _placeholder(conn)
    qt = _q(conn, table)
    where_sql = ""
    params: list[Any] = []
    if filter_info:
        col, val = filter_info
        qc = _q(conn, col)
        if val is None:
            where_sql = f' WHERE {qc} IS NULL'
        else:
            where_sql = f' WHERE {qc} = {ph}'
            params.append(val)
    cur.execute(f'SELECT COUNT(*) FROM {qt}{where_sql}', params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def fetch_row_by_pk(
    conn: Any, table: str, pk_col: str, pk_val: Any
) -> tuple[list[str], tuple | None]:
    cur = conn.cursor()
    ph = _placeholder(conn)
    cur.execute(f'SELECT * FROM {_q(conn, table)} WHERE {_q(conn, pk_col)} = {ph}', (pk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchone()


def fetch_related_rows(
    conn: Any,
    table: str,
    fk_col: str,
    fk_val: Any,
    sort_info: tuple[str, bool] | None = None,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    ph = _placeholder(conn)
    cur.execute(
        f'SELECT * FROM {_q(conn, table)} WHERE {_q(conn, fk_col)} = {ph}{order_clause(sort_info, conn)}',
        (fk_val,),
    )
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def fetch_related_rows_in(
    conn: Any,
    table: str,
    fk_col: str,
    fk_vals: list[Any],
    sort_info: tuple[str, bool] | None = None,
) -> tuple[list[str], list[tuple]]:
    """Batch variant using WHERE fk_col IN (...)."""
    vals = [v for v in fk_vals if v is not None]
    if not vals:
        return [], []

    unique_vals = list(dict.fromkeys(vals))
    ph = _placeholder(conn)
    cur = conn.cursor()
    all_rows: list[tuple] = []
    cols: list[str] = []

    chunk_size = 900  # stay below SQLite variable limits
    for i in range(0, len(unique_vals), chunk_size):
        chunk = unique_vals[i : i + chunk_size]
        placeholders = ",".join(ph for _ in chunk)
        cur.execute(
            f'SELECT * FROM {_q(conn, table)} WHERE {_q(conn, fk_col)} IN ({placeholders}){order_clause(sort_info, conn)}',
            chunk,
        )
        if not cols:
            cols = [d[0] for d in cur.description]
        all_rows.extend(cur.fetchall())

    return cols, all_rows


# ── sort helper ───────────────────────────────────────────────────────────────


def sql_sort_rows(
    conn: Any,
    table: str,
    cols: list[str],
    rows: list[tuple],
    sort_info: tuple[str, bool] | None,
) -> list[tuple]:
    """Re-fetch *rows* from DB in sorted order using PK IN (...)."""
    if not sort_info or not rows:
        return rows
    pk_col = get_pk_column(conn, table)
    if not pk_col:
        return rows
    try:
        pk_idx = cols.index(pk_col)
        pk_vals = [r[pk_idx] for r in rows]
        ph = _placeholder(conn)
        placeholders = ",".join(ph for _ in pk_vals)
        cur = conn.cursor()
        cur.execute(
            f'SELECT * FROM {_q(conn, table)} WHERE {_q(conn, pk_col)} IN ({placeholders}){order_clause(sort_info, conn)}',
            pk_vals,
        )
        res = cur.fetchall()
        return res if len(res) == len(rows) else rows
    except Exception:
        return rows
