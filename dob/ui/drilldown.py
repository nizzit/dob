"""
dob.ui.drilldown
~~~~~~~~~~~~~~~~
open_observation_for_row — single function replacing three copies of the
drill-down logic spread across ObservationScreen, ExpandedTableScreen,
RowPickerScreen.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textual.app import App

    from dob.db.schema import Schema
    from dob.settings.preferences import UserPreferences


def open_observation_for_row(
    app: "App",
    conn: sqlite3.Connection,
    schema: "Schema",
    prefs: "UserPreferences",
    table: str,
    cols: list[str],
    row: tuple,
) -> None:
    """Push an ObservationScreen for the given row onto the app screen stack."""
    # Import here to avoid circular deps
    from dob.db.queries import get_pk_column
    from dob.ui.screens.observation import ObservationScreen

    pk_col = get_pk_column(conn, table) or cols[0]
    row_dict = dict(zip(cols, row))
    pk_val = row_dict.get(pk_col, row[0])

    app.push_screen(ObservationScreen(conn, schema, prefs, table, pk_col, pk_val))
