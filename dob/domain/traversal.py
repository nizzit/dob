"""
dob.domain.traversal
~~~~~~~~~~~~~~~~~~~~
BFS graph traversal that builds an Observation from a seed row.

Public surface:
  build_observation(conn, schema, prefs, table, pk_col, pk_val)

Internal helpers are module-private (_prefixed).

Traversal strategy
------------------
1. Recursive pass follows outgoing links (schema.fk_from) from the
   current row.  Lookup tables and back-references to the seed table
   are skipped.
2. Each BFS wave is processed in batches (IN queries) to avoid N+1.
3. Incoming links to visited rows (schema.fk_to) are also followed
   recursively so the full neighbourhood is captured.
4. After traversal a final SQL sort pass guarantees ordering.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from dob.db.lookup import LookupCache
from dob.db.queries import (
    fetch_related_rows_in,
    fetch_row_by_pk,
    get_pk_column,
    sql_sort_rows,
)
from dob.db.schema import Schema
from dob.settings.preferences import UserPreferences

from .observation import Observation


# ── public entry-point ────────────────────────────────────────────────────────


def build_observation(
    conn: sqlite3.Connection,
    schema: Schema,
    prefs: UserPreferences,
    table: str,
    pk_col: str,
    pk_val: Any,
    lookup: LookupCache | None = None,
) -> Observation:
    """Traverse the FK graph from *table*[pk_col=pk_val] and return all related rows."""
    if lookup is None:
        lookup = LookupCache(conn)

    cols, seed_row = fetch_row_by_pk(conn, table, pk_col, pk_val)
    if seed_row is None:
        return Observation(seed_table=table, seed_row=(), seed_cols=cols)

    obs = Observation(seed_table=table, seed_row=seed_row, seed_cols=cols)
    seed_dict = dict(zip(cols, seed_row))

    visited: set[tuple] = set()
    visited.add((table, pk_col, pk_val))

    # queue items: (table, row_dict, row_cols, allow_outgoing)
    queue: list[tuple[str, dict[str, Any], list[str], bool]] = [
        (table, seed_dict, cols, True)
    ]

    while queue:
        wave = queue
        queue = []

        # group wave items by table for batch queries
        by_table: dict[str, list[tuple[dict[str, Any], bool]]] = {}
        for cur_table, cur_dict, _cur_cols, allow_outgoing in wave:
            by_table.setdefault(cur_table, []).append((cur_dict, allow_outgoing))

        for cur_table, items in by_table.items():
            _expand_outgoing(
                conn, schema, prefs, lookup, obs, visited, queue, cur_table, items
            )
            _expand_incoming(
                conn, schema, prefs, lookup, obs, visited, queue, cur_table, items
            )

    # seed must not appear in related
    obs.related.pop(table, None)
    obs.related_kind.pop(table, None)
    obs.related_via.pop(table, None)

    # final SQL sort to guarantee correctness for merged sets
    for tbl in list(obs.related.keys()):
        tbl_cols, tbl_rows = obs.related[tbl]
        sort_info = prefs.get_sort(tbl)
        if sort_info and len(tbl_rows) > 1:
            obs.related[tbl] = (
                tbl_cols,
                sql_sort_rows(conn, tbl, tbl_cols, tbl_rows, sort_info),
            )

    return obs


# ── BFS expansion phases ──────────────────────────────────────────────────────


def _expand_outgoing(
    conn: sqlite3.Connection,
    schema: Schema,
    prefs: UserPreferences,
    lookup: LookupCache,
    obs: Observation,
    visited: set,
    queue: list,
    cur_table: str,
    items: list[tuple[dict, bool]],
) -> None:
    """Follow outgoing FKs (fk_from) from current table in batch."""
    seed_dict = dict(zip(obs.seed_cols, obs.seed_row))

    for fk in schema.fk_from.get(cur_table, []):
        if lookup.is_lookup(fk.to_table):
            continue
        if fk.to_table == obs.seed_table:
            continue

        fk_vals = [
            d.get(fk.from_col)
            for d, allow in items
            if allow and d.get(fk.from_col) is not None
        ]
        if not fk_vals:
            continue

        sort_info = prefs.get_sort(fk.to_table)
        r_cols, r_rows = fetch_related_rows_in(
            conn, fk.to_table, fk.to_col, fk_vals, sort_info
        )
        r_rows = _apply_seed_anchor(schema, obs.seed_table, seed_dict, fk.to_table, r_cols, r_rows)
        _merge(obs.related, fk.to_table, r_cols, r_rows)
        if r_rows:
            _mark_kind(obs.related_kind, fk.to_table, "out")
            _mark_via(obs.related_via, fk.to_table, cur_table)

        _enqueue_new(conn, fk.to_table, r_cols, r_rows, visited, queue, allow_outgoing=True)


def _expand_incoming(
    conn: sqlite3.Connection,
    schema: Schema,
    prefs: UserPreferences,
    lookup: LookupCache,
    obs: Observation,
    visited: set,
    queue: list,
    cur_table: str,
    items: list[tuple[dict, bool]],
) -> None:
    """Follow incoming FKs (fk_to) to load tables that reference current rows."""
    seed_dict = dict(zip(obs.seed_cols, obs.seed_row))

    for fk in schema.fk_to.get(cur_table, []):
        if lookup.is_lookup(fk.from_table):
            continue
        if fk.from_table == obs.seed_table:
            continue

        ref_vals = [
            d.get(fk.to_col)
            for d, _allow in items
            if d.get(fk.to_col) is not None
        ]
        if not ref_vals:
            continue

        sort_info = prefs.get_sort(fk.from_table)
        r_cols, r_rows = fetch_related_rows_in(
            conn, fk.from_table, fk.from_col, ref_vals, sort_info
        )
        r_rows = _apply_seed_anchor(schema, obs.seed_table, seed_dict, fk.from_table, r_cols, r_rows)
        _merge(obs.related, fk.from_table, r_cols, r_rows)
        if r_rows:
            _mark_kind(obs.related_kind, fk.from_table, "in")
            _mark_via(obs.related_via, fk.from_table, cur_table)

        _enqueue_new(conn, fk.from_table, r_cols, r_rows, visited, queue, allow_outgoing=True)


# ── helpers ───────────────────────────────────────────────────────────────────


def _apply_seed_anchor(
    schema: Schema,
    seed_table: str,
    seed_dict: dict[str, Any],
    target_table: str,
    cols: list[str],
    rows: list[tuple],
) -> list[tuple]:
    """
    Keep only rows anchored to the seed row when target table has FK(s)
    back to seed_table.
    """
    if not rows:
        return rows
    anchor_fks = [
        fk for fk in schema.fk_from.get(target_table, []) if fk.to_table == seed_table
    ]
    if not anchor_fks:
        return rows

    col_idx = {c: i for i, c in enumerate(cols)}
    checks: list[tuple[int, Any]] = []
    for fk in anchor_fks:
        if fk.from_col not in col_idx or fk.to_col not in seed_dict:
            continue
        checks.append((col_idx[fk.from_col], seed_dict[fk.to_col]))

    if not checks:
        return rows

    return [r for r in rows if any(r[idx] == sv for idx, sv in checks)]


def _merge(
    store: dict[str, tuple[list[str], list[tuple]]],
    table: str,
    cols: list[str],
    rows: list[tuple],
) -> None:
    if not rows:
        return
    if table not in store:
        store[table] = (cols, list(rows))
    else:
        existing = store[table][1]
        seen = set(existing)
        for r in rows:
            if r not in seen:
                existing.append(r)
                seen.add(r)


def _mark_kind(kind_store: dict[str, str], table: str, kind: str) -> None:
    prev = kind_store.get(table)
    if prev is None:
        kind_store[table] = kind
    elif prev != kind:
        kind_store[table] = "both"


def _mark_via(via_store: dict[str, set[str]], table: str, via_table: str) -> None:
    via_store.setdefault(table, set()).add(via_table)


def _enqueue_new(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
    rows: list[tuple],
    visited: set,
    queue: list,
    allow_outgoing: bool,
) -> None:
    pk_col = get_pk_column(conn, table) or cols[0]
    for r_row in rows:
        r_dict = dict(zip(cols, r_row))
        r_pk_val = r_dict.get(pk_col, r_row[0])
        key = (table, pk_col, r_pk_val)
        if key not in visited:
            visited.add(key)
            queue.append((table, r_dict, cols, allow_outgoing))
