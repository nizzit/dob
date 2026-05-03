"""
dob.ui.screens.table_picker
~~~~~~~~~~~~~~~~~~~~~~~~~~~
TablePickerScreen — pick a table from the open database.
"""

from __future__ import annotations

import sqlite3

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from dob.db.schema import Schema
from dob.settings.preferences import UserPreferences
from dob.ui.screens.row_picker import RowPickerScreen


class TablePickerScreen(Screen):
    """Pick a table from the database."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back", show=True)]

    def __init__(
        self,
        conn: sqlite3.Connection,
        schema: Schema,
        prefs: UserPreferences,
        db_path: str,
    ) -> None:
        super().__init__()
        self._conn = conn
        self._schema = schema
        self._prefs = prefs
        self._db_path = db_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical():
            yield Label(
                f"[bold]Database:[/] [yellow]{self._db_path}[/]  -  pick a table",
                classes="screen-title",
            )
            items = [ListItem(Label(t), name=t) for t in self._schema.tables]
            yield ListView(*items, id="table-list")

    @on(ListView.Selected, "#table-list")
    def table_selected(self, event: ListView.Selected) -> None:
        self.app.push_screen(
            RowPickerScreen(self._conn, self._schema, self._prefs, event.item.name)
        )
