"""
dob.ui.flasher
~~~~~~~~~~~~~~
RowFlasher — countdown-based flash highlight for new rows in a DataTable.
"""

from __future__ import annotations

from textual.widgets import DataTable


_FLASH_TICKS = 3


def _mark_row(dt: DataTable, key: str, new: bool) -> None:
    """Prefix first cell with ▶ marker for new rows."""
    try:
        cell = dt.get_cell(key, dt.ordered_columns[0].key)
        val = str(cell).lstrip("▶ ")
        if new:
            val = f"▶ {val}"
        dt.update_cell(key, dt.ordered_columns[0].key, val)
    except Exception:
        pass


class RowFlasher:
    """
    Countdown-based flash highlight for new rows.

    Usage:
        flasher.add(row_key)         -- start flashing
        flasher.tick(datatable)      -- call on each poll tick
        flasher.has(row_key) -> bool -- check if key is flashing
        flasher.count() -> int       -- number of active highlights
        flasher.clear()              -- remove all highlights
    """

    def __init__(self) -> None:
        self._flash: dict[str, int] = {}

    def add(self, key: str) -> None:
        self._flash[key] = _FLASH_TICKS

    def has(self, key: str) -> bool:
        return key in self._flash

    def tick(self, dt: DataTable) -> None:
        if not self._flash:
            return
        expired = [k for k, v in self._flash.items() if v <= 1]
        for key in expired:
            _mark_row(dt, key, new=False)
            del self._flash[key]
        for key in self._flash:
            self._flash[key] -= 1

    def count(self) -> int:
        return len(self._flash)

    def clear(self) -> None:
        self._flash.clear()
