"""
dob.db.lookup
~~~~~~~~~~~~~
Lookup-table helpers with a per-connection cache.

A "lookup table" is a two-column table with exactly one PK column named
"id" and one value column (e.g. status(id, name)).  FK values pointing
at such a table are rendered inline as "id ≈ name".

The LookupCache is bound to a specific connection object and must be
discarded when the connection is replaced.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class LookupCache:
    """Per-connection cache for lookup-table metadata and values."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._meta: dict[str, str | None] = {}   # table → value_col | None
        self._vals: dict[tuple[str, Any], Any] = {}  # (table, id) → value

    # ── public API ────────────────────────────────────────────────────────────

    def value_column(self, table: str) -> str | None:
        """Return the value column for a 2-column (id, *) table, else None."""
        if table not in self._meta:
            self._meta[table] = self._detect_value_column(table)
        return self._meta[table]

    def is_lookup(self, table: str) -> bool:
        return self.value_column(table) is not None

    def fetch_value(self, table: str, id_val: Any) -> Any:
        """Return display value for lookup table row by id, or None."""
        key = (table, id_val)
        if key not in self._vals:
            self._vals[key] = self._load_value(table, id_val)
        return self._vals[key]

    def invalidate(self) -> None:
        """Clear all cached data (call after schema changes)."""
        self._meta.clear()
        self._vals.clear()

    # ── internals ─────────────────────────────────────────────────────────────

    def _detect_value_column(self, table: str) -> str | None:
        cur = self._conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        info = cur.fetchall()
        if len(info) != 2:
            return None
        cols = [r[1] for r in info]
        pk_cols = [r[1] for r in info if r[5] > 0]
        if pk_cols != ["id"]:
            return None
        for c in cols:
            if c != "id":
                return c
        return None

    def _load_value(self, table: str, id_val: Any) -> Any:
        value_col = self.value_column(table)
        if not value_col:
            return None
        cur = self._conn.cursor()
        cur.execute(f'SELECT "{value_col}" FROM "{table}" WHERE "id" = ?', (id_val,))
        row = cur.fetchone()
        return row[0] if row else None
