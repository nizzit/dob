"""
dob.ui.live_poller
~~~~~~~~~~~~~~~~~~
LivePoller — reusable helper that encapsulates the live-polling loop.

The fetch callback runs in a background thread worker so the Textual
event loop never blocks on a DB query.  The `on_new` UI-update callback
is invoked via `call_from_thread` on the main thread.

Usage
-----
Each screen creates a LivePoller, passing a fetch callback and a UI
update callback.  The poller manages the Textual timer and dispatches
the fetch to a thread.

    class MyScreen(Screen):
        def __init__(self, ...):
            self._poller = LivePoller(self, self._fetch_rows, self._on_new_rows)

        def action_toggle_live(self) -> None:
            self._poller.toggle()

        def _fetch_rows(self) -> tuple[list[str], list[tuple]]: ...
        def _on_new_rows(self, new_rows, all_rows): ...
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from textual.timer import Timer
from textual.widgets import Label
from textual.worker import Worker

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

    The `_fetch_all` callback runs in a background thread; `_on_new` is
    called via `call_from_thread` on the main Textual event loop thread.

    Parameters
    ----------
    owner           Textual widget/screen that owns the timer.
    fetch_all       Callable() → (cols, all_rows).  Runs in a thread.
    on_new          Callable(new_rows, all_rows).  Runs on the main thread.
    live_label_id   CSS id of the Label widget to update.
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
        self._worker: Worker | None = None
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
            self._cancel_worker()
        self._update_label()

    def toggle(self) -> None:
        if self.is_live:
            self.stop()
        else:
            self.start()

    def dispose(self) -> None:
        """Call from on_unmount to cancel the timer and any running worker."""
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._cancel_worker()

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """Timer callback (runs on event loop) — dispatches fetch to a thread."""
        self._worker = self._owner.run_worker(
            self._fetch_and_notify,
            thread=True,
            group="live-poller",
            exclusive=False,
        )

    def _fetch_and_notify(self) -> None:
        """Runs in a worker thread — fetches data, then posts results back."""
        try:
            _cols, all_rows = self._fetch_all()
        except Exception:
            return

        new_rows = [r for r in all_rows if r not in self._known]
        if new_rows:
            for r in new_rows:
                self._known.add(r)

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        label_extra = f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]"

        self._owner.app.call_from_thread(self._deliver, new_rows, all_rows, label_extra)

    def _deliver(
        self,
        new_rows: list[tuple],
        all_rows: list[tuple],
        label_extra: str,
    ) -> None:
        """Called on the main thread — updates UI if there are new rows."""
        if new_rows:
            self._on_new(new_rows, all_rows)
        self._update_label(label_extra)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _cancel_worker(self) -> None:
        """Cancel the active fetch worker if one is running."""
        try:
            self._owner.workers.cancel_group(self._owner, "live-poller")
        except Exception:
            pass
        self._worker = None

    def _update_label(self, extra: str = "") -> None:
        try:
            lbl = self._owner.query_one(self._label_id, Label)
            update_live_label(lbl, self.is_live, extra)
        except Exception:
            pass
