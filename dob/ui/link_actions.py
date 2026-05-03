"""
dob.ui.link_actions
~~~~~~~~~~~~~~~~~~~
open_link_menu — single function replacing three copies of action_link
spread across ObservationScreen, ExpandedTableScreen, RowPickerScreen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from textual.screen import Screen

    from dob.db.schema import Schema
    from dob.settings.links import VirtualLinks


def open_link_menu(
    screen: "Screen",
    schema: "Schema",
    db_path: str,
    table: str,
    from_col: str,
    on_changed: Callable[[], None],
) -> None:
    """
    Open LinkManagerScreen (if virtual links exist for *from_col*) or
    LinkBuilderScreen (if none exist).

    *on_changed* is called with no arguments if the user creates/removes
    any link so the caller can refresh its observation and UI.
    """
    # Import here to avoid circular deps (screens → link_actions → screens)
    from dob.ui.screens.link_builder import LinkBuilderScreen
    from dob.ui.screens.link_manager import LinkManagerScreen
    from dob.settings.links import VirtualLinks

    if not db_path:
        return

    existing = [
        fk
        for fk in schema.fk_from.get(table, [])
        if fk.virtual and fk.from_col == from_col
    ]

    if existing:
        def _on_manager_result(changed: bool) -> None:
            if changed:
                VirtualLinks.inject(schema, db_path)
                on_changed()

        screen.app.push_screen(
            LinkManagerScreen(
                db_path=db_path,
                schema=schema,
                from_table=table,
                from_col=from_col,
            ),
            _on_manager_result,
        )
    else:
        def _on_builder_result(saved: bool) -> None:
            if saved:
                VirtualLinks.inject(schema, db_path)
                screen.notify(  # type: ignore[attr-defined]
                    f"{table}.{from_col} linked",
                    title="Virtual link created",
                )
                on_changed()

        screen.app.push_screen(
            LinkBuilderScreen(
                db_path=db_path,
                schema=schema,
                from_table=table,
                from_col=from_col,
            ),
            _on_builder_result,
        )
