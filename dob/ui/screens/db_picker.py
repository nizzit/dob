"""
dob.ui.screens.db_picker
~~~~~~~~~~~~~~~~~~~~~~~~
DbPickerScreen — shown when a MySQL DSN does not include a database name.

Connects to the server without a database selected, runs ``SHOW DATABASES``,
lets the user pick one, then opens a full connection with that database.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from dob.db.connection import open_connection, parse_mysql_dsn
from dob.db.schema import load_schema
from dob.settings.preferences import UserPreferences
from dob.ui.screens.table_picker import TablePickerScreen

# System schemas that are never user databases
_SYSTEM_SCHEMAS = frozenset({
    "information_schema",
    "mysql",
    "performance_schema",
    "sys",
})


class DbPickerScreen(Screen):
    """Pick a database from a bare MySQL server connection."""

    BINDINGS = [Binding("escape,q", "app.pop_screen", "Back", show=True)]

    def __init__(self, bare_conn: object, original_dsn: str) -> None:
        super().__init__()
        self._bare_conn = bare_conn
        self._original_dsn = original_dsn
        self._databases: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Vertical():
            yield Label(
                f"[bold]MySQL:[/] [yellow]{self._original_dsn}[/]  —  select database",
                classes="screen-title",
            )
            yield ListView(id="db-list")

    def on_mount(self) -> None:
        self._load_databases()

    def _load_databases(self) -> None:
        """Populate the list view from SHOW DATABASES."""
        try:
            cur = self._bare_conn.cursor()
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
        except Exception as exc:
            self.query_one("#db-list", ListView).clear()
            self.query_one(".screen-title", Label).update(
                f"[red]Error listing databases: {exc}[/red]"
            )
            return

        self._databases = [
            r[0] for r in rows
            if r[0] not in _SYSTEM_SCHEMAS
        ]

        list_view: ListView = self.query_one("#db-list", ListView)
        list_view.clear()
        for name in self._databases:
            list_view.append(ListItem(Label(name), name=name))

    @on(ListView.Selected, "#db-list")
    def db_selected(self, event: ListView.Selected) -> None:
        """Close bare connection, open full connection, proceed to TablePicker."""
        db_name = event.item.name
        creds = parse_mysql_dsn(self._original_dsn)
        new_dsn = creds.to_dsn(database=db_name)

        # Close the bare connection
        try:
            self._bare_conn.close()
        except Exception:
            pass

        try:
            conn = open_connection(new_dsn)
            schema = load_schema(conn)
            schema.db_path = new_dsn
            prefs = UserPreferences(new_dsn)
            self.app.push_screen(
                TablePickerScreen(conn, schema, prefs, new_dsn)
            )
        except Exception as exc:
            self.query_one(".screen-title", Label).update(
                f"[red]Error opening database {db_name}: {exc}[/red]"
            )
