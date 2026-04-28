"""
DbObserver — minimal SQLite relationship explorer.

Flow:
  1. Open a .db file (passed as CLI arg or entered in the app)
  2. Pick a table  →  pick a row
  3. The app traverses all FK relations (both directions) and
     collects every related row from every connected table.
  4. ObservationScreen shows all gathered rows grouped by table.
     Live-mode (L) polls the DB every N seconds and highlights new rows.
"""

from __future__ import annotations

import sqlite3
import sys
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


@dataclass
class Schema:
    tables: list[str]
    fk_from: dict[str, list[FKInfo]] = field(default_factory=dict)
    fk_to:   dict[str, list[FKInfo]] = field(default_factory=dict)


def load_schema(conn: sqlite3.Connection) -> Schema:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    schema = Schema(tables=tables)
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

    return schema


def fetch_all_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}"')
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


def fetch_related_rows(
    conn: sqlite3.Connection, table: str, fk_col: str, fk_val: Any
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}" WHERE "{fk_col}" = ?', (fk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


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
    row_dict = dict(zip(cols, seed_row))

    # 1. FK from seed → parents
    for fk in schema.fk_from.get(table, []):
        fk_val = row_dict.get(fk.from_col)
        if fk_val is None:
            continue
        r_cols, r_rows = fetch_related_rows(conn, fk.to_table, fk.to_col, fk_val)
        _merge(obs.related, fk.to_table, r_cols, r_rows)

    # 2. FK pointing to seed → children
    for fk in schema.fk_to.get(table, []):
        seed_val = row_dict.get(fk.to_col)
        if seed_val is None:
            seed_val = pk_val if pk_col == fk.to_col else None
        if seed_val is None:
            continue
        r_cols, r_rows = fetch_related_rows(conn, fk.from_table, fk.from_col, seed_val)
        _merge(obs.related, fk.from_table, r_cols, r_rows)

    # 3. One level deeper: parents of related rows
    for rel_table, (r_cols, r_rows) in list(obs.related.items()):
        for r_row in r_rows:
            r_dict = dict(zip(r_cols, r_row))
            for fk in schema.fk_from.get(rel_table, []):
                if fk.to_table == table:
                    continue
                fk_val = r_dict.get(fk.from_col)
                if fk_val is None:
                    continue
                p_cols, p_rows = fetch_related_rows(conn, fk.to_table, fk.to_col, fk_val)
                _merge(obs.related, fk.to_table, p_cols, p_rows)

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


class TableBlock(Static):
    """
    One table section: header label + DataTable.
    Supports adding new rows with a flash highlight.
    """

    def __init__(self, table: str, cols: list[str], rows: list[tuple],
                 is_seed: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tbl_name  = table
        self.cols      = cols
        self.all_rows  = list(rows)
        self.is_seed   = is_seed
        # row_key → flash countdown (ticks remaining)
        self._flash: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        color = SEED_STYLE if self.is_seed else REL_STYLE
        marker = "●" if self.is_seed else "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        yield Label(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]",
            id=f"lbl-{self.id}",
        )
        dt = DataTable(zebra_stripes=True, id=f"dt-{self.id}", cursor_type="row")
        dt.add_columns(*self.cols)
        for row in self.all_rows:
            dt.add_row(*_row_strs(row))
        yield dt

    def _dt(self) -> DataTable:
        return self.query_one(f"#dt-{self.id}", DataTable)

    def _lbl(self) -> Label:
        return self.query_one(f"#lbl-{self.id}", Label)

    def add_new_rows(self, new_rows: list[tuple]) -> None:
        """Append rows and flash them green."""
        dt = self._dt()
        for row in new_rows:
            self.all_rows.append(row)
            key = f"new-{id(row)}"
            dt.add_row(*_row_strs(row), key=key)
            self._flash[key] = 3          # будет убран через 3 тика (~6 сек)
            # подсветка через стиль строки не поддерживается в Textual напрямую —
            # добавляем маркер в первую ячейку
            self._mark_row(dt, key, new=True)

        # обновляем счётчик в заголовке
        self._refresh_label()

    def tick_flash(self) -> None:
        """Called every poll tick. Removes highlight after countdown."""
        if not self._flash:
            return
        dt = self._dt()
        expired = [k for k, v in self._flash.items() if v <= 1]
        for key in expired:
            self._mark_row(dt, key, new=False)
            del self._flash[key]
        for key in self._flash:
            self._flash[key] -= 1

    def _mark_row(self, dt: DataTable, key: str, new: bool) -> None:
        """Prefix first cell with ▶ marker for new rows."""
        try:
            cell = dt.get_cell(key, dt.ordered_columns[0].key)
            val = str(cell)
            # strip old marker
            val = val.lstrip("▶ ")
            if new:
                val = f"▶ {val}"
            dt.update_cell(key, dt.ordered_columns[0].key, val)
        except Exception:
            pass

    def _refresh_label(self) -> None:
        color  = SEED_STYLE if self.is_seed else REL_STYLE
        marker = "●" if self.is_seed else "◆"
        tag    = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        new_cnt = len(self._flash)
        new_tag = f"  [bold green]+{new_cnt} new[/bold green]" if new_cnt else ""
        self._lbl().update(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]{new_tag}"
        )


# ─────────────────────────────────────────────
# Expanded (fullscreen) table modal
# ─────────────────────────────────────────────

class ExpandedTableScreen(ModalScreen):
    """Full-screen view of a single table. Esc to close."""

    BINDINGS = [Binding("escape,q,f", "dismiss", "Close")]

    def __init__(self, title: str, cols: list[str], rows: list[tuple]) -> None:
        super().__init__()
        self._title = title
        self._cols  = cols
        self._rows  = rows

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label(
            f"[bold cyan]{self._title}[/]  [dim]{len(self._rows)} rows — Esc / F to close[/dim]",
            id="expanded-title",
        )
        dt = DataTable(
            id="expanded-dt",
            zebra_stripes=True,
            cursor_type="row",
        )
        dt.add_columns(*self._cols)
        for row in self._rows:
            dt.add_row(*_row_strs(row))
        yield dt

    def on_mount(self) -> None:
        self.query_one("#expanded-dt", DataTable).focus()


# ─────────────────────────────────────────────
# Screens
# ─────────────────────────────────────────────

class ObservationScreen(Screen):
    """Shows the observation. Press L to toggle live polling."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("l",        "toggle_live",    "Live",   show=True),
        Binding("f",        "expand_focused", "Expand", show=True),
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
            blk = TableBlock(
                table=obs.seed_table,
                cols=obs.seed_cols,
                rows=[obs.seed_row] if obs.seed_row else [],
                is_seed=True,
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
                    blk = TableBlock(
                        table=tbl_name,
                        cols=cols,
                        rows=rows,
                        is_seed=False,
                        id=f"block-{bid}",
                        classes="obs-block",
                    )
                    self._blocks[bid] = blk
                    yield blk

    # ── live toggle ──────────────────────────

    def action_toggle_live(self) -> None:
        self.live = not self.live

    def watch_live(self, value: bool) -> None:
        status: Label = self.query_one("#live-status")
        if value:
            status.update(
                f"[bold green]● LIVE[/]  [dim]polling every {LIVE_INTERVAL}s — press L to stop[/dim]"
            )
            self._timer = self.set_interval(LIVE_INTERVAL, self._poll)
        else:
            status.update("[dim]○ live off — press L to start[/dim]")
            if self._timer:
                self._timer.stop()
                self._timer = None

    def on_mount(self) -> None:
        self.watch_live(False)   # set initial status label
        # give keyboard focus to scroll container so arrow keys work
        self.query_one("#obs-scroll").focus()

    # ── expand ───────────────────────────────

    def action_expand_focused(self) -> None:
        """Open the currently-focused DataTable in full-screen modal."""
        # find which block's DataTable has focus (or just take the first one)
        focused = self.focused
        target_block: TableBlock | None = None

        if isinstance(focused, DataTable):
            # walk up to find parent TableBlock
            node = focused.parent
            while node is not None:
                if isinstance(node, TableBlock):
                    target_block = node
                    break
                node = node.parent

        # fallback: expand whichever block is under scroll offset (first one)
        if target_block is None and self._blocks:
            target_block = next(iter(self._blocks.values()))

        if target_block is None:
            return

        self.app.push_screen(
            ExpandedTableScreen(
                title=target_block.tbl_name,
                cols=list(target_block.cols),
                rows=list(target_block.all_rows),
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
                self._blocks[real_tbl].add_new_rows(diff.new_rows)
            else:
                # новая таблица появилась — создаём блок и монтируем
                bid = real_tbl
                blk = TableBlock(
                    table=real_tbl,
                    cols=diff.cols,
                    rows=diff.new_rows,
                    is_seed=False,
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
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S")
            status: Label = self.query_one("#live-status")
            n_new = sum(len(d.new_rows) for d in diffs)
            new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
            status.update(
                f"[bold green]● LIVE[/]  [dim]last poll {ts}{new_tag} — press L to stop[/dim]"
            )


class RowPickerScreen(Screen):
    """Pick a row from a table."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

    def __init__(self, conn: sqlite3.Connection, schema: Schema, table: str) -> None:
        super().__init__()
        self.conn   = conn
        self.schema = schema
        self.table  = table
        self.pk_col = get_pk_column(conn, table)
        self.cols, self.rows = fetch_all_rows(conn, table)

    def on_mount(self) -> None:
        self.app.sub_title = f"{self.table}  ({len(self.rows)} rows) — Enter to observe"
        self.query_one("#row-table", DataTable).focus()

    def on_unmount(self) -> None:
        self.app.sub_title = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        dt = DataTable(id="row-table", zebra_stripes=True, cursor_type="row")
        dt.add_columns(*self.cols)
        for row in self.rows:
            dt.add_row(*[_fmt(v) for v in row], key=str(row))
        yield dt

    @on(DataTable.RowSelected, "#row-table")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if row_index >= len(self.rows):
            return
        raw_row  = self.rows[row_index]
        row_dict = dict(zip(self.cols, raw_row))
        pk_col   = self.pk_col or self.cols[0]
        pk_val   = row_dict.get(pk_col, raw_row[0])

        screen = ObservationScreen(self.conn, self.schema, self.table, pk_col, pk_val)
        self.app.push_screen(screen)


class TablePickerScreen(Screen):
    """Pick a table from the database."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

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
                f"[bold]Database:[/] [yellow]{self.db_path}[/]  —  pick a table",
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

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit")]

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
            schema = load_schema(conn)
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
"""


class DbObserverApp(App):
    TITLE = "DbObserver"
    CSS   = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

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
