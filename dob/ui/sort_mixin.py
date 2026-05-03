"""
dob.ui.sort_mixin
~~~~~~~~~~~~~~~~~
SortableMixin — single mixin replacing SortableSingleTableMixin and
SortableFocusedTableMixin.

Subclasses implement two abstract methods:
  _resolve_sort_target(widget=None) -> (table_name, col_names) | None
  _after_sort()

The mixin provides:
  action_sort_column()            — sort by column under cursor
  _sort_from_header_event(event)  — sort from header click
  _toggle_sort(table, col)        — toggle + persist
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.widgets import DataTable

from dob.settings.preferences import UserPreferences

if TYPE_CHECKING:
    pass


class SortableMixin:
    """
    Reusable sort actions.

    Subclass must set self._sort_prefs (UserPreferences) and implement
    _resolve_sort_target() and _after_sort().
    """

    # ── abstract interface ────────────────────────────────────────────────────

    def _resolve_sort_target(
        self, widget: Any = None
    ) -> tuple[str, list[str]] | None:
        """
        Return (table_name, col_names) for the given widget (or focused widget
        if widget is None).  Return None if the target cannot be determined.
        """
        raise NotImplementedError

    def _after_sort(self) -> None:
        """Called after a sort toggle is applied; typically triggers a reload."""
        raise NotImplementedError

    @property
    def _sort_prefs(self) -> UserPreferences:
        raise NotImplementedError

    # ── actions ───────────────────────────────────────────────────────────────

    def _toggle_sort(self, table: str, col: str) -> None:
        self._sort_prefs.toggle_sort(table, col)
        self._after_sort()

    def action_sort_column(self) -> None:
        focused = getattr(self, "focused", None)
        if not isinstance(focused, DataTable):
            self.notify(  # type: ignore[attr-defined]
                "Focus a table cell first", severity="warning"
            )
            return
        target = self._resolve_sort_target(focused)
        if target is None:
            return
        table, cols = target
        col_index = focused.cursor_column
        if col_index >= len(cols):
            return
        self._toggle_sort(table, cols[col_index])

    def _sort_from_header_event(self, event: DataTable.HeaderSelected) -> None:
        target = self._resolve_sort_target(event.data_table)
        if target is None:
            return
        table, cols = target
        if event.column_index >= len(cols):
            return
        self._toggle_sort(table, cols[event.column_index])
