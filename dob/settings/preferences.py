"""
dob.settings.preferences
~~~~~~~~~~~~~~~~~~~~~~~~
UserPreferences — runtime container for user-controlled table preferences
(sort order, column filters).

This is intentionally separate from Schema (which carries only structural
DB metadata) so they can evolve independently and be passed to domain /
UI layers without coupling.
"""

from __future__ import annotations

from typing import Any

from .store import ProjectSettings


class UserPreferences:
    """
    Mutable container for sort and filter preferences.

    Backed by ProjectSettings for persistence; all writes go through
    toggle_sort / set_filter / clear_filter which call save automatically.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._settings = ProjectSettings(db_path)
        # live copies that are mutated in-place during the session
        self.sorts: dict[str, tuple[str, bool]] = {
            k: (v[0], bool(v[1]))
            for k, v in self._settings.sorts.items()
        }
        self.filters: dict[str, tuple[str, Any]] = {
            k: (v[0], v[1])
            for k, v in self._settings.filters.items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }

    # ── sort ──────────────────────────────────────────────────────────────────

    def toggle_sort(self, table: str, col: str) -> None:
        current = self.sorts.get(table)
        if current and current[0] == col:
            new_sort: tuple[str, bool] = (col, not current[1])
        else:
            new_sort = (col, True)  # first press → descending
        self.sorts[table] = new_sort
        self._settings.patch(sorts=self.sorts)

    def get_sort(self, table: str) -> tuple[str, bool] | None:
        return self.sorts.get(table)

    # ── filter ────────────────────────────────────────────────────────────────

    def set_filter(self, table: str, col: str, value: Any) -> None:
        self.filters[table] = (col, value)
        self._settings.patch(filters=self.filters)

    def clear_filter(self, table: str) -> None:
        if table in self.filters:
            self.filters.pop(table)
            self._settings.patch(filters=self.filters)

    def get_filter(self, table: str) -> tuple[str, Any] | None:
        return self.filters.get(table)
