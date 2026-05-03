"""
DbObserver - minimal SQLite relationship explorer.

Flow:
  1. Open a .db file (passed as CLI arg or entered in the app)
  2. Pick a table  →  pick a row
  3. The app traverses all FK relations (both directions) and
     collects every related row from every connected table.
  4. ObservationScreen shows all gathered rows grouped by table.
     Live-mode (L) polls the DB every N seconds and highlights new rows.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

LIVE_INTERVAL = 2.0  # seconds between polls in live mode

# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────


@dataclass
class FKInfo:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    virtual: bool = False  # True - вручную заданная связь


@dataclass
class Schema:
    tables: list[str]
    fk_from: dict[str, list[FKInfo]] = field(default_factory=dict)
    fk_to: dict[str, list[FKInfo]] = field(default_factory=dict)
    db_path: str = ""  # путь к .db для VirtualLinks
    col_cache: dict[str, list[str]] = field(default_factory=dict)  # table→cols
    sort_prefs: dict[str, tuple[str, bool]] = field(
        default_factory=dict
    )  # table→(col, reverse)
    filter_prefs: dict[str, tuple[str, Any]] = field(
        default_factory=dict
    )  # table→(col, value)


def load_schema(conn: sqlite3.Connection, db_path: str = "") -> Schema:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    schema = Schema(tables=tables, db_path=db_path)
    if db_path:
        data = ProjectSettings.load(db_path)
        schema.sort_prefs = {k: (v[0], bool(v[1])) for k, v in data["sorts"].items()}
        schema.filter_prefs = {
            k: (v[0], v[1])
            for k, v in data.get("filters", {}).items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }

    # cache column names for each table
    for table in tables:
        cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
        schema.col_cache[table] = [d[0] for d in cur.description]

    for table in tables:
        schema.fk_from[table] = []
        cur.execute(f"PRAGMA foreign_key_list('{table}')")
        for row in cur.fetchall():
            fk = FKInfo(
                from_table=table, from_col=row[3], to_table=row[2], to_col=row[4]
            )
            schema.fk_from[table].append(fk)

    for table in tables:
        schema.fk_to[table] = []
    for table in tables:
        for fk in schema.fk_from[table]:
            schema.fk_to[fk.to_table].append(fk)

    # inject user-defined virtual links
    VirtualLinks.inject(schema)

    return schema


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
    cur.execute(f'SELECT * FROM "{table}"{where_sql}{_order_clause(sort_info)}', params)
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def fetch_row_by_pk(
    conn: sqlite3.Connection, table: str, pk_col: str, pk_val: Any
) -> tuple[list[str], tuple | None]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}" WHERE "{pk_col}" = ?', (pk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchone()


def get_pk_column(conn: sqlite3.Connection, table: str) -> str | None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    for row in cur.fetchall():
        if row[5] == 1:
            return row[1]
    return None


def get_pk_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return all PK column names for a table (composite PK support)."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    return {row[1] for row in cur.fetchall() if row[5] > 0}


_LOOKUP_META_CACHE: dict[tuple[int, str], str | None] = {}
_LOOKUP_VALUE_CACHE: dict[tuple[int, str, Any], Any] = {}


def get_lookup_value_column(conn: sqlite3.Connection, table: str) -> str | None:
    """Return dictionary value column for simple lookup table: id + one field."""
    key = (id(conn), table)
    if key in _LOOKUP_META_CACHE:
        return _LOOKUP_META_CACHE[key]

    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    info = cur.fetchall()
    if len(info) != 2:
        _LOOKUP_META_CACHE[key] = None
        return None

    cols = [r[1] for r in info]
    pk_cols = [r[1] for r in info if r[5] > 0]
    if pk_cols != ["id"]:
        _LOOKUP_META_CACHE[key] = None
        return None

    for c in cols:
        if c != "id":
            _LOOKUP_META_CACHE[key] = c
            return c

    _LOOKUP_META_CACHE[key] = None
    return None


def is_lookup_table(conn: sqlite3.Connection, table: str) -> bool:
    return get_lookup_value_column(conn, table) is not None


def fetch_lookup_value(conn: sqlite3.Connection, table: str, id_val: Any) -> Any:
    """Return display value for lookup table row by id, or None when missing."""
    key = (id(conn), table, id_val)
    if key in _LOOKUP_VALUE_CACHE:
        return _LOOKUP_VALUE_CACHE[key]

    value_col = get_lookup_value_column(conn, table)
    if not value_col:
        _LOOKUP_VALUE_CACHE[key] = None
        return None

    cur = conn.cursor()
    cur.execute(f'SELECT "{value_col}" FROM "{table}" WHERE "id" = ?', (id_val,))
    row = cur.fetchone()
    val = row[0] if row else None
    _LOOKUP_VALUE_CACHE[key] = val
    return val


def fetch_related_rows(
    conn: sqlite3.Connection,
    table: str,
    fk_col: str,
    fk_val: Any,
    sort_info: tuple[str, bool] | None = None,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(
        f'SELECT * FROM "{table}" WHERE "{fk_col}" = ?{_order_clause(sort_info)}',
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
    """Batch variant of fetch_related_rows using WHERE fk_col IN (...)."""
    vals = [v for v in fk_vals if v is not None]
    if not vals:
        return [], []

    unique_vals = list(dict.fromkeys(vals))
    cur = conn.cursor()
    all_rows: list[tuple] = []
    cols: list[str] = []

    # Conservative chunk to stay below SQLite variable limits.
    chunk_size = 900
    for i in range(0, len(unique_vals), chunk_size):
        chunk = unique_vals[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cur.execute(
            f'SELECT * FROM "{table}" WHERE "{fk_col}" IN ({placeholders}){_order_clause(sort_info)}',
            chunk,
        )
        if not cols:
            cols = [d[0] for d in cur.description]
        all_rows.extend(cur.fetchall())

    return cols, all_rows


# ─────────────────────────────────────────────
# Project Settings (Links & Sorts)
# ─────────────────────────────────────────────


class ProjectSettings:
    """
    Persists user-defined settings in <db>.dbobserver.json.
    Format: {
        "links": [ {from_table, from_col, to_table, to_col}, ... ],
        "sorts": { "table_name": ["col_name", reverse_bool], ... },
        "filters": { "table_name": ["col_name", value], ... }
    }
    """

    @staticmethod
    def _path(db_path: str) -> Path:
        return Path(db_path).with_suffix(".dbobserver.json")

    @classmethod
    def load(cls, db_path: str) -> dict:
        p = cls._path(db_path)
        if not p.exists():
            return {"links": [], "sorts": {}, "filters": {}}
        try:
            data = json.loads(p.read_text())
            return {
                "links": data.get("links", []),
                "sorts": data.get("sorts", {}),
                "filters": data.get("filters", {}),
            }
        except Exception:
            return {"links": [], "sorts": {}, "filters": {}}

    @classmethod
    def save(cls, db_path: str, data: dict) -> None:
        if not db_path:
            return
        cls._path(db_path).write_text(json.dumps(data, indent=2))


class VirtualLinks:
    """
    Helper for user-defined column→column links.
    """

    @classmethod
    def add(
        cls, db_path: str, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> None:
        data = ProjectSettings.load(db_path)
        entry = dict(
            from_table=from_table, from_col=from_col, to_table=to_table, to_col=to_col
        )
        if entry not in data["links"]:
            data["links"].append(entry)
            ProjectSettings.save(db_path, data)

    @classmethod
    def remove(
        cls, db_path: str, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> None:
        data = ProjectSettings.load(db_path)
        entry = dict(
            from_table=from_table, from_col=from_col, to_table=to_table, to_col=to_col
        )
        data["links"] = [ln for ln in data["links"] if ln != entry]
        ProjectSettings.save(db_path, data)

    @classmethod
    def inject(cls, schema: Schema) -> None:
        """Add virtual FKInfo entries to an already-loaded Schema in-place."""
        if not schema.db_path:
            return

        data = ProjectSettings.load(schema.db_path)
        for entry in data["links"]:
            ft, fc = entry["from_table"], entry["from_col"]
            tt, tc = entry["to_table"], entry["to_col"]
            if ft not in schema.fk_from:
                schema.fk_from[ft] = []
            if tt not in schema.fk_to:
                schema.fk_to[tt] = []
            fk = FKInfo(
                from_table=ft, from_col=fc, to_table=tt, to_col=tc, virtual=True
            )
            existing = {(f.from_col, f.to_table, f.to_col) for f in schema.fk_from[ft]}
            if (fc, tt, tc) not in existing:
                schema.fk_from[ft].append(fk)
                schema.fk_to[tt].append(fk)


def toggle_and_save_sort(schema: Schema, table: str, col_name: str) -> None:
    current_sort = schema.sort_prefs.get(table)
    if current_sort and current_sort[0] == col_name:
        new_sort = (col_name, not current_sort[1])
    else:
        new_sort = (col_name, True)  # первое нажатие → по убыванию
    schema.sort_prefs[table] = new_sort

    data = ProjectSettings.load(schema.db_path)
    data["sorts"] = schema.sort_prefs
    ProjectSettings.save(schema.db_path, data)


def set_and_save_filter(schema: Schema, table: str, col_name: str, value: Any) -> None:
    schema.filter_prefs[table] = (col_name, value)
    data = ProjectSettings.load(schema.db_path)
    data["filters"] = schema.filter_prefs
    ProjectSettings.save(schema.db_path, data)


def clear_and_save_filter(schema: Schema, table: str) -> None:
    if table in schema.filter_prefs:
        schema.filter_prefs.pop(table, None)
        data = ProjectSettings.load(schema.db_path)
        data["filters"] = schema.filter_prefs
        ProjectSettings.save(schema.db_path, data)


# ─────────────────────────────────────────────
# Graph traversal
# ─────────────────────────────────────────────


@dataclass
class Observation:
    """All rows gathered starting from a seed row."""

    seed_table: str
    seed_row: tuple
    seed_cols: list[str]
    # table → (columns, rows)
    related: dict[str, tuple[list[str], list[tuple]]] = field(default_factory=dict)
    # table → relation kind relative to traversal:
    #   "out"  = table is a target of outgoing link
    #   "in"   = table is a source of incoming link
    #   "both" = observed in both roles
    related_kind: dict[str, str] = field(default_factory=dict)
    # table → set of table names through which this table was reached
    related_via: dict[str, set[str]] = field(default_factory=dict)


def build_observation(
    conn: sqlite3.Connection,
    schema: Schema,
    table: str,
    pk_col: str,
    pk_val: Any,
) -> Observation:
    cols, seed_row = fetch_row_by_pk(conn, table, pk_col, pk_val)
    if seed_row is None:
        return Observation(seed_table=table, seed_row=(), seed_cols=cols)

    obs = Observation(seed_table=table, seed_row=seed_row, seed_cols=cols)

    # Traversal strategy:
    #
    # 1) Recursive pass follows only outgoing links from the current row
    #    (schema.fk_from: current_table.from_col -> to_table.to_col).
    #    This includes real and virtual links in exactly the direction
    #    they were declared.
    #
    # 2) If recursive traversal reaches a link that points back to the
    #    seed table, we stop that branch immediately (seed rows are not
    #    loaded again).
    #
    # 3) Incoming links to the seed table (schema.fk_to[seed]) are loaded
    #    once for context, but are never traversed recursively.
    visited: set[tuple] = set()
    # queue item: (table, row_dict, row_cols, allow_outgoing)
    # allow_outgoing=True  -> follow fk_from recursively
    # allow_outgoing=False -> do not follow fk_from, only load incoming context (fk_to)
    # NOTE: rows discovered via fk_to are now also enqueued with allow_outgoing=True
    # so their outgoing links are expanded recursively as requested.
    queue: list[tuple[str, dict[str, Any], list[str], bool]] = []

    seed_dict = dict(zip(cols, seed_row))
    visited.add((table, pk_col, pk_val))
    queue.append((table, seed_dict, cols, True))

    while queue:
        # Process one BFS wave in batches to avoid N+1 queries.
        wave = queue
        queue = []

        by_table: dict[str, list[tuple[dict[str, Any], bool]]] = {}
        for cur_table, cur_dict, _cur_cols, allow_outgoing in wave:
            by_table.setdefault(cur_table, []).append((cur_dict, allow_outgoing))

        for cur_table, items in by_table.items():
            # Recursive traversal: follow outgoing links (fk_from) in batch.
            for fk in schema.fk_from.get(cur_table, []):
                if is_lookup_table(conn, fk.to_table):
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

                sort_info = schema.sort_prefs.get(fk.to_table)
                r_cols, r_rows = fetch_related_rows_in(
                    conn, fk.to_table, fk.to_col, fk_vals, sort_info
                )
                r_rows = _apply_seed_anchor(
                    schema, obs.seed_table, seed_dict, fk.to_table, r_cols, r_rows
                )
                _merge(obs.related, fk.to_table, r_cols, r_rows)
                if r_rows:
                    _mark_related_kind(obs.related_kind, fk.to_table, "out")
                    _mark_related_via(obs.related_via, fk.to_table, cur_table)

                for r_row in r_rows:
                    r_pk_col = get_pk_column(conn, fk.to_table) or r_cols[0]
                    r_dict = dict(zip(r_cols, r_row))
                    r_pk_val = r_dict.get(r_pk_col, r_row[0])
                    key = (fk.to_table, r_pk_col, r_pk_val)
                    if key not in visited:
                        visited.add(key)
                        queue.append((fk.to_table, r_dict, r_cols, True))

            # Context expansion: load tables referencing current rows in batch.
            for fk in schema.fk_to.get(cur_table, []):
                if is_lookup_table(conn, fk.from_table):
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

                sort_info = schema.sort_prefs.get(fk.from_table)
                r_cols, r_rows = fetch_related_rows_in(
                    conn, fk.from_table, fk.from_col, ref_vals, sort_info
                )
                r_rows = _apply_seed_anchor(
                    schema, obs.seed_table, seed_dict, fk.from_table, r_cols, r_rows
                )
                _merge(obs.related, fk.from_table, r_cols, r_rows)
                if r_rows:
                    _mark_related_kind(obs.related_kind, fk.from_table, "in")
                    _mark_related_via(obs.related_via, fk.from_table, cur_table)

                for r_row in r_rows:
                    r_pk_col = get_pk_column(conn, fk.from_table) or r_cols[0]
                    r_dict = dict(zip(r_cols, r_row))
                    r_pk_val = r_dict.get(r_pk_col, r_row[0])
                    key = (fk.from_table, r_pk_col, r_pk_val)
                    if key not in visited:
                        visited.add(key)
                        queue.append((fk.from_table, r_dict, r_cols, True))

    # seed не должен дублироваться в related
    obs.related.pop(table, None)
    obs.related_kind.pop(table, None)
    obs.related_via.pop(table, None)

    # Final SQL sort for merged sets to guarantee correctness
    for tbl in list(obs.related.keys()):
        cols, rows = obs.related[tbl]
        sort_info = schema.sort_prefs.get(tbl)
        if sort_info and len(rows) > 1:
            obs.related[tbl] = (cols, sql_sort_rows(conn, tbl, cols, rows, sort_info))

    return obs


def _mark_related_kind(kind_store: dict[str, str], table: str, kind: str) -> None:
    prev = kind_store.get(table)
    if prev is None:
        kind_store[table] = kind
    elif prev != kind:
        kind_store[table] = "both"


def _mark_related_via(
    via_store: dict[str, set[str]], table: str, via_table: str
) -> None:
    via_store.setdefault(table, set()).add(via_table)


def _apply_seed_anchor(
    schema: Schema,
    seed_table: str,
    seed_dict: dict[str, Any],
    target_table: str,
    cols: list[str],
    rows: list[tuple],
) -> list[tuple]:
    """Keep only rows anchored to the seed row when target table points to seed.

    If target_table has FK(s) to seed_table, at least one of those FK values must
    match the corresponding value in the seed row.
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
        if fk.from_col not in col_idx:
            continue
        if fk.to_col not in seed_dict:
            continue
        checks.append((col_idx[fk.from_col], seed_dict[fk.to_col]))

    if not checks:
        return rows

    return [r for r in rows if any(r[idx] == seed_val for idx, seed_val in checks)]


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


# ─────────────────────────────────────────────
# Live diff: compare old vs new observation
# ─────────────────────────────────────────────


@dataclass
class TableDiff:
    """New rows that appeared in one table since last poll."""

    table: str
    cols: list[str]
    new_rows: list[tuple]


def diff_observations(old: Observation, new: Observation) -> list[TableDiff]:
    """Return diffs for every table that gained new rows."""
    diffs: list[TableDiff] = []

    all_tables = set(old.related) | set(new.related)
    for tbl in all_tables:
        new_cols, new_rows = new.related.get(tbl, ([], []))
        old_rows_set = set(old.related[tbl][1]) if tbl in old.related else set()

        added = [r for r in new_rows if r not in old_rows_set]
        if added:
            diffs.append(TableDiff(table=tbl, cols=new_cols, new_rows=added))

    # seed row change (update)
    if old.seed_row != new.seed_row and new.seed_row:
        diffs.insert(
            0,
            TableDiff(
                table=f"{new.seed_table} (seed updated)",
                cols=new.seed_cols,
                new_rows=[new.seed_row],
            ),
        )

    return diffs


# ─────────────────────────────────────────────
# Widgets
# ─────────────────────────────────────────────

NEW_STYLE = "bold green"
SEED_STYLE = "bold cyan"
REL_STYLE = "bold yellow"


def _direction_tag(kind: str) -> str:
    if kind == "out":
        return " [dim]→[/dim]"
    if kind == "in":
        return " [dim]←[/dim]"
    if kind == "both":
        return " [dim]↔[/dim]"
    return ""


def _via_text(via_tables: set[str] | None) -> str:
    if not via_tables:
        return ""
    tables = sorted(via_tables)
    if len(tables) > 4:
        shown = ", ".join(tables[:4])
        return f" [dim]via: {shown}, +{len(tables) - 4}[/dim]"
    return f" [dim]via: {', '.join(tables)}[/dim]"


def _fmt(v: Any) -> str:
    return str(v) if v is not None else "NULL"


def _parse_filter_value(raw: str) -> Any:
    text = raw.strip()
    if text.upper() == "NULL":
        return None
    if text == "":
        return ""
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        try:
            return int(text)
        except Exception:
            pass
    try:
        if any(ch in text for ch in (".", "e", "E")):
            return float(text)
    except Exception:
        pass
    return text


def _filter_caption(filter_info: tuple[str, Any] | None) -> str:
    if not filter_info:
        return ""
    col, val = filter_info
    return f"  filter: {col} = {_fmt(val)}"


def _row_strs(
    row: tuple, inline_map: dict[int, tuple[str, Any]] | None = None
) -> list[str]:
    inline_map = inline_map or {}
    out: list[str] = []
    for i, v in enumerate(row):
        base = _fmt(v)
        if i in inline_map:
            _, ref_value = inline_map[i]
            rendered = _fmt(ref_value)
            out.append(f"{base} [dim]≈ {rendered}[/dim]")
        else:
            out.append(base)
    return out


def _order_clause(sort_info: tuple[str, bool] | None) -> str:
    if not sort_info:
        return ""
    return f' ORDER BY "{sort_info[0]}" {"DESC" if sort_info[1] else "ASC"}'


def sql_sort_rows(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
    rows: list[tuple],
    sort_info: tuple[str, bool] | None,
) -> list[tuple]:
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
            f'SELECT * FROM "{table}" WHERE "{pk_col}" IN ({placeholders}){_order_clause(sort_info)}',
            pk_vals,
        )
        res = cur.fetchall()
        return res if len(res) == len(rows) else rows
    except Exception:
        return rows


def _mark_row(dt: DataTable, key: str, new: bool) -> None:
    """Prefix first cell with ▶ marker for new rows."""
    try:
        cell = dt.get_cell(key, dt.ordered_columns[0].key)
        val = str(cell).lstrip("▶ ")
        if new:
            val = f"▶ {val}"
        dt.update_cell(key, dt.ordered_columns[0].key, val)
    except Exception:
        pass


def update_live_label(lbl: Label | None, is_live: bool, extra: str = "") -> None:
    if lbl is None:
        return
    if is_live:
        lbl.update(
            f"[bold green]● LIVE[/]  [dim]polling every {LIVE_INTERVAL}s - press L to stop[/dim]{extra}"
        )
    else:
        lbl.update("[dim]○ live off - press L to start[/dim]")


def _app_live_get(app: App, table: str | None) -> bool:
    if not table:
        return False
    getter = getattr(app, "is_table_live", None)
    if callable(getter):
        return bool(getter(table))
    return False


def _app_live_set(app: App, table: str | None, value: bool) -> None:
    if not table:
        return
    setter = getattr(app, "set_table_live", None)
    if callable(setter):
        setter(table, value)


class RowFlasher:
    def __init__(self) -> None:
        self._flash: dict[str, int] = {}

    def add(self, key: str) -> None:
        self._flash[key] = 3

    def tick(self, dt: DataTable) -> None:
        if not self._flash:
            return
        expired = [k for k, v in self._flash.items() if v <= 1]
        for key in expired:
            _mark_row(dt, key, new=False)
            del self._flash[key]
        for key in self._flash:
            self._flash[key] -= 1

    def count(self) -> int:
        return len(self._flash)

    def clear(self) -> None:
        self._flash.clear()


def _build_col_meta(
    conn: sqlite3.Connection,
    schema: Schema,
    table: str,
) -> tuple[set[str], dict[str, FKInfo]]:
    """Return (pk_cols, fk_cols) for a table.

    pk_cols - set of PK column names.
    fk_cols - mapping col_name → FKInfo for columns that participate in any FK
              (real or virtual) originating from this table.
    """
    pk_cols = get_pk_columns(conn, table)
    fk_cols: dict[str, FKInfo] = {}
    for fk in schema.fk_from.get(table, []):
        # if a column already has a real FK, don't overwrite with virtual
        if fk.from_col not in fk_cols or fk.virtual is False:
            fk_cols[fk.from_col] = fk
    return pk_cols, fk_cols


def _col_header(
    col: str,
    pk_cols: set[str],
    fk_cols: dict[str, FKInfo],
    sort_info: tuple[str, bool] | None = None,
    filter_info: tuple[str, Any] | None = None,
) -> str:
    """
    Return a column header string with relationship indicators:
      🔑 Primary key
      🔗 Real FK to another table
      ✨ Virtual (user-defined) link
      ↑↓ Sort indicator
      ◉ Active filter column
    Multiple indicators are stacked (e.g. 🔑🔗 for FK that is also PK).
    """
    if col in pk_cols:
        pk_prefix = "[bold yellow]*[/bold yellow]"
    else:
        pk_prefix = ""

    if col in fk_cols:
        fk = fk_cols[col]
        if fk.virtual:
            fk_prefix = "[#b57ed6]~[/#b57ed6]"
        else:
            fk_prefix = "[bold cyan]→[/bold cyan]"
    else:
        fk_prefix = ""

    suffix = ""
    if sort_info and sort_info[0] == col:
        suffix += " [bold]↓[/bold]" if sort_info[1] else " [bold]↑[/bold]"
    if filter_info and filter_info[0] == col:
        suffix += " [bold magenta]◉[/bold magenta]"

    if pk_prefix or fk_prefix:
        return f"{pk_prefix}{fk_prefix}{col}{suffix}"
    return f"{col}{suffix}"


def _build_inline_lookup_map(
    conn: sqlite3.Connection,
    schema: Schema,
    table: str,
    cols: list[str],
    row: tuple,
) -> dict[int, tuple[str, Any]]:
    """Map column index -> (lookup_table, lookup_value) for inline rendering."""
    col_idx = {c: i for i, c in enumerate(cols)}
    inline: dict[int, tuple[str, Any]] = {}

    for fk in schema.fk_from.get(table, []):
        if not is_lookup_table(conn, fk.to_table):
            continue
        if fk.from_col not in col_idx:
            continue

        idx = col_idx[fk.from_col]
        fk_val = row[idx]
        if fk_val is None:
            continue

        lookup_val = fetch_lookup_value(conn, fk.to_table, fk_val)
        if lookup_val is None:
            continue

        inline[idx] = (fk.to_table, lookup_val)

    return inline


class TableBlock(Static):
    """
    One table section: header label + DataTable.
    Supports adding new rows with a flash highlight.
    """

    def __init__(
        self,
        table: str,
        cols: list[str],
        rows: list[tuple],
        is_seed: bool = False,
        pk_cols: set[str] | None = None,
        fk_cols: dict[str, FKInfo] | None = None,
        schema: Schema | None = None,
        conn: sqlite3.Connection | None = None,
        relation_kind: str = "",
        relation_via: set[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.tbl_name = table
        self.cols = cols
        self.all_rows = list(rows)
        self.is_seed = is_seed
        self.schema = schema
        self._conn = conn
        self.relation_kind = relation_kind
        self.relation_via = set(relation_via or set())
        self._pk_cols: set[str] = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._flasher = RowFlasher()

    def compose(self) -> ComposeResult:
        if self.is_seed:
            color, marker = SEED_STYLE, "●"
        else:
            color, marker = REL_STYLE, "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        dir_tag = "" if self.is_seed else _direction_tag(self.relation_kind)
        via_tag = "" if self.is_seed else _via_text(self.relation_via)
        yield Label(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]{dir_tag}{via_tag}",
            id=f"lbl-{self.id}",
        )
        dt = DataTable(zebra_stripes=True, id=f"dt-{self.id}", cursor_type="cell")

        sort_info = self.schema.sort_prefs.get(self.tbl_name) if self.schema else None
        filter_info = (
            self.schema.filter_prefs.get(self.tbl_name) if self.schema else None
        )
        headers = [
            _col_header(c, self._pk_cols, self._fk_cols, sort_info, filter_info)
            for c in self.cols
        ]
        dt.add_columns(*headers)
        for row in self.all_rows:
            inline_map = {}
            if self.schema and self._conn:
                inline_map = _build_inline_lookup_map(
                    self._conn, self.schema, self.tbl_name, self.cols, row
                )
            dt.add_row(*_row_strs(row, inline_map), key=str(row))
        yield dt

    def refresh_col_meta(self, conn: sqlite3.Connection) -> None:
        """Re-read FK/PK metadata from schema (call after schema changes)."""
        if self.schema:
            self._pk_cols, self._fk_cols = _build_col_meta(
                conn, self.schema, self.tbl_name
            )

    def update_rows(self, rows: list[tuple]) -> None:
        """Replace all rows and completely redraw the data table."""
        self.all_rows = list(rows)
        dt = self._dt()
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        sort_info = self.schema.sort_prefs.get(self.tbl_name) if self.schema else None
        filter_info = (
            self.schema.filter_prefs.get(self.tbl_name) if self.schema else None
        )
        headers = [
            _col_header(c, self._pk_cols, self._fk_cols, sort_info, filter_info)
            for c in self.cols
        ]
        dt.add_columns(*headers)
        for row in self.all_rows:
            key = str(row)
            inline_map = {}
            if self.schema and self._conn:
                inline_map = _build_inline_lookup_map(
                    self._conn, self.schema, self.tbl_name, self.cols, row
                )
            dt.add_row(*_row_strs(row, inline_map), key=key)
            if key in self._flasher._flash:
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self.all_rows) - 1)), column=cur_c)
        self._refresh_label()

    def _dt(self) -> DataTable:
        return self.query_one(f"#dt-{self.id}", DataTable)

    def _lbl(self) -> Label:
        return self.query_one(f"#lbl-{self.id}", Label)

    def tick_flash(self) -> None:
        """Called every poll tick. Removes highlight after countdown."""
        if self._flasher.count() > 0:
            self._flasher.tick(self._dt())
            self._refresh_label()

    def set_relation_kind(self, relation_kind: str) -> None:
        self.relation_kind = relation_kind
        self._refresh_label()

    def set_relation_via(self, relation_via: set[str] | None) -> None:
        self.relation_via = set(relation_via or set())
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self.is_seed:
            color, marker = SEED_STYLE, "●"
        else:
            color, marker = REL_STYLE, "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        dir_tag = "" if self.is_seed else _direction_tag(self.relation_kind)
        via_tag = "" if self.is_seed else _via_text(self.relation_via)
        new_cnt = self._flasher.count()
        new_tag = f"  [bold green]+{new_cnt} new[/bold green]" if new_cnt else ""
        self._lbl().update(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]{dir_tag}{via_tag}{new_tag}"
        )


# ─────────────────────────────────────────────
# Virtual-link manager modal
# ─────────────────────────────────────────────


class LinkManagerScreen(ModalScreen[bool]):
    """
    Menu for managing existing virtual links on a column.
    Shows all current links and lets the user:
      • Create a new link  (opens LinkBuilderScreen)
      • Edit an existing link  (delete old + create new)
      • Delete an existing link
    Dismisses(True) if any change was made.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def __init__(
        self,
        db_path: str,
        schema: Schema,
        from_table: str,
        from_col: str,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._schema = schema
        self._from_table = from_table
        self._from_col = from_col
        self._changed = False
        # all virtual links from this column
        self._links: list[FKInfo] = [
            fk
            for fk in schema.fk_from.get(from_table, [])
            if fk.virtual and fk.from_col == from_col
        ]
        # action items: (kind, label, fk|None)
        #   kind = "new" | "edit" | "delete"
        self._items: list[tuple[str, str, FKInfo | None]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="mgr-heading")
        yield ListView(id="mgr-list")
        yield Label("[dim]Enter - select │ Esc - cancel[/dim]", id="mgr-hint")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        # refresh links from schema
        self._links = [
            fk
            for fk in self._schema.fk_from.get(self._from_table, [])
            if fk.virtual and fk.from_col == self._from_col
        ]

        heading: Label = self.query_one("#mgr-heading", Label)
        heading.update(
            f"[bold cyan]Virtual links[/]  "
            f"[bold]{self._from_table}[/].[bold yellow]{self._from_col}[/]  "
            f"[dim]({len(self._links)} link{'s' if len(self._links) != 1 else ''})[/dim]"
        )

        lv: ListView = self.query_one("#mgr-list", ListView)
        lv.clear()
        self._items = []

        # ── «create new» item ───────────────────────────
        self._items.append(("new", "", None))
        lv.append(
            ListItem(
                Label("[bold green]＋  Create new link[/bold green]"),
                name="new",
            )
        )

        if self._links:
            # separator label (not selectable, but visually distinct)
            lv.append(
                ListItem(
                    Label("[dim]─── existing links ─────────────────────[/dim]"),
                    name="_sep",
                )
            )
            self._items.append(("_sep", "", None))

            for fk in self._links:
                label_text = f"[cyan]{fk.to_table}[/].[yellow]{fk.to_col}[/]"
                # edit action
                edit_label = f"[bold]✎[/bold]  {label_text}  [dim]edit[/dim]"
                self._items.append(("edit", "", fk))
                lv.append(
                    ListItem(Label(edit_label), name=f"edit:{fk.to_table}:{fk.to_col}")
                )

                # delete action
                del_label = f"[bold red]✕[/bold red]  {label_text}  [dim]delete[/dim]"
                self._items.append(("delete", "", fk))
                lv.append(
                    ListItem(Label(del_label), name=f"del:{fk.to_table}:{fk.to_col}")
                )

        lv.focus()
        lv.index = 0

    @on(ListView.Selected, "#mgr-list")
    def item_selected(self, event: ListView.Selected) -> None:
        name = event.item.name or ""

        if name == "_sep":
            return  # separator – ignore

        if name == "new":
            self._open_builder(edit_fk=None)
            return

        if name.startswith("edit:"):
            _, to_table, to_col = name.split(":", 2)
            fk = self._find_link(to_table, to_col)
            if fk:
                self._open_builder(edit_fk=fk)
            return

        if name.startswith("del:"):
            _, to_table, to_col = name.split(":", 2)
            fk = self._find_link(to_table, to_col)
            if fk:
                self._delete_link(fk)
            return

    def _find_link(self, to_table: str, to_col: str) -> FKInfo | None:
        for fk in self._links:
            if fk.to_table == to_table and fk.to_col == to_col:
                return fk
        return None

    def _delete_link(self, fk: FKInfo) -> None:
        VirtualLinks.remove(
            self._db_path,
            fk.from_table,
            fk.from_col,
            fk.to_table,
            fk.to_col,
        )
        # remove from schema in-place
        self._schema.fk_from.get(fk.from_table, []).remove(fk)
        self._schema.fk_to.get(fk.to_table, []).remove(fk)
        self._changed = True
        self._rebuild()
        self.notify(
            f"{fk.from_table}.{fk.from_col} → {fk.to_table}.{fk.to_col} removed",
            title="Link deleted",
        )

    def _open_builder(self, edit_fk: FKInfo | None) -> None:
        """Open LinkBuilderScreen. If edit_fk given, delete old link first on success."""

        def on_result(saved: bool) -> None:
            if saved:
                if edit_fk is not None:
                    # delete the old link that was replaced
                    VirtualLinks.remove(
                        self._db_path,
                        edit_fk.from_table,
                        edit_fk.from_col,
                        edit_fk.to_table,
                        edit_fk.to_col,
                    )
                    try:
                        self._schema.fk_from.get(edit_fk.from_table, []).remove(edit_fk)
                        self._schema.fk_to.get(edit_fk.to_table, []).remove(edit_fk)
                    except ValueError:
                        pass
                VirtualLinks.inject(self._schema)
                self._changed = True
                self._rebuild()

        self.app.push_screen(
            LinkBuilderScreen(
                db_path=self._db_path,
                schema=self._schema,
                from_table=self._from_table,
                from_col=self._from_col,
            ),
            on_result,
        )

    def on_unmount(self) -> None:
        pass

    def action_cancel(self) -> None:
        self.dismiss(self._changed)


# ─────────────────────────────────────────────
# Link builder modal
# ─────────────────────────────────────────────


class LinkBuilderScreen(ModalScreen[bool]):
    """
    Two-step wizard to create a virtual FK link.
    Source table+column are already known (selected cell on ObservationScreen).
      Step 1 - pick target table
      Step 2 - pick target column
    Saves via VirtualLinks.add() and dismisses(True) on success.
    """

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel", show=True)]

    def __init__(
        self,
        db_path: str,
        schema: Schema,
        from_table: str,
        from_col: str,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._schema = schema
        self._from_table = from_table
        self._from_col = from_col
        self._target_table: str | None = None
        self._step = 1

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="link-heading")
        yield ListView(id="link-list")
        yield Label("[dim]Enter - select │ Esc - cancel[/dim]", id="link-hint")

    def on_mount(self) -> None:
        self._render_step()

    # ── rendering ───────────────────────────

    def _render_step(self) -> None:
        lv: ListView = self.query_one("#link-list", ListView)
        lv.clear()
        heading: Label = self.query_one("#link-heading", Label)

        if self._step == 1:
            heading.update(
                f"[bold cyan]Link builder[/] - "
                f"[bold]{self._from_table}[/].[bold yellow]{self._from_col}[/] → ? "
                f"[dim]Step 1 of 2: pick target table[/dim]"
            )
            for tbl in self._schema.tables:
                if tbl == self._from_table:
                    continue
                lv.append(ListItem(Label(f"[cyan]{tbl}[/]"), name=tbl))

        elif self._step == 2:
            # Existing targets for this source column.
            # - real_targets are hidden (already guaranteed by schema FK)
            # - virtual_targets are shown but marked as already linked
            real_targets = {
                (fk.to_table, fk.to_col)
                for fk in self._schema.fk_from.get(self._from_table, [])
                if (not fk.virtual) and fk.from_col == self._from_col
            }
            virtual_targets = {
                (fk.to_table, fk.to_col)
                for fk in self._schema.fk_from.get(self._from_table, [])
                if fk.virtual and fk.from_col == self._from_col
            }
            heading.update(
                f"[bold cyan]Link builder[/] - "
                f"[bold]{self._from_table}[/].[bold yellow]{self._from_col}[/] → "
                f"[bold cyan]{self._target_table}[/].[?] "
                f"[dim]Step 2 of 2: pick target column[/dim]"
            )
            shown = 0
            for col in self._schema.col_cache.get(self._target_table, []):
                if (self._target_table, col) in real_targets:
                    continue
                badge = (
                    "  [dim green](✓ linked)[/dim green]"
                    if (self._target_table, col) in virtual_targets
                    else ""
                )
                lv.append(ListItem(Label(f"[yellow]{col}[/]{badge}"), name=col))
                shown += 1

            if shown == 0:
                lv.append(
                    ListItem(
                        Label(
                            "[dim]No available columns (real FK already exists).[/dim]"
                        ),
                        name="_none",
                    )
                )

        lv.focus()

    # ── events ────────────────────────────

    @on(ListView.Selected, "#link-list")
    def item_selected(self, event: ListView.Selected) -> None:
        name = event.item.name
        if self._step == 1:
            self._target_table = name
            self._step = 2
            self._render_step()
        elif self._step == 2:
            if name == "_none":
                return
            # Avoid saving duplicates (especially when a real FK already exists).
            dup = any(
                fk.from_col == self._from_col
                and fk.to_table == self._target_table
                and fk.to_col == name
                for fk in self._schema.fk_from.get(self._from_table, [])
            )
            if dup:
                self.notify(
                    f"Link already exists: {self._from_table}.{self._from_col} → {self._target_table}.{name}",
                    title="Duplicate link",
                    severity="warning",
                )
                return

            VirtualLinks.add(
                self._db_path,
                self._from_table,
                self._from_col,
                self._target_table,
                name,
            )
            self.dismiss(True)


class FilterValueScreen(ModalScreen[str | None]):
    """Prompt for a filter value. Empty value clears filter."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel", show=True)]

    def __init__(self, table: str, column: str, current: Any = None) -> None:
        super().__init__()
        self._table = table
        self._column = column
        self._current = current

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label(
            f"[bold cyan]Filter[/] [bold]{self._table}[/].[bold yellow]{self._column}[/]"
            "  [dim](exact match, NULL supported)[/dim]"
        )
        placeholder = "value (empty = clear, NULL = IS NULL)"
        yield Input(
            value="" if self._current is None else str(self._current),
            placeholder=placeholder,
            id="filter-value-input",
        )

    def on_mount(self) -> None:
        self.query_one("#filter-value-input", Input).focus()

    @on(Input.Submitted, "#filter-value-input")
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


# ─────────────────────────────────────────────
# Russian keyboard layout support
# ─────────────────────────────────────────────

# Maps Russian ЙЦУКЕН keys to their English QWERTY equivalents
_RU_TO_EN: dict[str, str] = {
    "й": "q",
    "ц": "w",
    "у": "e",
    "к": "r",
    "е": "t",
    "н": "y",
    "г": "u",
    "ш": "i",
    "щ": "o",
    "з": "p",
    "ф": "a",
    "ы": "s",
    "в": "d",
    "а": "f",
    "п": "g",
    "р": "h",
    "о": "j",
    "л": "k",
    "д": "l",
    "я": "z",
    "ч": "x",
    "с": "c",
    "м": "v",
    "и": "b",
    "т": "n",
    "ь": "m",
    # punctuation on RU layout (same physical keys as EN)
    ".": "/",
    ",": "/",
}


class RuKeysMixin:
    """Mixin that re-fires key events translated from Russian layout to English."""

    async def on_key(self, event: events.Key) -> None:
        en = _RU_TO_EN.get(event.character or "")
        if not en:
            return
        for binding in self.BINDINGS:  # type: ignore[attr-defined]
            keys = [k.strip() for k in binding.key.split(",")]
            if en in keys:
                await self.run_action(binding.action)  # type: ignore[attr-defined]
                event.stop()
                break


class SortableSingleTableMixin:
    """Reusable sort actions for screens that display one DataTable of one DB table."""

    def _sort_schema(self) -> "Schema | None":
        raise NotImplementedError

    def _sort_table_name(self) -> str | None:
        raise NotImplementedError

    def _sort_columns(self) -> list[str]:
        raise NotImplementedError

    def _sort_datatable_id(self) -> str:
        raise NotImplementedError

    def _after_sort_toggle(self) -> None:
        raise NotImplementedError

    def _toggle_sort(self, col_name: str) -> None:
        schema = self._sort_schema()
        table = self._sort_table_name()
        if not schema or not table:
            return
        toggle_and_save_sort(schema, table, col_name)
        self._after_sort_toggle()

    def action_sort_column(self) -> None:
        schema = self._sort_schema()
        table = self._sort_table_name()
        if not schema or not table:
            return
        dt = self.query_one(self._sort_datatable_id(), DataTable)  # type: ignore[attr-defined]
        col_index = dt.cursor_column
        cols = self._sort_columns()
        if col_index >= len(cols):
            return
        self._toggle_sort(cols[col_index])


class SortableFocusedTableMixin:
    """Reusable sorting for screens where active table depends on focused DataTable."""

    def _focused_sort_schema(self) -> "Schema | None":
        raise NotImplementedError

    def _focused_sort_block_for_widget(self, widget) -> "TableBlock | None":
        raise NotImplementedError

    def _after_focused_sort_toggle(self) -> None:
        raise NotImplementedError

    def _focused_sort_missing_focus_message(self) -> str:
        return "Focus a table cell first"

    def _toggle_focused_sort(self, table: str, col_name: str) -> None:
        schema = self._focused_sort_schema()
        if not schema:
            return
        toggle_and_save_sort(schema, table, col_name)
        self._after_focused_sort_toggle()

    def action_sort_column(self) -> None:
        focused = getattr(self, "focused", None)
        if not isinstance(focused, DataTable):
            self.notify(self._focused_sort_missing_focus_message(), severity="warning")  # type: ignore[attr-defined]
            return
        block = self._focused_sort_block_for_widget(focused)
        if block is None:
            return
        col_index = focused.cursor_column
        if col_index >= len(block.cols):
            return
        self._toggle_focused_sort(block.tbl_name, block.cols[col_index])

    def _sort_from_header_event(self, event: DataTable.HeaderSelected) -> None:
        block = self._focused_sort_block_for_widget(event.data_table)
        if block is None:
            return
        if event.column_index >= len(block.cols):
            return
        self._toggle_focused_sort(block.tbl_name, block.cols[event.column_index])


# ─────────────────────────────────────────────
# Expanded (fullscreen) table modal
# ─────────────────────────────────────────────


class ExpandedTableScreen(SortableSingleTableMixin, RuKeysMixin, ModalScreen):
    """Full-screen view of a single table. Esc to close. L to toggle live."""

    BINDINGS = [
        Binding("escape,q,f", "dismiss", "Close", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(
        self,
        title: str,
        cols: list[str],
        rows: list[tuple],
        *,
        conn: sqlite3.Connection | None = None,
        schema: "Schema | None" = None,
        tbl_name: str | None = None,
        pk_cols: set[str] | None = None,
        fk_cols: "dict[str, FKInfo] | None" = None,
        # context of the seed row that produced this table (for filtered live polling)
        seed_table: str | None = None,
        seed_pk_col: str | None = None,
        seed_pk_val: Any = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._cols = cols
        self._rows = list(rows)
        self._conn = conn
        self._schema = schema
        self._tbl_name = tbl_name
        self._pk_cols: set[str] = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._timer: Timer | None = None
        # seed context – used by _poll to fetch only related rows
        self._seed_table = seed_table
        self._seed_pk_col = seed_pk_col
        self._seed_pk_val = seed_pk_val
        # set of rows already shown (for diff highlighting)
        self._known_rows: set[tuple] = set(rows)
        self._flasher = RowFlasher()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        yield Label(
            f"[bold cyan]{self._title}[/]  [dim]{len(self._rows)} rows - Esc / F to close[/dim]",
            id="expanded-title",
        )

        self._rows = sql_sort_rows(
            self._conn,
            self._tbl_name,
            self._cols,
            self._rows,
            self._schema.sort_prefs.get(self._tbl_name) if self._schema else None,
        )

        dt = DataTable(
            id="expanded-dt",
            zebra_stripes=True,
            cursor_type="cell",
        )
        sort_info = (
            self._schema.sort_prefs.get(self._tbl_name) if self._schema else None
        )
        filter_info = (
            self._schema.filter_prefs.get(self._tbl_name)
            if self._schema and self._tbl_name
            else None
        )
        headers = [
            _col_header(c, self._pk_cols, self._fk_cols, sort_info, filter_info)
            for c in self._cols
        ]
        dt.add_columns(*headers)
        for row in self._rows:
            dt.add_row(*_row_strs(row), key=str(row))
        yield dt

    @on(DataTable.HeaderSelected, "#expanded-dt")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.column_index >= len(self._cols):
            return
        self._toggle_sort(self._cols[event.column_index])

    def _sort_schema(self) -> "Schema | None":
        return self._schema

    def _sort_table_name(self) -> str | None:
        return self._tbl_name

    def _sort_columns(self) -> list[str]:
        return self._cols

    def _sort_datatable_id(self) -> str:
        return "#expanded-dt"

    def _after_sort_toggle(self) -> None:
        self._redraw_dt()

    def _redraw_dt(self) -> None:
        if not self._schema or not self._tbl_name:
            return
        dt = self.query_one("#expanded-dt", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        self._rows = sql_sort_rows(
            self._conn,
            self._tbl_name,
            self._cols,
            self._rows,
            self._schema.sort_prefs.get(self._tbl_name),
        )
        dt.clear(columns=True)
        sort_info = self._schema.sort_prefs.get(self._tbl_name)
        filter_info = self._schema.filter_prefs.get(self._tbl_name)
        headers = [
            _col_header(c, self._pk_cols, self._fk_cols, sort_info, filter_info)
            for c in self._cols
        ]
        dt.add_columns(*headers)
        for row in self._rows:
            key = str(row)
            dt.add_row(*_row_strs(row), key=key)
            if key in self._flasher._flash:
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self._rows) - 1)), column=cur_c)

    def on_mount(self) -> None:
        self.live = _app_live_get(self.app, self._tbl_name)
        self.query_one("#expanded-dt", DataTable).focus()

    # ── live toggle ──────────────────────────

    def action_toggle_live(self) -> None:
        if self._conn is None or self._tbl_name is None:
            self.notify("Live mode unavailable (no DB context)", severity="warning")
            return
        self.live = not self.live
        _app_live_set(self.app, self._tbl_name, self.live)

    def watch_live(self, value: bool) -> None:
        self._update_live_label()
        if value:
            self._timer = self.set_interval(LIVE_INTERVAL, self._poll)
        else:
            if self._timer:
                self._timer.stop()
                self._timer = None

    def _update_live_label(self, extra: str = "") -> None:
        try:
            lbl = self.query_one("#live-status", Label)
            update_live_label(lbl, self.live, extra)
        except Exception:
            pass

    # ── poll ─────────────────────────────────

    def _poll(self) -> None:
        if self._conn is None or self._tbl_name is None:
            return
        try:
            # If we have seed context – fetch only the rows that are related
            # to the original seed row (same filtering as ObservationScreen).
            # Fall back to fetch_all_rows only when context is unavailable
            # (e.g. the screen was opened directly, not from ObservationScreen).
            if self._seed_table and self._seed_pk_col and self._seed_pk_val is not None:
                new_obs = build_observation(
                    self._conn,
                    self._schema,
                    self._seed_table,
                    self._seed_pk_col,
                    self._seed_pk_val,
                )
                if self._tbl_name == self._seed_table:
                    related_rows = [new_obs.seed_row] if new_obs.seed_row else []
                else:
                    related_rows = new_obs.related.get(
                        self._tbl_name, (self._cols, [])
                    )[1]
                all_rows = related_rows
            else:
                sort_info = (
                    self._schema.sort_prefs.get(self._tbl_name)
                    if self._schema
                    else None
                )
                _, all_rows = fetch_all_rows(self._conn, self._tbl_name, sort_info)
        except Exception:
            return

        new_rows = [r for r in all_rows if r not in self._known_rows]
        if new_rows:
            for row in new_rows:
                self._known_rows.add(row)
                self._flasher.add(str(row))

            self._rows = all_rows
            self._redraw_dt()
            self._refresh_title()

        # tick flash
        self._flasher.tick(self.query_one("#expanded-dt", DataTable))

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_live_label(
            f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]"
            if self.live
            else ""
        )

    def _refresh_title(self) -> None:
        try:
            lbl: Label = self.query_one("#expanded-title", Label)
            lbl.update(
                f"[bold cyan]{self._title}[/]  "
                f"[dim]{len(self._rows)} rows - Esc / F to close[/dim]"
            )
        except Exception:
            pass

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()

    # ── drill-down on row select ──────────────

    @on(DataTable.CellSelected, "#expanded-dt")
    def row_drilldown(self, event: DataTable.CellSelected) -> None:
        if not self._conn or not self._schema or not self._tbl_name:
            return

        row_index = event.coordinate.row
        if row_index >= len(self._rows):
            return

        raw_row = self._rows[row_index]
        row_dict = dict(zip(self._cols, raw_row))
        pk_col = get_pk_column(self._conn, self._tbl_name) or self._cols[0]
        pk_val = row_dict.get(pk_col, raw_row[0])

        self.app.push_screen(
            ObservationScreen(
                self._conn,
                self._schema,
                self._tbl_name,
                pk_col,
                pk_val,
            )
        )

    # ── link builder / manager ───────────────

    def action_link(self) -> None:
        if not self._schema or not self._schema.db_path or not self._tbl_name:
            return

        dt = self.query_one("#expanded-dt", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self._cols):
            return
        from_col = self._cols[col_index]

        existing = [
            fk
            for fk in self._schema.fk_from.get(self._tbl_name, [])
            if fk.virtual and fk.from_col == from_col
        ]

        def on_change(changed: bool) -> None:
            if changed:
                VirtualLinks.inject(self._schema)

        if existing:
            self.app.push_screen(
                LinkManagerScreen(
                    db_path=self._schema.db_path,
                    schema=self._schema,
                    from_table=self._tbl_name,
                    from_col=from_col,
                ),
                on_change,
            )
        else:

            def on_builder_result(saved: bool) -> None:
                if saved:
                    VirtualLinks.inject(self._schema)
                    self.notify(
                        f"{self._tbl_name}.{from_col} linked",
                        title="Virtual link created",
                    )

            self.app.push_screen(
                LinkBuilderScreen(
                    db_path=self._schema.db_path,
                    schema=self._schema,
                    from_table=self._tbl_name,
                    from_col=from_col,
                ),
                on_builder_result,
            )


# ─────────────────────────────────────────────
# Screens
# ─────────────────────────────────────────────


class ObservationScreen(SortableFocusedTableMixin, RuKeysMixin, Screen):
    """Shows the observation. Press L to toggle live polling."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("f", "expand_focused", "Expand", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(
        self,
        conn: sqlite3.Connection,
        schema: Schema,
        table: str,
        pk_col: str,
        pk_val: Any,
    ) -> None:
        super().__init__()
        self._conn = conn
        self._schema = schema
        self._table = table
        self._pk_col = pk_col
        self._pk_val = pk_val

        self._obs = build_observation(conn, schema, table, pk_col, pk_val)
        self._timer: Timer | None = None
        # block_id → TableBlock
        self._blocks: dict[str, TableBlock] = {}

    # ── compose ──────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        with VerticalScroll(id="obs-scroll"):
            obs = self._obs

            # seed block
            bid = "seed"
            _pk, _fk = _build_col_meta(self._conn, self._schema, obs.seed_table)
            blk = TableBlock(
                table=obs.seed_table,
                cols=obs.seed_cols,
                rows=[obs.seed_row] if obs.seed_row else [],
                is_seed=True,
                pk_cols=_pk,
                fk_cols=_fk,
                schema=self._schema,
                conn=self._conn,
                id=f"block-{bid}",
                classes="obs-block",
            )
            self._blocks[bid] = blk
            yield blk

            if not obs.related:
                yield Label("[dim]No related records found.[/dim]")
            else:
                for tbl_name, (cols, rows) in obs.related.items():
                    bid = tbl_name
                    _pk, _fk = _build_col_meta(self._conn, self._schema, tbl_name)
                    blk = TableBlock(
                        table=tbl_name,
                        cols=cols,
                        rows=rows,
                        is_seed=False,
                        pk_cols=_pk,
                        fk_cols=_fk,
                        schema=self._schema,
                        conn=self._conn,
                        relation_kind=obs.related_kind.get(tbl_name, ""),
                        relation_via=obs.related_via.get(tbl_name, set()),
                        id=f"block-{bid}",
                        classes="obs-block",
                    )
                    self._blocks[bid] = blk
                    yield blk

    # ── live toggle ──────────────────────────

    def action_toggle_live(self) -> None:
        self.live = not self.live
        _app_live_set(self.app, self._table, self.live)

    def watch_live(self, value: bool) -> None:
        try:
            lbl = self.query_one("#live-status", Label)
            update_live_label(lbl, value)
        except Exception:
            pass

        if value:
            self._timer = self.set_interval(LIVE_INTERVAL, self._poll)
        else:
            if self._timer:
                self._timer.stop()
                self._timer = None

    def action_link(self) -> None:
        """Open link manager (or builder if no links yet) for the focused column."""
        db_path = self._schema.db_path
        if not db_path:
            return

        # find focused DataTable and its parent TableBlock
        focused = self.focused
        if not isinstance(focused, DataTable):
            self.notify("Focus a table cell first", severity="warning")
            return
        block = self._block_for_widget(focused)
        if block is None:
            return

        # get the column name of the cursor cell
        col_index = focused.cursor_column
        if col_index >= len(block.cols):
            return
        from_col = block.cols[col_index]

        existing = [
            fk
            for fk in self._schema.fk_from.get(block.tbl_name, [])
            if fk.virtual and fk.from_col == from_col
        ]

        def on_result(changed: bool) -> None:
            if changed:
                VirtualLinks.inject(self._schema)
                self._obs = build_observation(
                    self._conn,
                    self._schema,
                    self._table,
                    self._pk_col,
                    self._pk_val,
                )
                self._rebuild_blocks()

        if existing:
            self.app.push_screen(
                LinkManagerScreen(
                    db_path=db_path,
                    schema=self._schema,
                    from_table=block.tbl_name,
                    from_col=from_col,
                ),
                on_result,
            )
        else:

            def on_builder_result(saved: bool) -> None:
                if saved:
                    on_result(True)
                    self.notify(
                        f"{block.tbl_name}.{from_col} linked",
                        title="Virtual link created",
                    )

            self.app.push_screen(
                LinkBuilderScreen(
                    db_path=db_path,
                    schema=self._schema,
                    from_table=block.tbl_name,
                    from_col=from_col,
                ),
                on_builder_result,
            )

    def _focused_sort_schema(self) -> "Schema | None":
        return self._schema

    def _focused_sort_block_for_widget(self, widget) -> "TableBlock | None":
        return self._block_for_widget(widget)

    def _after_focused_sort_toggle(self) -> None:
        self._reload()

    def _rebuild_blocks(self) -> None:
        """Sync displayed blocks to current observation (add, update, remove)."""
        scroll: VerticalScroll = self.query_one("#obs-scroll")
        obs = self._obs

        # update seed block
        seed_blk = self._blocks.get("seed")
        if seed_blk:
            seed_blk.refresh_col_meta(self._conn)
            seed_blk.update_rows([obs.seed_row] if obs.seed_row else [])

        # remove blocks for tables no longer in observation
        gone = [
            tbl
            for tbl in list(self._blocks)
            if tbl != "seed" and tbl not in obs.related
        ]
        for tbl in gone:
            blk = self._blocks.pop(tbl)
            blk.remove()

        # update existing blocks / add new ones
        for tbl_name, (cols, rows) in obs.related.items():
            if tbl_name in self._blocks:
                self._blocks[tbl_name].refresh_col_meta(self._conn)
                self._blocks[tbl_name].set_relation_kind(
                    obs.related_kind.get(tbl_name, "")
                )
                self._blocks[tbl_name].set_relation_via(
                    obs.related_via.get(tbl_name, set())
                )
                self._blocks[tbl_name].update_rows(rows)
            else:
                _pk, _fk = _build_col_meta(self._conn, self._schema, tbl_name)
                blk = TableBlock(
                    table=tbl_name,
                    cols=cols,
                    rows=rows,
                    is_seed=False,
                    pk_cols=_pk,
                    fk_cols=_fk,
                    schema=self._schema,
                    conn=self._conn,
                    relation_kind=obs.related_kind.get(tbl_name, ""),
                    relation_via=obs.related_via.get(tbl_name, set()),
                    id=f"block-{tbl_name}",
                    classes="obs-block",
                )
                self._blocks[tbl_name] = blk
                scroll.mount(blk)

    def _sync_live_from_app(self) -> None:
        self.live = _app_live_get(self.app, self._table)

    def on_mount(self) -> None:
        self._sync_live_from_app()
        # give keyboard focus to scroll container so arrow keys work
        self.query_one("#obs-scroll").focus()

    def on_screen_resume(self) -> None:
        self._sync_live_from_app()

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()

    # ── expand ───────────────────────────────

    # ── helpers ──────────────────────────────

    def _block_for_widget(self, widget) -> TableBlock | None:
        """Walk up the DOM from widget to find its parent TableBlock."""
        node = widget
        while node is not None:
            if isinstance(node, TableBlock):
                return node
            node = node.parent
        return None

    # ── drill-down on row select ──────────────

    @on(DataTable.HeaderSelected)
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self._sort_from_header_event(event)

    def _reload(self) -> None:
        """Fetch fresh observation after sort changes, completely redraw all blocks."""
        self._obs = build_observation(
            self._conn,
            self._schema,
            self._table,
            self._pk_col,
            self._pk_val,
        )
        for bid, blk in self._blocks.items():
            real_tbl = blk.tbl_name
            if real_tbl == self._obs.seed_table:
                blk.update_rows([self._obs.seed_row] if self._obs.seed_row else [])
            elif real_tbl in self._obs.related:
                blk.set_relation_kind(self._obs.related_kind.get(real_tbl, ""))
                blk.set_relation_via(self._obs.related_via.get(real_tbl, set()))
                blk.update_rows(self._obs.related[real_tbl][1])

    @on(DataTable.CellSelected)
    def row_drilldown(self, event: DataTable.CellSelected) -> None:
        """Open a new ObservationScreen for the row of the selected cell."""
        block = self._block_for_widget(event.data_table)
        if block is None:
            return

        # don't drill into the seed block
        if block.is_seed:
            return

        row_index = event.coordinate.row
        if row_index >= len(block.all_rows):
            return

        raw_row = block.all_rows[row_index]
        row_dict = dict(zip(block.cols, raw_row))
        pk_col = get_pk_column(self._conn, block.tbl_name) or block.cols[0]
        pk_val = row_dict.get(pk_col, raw_row[0])

        self.app.push_screen(
            ObservationScreen(
                self._conn,
                self._schema,
                block.tbl_name,
                pk_col,
                pk_val,
            )
        )

    # ── expand ───────────────────────────────

    def action_expand_focused(self) -> None:
        """Open the currently-focused DataTable in full-screen modal."""
        focused = self.focused
        target_block = (
            self._block_for_widget(focused) if isinstance(focused, DataTable) else None
        )

        # fallback: first block
        if target_block is None and self._blocks:
            target_block = next(iter(self._blocks.values()))

        if target_block is None:
            return

        _pk, _fk = _build_col_meta(self._conn, self._schema, target_block.tbl_name)
        self.app.push_screen(
            ExpandedTableScreen(
                title=target_block.tbl_name,
                cols=list(target_block.cols),
                rows=list(target_block.all_rows),
                conn=self._conn,
                schema=self._schema,
                tbl_name=target_block.tbl_name,
                pk_cols=_pk,
                fk_cols=_fk,
                # seed context so live polling only shows related rows
                seed_table=self._table,
                seed_pk_col=self._pk_col,
                seed_pk_val=self._pk_val,
            )
        )

    # ── poll ─────────────────────────────────

    def _poll(self) -> None:
        """Fetch fresh observation, compute diff, update UI."""
        try:
            new_obs = build_observation(
                self._conn,
                self._schema,
                self._table,
                self._pk_col,
                self._pk_val,
            )
        except Exception:
            return

        diffs = diff_observations(self._obs, new_obs)
        scroll: VerticalScroll = self.query_one("#obs-scroll")

        for diff in diffs:
            tbl = diff.table
            # strip "(seed updated)" suffix to get real table name for lookup
            real_tbl = tbl.replace(" (seed updated)", "")

            if real_tbl in self._blocks:
                blk = self._blocks[real_tbl]
                for r in diff.new_rows:
                    blk._flasher.add(str(r))
                if real_tbl == new_obs.seed_table:
                    blk.update_rows([new_obs.seed_row] if new_obs.seed_row else [])
                else:
                    blk.set_relation_kind(new_obs.related_kind.get(real_tbl, ""))
                    blk.set_relation_via(new_obs.related_via.get(real_tbl, set()))
                    blk.update_rows(new_obs.related[real_tbl][1])
            else:
                # новая таблица появилась - создаём блок и монтируем
                bid = real_tbl
                _pk, _fk = _build_col_meta(self._conn, self._schema, real_tbl)
                blk = TableBlock(
                    table=real_tbl,
                    cols=diff.cols,
                    rows=diff.new_rows,
                    is_seed=False,
                    pk_cols=_pk,
                    fk_cols=_fk,
                    schema=self._schema,
                    conn=self._conn,
                    relation_kind=new_obs.related_kind.get(real_tbl, ""),
                    relation_via=new_obs.related_via.get(real_tbl, set()),
                    id=f"block-{bid}",
                    classes="obs-block",
                )
                self._blocks[bid] = blk
                scroll.mount(blk)

        # tick flash counters on all existing blocks
        for blk in self._blocks.values():
            blk.tick_flash()

        # update our snapshot
        self._obs = new_obs

        # refresh live-status with last-updated time
        if self.live:
            ts = datetime.now().strftime("%H:%M:%S")
            n_new = sum(len(d.new_rows) for d in diffs)
            new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
            try:
                lbl = self.query_one("#live-status", Label)
                update_live_label(
                    lbl,
                    self.live,
                    f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]",
                )
            except Exception:
                pass


class RowPickerScreen(SortableSingleTableMixin, RuKeysMixin, Screen):
    """Pick a row from a table. L - live mode, R - manual refresh."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("/", "filter_column", "Filter", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(self, conn: sqlite3.Connection, schema: Schema, table: str) -> None:
        super().__init__()
        self.conn = conn
        self.schema = schema
        self.table = table
        self.pk_col = get_pk_column(conn, table)
        self.cols, self.rows = fetch_all_rows(
            conn,
            table,
            filter_info=schema.filter_prefs.get(table),
        )
        self._known_rows: set[tuple] = set(self.rows)
        self._flasher = RowFlasher()
        self._timer: Timer | None = None

    # ── live ─────────────────────────────────

    def action_toggle_live(self) -> None:
        self.live = not self.live
        _app_live_set(self.app, self.table, self.live)

    def watch_live(self, value: bool) -> None:
        self._update_live_label()
        if value:
            self._timer = self.set_interval(LIVE_INTERVAL, self._live_poll)
        else:
            if self._timer:
                self._timer.stop()
                self._timer = None

    def _update_live_label(self, extra: str = "") -> None:
        try:
            lbl = self.query_one("#live-status", Label)
            update_live_label(lbl, self.live, extra)
        except Exception:
            pass

    def _live_poll(self) -> None:
        sort_info = self.schema.sort_prefs.get(self.table)
        filter_info = self.schema.filter_prefs.get(self.table)
        _, all_rows = fetch_all_rows(self.conn, self.table, sort_info, filter_info)
        new_rows = [r for r in all_rows if r not in self._known_rows]

        if new_rows:
            for row in new_rows:
                self._known_rows.add(row)
                self._flasher.add(str(row))
            self.rows = all_rows
            self._redraw_dt()
            self.app.sub_title = f"{self.table}  ({len(self.rows)} rows){_filter_caption(filter_info)} - Enter to observe"

        # tick flash
        self._flasher.tick(self.query_one("#row-table", DataTable))

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_live_label(
            f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]"
        )

    # ── reload (manual refresh) ───────────────

    def _reload(self) -> None:
        sort_info = self.schema.sort_prefs.get(self.table)
        filter_info = self.schema.filter_prefs.get(self.table)
        self.cols, self.rows = fetch_all_rows(
            self.conn, self.table, sort_info, filter_info
        )
        self._known_rows = set(self.rows)
        self._flasher.clear()
        self._redraw_dt()
        self.app.sub_title = f"{self.table}  ({len(self.rows)} rows){_filter_caption(filter_info)} - Enter to observe"

    def action_refresh(self) -> None:
        self._reload()
        self.query_one("#row-table", DataTable).focus()

    def action_filter_column(self) -> None:
        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols):
            return
        col_name = self.cols[col_index]
        current = None
        active = self.schema.filter_prefs.get(self.table)
        if active and active[0] == col_name:
            current = active[1]

        def on_value(raw: str | None) -> None:
            if raw is None:
                return
            value_text = raw.strip()
            if value_text == "":
                clear_and_save_filter(self.schema, self.table)
                self.notify(f"Filter cleared: {self.table}", title="Filter")
            else:
                value = _parse_filter_value(value_text)
                set_and_save_filter(self.schema, self.table, col_name, value)
                self.notify(
                    f"Filter set: {self.table}.{col_name} = {_fmt(value)}",
                    title="Filter",
                )
            self._reload()
            self.query_one("#row-table", DataTable).focus()

        self.app.push_screen(FilterValueScreen(self.table, col_name, current), on_value)

    def _sync_live_from_app(self) -> None:
        self.live = _app_live_get(self.app, self.table)

    def on_mount(self) -> None:
        self.app.sub_title = f"{self.table}  ({len(self.rows)} rows){_filter_caption(self.schema.filter_prefs.get(self.table))} - Enter to observe"
        self._sync_live_from_app()
        self.query_one("#row-table", DataTable).focus()

    def on_screen_resume(self) -> None:
        self._sync_live_from_app()

    def on_unmount(self) -> None:
        self.app.sub_title = ""
        if self._timer:
            self._timer.stop()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        pk_cols, fk_cols = _build_col_meta(self.conn, self.schema, self.table)
        dt = DataTable(id="row-table", zebra_stripes=True, cursor_type="cell")

        self.rows = sql_sort_rows(
            self.conn,
            self.table,
            self.cols,
            self.rows,
            self.schema.sort_prefs.get(self.table),
        )
        sort_info = self.schema.sort_prefs.get(self.table)
        filter_info = self.schema.filter_prefs.get(self.table)
        headers = [
            _col_header(c, pk_cols, fk_cols, sort_info, filter_info) for c in self.cols
        ]
        dt.add_columns(*headers)
        for row in self.rows:
            dt.add_row(*_row_strs(row), key=str(row))
        yield dt

    @on(DataTable.HeaderSelected, "#row-table")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self._toggle_sort(self.cols[event.column_index])

    def _sort_schema(self) -> "Schema | None":
        return self.schema

    def _sort_table_name(self) -> str | None:
        return self.table

    def _sort_columns(self) -> list[str]:
        return self.cols

    def _sort_datatable_id(self) -> str:
        return "#row-table"

    def _after_sort_toggle(self) -> None:
        self._reload()

    def _redraw_dt(self) -> None:
        dt = self.query_one("#row-table", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        pk_cols, fk_cols = _build_col_meta(self.conn, self.schema, self.table)
        sort_info = self.schema.sort_prefs.get(self.table)
        filter_info = self.schema.filter_prefs.get(self.table)
        headers = [
            _col_header(c, pk_cols, fk_cols, sort_info, filter_info) for c in self.cols
        ]
        dt.add_columns(*headers)
        for row in self.rows:
            key = str(row)
            dt.add_row(*_row_strs(row), key=key)
            if key in self._flasher._flash:
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self.rows) - 1)), column=cur_c)

    @on(DataTable.CellSelected, "#row-table")
    def row_selected(self, event: DataTable.CellSelected) -> None:
        row_index = event.coordinate.row
        if row_index >= len(self.rows):
            return
        raw_row = self.rows[row_index]
        row_dict = dict(zip(self.cols, raw_row))
        pk_col = self.pk_col or self.cols[0]
        pk_val = row_dict.get(pk_col, raw_row[0])

        screen = ObservationScreen(self.conn, self.schema, self.table, pk_col, pk_val)
        self.app.push_screen(screen)

    # ── link builder / manager ───────────────

    def action_link(self) -> None:
        if not self.schema or not self.schema.db_path or not self.table:
            return

        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols):
            return
        from_col = self.cols[col_index]

        existing = [
            fk
            for fk in self.schema.fk_from.get(self.table, [])
            if fk.virtual and fk.from_col == from_col
        ]

        def on_change(changed: bool) -> None:
            if changed:
                VirtualLinks.inject(self.schema)
                self._reload()

        if existing:
            self.app.push_screen(
                LinkManagerScreen(
                    db_path=self.schema.db_path,
                    schema=self.schema,
                    from_table=self.table,
                    from_col=from_col,
                ),
                on_change,
            )
        else:

            def on_builder_result(saved: bool) -> None:
                if saved:
                    VirtualLinks.inject(self.schema)
                    self.notify(
                        f"{self.table}.{from_col} linked", title="Virtual link created"
                    )
                    self._reload()

            self.app.push_screen(
                LinkBuilderScreen(
                    db_path=self.schema.db_path,
                    schema=self.schema,
                    from_table=self.table,
                    from_col=from_col,
                ),
                on_builder_result,
            )


class TablePickerScreen(RuKeysMixin, Screen):
    """Pick a table from the database."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back", show=True)]

    def __init__(self, conn: sqlite3.Connection, schema: Schema, db_path: str) -> None:
        super().__init__()
        self.conn = conn
        self.schema = schema
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical():
            yield Label(
                f"[bold]Database:[/] [yellow]{self.db_path}[/]  -  pick a table",
                classes="screen-title",
            )
            items = [ListItem(Label(t), name=t) for t in self.schema.tables]
            yield ListView(*items, id="table-list")

    @on(ListView.Selected, "#table-list")
    def table_selected(self, event: ListView.Selected) -> None:
        self.app.push_screen(RowPickerScreen(self.conn, self.schema, event.item.name))


class OpenDBScreen(Screen):
    """Entry screen."""

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit", show=True)]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical(id="open-db-container"):
            yield Static(
                "[bold cyan]DbObserver[/]\n[dim]SQLite relationship explorer[/dim]",
                id="logo",
            )
            yield Label("Path to SQLite database file:")
            yield Input(placeholder="e.g. ./test.db", id="db-path-input")
            yield Label("", id="error-label")

    def on_mount(self) -> None:
        inp: Input = self.query_one("#db-path-input")
        if inp.value:
            self._try_open(inp.value)

    def set_initial_path(self, path: str) -> None:
        self.query_one("#db-path-input", Input).value = path

    @on(Input.Submitted, "#db-path-input")
    def submitted(self, event: Input.Submitted) -> None:
        self._try_open(event.value.strip())

    def _try_open(self, path: str) -> None:
        err: Label = self.query_one("#error-label")
        if not path:
            err.update("[red]Please enter a path.[/red]")
            return
        p = Path(path)
        if not p.exists():
            err.update(f"[red]File not found: {path}[/red]")
            return
        try:
            # WAL mode + check_same_thread=False so timer thread can read
            conn = sqlite3.connect(str(p), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            schema = load_schema(conn, db_path=str(p))
            self.app.push_screen(TablePickerScreen(conn, schema, str(p)))
        except Exception as exc:
            err.update(f"[red]Error: {exc}[/red]")


# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────

CSS = """
Screen {
    background: $surface;
}

#open-db-container {
    align: center middle;
    padding: 4 8;
    height: 100%;
}

#logo {
    text-align: center;
    margin-bottom: 2;
    color: $accent;
}

Input {
    margin: 1 0;
    width: 60;
}

.screen-title {
    margin: 1 2;
}

#live-status {
    margin: 0 2;
    height: 1;
}

.obs-block {
    margin: 1 1 0 1;
    height: auto;
}

DataTable {
    height: auto;
    max-height: 12;
    margin-bottom: 1;
}

#row-table {
    height: 1fr;
    max-height: 100%;
    margin-bottom: 0;
}

ExpandedTableScreen {
    align: center middle;
}

#expanded-title {
    margin: 0 2 0 2;
    height: 1;
}

ExpandedTableScreen DataTable {
    width: 100%;
    height: 1fr;
    max-height: 100%;
}

#error-label {
    color: $error;
}

LinkBuilderScreen {
    align: center middle;
}

LinkBuilderScreen #link-heading {
    margin: 0 2 1 2;
    height: 1;
}

LinkBuilderScreen ListView {
    width: 80;
    height: 1fr;
    max-height: 30;
    border: solid $primary;
}

LinkBuilderScreen #link-hint {
    margin: 1 2 0 2;
    height: 1;
}

LinkManagerScreen {
    align: center middle;
}

LinkManagerScreen #mgr-heading {
    margin: 0 2 1 2;
    height: 1;
}

LinkManagerScreen ListView {
    width: 80;
    height: 1fr;
    max-height: 30;
    border: solid $accent;
}

LinkManagerScreen #mgr-hint {
    margin: 1 2 0 2;
    height: 1;
}
"""


class DbObserverApp(App):
    TITLE = "DbObserver"
    CSS = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=True)]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.initial_db_path = db_path
        self._table_live_state: dict[str, bool] = {}

    def is_table_live(self, table: str) -> bool:
        return self._table_live_state.get(table, False)

    def set_table_live(self, table: str, value: bool) -> None:
        self._table_live_state[table] = bool(value)

    def on_mount(self) -> None:
        screen = OpenDBScreen()
        self.push_screen(screen)
        if self.initial_db_path:
            self.call_after_refresh(self._auto_open)

    def _auto_open(self) -> None:
        screen = self.screen
        if isinstance(screen, OpenDBScreen):
            screen.set_initial_path(self.initial_db_path)
            screen._try_open(self.initial_db_path)


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    DbObserverApp(db_path=db_path).run()


if __name__ == "__main__":
    main()
