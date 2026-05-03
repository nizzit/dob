"""
dob.db.queries
~~~~~~~~~~~~~~
All SQL read helpers.  No business logic, no settings, no UI.
"""

from __future__ import annotations

import sqlite3
from typing import Any


# ── ordering ──────────────────────────────────────────────────────────────────


def order_clause(sort_info: tuple[str, bool] | None) -> str:
    """Return an ORDER BY clause string (empty if *sort_info* is None)."""
    if not sort_info:
        return ""
    return f' ORDER BY "{sort_info[0]}" {"DESC" if sort_info[1] else "ASC"}'


# ── PK helpers ────────────────────────────────────────────────────────────────


def get_pk_column(conn: sqlite3.Connection, table: str) -> str | None:
    """Return the single PK column name, or None for composite / no PK."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    for row in cur.fetchall():
        if row[5] == 1:
            return row[1]
    return None


def get_pk_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return all PK column names (composite PK support)."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    return {row[1] for row in cur.fetchall() if row[5] > 0}


# ── row fetchers ──────────────────────────────────────────────────────────────


def fetch_all_rows(
    conn: sqlite3.Connection,
    table: str,
    sort_info: tuple[str, bool] | None = None,
    filter_info: tuple[str, Any] | None = None,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    where_sql = ""
    params: list[Any] = []
    if filter_info:
        col, val = filter_info
        if val is None:
            where_sql = f' WHERE "{col}" IS NULL'
        else:
            where_sql = f' WHERE "{col}" = ?'
            params.append(val)
    cur.execute(
        f'SELECT * FROM "{table}"{where_sql}{order_clause(sort_info)}', params
    )
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def fetch_row_by_pk(
    conn: sqlite3.Connection, table: str, pk_col: str, pk_val: Any
) -> tuple[list[str], tuple | None]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}" WHERE "{pk_col}" = ?', (pk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchone()


def fetch_related_rows(
    conn: sqlite3.Connection,
    table: str,
    fk_col: str,
    fk_val: Any,
    sort_info: tuple[str, bool] | None = None,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(
        f'SELECT * FROM "{table}" WHERE "{fk_col}" = ?{order_clause(sort_info)}',
        (fk_val,),
    )
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def fetch_related_rows_in(
    conn: sqlite3.Connection,
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
    cur = conn.cursor()
    all_rows: list[tuple] = []
    cols: list[str] = []

    chunk_size = 900  # stay below SQLite variable limits
    for i in range(0, len(unique_vals), chunk_size):
        chunk = unique_vals[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cur.execute(
            f'SELECT * FROM "{table}" WHERE "{fk_col}" IN ({placeholders}){order_clause(sort_info)}',
            chunk,
        )
        if not cols:
            cols = [d[0] for d in cur.description]
        all_rows.extend(cur.fetchall())

    return cols, all_rows


# ── sort helper ───────────────────────────────────────────────────────────────


def sql_sort_rows(
    conn: sqlite3.Connection,
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
        placeholders = ",".join("?" for _ in pk_vals)
        cur = conn.cursor()
        cur.execute(
            f'SELECT * FROM "{table}" WHERE "{pk_col}" IN ({placeholders}){order_clause(sort_info)}',
            pk_vals,
        )
        res = cur.fetchall()
        return res if len(res) == len(rows) else rows
    except Exception:
        return rows
