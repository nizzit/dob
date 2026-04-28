"""
DbObserver — minimal SQLite relationship explorer.

Flow:
  1. Open a .db file (passed as CLI arg or entered in the app)
  2. Pick a table  →  pick a row
  3. The app traverses all FK relations (both directions) and
     collects every related row from every connected table.
  4. A flat "observation" view shows all gathered rows grouped by table.
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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
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
    # table → list of FK definitions originating FROM that table
    fk_from: dict[str, list[FKInfo]] = field(default_factory=dict)
    # table → list of FK definitions pointing TO that table
    fk_to: dict[str, list[FKInfo]] = field(default_factory=dict)


def load_schema(conn: sqlite3.Connection) -> Schema:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

    schema = Schema(tables=tables)
    for table in tables:
        schema.fk_from[table] = []
        cur.execute(f"PRAGMA foreign_key_list('{table}')")
        for row in cur.fetchall():
            # id, seq, table, from, to, on_update, on_delete, match
            fk = FKInfo(
                from_table=table,
                from_col=row[3],
                to_table=row[2],
                to_col=row[4],
            )
            schema.fk_from[table].append(fk)

    # build reverse index
    for table in tables:
        schema.fk_to[table] = []
    for table in tables:
        for fk in schema.fk_from[table]:
            schema.fk_to[fk.to_table].append(fk)

    return schema


def fetch_all_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM \"{table}\"")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def fetch_row_by_pk(
    conn: sqlite3.Connection, table: str, pk_col: str, pk_val: Any
) -> tuple[list[str], tuple | None]:
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM \"{table}\" WHERE \"{pk_col}\" = ?", (pk_val,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return cols, row


def get_pk_column(conn: sqlite3.Connection, table: str) -> str | None:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    for row in cur.fetchall():
        if row[5] == 1:  # pk flag
            return row[1]
    return None


def fetch_related_rows(
    conn: sqlite3.Connection,
    table: str,
    fk_col: str,
    fk_val: Any,
) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM \"{table}\" WHERE \"{fk_col}\" = ?", (fk_val,))
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


# ─────────────────────────────────────────────
# Graph traversal
# ─────────────────────────────────────────────

@dataclass
class Observation:
    """All rows gathered starting from a seed row."""
    seed_table: str
    seed_row: tuple
    seed_cols: list[str]
    # table_name → (columns, [rows])
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

    # 1. Follow FK's from the seed row  → parent tables
    for fk in schema.fk_from.get(table, []):
        fk_val = row_dict.get(fk.from_col)
        if fk_val is None:
            continue
        r_cols, r_rows = fetch_related_rows(conn, fk.to_table, fk.to_col, fk_val)
        _merge(obs.related, fk.to_table, r_cols, r_rows)

    # 2. Follow FK's pointing TO this table → child tables
    for fk in schema.fk_to.get(table, []):
        seed_val = row_dict.get(fk.to_col)
        if seed_val is None:
            # try PK
            if pk_col == fk.to_col:
                seed_val = pk_val
            else:
                continue
        r_cols, r_rows = fetch_related_rows(conn, fk.from_table, fk.from_col, seed_val)
        _merge(obs.related, fk.from_table, r_cols, r_rows)

    # 3. One level deeper: for every collected related row, follow ITS FK's
    #    (only upward / parents to avoid explosion)
    for rel_table, (r_cols, r_rows) in list(obs.related.items()):
        for r_row in r_rows:
            r_dict = dict(zip(r_cols, r_row))
            for fk in schema.fk_from.get(rel_table, []):
                if fk.to_table == table:
                    continue  # already have it
                fk_val = r_dict.get(fk.from_col)
                if fk_val is None:
                    continue
                p_cols, p_rows = fetch_related_rows(conn, fk.to_table, fk.to_col, fk_val)
                _merge(obs.related, fk.to_table, p_cols, p_rows)

    return obs


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
        existing_rows = store[table][1]
        seen = set(existing_rows)
        for r in rows:
            if r not in seen:
                existing_rows.append(r)
                seen.add(r)


# ─────────────────────────────────────────────
# Screens
# ─────────────────────────────────────────────


class ObservationScreen(Screen):
    """Shows the collected observation as stacked DataTables."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

    def __init__(self, obs: Observation) -> None:
        super().__init__()
        self.obs = obs

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with VerticalScroll():
            obs = self.obs
            # Seed row
            yield Label(
                f"[bold cyan]● {obs.seed_table}[/]  [dim](seed)[/dim]",
                classes="table-label",
            )
            seed_table = DataTable(zebra_stripes=True, classes="obs-table")
            seed_table.add_columns(*obs.seed_cols)
            seed_table.add_row(*[str(v) if v is not None else "NULL" for v in obs.seed_row])
            yield seed_table

            if not obs.related:
                yield Label("[dim]No related records found.[/dim]")
            else:
                for tbl_name, (cols, rows) in obs.related.items():
                    yield Label(
                        f"[bold green]◆ {tbl_name}[/]  [dim]({len(rows)} row{'s' if len(rows)!=1 else ''})[/dim]",
                        classes="table-label",
                    )
                    dt = DataTable(zebra_stripes=True, classes="obs-table")
                    dt.add_columns(*cols)
                    for row in rows:
                        dt.add_row(*[str(v) if v is not None else "NULL" for v in row])
                    yield dt


class RowPickerScreen(Screen):
    """Pick a row from a table."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

    def __init__(
        self,
        conn: sqlite3.Connection,
        schema: Schema,
        table: str,
    ) -> None:
        super().__init__()
        self.conn = conn
        self.schema = schema
        self.table = table
        self.pk_col = get_pk_column(conn, table)
        self.cols, self.rows = fetch_all_rows(conn, table)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical():
            yield Label(
                f"[bold]Table:[/] [cyan]{self.table}[/]  —  pick a row (Enter to observe)",
                classes="screen-title",
            )
            dt = DataTable(
                id="row-table",
                zebra_stripes=True,
                cursor_type="row",
            )
            dt.add_columns(*self.cols)
            for row in self.rows:
                dt.add_row(*[str(v) if v is not None else "NULL" for v in row], key=str(row))
            yield dt

    def on_mount(self) -> None:
        self.query_one("#row-table", DataTable).focus()

    @on(DataTable.RowSelected, "#row-table")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if row_index >= len(self.rows):
            return
        raw_row = self.rows[row_index]
        row_dict = dict(zip(self.cols, raw_row))

        if self.pk_col and self.pk_col in row_dict:
            pk_val = row_dict[self.pk_col]
        else:
            # fallback: use first column
            pk_col = self.cols[0]
            pk_val = raw_row[0]
            self.pk_col = pk_col

        obs = build_observation(
            self.conn, self.schema, self.table, self.pk_col, pk_val
        )
        self.app.push_screen(ObservationScreen(obs))


class TablePickerScreen(Screen):
    """Pick a table from the database."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back")]

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
                f"[bold]Database:[/] [yellow]{self.db_path}[/]  —  pick a table",
                classes="screen-title",
            )
            items = [ListItem(Label(t), name=t) for t in self.schema.tables]
            yield ListView(*items, id="table-list")

    @on(ListView.Selected, "#table-list")
    def table_selected(self, event: ListView.Selected) -> None:
        table = event.item.name
        self.app.push_screen(
            RowPickerScreen(self.conn, self.schema, table)
        )


class OpenDBScreen(Screen):
    """Entry screen — enter path to a SQLite database."""

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
            yield Input(placeholder="e.g. ./lazyfit.db", id="db-path-input")
            yield Label("", id="error-label")

    def on_mount(self) -> None:
        # if a path was pre-filled via CLI, trigger immediately
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
            conn = sqlite3.connect(str(p))
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

.table-label {
    margin: 1 1 0 1;
}

.obs-table {
    margin: 0 1 1 1;
    height: auto;
    max-height: 20;
}

DataTable {
    height: auto;
    max-height: 25;
}

#error-label {
    color: $error;
}
"""


class DbObserverApp(App):
    TITLE = "DbObserver"
    CSS = CSS
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.initial_db_path = db_path

    def on_mount(self) -> None:
        screen = OpenDBScreen()
        self.push_screen(screen)
        if self.initial_db_path:
            # small delay so the screen is fully rendered
            self.call_after_refresh(self._auto_open)

    def _auto_open(self) -> None:
        screen: OpenDBScreen = self.screen  # type: ignore
        if isinstance(screen, OpenDBScreen):
            screen.set_initial_path(self.initial_db_path)
            screen._try_open(self.initial_db_path)


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = DbObserverApp(db_path=db_path)
    app.run()


if __name__ == "__main__":
    main()
