"""
dob.ui.screens.open_db
~~~~~~~~~~~~~~~~~~~~~~
OpenDBScreen — entry screen for entering/loading a DB path or MySQL DSN.

Supports two modes:
  • SQLite — enter a local file path (e.g. ``./test.db``)
  • MySQL  — enter a DSN starting with ``mysql://``
             (e.g. ``mysql://user:pass@localhost/mydb``)
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, Static

from dob.db.connection import open_connection
from dob.db.schema import load_schema
from dob.settings.links import VirtualLinks
from dob.settings.preferences import UserPreferences
from dob.ui.screens.table_picker import TablePickerScreen

_MYSQL_SCHEME = "mysql://"


class OpenDBScreen(Screen):
    """Entry screen — enter a SQLite path or a MySQL DSN."""

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit", show=True)]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical(id="open-db-container"):
            yield Static(
                "[bold cyan]dob[/]\n[dim]SQL relationship explorer[/dim]",
                id="logo",
            )
            yield Label("SQLite path or MySQL DSN:")
            yield Input(
                placeholder="e.g. ./test.db  or  mysql://user:pass@host/db",
                id="db-path-input",
            )
            yield Label("", id="error-label")
            yield Static(
                "[dim]SQLite: enter a file path\n"
                "MySQL:  mysql://user:pass@host[:port]/database[/dim]",
                id="hint-label",
            )

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
            err.update("[red]Please enter a path or DSN.[/red]")
            return

        is_mysql = path.startswith(_MYSQL_SCHEME)

        # For SQLite, validate the file exists before trying to connect
        if not is_mysql:
            p = Path(path)
            if not p.exists():
                err.update(f"[red]File not found: {path}[/red]")
                return

        try:
            conn = open_connection(path)
            schema = load_schema(conn)
            # VirtualLinks are only meaningful for SQLite (file-based) databases
            schema.db_path = str(Path(path)) if not is_mysql else path
            if not is_mysql:
                VirtualLinks.inject(schema, str(Path(path)))
            prefs = UserPreferences(schema.db_path)
            self.app.push_screen(TablePickerScreen(conn, schema, prefs, path))
        except Exception as exc:
            err.update(f"[red]Error: {exc}[/red]")
