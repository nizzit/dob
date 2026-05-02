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
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual import on
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

LIVE_INTERVAL = 2.0   # seconds between polls in live mode

# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────


@dataclass
class FKInfo:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    virtual: bool = False   # True - вручную заданная связь


@dataclass
class Schema:
    tables: list[str]
    fk_from:   dict[str, list[FKInfo]] = field(default_factory=dict)
    fk_to:     dict[str, list[FKInfo]] = field(default_factory=dict)
    db_path:   str = ""                 # путь к .db для VirtualLinks
    col_cache: dict[str, list[str]] = field(default_factory=dict)  # table→cols
    sort_prefs: dict[str, tuple[str, bool]] = field(default_factory=dict) # table→(col, reverse)


def load_schema(conn: sqlite3.Connection, db_path: str = "") -> Schema:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    schema = Schema(tables=tables, db_path=db_path)
    if db_path:
        data = ProjectSettings.load(db_path)
        schema.sort_prefs = {k: (v[0], bool(v[1])) for k, v in data["sorts"].items()}

    # cache column names for each table
    for table in tables:
        cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
        schema.col_cache[table] = [d[0] for d in cur.description]

    for table in tables:
        schema.fk_from[table] = []
        cur.execute(f"PRAGMA foreign_key_list('{table}')")
        for row in cur.fetchall():
            fk = FKInfo(from_table=table, from_col=row[3],
                        to_table=row[2],  to_col=row[4])
            schema.fk_from[table].append(fk)

    for table in tables:
        schema.fk_to[table] = []
    for table in tables:
        for fk in schema.fk_from[table]:
            schema.fk_to[fk.to_table].append(fk)

    # inject user-defined virtual links
    VirtualLinks.inject(schema)

    return schema


def fetch_all_rows(conn: sqlite3.Connection, table: str, sort_info: tuple[str, bool] | None = None) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}"{_order_clause(sort_info)}')
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


def fetch_related_rows(
    conn: sqlite3.Connection, table: str, fk_col: str, fk_val: Any, sort_info: tuple[str, bool] | None = None
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}" WHERE "{fk_col}" = ?{_order_clause(sort_info)}', (fk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


# ─────────────────────────────────────────────
# Project Settings (Links & Sorts)
# ─────────────────────────────────────────────

class ProjectSettings:
    """
    Persists user-defined settings in <db>.dbobserver.json.
    Format: {
        "links": [ {from_table, from_col, to_table, to_col}, ... ],
        "sorts": { "table_name": ["col_name", reverse_bool], ... }
    }
    """

    @staticmethod
    def _path(db_path: str) -> Path:
        return Path(db_path).with_suffix(".dbobserver.json")

    @classmethod
    def load(cls, db_path: str) -> dict:
        p = cls._path(db_path)
        if not p.exists():
            return {"links": [], "sorts": {}}
        try:
            data = json.loads(p.read_text())
            return {
                "links": data.get("links", []),
                "sorts": data.get("sorts", {})
            }
        except Exception:
            return {"links": [], "sorts": {}}

    @classmethod
    def save(cls, db_path: str, data: dict) -> None:
        if not db_path: return
        cls._path(db_path).write_text(json.dumps(data, indent=2))

class VirtualLinks:
    """
    Helper for user-defined column→column links.
    """

    @classmethod
    def add(cls, db_path: str, from_table: str, from_col: str,
            to_table: str, to_col: str) -> None:
        data = ProjectSettings.load(db_path)
        entry = dict(from_table=from_table, from_col=from_col,
                     to_table=to_table,   to_col=to_col)
        if entry not in data["links"]:
            data["links"].append(entry)
            ProjectSettings.save(db_path, data)

    @classmethod
    def remove(cls, db_path: str, from_table: str, from_col: str,
               to_table: str, to_col: str) -> None:
        data = ProjectSettings.load(db_path)
        entry = dict(from_table=from_table, from_col=from_col,
                     to_table=to_table,   to_col=to_col)
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
            tt, tc = entry["to_table"],   entry["to_col"]
            if ft not in schema.fk_from:
                schema.fk_from[ft] = []
            if tt not in schema.fk_to:
                schema.fk_to[tt] = []
            fk = FKInfo(from_table=ft, from_col=fc,
                        to_table=tt,  to_col=tc, virtual=True)
            existing = {(f.from_col, f.to_table, f.to_col)
                        for f in schema.fk_from[ft]}
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

# ─────────────────────────────────────────────
# Graph traversal
# ─────────────────────────────────────────────

@dataclass
class Observation:
    """All rows gathered starting from a seed row."""
    seed_table: str
    seed_row:   tuple
    seed_cols:  list[str]
    # table → (columns, rows)
    related: dict[str, tuple[list[str], list[tuple]]] = field(default_factory=dict)


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

    # BFS по графу FK-связей.
    # visited: set of (table, pk_col, pk_val) - предотвращает рекурсию.
    # queue: list of (tbl, row_dict, cols) - строки, которые нужно раскрыть.
    visited: set[tuple] = set()
    queue: list[tuple[str, dict, list[str]]] = []

    seed_dict = dict(zip(cols, seed_row))
    visited.add((table, pk_col, pk_val))
    queue.append((table, seed_dict, cols))

    while queue:
        cur_table, cur_dict, cur_cols = queue.pop(0)

        # ── FK from cur_table → parent tables ──────────────────────────
        for fk in schema.fk_from.get(cur_table, []):
            fk_val = cur_dict.get(fk.from_col)
            if fk_val is None:
                continue
            sort_info = schema.sort_prefs.get(fk.to_table)
            r_cols, r_rows = fetch_related_rows(conn, fk.to_table, fk.to_col, fk_val, sort_info)
            _merge(obs.related, fk.to_table, r_cols, r_rows)
            # enqueue newly discovered rows
            for r_row in r_rows:
                r_pk_col = get_pk_column(conn, fk.to_table) or r_cols[0]
                r_dict = dict(zip(r_cols, r_row))
                r_pk_val = r_dict.get(r_pk_col, r_row[0])
                key = (fk.to_table, r_pk_col, r_pk_val)
                if key not in visited:
                    visited.add(key)
                    queue.append((fk.to_table, r_dict, r_cols))

        # ── FK pointing to cur_table → child tables ─────────────────────
        for fk in schema.fk_to.get(cur_table, []):
            ref_val = cur_dict.get(fk.to_col)
            if ref_val is None:
                continue
            sort_info = schema.sort_prefs.get(fk.from_table)
            r_cols, r_rows = fetch_related_rows(conn, fk.from_table, fk.from_col, ref_val, sort_info)
            _merge(obs.related, fk.from_table, r_cols, r_rows)
            for r_row in r_rows:
                r_pk_col = get_pk_column(conn, fk.from_table) or r_cols[0]
                r_dict = dict(zip(r_cols, r_row))
                r_pk_val = r_dict.get(r_pk_col, r_row[0])
                key = (fk.from_table, r_pk_col, r_pk_val)
                if key not in visited:
                    visited.add(key)
                    queue.append((fk.from_table, r_dict, r_cols))

    # seed не должен дублироваться в related
    obs.related.pop(table, None)
    
    # Final SQL sort for merged sets to guarantee correctness
    for tbl in list(obs.related.keys()):
        cols, rows = obs.related[tbl]
        sort_info = schema.sort_prefs.get(tbl)
        if sort_info and len(rows) > 1:
            obs.related[tbl] = (cols, sql_sort_rows(conn, tbl, cols, rows, sort_info))

    return obs


def _merge(
    store: dict[str, tuple[list[str], list[tuple]]],
    table: str,
    cols:  list[str],
    rows:  list[tuple],
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
    table:   str
    cols:    list[str]
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
        diffs.insert(0, TableDiff(
            table=f"{new.seed_table} (seed updated)",
            cols=new.seed_cols,
            new_rows=[new.seed_row],
        ))

    return diffs


# ─────────────────────────────────────────────
# Widgets
# ─────────────────────────────────────────────

NEW_STYLE  = "bold green"
SEED_STYLE = "bold cyan"
REL_STYLE  = "bold yellow"

def _fmt(v: Any) -> str:
    return str(v) if v is not None else "NULL"

def _row_strs(row: tuple) -> list[str]:
    return [_fmt(v) for v in row]

def _order_clause(sort_info: tuple[str, bool] | None) -> str:
    if not sort_info:
        return ""
    return f' ORDER BY "{sort_info[0]}" {"DESC" if sort_info[1] else "ASC"}'

def sql_sort_rows(conn: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple], sort_info: tuple[str, bool] | None) -> list[tuple]:
    if not sort_info or not rows: return rows
    pk_col = get_pk_column(conn, table)
    if not pk_col: return rows
    try:
        pk_idx = cols.index(pk_col)
        pk_vals = [r[pk_idx] for r in rows]
        placeholders = ",".join("?" for _ in pk_vals)
        cur = conn.cursor()
        cur.execute(f'SELECT * FROM "{table}" WHERE "{pk_col}" IN ({placeholders}){_order_clause(sort_info)}', pk_vals)
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
    sort_info: tuple[str, bool] | None = None
) -> str:
    """
    Return a column header string with relationship indicators:
      🔑 Primary key
      🔗 Real FK to another table
      ✨ Virtual (user-defined) link
      ↑↓ Sort indicator
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
        suffix = " [bold]↓[/bold]" if sort_info[1] else " [bold]↑[/bold]"

    if pk_prefix or fk_prefix:
        return f"{pk_prefix}{fk_prefix}{col}{suffix}"
    return f"{col}{suffix}"


class TableBlock(Static):
    """
    One table section: header label + DataTable.
    Supports adding new rows with a flash highlight.
    """

    def __init__(self, table: str, cols: list[str], rows: list[tuple],
                 is_seed: bool = False,
                 pk_cols: set[str] | None = None,
                 fk_cols: dict[str, FKInfo] | None = None,
                 schema: Schema | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.tbl_name  = table
        self.cols      = cols
        self.all_rows  = list(rows)
        self.is_seed   = is_seed
        self.schema    = schema
        self._pk_cols: set[str]          = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._flasher = RowFlasher()

    def compose(self) -> ComposeResult:
        color = SEED_STYLE if self.is_seed else REL_STYLE
        marker = "●" if self.is_seed else "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        yield Label(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]",
            id=f"lbl-{self.id}",
        )
        dt = DataTable(zebra_stripes=True, id=f"dt-{self.id}", cursor_type="cell")
        
        sort_info = self.schema.sort_prefs.get(self.tbl_name) if self.schema else None
        headers = [_col_header(c, self._pk_cols, self._fk_cols, sort_info) for c in self.cols]
        dt.add_columns(*headers)
        for row in self.all_rows:
            dt.add_row(*_row_strs(row), key=str(row))
        yield dt

    def update_rows(self, rows: list[tuple]) -> None:
        """Replace all rows and completely redraw the data table."""
        self.all_rows = list(rows)
        dt = self._dt()
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        sort_info = self.schema.sort_prefs.get(self.tbl_name) if self.schema else None
        headers = [_col_header(c, self._pk_cols, self._fk_cols, sort_info) for c in self.cols]
        dt.add_columns(*headers)
        for row in self.all_rows:
            key = str(row)
            dt.add_row(*_row_strs(row), key=key)
            if key in self._flasher._flash:
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self.all_rows)-1)), column=cur_c)
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

    def _refresh_label(self) -> None:
        color  = SEED_STYLE if self.is_seed else REL_STYLE
        marker = "●" if self.is_seed else "◆"
        tag    = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        new_cnt = self._flasher.count()
        new_tag = f"  [bold green]+{new_cnt} new[/bold green]" if new_cnt else ""
        self._lbl().update(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]{new_tag}"
        )


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
        db_path:    str,
        schema:     Schema,
        from_table: str,
        from_col:   str,
    ) -> None:
        super().__init__()
        self._db_path    = db_path
        self._schema     = schema
        self._from_table = from_table
        self._from_col   = from_col
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
            # mark columns that are already a target of a link from this source
            existing_targets = {
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
            for col in self._schema.col_cache.get(self._target_table, []):
                badge = "  [dim green](✓ linked)[/dim green]" if (self._target_table, col) in existing_targets else ""
                lv.append(ListItem(Label(f"[yellow]{col}[/]{badge}"), name=col))

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
            VirtualLinks.add(
                self._db_path,
                self._from_table, self._from_col,
                self._target_table, name,
            )
            self.dismiss(True)


# ─────────────────────────────────────────────
# Expanded (fullscreen) table modal
# ─────────────────────────────────────────────

class ExpandedTableScreen(ModalScreen):
    """Full-screen view of a single table. Esc to close. L to toggle live."""

    BINDINGS = [
        Binding("escape,q,f", "dismiss",     "Close", show=True),
        Binding("l",          "toggle_live",  "Live", show=True),
        Binding("k",          "link",         "Link cols", show=True),
        Binding("s",          "sort_column",  "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(
        self,
        title:  str,
        cols:   list[str],
        rows:   list[tuple],
        *,
        conn:    sqlite3.Connection | None = None,
        schema:  "Schema | None" = None,
        tbl_name: str | None = None,
        pk_cols:  set[str] | None = None,
        fk_cols:  "dict[str, FKInfo] | None" = None,
    ) -> None:
        super().__init__()
        self._title    = title
        self._cols     = cols
        self._rows     = list(rows)
        self._conn     = conn
        self._schema   = schema
        self._tbl_name = tbl_name
        self._pk_cols: set[str]          = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._timer: Timer | None = None
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
        
        self._rows = sql_sort_rows(self._conn, self._tbl_name, self._cols, self._rows, self._schema.sort_prefs.get(self._tbl_name) if self._schema else None)
        
        dt = DataTable(
            id="expanded-dt",
            zebra_stripes=True,
            cursor_type="cell",
        )
        sort_info = self._schema.sort_prefs.get(self._tbl_name) if self._schema else None
        headers = [_col_header(c, self._pk_cols, self._fk_cols, sort_info) for c in self._cols]
        dt.add_columns(*headers)
        for row in self._rows:
            dt.add_row(*_row_strs(row), key=str(row))
        yield dt

    @on(DataTable.HeaderSelected, "#expanded-dt")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if not self._schema or not self._tbl_name: return
        self._toggle_sort(self._cols[event.column_index])

    def _redraw_dt(self) -> None:
        if not self._schema or not self._tbl_name: return
        dt = self.query_one("#expanded-dt", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        self._rows = sql_sort_rows(self._conn, self._tbl_name, self._cols, self._rows, self._schema.sort_prefs.get(self._tbl_name))
        dt.clear(columns=True)
        sort_info = self._schema.sort_prefs.get(self._tbl_name)
        headers = [_col_header(c, self._pk_cols, self._fk_cols, sort_info) for c in self._cols]
        dt.add_columns(*headers)
        for row in self._rows:
            key = str(row)
            dt.add_row(*_row_strs(row), key=key)
            if key in self._flasher._flash:
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self._rows) - 1)), column=cur_c)

    def on_mount(self) -> None:
        self._update_live_label()
        self.query_one("#expanded-dt", DataTable).focus()

    # ── live toggle ──────────────────────────

    def action_toggle_live(self) -> None:
        if self._conn is None or self._tbl_name is None:
            self.notify("Live mode unavailable (no DB context)", severity="warning")
            return
        self.live = not self.live

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
            _, all_rows = fetch_all_rows(self._conn, self._tbl_name)
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
            if self.live else ""
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

        raw_row  = self._rows[row_index]
        row_dict = dict(zip(self._cols, raw_row))
        pk_col   = get_pk_column(self._conn, self._tbl_name) or self._cols[0]
        pk_val   = row_dict.get(pk_col, raw_row[0])

        self.app.push_screen(
            ObservationScreen(
                self._conn, self._schema,
                self._tbl_name, pk_col, pk_val,
            )
        )

    # ── link builder ─────────────────────────

    def action_link(self) -> None:
        if not self._schema or not self._schema.db_path or not self._tbl_name:
            return

        dt = self.query_one("#expanded-dt", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self._cols):
            return
        from_col = self._cols[col_index]

        def on_result(saved: bool) -> None:
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
            on_result,
        )


# ─────────────────────────────────────────────
# Screens
# ─────────────────────────────────────────────

class ObservationScreen(Screen):
    """Shows the observation. Press L to toggle live polling."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("l",        "toggle_live",    "Live",   show=True),
        Binding("f",        "expand_focused", "Expand", show=True),
        Binding("k",        "link",           "Link cols", show=True),
        Binding("s",        "sort_column",    "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(
        self,
        conn:    sqlite3.Connection,
        schema:  Schema,
        table:   str,
        pk_col:  str,
        pk_val:  Any,
    ) -> None:
        super().__init__()
        self._conn   = conn
        self._schema = schema
        self._table  = table
        self._pk_col = pk_col
        self._pk_val = pk_val

        self._obs    = build_observation(conn, schema, table, pk_col, pk_val)
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
                        id=f"block-{bid}",
                        classes="obs-block",
                    )
                    self._blocks[bid] = blk
                    yield blk

    # ── live toggle ──────────────────────────

    def action_toggle_live(self) -> None:
        self.live = not self.live

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
        """Open LinkBuilderScreen using the currently focused cell as source."""
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

        # get the column name of the cursor cell (strip badge prefix)
        col_index = focused.cursor_column
        if col_index >= len(block.cols):
            return
        from_col = block.cols[col_index]

        def on_result(saved: bool) -> None:
            if saved:
                VirtualLinks.inject(self._schema)
                self._obs = build_observation(
                    self._conn, self._schema,
                    self._table, self._pk_col, self._pk_val,
                )
                self.notify(
                    f"{block.tbl_name}.{from_col} linked",
                    title="Virtual link created",
                )
                self._rebuild_blocks()

        self.app.push_screen(
            LinkBuilderScreen(
                db_path=db_path,
                schema=self._schema,
                from_table=block.tbl_name,
                from_col=from_col,
            ),
            on_result,
        )

    def action_sort_column(self) -> None:
        focused = self.focused
        if not isinstance(focused, DataTable):
            self.notify("Focus a table cell first", severity="warning")
            return
        block = self._block_for_widget(focused)
        if block is None:
            return

        col_index = focused.cursor_column
        if col_index >= len(block.cols):
            return
        col_name = block.cols[col_index]
        toggle_and_save_sort(self._schema, block.tbl_name, col_name)
        self._reload()

    def _rebuild_blocks(self) -> None:
        """Mount blocks for tables that appeared after a schema change."""
        scroll: VerticalScroll = self.query_one("#obs-scroll")
        obs = self._obs
        for tbl_name, (cols, rows) in obs.related.items():
            if tbl_name in self._blocks:
                continue   # already displayed
            bid = tbl_name
            _pk, _fk = _build_col_meta(self._conn, self._schema, tbl_name)
            blk = TableBlock(
                table=tbl_name, cols=cols, rows=rows,
                is_seed=False, pk_cols=_pk, fk_cols=_fk, schema=self._schema,
                id=f"block-{bid}", classes="obs-block",
            )
            self._blocks[bid] = blk
            scroll.mount(blk)

    def on_mount(self) -> None:
        self.watch_live(False)   # set initial status label
        # give keyboard focus to scroll container so arrow keys work
        self.query_one("#obs-scroll").focus()

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
        block = self._block_for_widget(event.data_table)
        if not block: return
        col_name = block.cols[event.column_index]
        toggle_and_save_sort(self._schema, block.tbl_name, col_name)
        self._reload()

    def _reload(self) -> None:
        """Fetch fresh observation after sort changes, completely redraw all blocks."""
        self._obs = build_observation(
            self._conn, self._schema,
            self._table, self._pk_col, self._pk_val,
        )
        for bid, blk in self._blocks.items():
            real_tbl = blk.tbl_name
            if real_tbl == self._obs.seed_table:
                blk.update_rows([self._obs.seed_row] if self._obs.seed_row else [])
            elif real_tbl in self._obs.related:
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

        raw_row  = block.all_rows[row_index]
        row_dict = dict(zip(block.cols, raw_row))
        pk_col   = get_pk_column(self._conn, block.tbl_name) or block.cols[0]
        pk_val   = row_dict.get(pk_col, raw_row[0])

        self.app.push_screen(
            ObservationScreen(
                self._conn, self._schema,
                block.tbl_name, pk_col, pk_val,
            )
        )

    # ── expand ───────────────────────────────

    def action_expand_focused(self) -> None:
        """Open the currently-focused DataTable in full-screen modal."""
        focused = self.focused
        target_block = (
            self._block_for_widget(focused)
            if isinstance(focused, DataTable) else None
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
            )
        )

    # ── poll ─────────────────────────────────

    def _poll(self) -> None:
        """Fetch fresh observation, compute diff, update UI."""
        try:
            new_obs = build_observation(
                self._conn, self._schema,
                self._table, self._pk_col, self._pk_val,
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
                update_live_label(lbl, self.live, f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]")
            except Exception:
                pass


class RowPickerScreen(Screen):
    """Pick a row from a table. L - live mode, R - manual refresh."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("r",        "refresh",        "Refresh", show=True),
        Binding("l",        "toggle_live",    "Live", show=True),
        Binding("k",        "link",           "Link cols", show=True),
        Binding("s",        "sort_column",    "Sort", show=True),
    ]

    live: reactive[bool] = reactive(False)

    def __init__(self, conn: sqlite3.Connection, schema: Schema, table: str) -> None:
        super().__init__()
        self.conn   = conn
        self.schema = schema
        self.table  = table
        self.pk_col = get_pk_column(conn, table)
        self.cols, self.rows = fetch_all_rows(conn, table)
        self._known_rows: set[tuple] = set(self.rows)
        self._flasher = RowFlasher()
        self._timer: Timer | None = None

    # ── live ─────────────────────────────────

    def action_toggle_live(self) -> None:
        self.live = not self.live

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
        _, all_rows = fetch_all_rows(self.conn, self.table)
        new_rows = [r for r in all_rows if r not in self._known_rows]

        if new_rows:
            for row in new_rows:
                self._known_rows.add(row)
                self._flasher.add(str(row))
            self.rows = all_rows
            self._redraw_dt()
            self.app.sub_title = f"{self.table}  ({len(self.rows)} rows) - Enter to observe"

        # tick flash
        self._flasher.tick(self.query_one("#row-table", DataTable))

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_live_label(f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]")

    # ── reload (manual refresh) ───────────────

    def _reload(self) -> None:
        sort_info = self.schema.sort_prefs.get(self.table)
        self.cols, self.rows = fetch_all_rows(self.conn, self.table, sort_info)
        self._known_rows = set(self.rows)
        self._flasher.clear()
        self._redraw_dt()
        self.app.sub_title = f"{self.table}  ({len(self.rows)} rows) - Enter to observe"

    def action_refresh(self) -> None:
        self._reload()
        self.query_one("#row-table", DataTable).focus()

    def on_mount(self) -> None:
        self.app.sub_title = f"{self.table}  ({len(self.rows)} rows) - Enter to observe"
        self._update_live_label()
        self.query_one("#row-table", DataTable).focus()

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
        
        self.rows = sql_sort_rows(self.conn, self.table, self.cols, self.rows, self.schema.sort_prefs.get(self.table))
        sort_info = self.schema.sort_prefs.get(self.table)
        headers = [_col_header(c, pk_cols, fk_cols, sort_info) for c in self.cols]
        dt.add_columns(*headers)
        for row in self.rows:
            dt.add_row(*_row_strs(row), key=str(row))
        yield dt

    @on(DataTable.HeaderSelected, "#row-table")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self._toggle_sort(self.cols[event.column_index])

    def action_sort_column(self) -> None:
        if not self.schema or not self.table: return
        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols): return
        self._toggle_sort(self.cols[col_index])

    def _toggle_sort(self, col_name: str) -> None:
        toggle_and_save_sort(self.schema, self.table, col_name)
        self._reload()

    def _redraw_dt(self) -> None:
        dt = self.query_one("#row-table", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        pk_cols, fk_cols = _build_col_meta(self.conn, self.schema, self.table)
        sort_info = self.schema.sort_prefs.get(self.table)
        headers = [_col_header(c, pk_cols, fk_cols, sort_info) for c in self.cols]
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
        raw_row  = self.rows[row_index]
        row_dict = dict(zip(self.cols, raw_row))
        pk_col   = self.pk_col or self.cols[0]
        pk_val   = row_dict.get(pk_col, raw_row[0])

        screen = ObservationScreen(self.conn, self.schema, self.table, pk_col, pk_val)
        self.app.push_screen(screen)

    # ── link builder ─────────────────────────

    def action_link(self) -> None:
        if not self.schema or not self.schema.db_path or not self.table:
            return

        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols):
            return
        from_col = self.cols[col_index]

        def on_result(saved: bool) -> None:
            if saved:
                VirtualLinks.inject(self.schema)
                self.notify(
                    f"{self.table}.{from_col} linked",
                    title="Virtual link created",
                )
                self._reload()

        self.app.push_screen(
            LinkBuilderScreen(
                db_path=self.schema.db_path,
                schema=self.schema,
                from_table=self.table,
                from_col=from_col,
            ),
            on_result,
        )


class TablePickerScreen(Screen):
    """Pick a table from the database."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back", show=True)]

    def __init__(self, conn: sqlite3.Connection, schema: Schema, db_path: str) -> None:
        super().__init__()
        self.conn    = conn
        self.schema  = schema
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
        self.app.push_screen(
            RowPickerScreen(self.conn, self.schema, event.item.name)
        )


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
"""


class DbObserverApp(App):
    TITLE = "DbObserver"
    CSS   = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=True)]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.initial_db_path = db_path

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
