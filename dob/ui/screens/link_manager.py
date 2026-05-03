"""
dob.ui.screens.link_manager
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Modal menu for managing virtual links on a column.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from dob.db.schema import FKInfo, Schema
from dob.settings.links import VirtualLinks


class LinkManagerScreen(ModalScreen[bool]):
    """
    List all virtual links on a column and let the user:
      • Create a new link (opens LinkBuilderScreen)
      • Edit an existing link (delete old + create new)
      • Delete an existing link
    Dismisses(True) if any change was made.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

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
        self._changed = False
        self._links: list[FKInfo] = []
        self._items: list[tuple[str, str, FKInfo | None]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="mgr-heading")
        yield ListView(id="mgr-list")
        yield Label("[dim]Enter - select │ Esc - cancel[/dim]", id="mgr-hint")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        self._links = [
            fk
            for fk in self._schema.fk_from.get(self._from_table, [])
            if fk.virtual and fk.from_col == self._from_col
        ]

        heading: Label = self.query_one("#mgr-heading", Label)
        heading.update(
            f"[bold cyan]Virtual links[/]  "
            f"[bold]{self._from_table}[/].[bold yellow]{self._from_col}[/]  "
            f"[dim]({len(self._links)} link{'s' if len(self._links) != 1 else ''})[/dim]"
        )

        lv: ListView = self.query_one("#mgr-list", ListView)
        lv.clear()
        self._items = []

        self._items.append(("new", "", None))
        lv.append(ListItem(Label("[bold green]＋  Create new link[/bold green]"), name="new"))

        if self._links:
            lv.append(
                ListItem(
                    Label("[dim]─── existing links ─────────────────────[/dim]"),
                    name="_sep",
                )
            )
            self._items.append(("_sep", "", None))
            for fk in self._links:
                label_text = f"[cyan]{fk.to_table}[/].[yellow]{fk.to_col}[/]"
                self._items.append(("edit", "", fk))
                lv.append(
                    ListItem(
                        Label(f"[bold]✎[/bold]  {label_text}  [dim]edit[/dim]"),
                        name=f"edit:{fk.to_table}:{fk.to_col}",
                    )
                )
                self._items.append(("delete", "", fk))
                lv.append(
                    ListItem(
                        Label(f"[bold red]✕[/bold red]  {label_text}  [dim]delete[/dim]"),
                        name=f"del:{fk.to_table}:{fk.to_col}",
                    )
                )

        lv.focus()
        lv.index = 0

    @on(ListView.Selected, "#mgr-list")
    def item_selected(self, event: ListView.Selected) -> None:
        name = event.item.name or ""
        if name == "_sep":
            return
        if name == "new":
            self._open_builder(edit_fk=None)
            return
        if name.startswith("edit:"):
            _, to_table, to_col = name.split(":", 2)
            fk = self._find_link(to_table, to_col)
            if fk:
                self._open_builder(edit_fk=fk)
            return
        if name.startswith("del:"):
            _, to_table, to_col = name.split(":", 2)
            fk = self._find_link(to_table, to_col)
            if fk:
                self._delete_link(fk)

    def _find_link(self, to_table: str, to_col: str) -> FKInfo | None:
        for fk in self._links:
            if fk.to_table == to_table and fk.to_col == to_col:
                return fk
        return None

    def _delete_link(self, fk: FKInfo) -> None:
        VirtualLinks.remove(
            self._db_path, fk.from_table, fk.from_col, fk.to_table, fk.to_col
        )
        try:
            self._schema.fk_from.get(fk.from_table, []).remove(fk)
            self._schema.fk_to.get(fk.to_table, []).remove(fk)
        except ValueError:
            pass
        self._changed = True
        self._rebuild()
        self.notify(
            f"{fk.from_table}.{fk.from_col} → {fk.to_table}.{fk.to_col} removed",
            title="Link deleted",
        )

    def _open_builder(self, edit_fk: FKInfo | None) -> None:
        from dob.ui.screens.link_builder import LinkBuilderScreen

        def on_result(saved: bool) -> None:
            if saved:
                if edit_fk is not None:
                    VirtualLinks.remove(
                        self._db_path,
                        edit_fk.from_table,
                        edit_fk.from_col,
                        edit_fk.to_table,
                        edit_fk.to_col,
                    )
                    try:
                        self._schema.fk_from.get(edit_fk.from_table, []).remove(edit_fk)
                        self._schema.fk_to.get(edit_fk.to_table, []).remove(edit_fk)
                    except ValueError:
                        pass
                VirtualLinks.inject(self._schema, self._db_path)
                self._changed = True
                self._rebuild()

        self.app.push_screen(
            LinkBuilderScreen(
                db_path=self._db_path,
                schema=self._schema,
                from_table=self._from_table,
                from_col=self._from_col,
            ),
            on_result,
        )

    def action_cancel(self) -> None:
        self.dismiss(self._changed)
