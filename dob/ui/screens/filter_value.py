"""
dob.ui.screens.filter_value
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Modal screen that prompts for a filter value.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label


class FilterValueScreen(ModalScreen[str | None]):
    """Prompt for a filter value.  Empty input clears the active filter."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel", show=True)]

    def __init__(self, table: str, column: str, current: object = None) -> None:
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
        yield Input(
            value="" if self._current is None else str(self._current),
            placeholder="value (empty = clear, NULL = IS NULL)",
            id="filter-value-input",
        )

    def on_mount(self) -> None:
        self.query_one("#filter-value-input", Input).focus()

    @on(Input.Submitted, "#filter-value-input")
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
