"""
dob.ui.live_poller
~~~~~~~~~~~~~~~~~~
LivePoller — reusable helper that encapsulates the live-polling loop.

Replaces three nearly-identical poll implementations in:
  ObservationScreen, ExpandedTableScreen, RowPickerScreen.

Usage
-----
Each screen creates a LivePoller, passing a fetch callback and a UI
update callback.  The poller manages the Textual timer and calls the
callbacks on each tick.

    class MyScreen(Screen):
        def __init__(self, ...):
            self._poller = LivePoller(self, self._fetch_rows, self._on_new_rows)

        def action_toggle_live(self) -> None:
            self._poller.toggle()

        def _fetch_rows(self) -> list[tuple]: ...   # return all current rows
        def _on_new_rows(self, new_rows, all_rows, ts): ...  # update UI
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from textual.timer import Timer
from textual.widgets import Label

LIVE_INTERVAL = 2.0  # seconds between polls


def update_live_label(lbl: Label | None, is_live: bool, extra: str = "") -> None:
    """Update the live-status label widget."""
    if lbl is None:
        return
    if is_live:
        lbl.update(
            f"[bold green]● LIVE[/]  [dim]polling every {LIVE_INTERVAL}s"
            f" - press L to stop[/dim]{extra}"
        )
    else:
        lbl.update("[dim]○ live off - press L to start[/dim]")


class LivePoller:
    """
    Encapsulates live polling: timer lifecycle, new-row detection, label update.

    Parameters
    ----------
    owner       Textual widget/screen that owns the timer (for set_interval).
    fetch_all   Callable returning (cols, all_rows) – pure DB read, no side effects.
    on_new      Callable(new_rows, all_rows) called when new rows are detected.
    live_label_id  CSS id of the Label widget to update (default "#live-status").
    """

    def __init__(
        self,
        owner: Any,
        fetch_all: Callable[[], tuple[list[str], list[tuple]]],
        on_new: Callable[[list[tuple], list[tuple]], None],
        live_label_id: str = "#live-status",
    ) -> None:
        self._owner = owner
        self._fetch_all = fetch_all
        self._on_new = on_new
        self._label_id = live_label_id
        self._timer: Timer | None = None
        self._known: set[tuple] = set()
        self.is_live: bool = False

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def seed(self, rows: list[tuple]) -> None:
        """Initialise the known-row set from an initial data snapshot."""
        self._known = set(rows)

    def start(self) -> None:
        if not self.is_live:
            self.is_live = True
            self._timer = self._owner.set_interval(LIVE_INTERVAL, self._poll)
        self._update_label()

    def stop(self) -> None:
        if self.is_live:
            self.is_live = False
            if self._timer:
                self._timer.stop()
                self._timer = None
        self._update_label()

    def toggle(self) -> None:
        if self.is_live:
            self.stop()
        else:
            self.start()

    def dispose(self) -> None:
        """Call from on_unmount to cancel the timer."""
        if self._timer:
            self._timer.stop()
            self._timer = None

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            _cols, all_rows = self._fetch_all()
        except Exception:
            return

        new_rows = [r for r in all_rows if r not in self._known]
        if new_rows:
            for r in new_rows:
                self._known.add(r)
            self._on_new(new_rows, all_rows)

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_label(
            f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]"
        )

    # ── label ─────────────────────────────────────────────────────────────────

    def _update_label(self, extra: str = "") -> None:
        try:
            lbl = self._owner.query_one(self._label_id, Label)
            update_live_label(lbl, self.is_live, extra)
        except Exception:
            pass
