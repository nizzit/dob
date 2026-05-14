"""
dob.ui.screens.row_picker
~~~~~~~~~~~~~~~~~~~~~~~~~
RowPickerScreen — pick a row from a table to observe.

All DB I/O runs in background thread workers (@work(thread=True)) so
the Textual event loop never blocks.  The screen mounts immediately
with a LoadingIndicator; data is applied via call_from_thread once
the worker completes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, LoadingIndicator

from dob.db.lookup import LookupCache
from dob.db.queries import count_rows, fetch_all_rows, get_pk_column
from dob.db.schema import Schema
from dob.settings.filters import parse_filter_value
from dob.settings.links import VirtualLinks
from dob.settings.preferences import UserPreferences
from dob.ui.drilldown import open_observation_for_row
from dob.ui.flasher import RowFlasher, _mark_row
from dob.ui.formatting import filter_caption, HeaderBuilder, fmt
from dob.ui.link_actions import open_link_menu
from dob.ui.live_poller import LIVE_INTERVAL, update_live_label
from dob.ui.screens.filter_value import FilterValueScreen
from dob.ui.sort_mixin import SortableMixin
from dob.ui.widgets.table_block import _build_col_meta


# How many rows to load per page (initial + each incremental load).
_PAGE_SIZE = 200


class RowPickerScreen(SortableMixin, Screen):
    """Pick a row from a table.  L - live mode, R - manual refresh, / - filter."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("/", "filter_column", "Filter", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    def __init__(self, conn: sqlite3.Connection, schema: Schema, prefs: UserPreferences, table: str) -> None:
        super().__init__()
        self._conn = conn
        self._schema = schema
        self._prefs = prefs
        self.table = table
        self._lookup = LookupCache(conn)
        # These are populated once the first worker finishes
        self.pk_col: str | None = None
        self.cols: list[str] = []
        self.rows: list[tuple] = []
        # Pagination state
        self._offset: int = 0
        self._total: int = 0
        self._all_loaded: bool = False
        self._known_rows: set[tuple] = set()
        self._flasher = RowFlasher()
        self._timer = None
        self._is_live = False

    # ── SortableMixin interface ───────────────────────────────────────────────

    @property
    def _sort_prefs(self) -> UserPreferences:
        return self._prefs

    def _resolve_sort_target(self, widget: Any = None) -> tuple[str, list[str]] | None:
        return self.table, self.cols

    def _after_sort(self) -> None:
        self._reload()

    # ── compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        yield LoadingIndicator(id="loading")
        pk_cols, fk_cols = _build_col_meta(self._conn, self._schema, self.table)
        self._pk_cols_meta = pk_cols
        self._fk_cols_meta = fk_cols
        dt = DataTable(id="row-table", zebra_stripes=True, cursor_type="cell")
        dt.display = False
        yield dt

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        live = getattr(self.app, "is_table_live", lambda t: False)(self.table)
        if live:
            self._is_live = True  # flag only; timer started after data loads
        self._load_data()

    def on_screen_resume(self) -> None:
        live = getattr(self.app, "is_table_live", lambda t: False)(self.table)
        if live and not self._is_live:
            self._start_live()
        elif not live and self._is_live:
            self._stop_live()

    def on_unmount(self) -> None:
        self.app.sub_title = ""
        if self._timer:
            self._timer.stop()

    # ── background worker ─────────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="row-load")
    def _load_data(self) -> None:
        """Fetch count + first page in a background thread."""
        sort_info = self._prefs.get_sort(self.table)
        filter_info = self._prefs.get_filter(self.table)
        pk_col = get_pk_column(self._conn, self.table)
        total = count_rows(self._conn, self.table, filter_info)
        cols, rows = fetch_all_rows(
            self._conn, self.table, sort_info, filter_info,
            limit=_PAGE_SIZE, offset=0,
        )
        self.app.call_from_thread(self._apply_data, pk_col, cols, rows, total)

    def _apply_data(
        self,
        pk_col: str | None,
        cols: list[str],
        rows: list[tuple],
        total: int,
    ) -> None:
        """Apply fetched data to the UI (runs on the main thread)."""
        self.pk_col = pk_col
        self.cols = cols
        self.rows = rows
        self._total = total
        self._offset = len(rows)
        self._all_loaded = self._offset >= total
        self._known_rows = set(rows)
        self._flasher.clear()

        sort_info = self._prefs.get_sort(self.table)
        filter_info = self._prefs.get_filter(self.table)

        dt = self.query_one("#row-table", DataTable)
        dt.clear(columns=True)
        headers = HeaderBuilder(
            self._pk_cols_meta, self._fk_cols_meta, sort_info, filter_info
        ).headers(cols)
        dt.add_columns(*headers)
        for row in rows:
            dt.add_row(*[fmt(v) for v in row], key=str(row))

        # Hide spinner, show table
        self.query_one("#loading", LoadingIndicator).display = False
        dt.display = True
        dt.focus()

        self._update_subtitle()

        # Start live timer now if live mode was requested before data arrived
        if self._is_live and self._timer is None:
            self._start_live()

    # ── live ─────────────────────────────────────────────────────────────────

    def action_toggle_live(self) -> None:
        if self._is_live:
            self._stop_live()
        else:
            self._start_live()
        setter = getattr(self.app, "set_table_live", None)
        if callable(setter):
            setter(self.table, self._is_live)

    def _start_live(self) -> None:
        self._is_live = True
        self._timer = self.set_interval(LIVE_INTERVAL, self._live_poll_dispatch)
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

    def _live_poll_dispatch(self) -> None:
        """Timer callback — dispatches DB fetch to a worker thread."""
        self._live_poll_worker()

    @work(thread=True, group="row-live-poll")
    def _live_poll_worker(self) -> None:
        sort_info = self._prefs.get_sort(self.table)
        filter_info = self._prefs.get_filter(self.table)
        _, all_rows = fetch_all_rows(self._conn, self.table, sort_info, filter_info)
        self.app.call_from_thread(self._apply_live_poll, all_rows)

    def _apply_live_poll(self, all_rows: list[tuple]) -> None:
        """Apply live-poll results on the main thread."""
        new_rows = [r for r in all_rows if r not in self._known_rows]
        if new_rows:
            for row in new_rows:
                self._known_rows.add(row)
                self._flasher.add(str(row))
            self.rows = list(all_rows)
            self._offset = len(all_rows)
            self._total = len(all_rows)
            self._all_loaded = True
            self._redraw_dt()
            self._update_subtitle()
        try:
            dt = self.query_one("#row-table", DataTable)
            self._flasher.tick(dt)
        except Exception:
            pass
        ts = datetime.now().strftime("%H:%M:%S")
        n_new = len(new_rows)
        new_tag = f"  [bold green]+{n_new} rows[/]" if n_new else ""
        self._update_live_label(f"  [dim]last poll {ts}{new_tag} - press L to stop[/dim]")

    # ── reload / refresh ─────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Trigger a background reload (cancels any in-flight load via exclusive group)."""
        try:
            loading = self.query_one("#loading", LoadingIndicator)
            loading.display = True
            dt = self.query_one("#row-table", DataTable)
            dt.display = False
        except Exception:
            pass
        self._load_data()

    def action_refresh(self) -> None:
        self._reload()

    def _update_subtitle(self) -> None:
        fi = self._prefs.get_filter(self.table)
        loaded = len(self.rows)
        total = self._total
        if self._all_loaded or loaded == total:
            count_tag = f"{loaded} rows"
        else:
            count_tag = f"{loaded}/{total} rows"
        self.app.sub_title = (
            f"{self.table}  ({count_tag}){filter_caption(fi)} - Enter to observe"
        )

    def action_filter_column(self) -> None:
        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols):
            return
        col_name = self.cols[col_index]
        active = self._prefs.get_filter(self.table)
        current = active[1] if active and active[0] == col_name else None

        def on_value(raw: str | None) -> None:
            if raw is None:
                return
            text = raw.strip()
            if text == "":
                self._prefs.clear_filter(self.table)
                self.notify(f"Filter cleared: {self.table}", title="Filter")
            else:
                value = parse_filter_value(text)
                self._prefs.set_filter(self.table, col_name, value)
                self.notify(
                    f"Filter set: {self.table}.{col_name} = {fmt(value)}", title="Filter"
                )
            self._reload()
            try:
                self.query_one("#row-table", DataTable).focus()
            except Exception:
                pass

        self.app.push_screen(FilterValueScreen(self.table, col_name, current), on_value)

    # ── events ────────────────────────────────────────────────────────────────

    @on(DataTable.CellHighlighted, "#row-table")
    def on_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Load the next page when cursor approaches the last loaded row."""
        if self._all_loaded or self._is_live:
            return
        dt = self.query_one("#row-table", DataTable)
        if event.coordinate.row >= dt.row_count - 20:
            self._load_next_page()

    @on(DataTable.HeaderSelected, "#row-table")
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.column_index < len(self.cols):
            self._toggle_sort(self.table, self.cols[event.column_index])

    @on(DataTable.CellSelected, "#row-table")
    def row_selected(self, event: DataTable.CellSelected) -> None:
        row_index = event.coordinate.row
        if row_index >= len(self.rows):
            return
        open_observation_for_row(
            self.app, self._conn, self._schema, self._prefs,
            self.table, self.cols, self.rows[row_index],
        )

    def action_link(self) -> None:
        db_path = getattr(self._schema, "db_path", "")
        if not db_path:
            return
        dt = self.query_one("#row-table", DataTable)
        col_index = dt.cursor_column
        if col_index >= len(self.cols):
            return
        from_col = self.cols[col_index]

        def on_changed() -> None:
            VirtualLinks.inject(self._schema, db_path)
            self._reload()

        open_link_menu(self, self._schema, db_path, self.table, from_col, on_changed)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_next_page(self) -> None:
        """Append the next page of rows to the DataTable."""
        if self._all_loaded:
            return
        sort_info = self._prefs.get_sort(self.table)
        filter_info = self._prefs.get_filter(self.table)
        _, new_rows = fetch_all_rows(
            self._conn, self.table, sort_info, filter_info,
            limit=_PAGE_SIZE, offset=self._offset,
        )
        if not new_rows:
            self._all_loaded = True
            return
        dt = self.query_one("#row-table", DataTable)
        for row in new_rows:
            self._known_rows.add(row)
            self.rows.append(row)
            dt.add_row(*[fmt(v) for v in row], key=str(row))
        self._offset += len(new_rows)
        self._all_loaded = self._offset >= self._total
        self._update_subtitle()

    def _redraw_dt(self) -> None:
        dt = self.query_one("#row-table", DataTable)
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        sort_info = self._prefs.get_sort(self.table)
        filter_info = self._prefs.get_filter(self.table)
        headers = HeaderBuilder(
            self._pk_cols_meta, self._fk_cols_meta, sort_info, filter_info
        ).headers(self.cols)
        dt.add_columns(*headers)
        for row in self.rows:
            key = str(row)
            dt.add_row(*[fmt(v) for v in row], key=key)
            if self._flasher.has(key):
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self.rows) - 1)), column=cur_c)
