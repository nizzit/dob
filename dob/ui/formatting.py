"""
dob.ui.formatting
~~~~~~~~~~~~~~~~~
Formatting helpers for the UI layer.

Includes:
  - fmt(v)            — None → "NULL", else str
  - filter_caption    — header suffix for active filter
  - row_strs          — row tuple → list of display strings (with inline lookups)
  - direction_tag     — FK direction marker (→ / ← / ↔)
  - via_text          — "via: table1, table2" annotaton
  - col_header        — single column header with PK/FK/sort/filter badges
  - HeaderBuilder     — per-table helper that builds the full headers list
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dob.db.lookup import LookupCache
    from dob.db.schema import FKInfo, Schema
    from dob.settings.preferences import UserPreferences


# ── basic value formatting ────────────────────────────────────────────────────


def fmt(v: Any) -> str:
    return str(v) if v is not None else "NULL"


def filter_caption(filter_info: tuple[str, Any] | None) -> str:
    if not filter_info:
        return ""
    col, val = filter_info
    return f"  filter: {col} = {fmt(val)}"


# ── row rendering ─────────────────────────────────────────────────────────────


def row_strs(
    row: tuple,
    inline_map: dict[int, tuple[str, Any]] | None = None,
) -> list[str]:
    """Convert a row tuple to display strings, optionally with lookup annotations."""
    inline_map = inline_map or {}
    out: list[str] = []
    for i, v in enumerate(row):
        base = fmt(v)
        if i in inline_map:
            _, ref_value = inline_map[i]
            rendered = fmt(ref_value)
            out.append(f"{base} [dim]≈ {rendered}[/dim]")
        else:
            out.append(base)
    return out


def build_inline_lookup_map(
    lookup: "LookupCache",
    schema: "Schema",
    table: str,
    cols: list[str],
    row: tuple,
) -> dict[int, tuple[str, Any]]:
    """Map column index → (lookup_table, lookup_value) for inline rendering."""
    col_idx = {c: i for i, c in enumerate(cols)}
    inline: dict[int, tuple[str, Any]] = {}

    for fk in schema.fk_from.get(table, []):
        if not lookup.is_lookup(fk.to_table):
            continue
        if fk.from_col not in col_idx:
            continue
        idx = col_idx[fk.from_col]
        fk_val = row[idx]
        if fk_val is None:
            continue
        lookup_val = lookup.fetch_value(fk.to_table, fk_val)
        if lookup_val is None:
            continue
        inline[idx] = (fk.to_table, lookup_val)

    return inline


# ── direction / via tags ──────────────────────────────────────────────────────


def direction_tag(kind: str) -> str:
    if kind == "out":
        return " [dim]→[/dim]"
    if kind == "in":
        return " [dim]←[/dim]"
    if kind == "both":
        return " [dim]↔[/dim]"
    return ""


def via_text(via_tables: set[str] | None) -> str:
    if not via_tables:
        return ""
    tables = sorted(via_tables)
    if len(tables) > 4:
        shown = ", ".join(tables[:4])
        return f" [dim]via: {shown}, +{len(tables) - 4}[/dim]"
    return f" [dim]via: {', '.join(tables)}[/dim]"


# ── column header ─────────────────────────────────────────────────────────────


def col_header(
    col: str,
    pk_cols: set[str],
    fk_cols: "dict[str, FKInfo]",
    sort_info: tuple[str, bool] | None = None,
    filter_info: tuple[str, Any] | None = None,
) -> str:
    """
    Return a column header string with relationship indicators:
      * Primary key    → Real FK    ~ Virtual link
      ↑↓ Sort          ◉ Active filter
    """
    pk_prefix = "[bold yellow]*[/bold yellow]" if col in pk_cols else ""

    if col in fk_cols:
        fk = fk_cols[col]
        fk_prefix = "[#b57ed6]~[/#b57ed6]" if fk.virtual else "[bold cyan]→[/bold cyan]"
    else:
        fk_prefix = ""

    suffix = ""
    if sort_info and sort_info[0] == col:
        suffix += " [bold]↓[/bold]" if sort_info[1] else " [bold]↑[/bold]"
    if filter_info and filter_info[0] == col:
        suffix += " [bold magenta]◉[/bold magenta]"

    if pk_prefix or fk_prefix:
        return f"{pk_prefix}{fk_prefix}{col}{suffix}"
    return f"{col}{suffix}"


# ── HeaderBuilder ─────────────────────────────────────────────────────────────


class HeaderBuilder:
    """
    Reusable helper that builds the column header list for a given table.

    Eliminates the 5+ duplicate header-construction loops scattered across
    TableBlock, ExpandedTableScreen and RowPickerScreen.
    """

    def __init__(
        self,
        pk_cols: set[str],
        fk_cols: "dict[str, FKInfo]",
        sort_info: tuple[str, bool] | None,
        filter_info: tuple[str, Any] | None,
    ) -> None:
        self._pk_cols = pk_cols
        self._fk_cols = fk_cols
        self._sort_info = sort_info
        self._filter_info = filter_info

    def headers(self, cols: list[str]) -> list[str]:
        return [
            col_header(c, self._pk_cols, self._fk_cols, self._sort_info, self._filter_info)
            for c in cols
        ]
