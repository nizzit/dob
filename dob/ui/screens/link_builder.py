"""
dob.ui.screens.link_builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Two-step wizard modal for creating a virtual FK link.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from dob.db.schema import Schema
from dob.settings.links import VirtualLinks


class LinkBuilderScreen(ModalScreen[bool]):
    """
    Step 1 — pick target table.
    Step 2 — pick target column.
    Saves via VirtualLinks.add() and dismisses(True) on success.
    """

    BINDINGS = [Binding("escape", "escape_pressed", "Cancel", show=True)]

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
                        Label("[dim]No available columns (real FK already exists).[/dim]"),
                        name="_none",
                    )
                )

        lv.focus()

    def action_escape_pressed(self) -> None:
        if self._step == 2:
            self._target_table = None
            self._step = 1
            self._render_step()
        else:
            self.dismiss(False)

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
            dup = any(
                fk.from_col == self._from_col
                and fk.to_table == self._target_table
                and fk.to_col == name
                for fk in self._schema.fk_from.get(self._from_table, [])
            )
            if dup:
                self.notify(
                    f"Link already exists: {self._from_table}.{self._from_col} → "
                    f"{self._target_table}.{name}",
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
