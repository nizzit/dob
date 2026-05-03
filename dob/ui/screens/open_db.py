"""
dob.ui.screens.open_db
~~~~~~~~~~~~~~~~~~~~~~
OpenDBScreen — entry screen for entering/loading a DB path.
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


class OpenDBScreen(Screen):
    """Entry screen — enter a path to a SQLite database."""

    BINDINGS = [Binding("ctrl+q", "app.quit", "Quit", show=True)]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical(id="open-db-container"):
            yield Static(
                "[bold cyan]dob[/]\n[dim]SQLite relationship explorer[/dim]",
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
            conn = open_connection(p)
            schema = load_schema(conn)
            schema.db_path = str(p)
            VirtualLinks.inject(schema, str(p))
            prefs = UserPreferences(str(p))
            self.app.push_screen(TablePickerScreen(conn, schema, prefs, str(p)))
        except Exception as exc:
            err.update(f"[red]Error: {exc}[/red]")
