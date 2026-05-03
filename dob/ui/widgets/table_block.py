"""
dob.ui.widgets.table_block
~~~~~~~~~~~~~~~~~~~~~~~~~~
TableBlock — one table section: header label + DataTable.

Supports adding new rows with flash highlight, sort/filter-aware headers,
and FK/PK column badges.

Dependencies are injected (LookupCache, Schema, UserPreferences) rather
than accepting a raw sqlite3.Connection so the widget stays testable and
decoupled from the DB layer.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.widgets import DataTable, Label, Static

from dob.db.lookup import LookupCache
from dob.db.queries import get_pk_columns
from dob.db.schema import FKInfo, Schema
from dob.settings.preferences import UserPreferences
from dob.ui.flasher import RowFlasher, _mark_row
from dob.ui.formatting import (
    HeaderBuilder,
    build_inline_lookup_map,
    col_header,
    direction_tag,
    row_strs,
    via_text,
)

if TYPE_CHECKING:
    pass

# ── style constants ───────────────────────────────────────────────────────────

NEW_STYLE = "bold green"
SEED_STYLE = "bold cyan"
REL_STYLE = "bold yellow"


# ── helpers ───────────────────────────────────────────────────────────────────


def _build_col_meta(
    conn: sqlite3.Connection,
    schema: Schema,
    table: str,
) -> tuple[set[str], dict[str, FKInfo]]:
    """Return (pk_cols, fk_cols) for a table."""
    pk_cols = get_pk_columns(conn, table)
    fk_cols: dict[str, FKInfo] = {}
    for fk in schema.fk_from.get(table, []):
        if fk.from_col not in fk_cols or not fk.virtual:
            fk_cols[fk.from_col] = fk
    return pk_cols, fk_cols


# ── widget ────────────────────────────────────────────────────────────────────


class TableBlock(Static):
    """
    One table section: label + DataTable.

    Parameters
    ----------
    table         SQLite table name
    cols          Column names
    rows          Initial rows
    is_seed       True for the seed record block
    pk_cols       PK column names (from _build_col_meta)
    fk_cols       FK column name → FKInfo mapping
    schema        Schema object (for FK metadata)
    prefs         UserPreferences (for sort/filter state)
    lookup        LookupCache (for inline FK rendering)
    relation_kind "in" | "out" | "both" | ""
    relation_via  Set of table names via which this table was reached
    """

    def __init__(
        self,
        table: str,
        cols: list[str],
        rows: list[tuple],
        is_seed: bool = False,
        pk_cols: set[str] | None = None,
        fk_cols: dict[str, FKInfo] | None = None,
        schema: Schema | None = None,
        prefs: UserPreferences | None = None,
        lookup: LookupCache | None = None,
        relation_kind: str = "",
        relation_via: set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.tbl_name = table
        self.cols = cols
        self.all_rows = list(rows)
        self.is_seed = is_seed
        self.schema = schema
        self.prefs = prefs
        self.lookup = lookup
        self.relation_kind = relation_kind
        self.relation_via = set(relation_via or set())
        self._pk_cols: set[str] = pk_cols or set()
        self._fk_cols: dict[str, FKInfo] = fk_cols or {}
        self._flasher = RowFlasher()

    # ── compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        if self.is_seed:
            color, marker = SEED_STYLE, "●"
        else:
            color, marker = REL_STYLE, "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        dir_tag = "" if self.is_seed else direction_tag(self.relation_kind)
        via_tag = "" if self.is_seed else via_text(self.relation_via)
        yield Label(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]{dir_tag}{via_tag}",
            id=f"lbl-{self.id}",
        )
        dt = DataTable(zebra_stripes=True, id=f"dt-{self.id}", cursor_type="cell")
        headers = self._make_headers()
        dt.add_columns(*headers)
        for row in self.all_rows:
            inline_map = self._inline_map(row)
            dt.add_row(*row_strs(row, inline_map), key=str(row))
        yield dt

    # ── public API ────────────────────────────────────────────────────────────

    def refresh_col_meta(self, conn: sqlite3.Connection) -> None:
        """Re-read FK/PK metadata from schema (call after schema changes)."""
        if self.schema:
            self._pk_cols, self._fk_cols = _build_col_meta(
                conn, self.schema, self.tbl_name
            )

    def update_rows(self, rows: list[tuple]) -> None:
        """Replace all rows and completely redraw the DataTable."""
        self.all_rows = list(rows)
        dt = self._dt()
        cur_r, cur_c = dt.cursor_row, dt.cursor_column
        dt.clear(columns=True)
        headers = self._make_headers()
        dt.add_columns(*headers)
        for row in self.all_rows:
            key = str(row)
            inline_map = self._inline_map(row)
            dt.add_row(*row_strs(row, inline_map), key=key)
            if self._flasher.has(key):
                _mark_row(dt, key, new=True)
        dt.move_cursor(row=min(cur_r, max(0, len(self.all_rows) - 1)), column=cur_c)
        self._refresh_label()

    def set_relation_kind(self, relation_kind: str) -> None:
        self.relation_kind = relation_kind
        self._refresh_label()

    def set_relation_via(self, relation_via: set[str] | None) -> None:
        self.relation_via = set(relation_via or set())
        self._refresh_label()

    def tick_flash(self) -> None:
        """Call on each poll tick to count down flash highlights."""
        if self._flasher.count() > 0:
            self._flasher.tick(self._dt())
            self._refresh_label()

    # ── internals ─────────────────────────────────────────────────────────────

    def _dt(self) -> DataTable:
        return self.query_one(f"#dt-{self.id}", DataTable)

    def _lbl(self) -> Label:
        return self.query_one(f"#lbl-{self.id}", Label)

    def _make_headers(self) -> list[str]:
        sort_info = self.prefs.get_sort(self.tbl_name) if self.prefs else None
        filter_info = self.prefs.get_filter(self.tbl_name) if self.prefs else None
        builder = HeaderBuilder(self._pk_cols, self._fk_cols, sort_info, filter_info)
        return builder.headers(self.cols)

    def _inline_map(self, row: tuple) -> dict[int, tuple[str, Any]]:
        if self.schema and self.lookup:
            return build_inline_lookup_map(
                self.lookup, self.schema, self.tbl_name, self.cols, row
            )
        return {}

    def _refresh_label(self) -> None:
        if self.is_seed:
            color, marker = SEED_STYLE, "●"
        else:
            color, marker = REL_STYLE, "◆"
        tag = "(seed)" if self.is_seed else f"({len(self.all_rows)} rows)"
        dir_tag = "" if self.is_seed else direction_tag(self.relation_kind)
        via_tag = "" if self.is_seed else via_text(self.relation_via)
        new_cnt = self._flasher.count()
        new_tag = f"  [bold green]+{new_cnt} new[/bold green]" if new_cnt else ""
        self._lbl().update(
            f"[{color}]{marker} {self.tbl_name}[/]  [dim]{tag}[/dim]"
            f"{dir_tag}{via_tag}{new_tag}"
        )
