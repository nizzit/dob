"""
dob.ui.screens.expanded
~~~~~~~~~~~~~~~~~~~~~~~
ExpandedTableScreen — full-screen view of a single table.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Label

from dob.db.lookup import LookupCache
from dob.db.queries import fetch_all_rows, sql_sort_rows
from dob.db.schema import FKInfo, Schema
from dob.domain.traversal import build_observation
from dob.settings.preferences import UserPreferences
from dob.ui.flasher import RowFlasher, _mark_row
from dob.ui.formatting import HeaderBuilder, row_strs
from dob.ui.link_actions import open_link_menu
from dob.ui.live_poller import LIVE_INTERVAL, update_live_label
from dob.ui.sort_mixin import SortableMixin
from dob.ui.widgets.table_block import _build_col_meta


class ExpandedTableScreen(SortableMixin, ModalScreen):
    """Full-screen view of a single table.  Esc to close.  L to toggle live."""

    BINDINGS = [
        Binding("escape,q,f", "dismiss", "Close", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    def __init__(
        self,
        title: str,
        cols: list[str],
        rows: list[tuple],
        *,
        conn: sqlite3.Connection | None = None,
        schema: Schema | None = None,
        prefs: UserPreferences | None = None,
        tbl_name: str | None = None,
        pk_cols: set[str] | None = None,
        fk_cols: dict[str, FKInfo] | None = None,
        seed_table: str | None = None,
        seed_pk_col: str | None = None,
        seed_pk_val: Any = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._cols = cols
        self._rows = list(rows)
        self._conn = conn
        self._schema = schema
        self._prefs = prefs
        self._tbl_name = tbl_name
        self._pk_cols: set[str] = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._seed_table = seed_table
        self._seed_pk_col = seed_pk_col
        self._seed_pk_val = seed_pk_val
        self._known_rows: set[tuple] = set(rows)
        self._flasher = RowFlasher()
        self._lookup = LookupCache(conn) if conn else None
        self._timer = None
        self._is_live = False

    # ── SortableMixin interface ───────────────────────────────────────────────

    @property
    def _sort_prefs(self) -> UserPreferences:
        return self._prefs  # type: ignore[return-value]

    def _resolve_sort_target(self, widget: Any = None) -> tuple[str, list[str]] | None:
        if not self._tbl_name:
            return None
        return self._tbl_name, self._cols

    def _after_sort(self) -> None:
        self._redraw_dt()

    # ── compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        yield Label(
            f"[bold cyan]{self._title}[/]  [dim]{len(self._rows)} rows - Esc / F to close[/dim]",
            id="expanded-title",
        )
        if self._prefs and self._tbl_name:
            self._rows = sql_sort_rows(
                self._conn, self._tbl_name, self._cols, self._rows,
                self._prefs.get_sort(self._tbl_name),
            )
        dt = DataTable(id="expanded-dt", zebra_stripes=True, cursor_type="cell")
        dt.add_columns(*self._make_headers())
        for row in self._rows:
            dt.add_row(*row_strs(row), key=str(row))
        yield dt

    def on_mount(self) -> None:
        live = getattr(self.app, "is_table_live", lambda t: False)(self._tbl_name)
        if live:
            self._start_live()
        self.query_one("#expanded-dt", DataTable).focus()

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()
        # Cancel any in-flight poll worker
        self.workers.cancel_group(self, "expanded-live-poll")

    # ── live ─────────────────────────────────────────────────────────────────

    def action_toggle_live(self) -> None:
        if self._conn is None or self._tbl_name is None:
            self.notify("Live mode unavailable (no DB context)", severity="warning")
            return
        if self._is_live:
            self._stop_live()
        else:
            self._start_live()
        setter = getattr(self.app, "set_table_live", None)
        if callable(setter):
            setter(self._tbl_name, self._is_live)

    def _start_live(self) -> None:
        self._is_live = True
        self._timer = self.set_interval(LIVE_INTERVAL, self._poll)
        self._update_live_label()

    def _stop_live(self) -> None:
        self._is_live = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._update_live_label()

    def _update_live_label(self, extra: str = "") -> None:
        try:
            lbl = self.query_one("#live-status", Label)
            update_live_label(lbl, self._is_live, extra)
        except Exception:
            pass

    def _poll(self) -> None:
        """Timer callback — dispatches the blocking fetch to a worker thread."""
        self._poll_worker()

    @work(thread=True, group="expanded-live-poll")
    def _poll_worker(self) -> None:
        """Fetch data in a background thread; apply updates on main thread."""
        if self._conn is None or self._tbl_name is None:
            return
        try:
            if self._seed_table and self._seed_pk_col and self._seed_pk_val is not None:
                new_obs = build_observation(
                    self._conn, self._schema, self._prefs,
                    self._seed_table, self._seed_pk_col, self._seed_pk_val,
                )
                all_rows = (
                    [new_obs.seed_row] if self._tbl_name == self._seed_table and new_obs.seed_row
                    else new_obs.related.get(self._tbl_name, (self._cols, []))[1]
                )
            else:
                sort_info = self._prefs.get_sort(self._tbl_name) if self._prefs else None
                _, all_rows = fetch_all_rows(self._conn, self._tbl_name, sort_info)
        except Exception:
            return
        self.app.call_from_thread(self._apply_poll, all_rows)

    def _apply_poll(self, all_rows: list[tuple]) -> None:
        """Apply poll results on the main thread."""
        new_rows = [r for r in all_rows if r not in self._known_rows]
        if new_rows:
            for row in new_rows:
                self._known_rows.add(row)
                self._flasher.add(str(row))
            self._rows = all_rows
            self._redraw_dt()
            self._refresh_title()

        try:
            self._flasher.tick(self.query_one("#expanded-dt", DataTable))
        except Exception:
            pass

        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_live_label(f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]")

    # ── events ────────────────────────────────────────────────────────────────

    @on(DataTable.HeaderSelected, "#expanded-dt")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.column_index >= len(self._cols):
            return
        self._toggle_sort(self._tbl_name, self._cols[event.column_index])

    @on(DataTable.CellSelected, "#expanded-dt")
    def row_drilldown(self, event: DataTable.CellSelected) -> None:
        if not self._conn or not self._schema or not self._tbl_name:
            return
        row_index = event.coordinate.row
        if row_index >= len(self._rows):
            return
        from dob.ui.drilldown import open_observation_for_row
        open_observation_for_row(
            self.app, self._conn, self._schema, self._prefs,
            self._tbl_name, self._cols, self._rows[row_index],
        )

    def action_link(self) -> None:
        if not self._schema or not self._tbl_name:
            return
        db_path = getattr(self._schema, "db_path", "")
        dt = self.query_one("#expanded-dt", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self._cols):
            return
        from_col = self._cols[col_index]
        open_link_menu(
            self, self._schema, db_path, self._tbl_name, from_col,
            on_changed=lambda: None,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_headers(self) -> list[str]:
        sort_info = self._prefs.get_sort(self._tbl_name) if self._prefs and self._tbl_name else None
        filter_info = self._prefs.get_filter(self._tbl_name) if self._prefs and self._tbl_name else None
        return HeaderBuilder(self._pk_cols, self._fk_cols, sort_info, filter_info).headers(self._cols)

    def _redraw_dt(self) -> None:
        if not self._tbl_name:
            return
        dt = self.query_one("#expanded-dt", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        if self._prefs:
            self._rows = sql_sort_rows(
                self._conn, self._tbl_name, self._cols, self._rows,
                self._prefs.get_sort(self._tbl_name),
            )
        dt.clear(columns=True)
        dt.add_columns(*self._make_headers())
        for row in self._rows:
            key = str(row)
            dt.add_row(*row_strs(row), key=key)
            if self._flasher.has(key):
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self._rows) - 1)), column=cur_c)

    def _refresh_title(self) -> None:
        try:
            lbl: Label = self.query_one("#expanded-title", Label)
            lbl.update(
                f"[bold cyan]{self._title}[/]  "
                f"[dim]{len(self._rows)} rows - Esc / F to close[/dim]"
            )
        except Exception:
            pass
