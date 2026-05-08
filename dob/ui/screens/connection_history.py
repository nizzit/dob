"""
dob.ui.screens.connection_history
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ConnectionHistoryScreen — interactive dialog for selecting a previously
successful connection, with live filtering.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from dob.settings.connection_history import ConnectionEntry, ConnectionHistory


class ConnectionHistoryScreen(ModalScreen[str | None]):
    """Modal screen showing saved connections with a search filter."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("up", "list_up", "Up", show=False),
        Binding("down", "list_down", "Down", show=False),
        Binding("enter", "list_select", "Select", show=False),
        Binding("d", "delete_selected", "Delete", show=True),
    ]

    def __init__(self, history: ConnectionHistory | None = None) -> None:
        """
        Parameters
        ----------
        history:
            ConnectionHistory instance.  If *None*, a default instance is
            created (production path).  Non-None is used for testing.
        """
        super().__init__()
        self._history = history
        self._entries: list[ConnectionEntry] = []
        self._query = ""

    def compose(self) -> ComposeResult:
        yield Label(
            "[bold]Select a saved connection[/]",
            id="ch-title",
        )
        yield Input(
            placeholder="Filter by DSN or label…",
            id="ch-filter",
        )
        yield ListView(id="ch-list")
        yield Static("", id="ch-hint", classes="dim")
        yield Footer()

    def on_mount(self) -> None:
        self._load_entries()
        self.query_one("#ch-filter", Input).focus()

    # -- entry loading ---------------------------------------------------

    def _load_entries(self, *, query: str = "") -> None:
        history = self._history or ConnectionHistory()
        if query:
            self._entries = history.search(query)
        else:
            self._entries = history.get_all()
        self._render_list()

    def _render_list(self) -> None:
        list_view: ListView = self.query_one("#ch-list", ListView)
        list_view.clear()
        if not self._entries:
            list_view.append(
                ListItem(
                    Label("[dim]No saved connections[/]"),
                    name="__empty__",
                )
            )
            return
        for entry in self._entries:
            icon = "🐬" if entry.db_type == "mysql" else "📦"
            list_view.append(
                ListItem(
                    Static(
                        f"{icon}  {entry.display_name()}",
                        classes="ch-entry",
                    ),
                    name=entry.dsn,
                )
            )

    # -- events ----------------------------------------------------------

    @on(Input.Changed, "#ch-filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self._query = event.value.strip()
        self._load_entries(query=self._query)

    @on(ListView.Selected, "#ch-list")
    def entry_selected(self, event: ListView.Selected) -> None:
        dsn = event.item.name
        if dsn is None or dsn == "__empty__":
            return
        (self._history or ConnectionHistory()).add_or_update(dsn, "")
        self.dismiss(dsn)

    # -- key bindings ----------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_list_up(self) -> None:
        lv: ListView = self.query_one("#ch-list", ListView)
        if lv.index is not None:
            lv.index = max(0, lv.index - 1)

    def action_list_down(self) -> None:
        lv: ListView = self.query_one("#ch-list", ListView)
        if lv.index is not None:
            lv.index = min(len(self._entries) - 1, lv.index + 1)

    def action_list_select(self) -> None:
        lv: ListView = self.query_one("#ch-list", ListView)
        if lv.current_item:
            dsn = lv.current_item.name
            if dsn and dsn != "__empty__":
                (self._history or ConnectionHistory()).add_or_update(dsn, "")
                self.dismiss(dsn)

    def action_delete_selected(self) -> None:
        lv: ListView = self.query_one("#ch-list", ListView)
        if lv.current_item:
            dsn = lv.current_item.name
            if dsn and dsn != "__empty__":
                history = self._history or ConnectionHistory()
                history.remove(dsn)
                self._load_entries(query=self._query)
