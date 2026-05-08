"""
dob.app
~~~~~~~
DobApp — root Textual application.

Live state is managed centrally via LiveStateRegistry instead of
ad-hoc getattr/setattr on the App object.
"""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import App
from textual.binding import Binding

from dob.ui.ru_layout import handle_ru_key
from dob.ui.screens.connection_history import ConnectionHistoryScreen
from dob.ui.screens.open_db import OpenDBScreen


class LiveStateRegistry:
    """Tracks which tables are currently in live-polling mode."""

    def __init__(self) -> None:
        self._state: dict[str, bool] = {}

    def get(self, table: str) -> bool:
        return self._state.get(table, False)

    def set(self, table: str, value: bool) -> None:
        self._state[table] = bool(value)


class DobApp(App):
    TITLE = "dob"
    CSS_PATH = Path(__file__).parent / "ui" / "styles.tcss"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=True)]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.initial_db_path = db_path
        self.live_state = LiveStateRegistry()

    # ── backward-compatible helpers (screens call these) ─────────────────────

    def is_table_live(self, table: str) -> bool:
        return self.live_state.get(table)

    def set_table_live(self, table: str, value: bool) -> None:
        self.live_state.set(table, value)

    # ── global RU layout hook (replaces per-screen RuKeysMixin) ───────────────

    async def on_key(self, event: events.Key) -> None:
        # Try to translate Russian key on the currently focused widget first,
        # then fall back to the active screen.
        target = self.focused or self.screen
        await handle_ru_key(target, event)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        if self.initial_db_path:
            self.push_screen(OpenDBScreen())
            self.call_after_refresh(self._auto_open)
        else:
            self.push_screen(
                ConnectionHistoryScreen(),
                callback=self._on_history_result,
            )

    def _on_history_result(self, dsn: str | None) -> None:
        """Handle connection-history dialog result."""
        from dob.db.connection import (
            open_connection,
            open_mysql_bare,
            parse_mysql_dsn,
        )
        from dob.db.schema import load_schema
        from dob.settings.links import VirtualLinks
        from dob.settings.preferences import UserPreferences
        from dob.ui.screens.db_picker import DbPickerScreen
        from dob.ui.screens.table_picker import TablePickerScreen

        if dsn:
            is_mysql = dsn.startswith("mysql://")
            # MySQL bare (no database) → need picker
            if is_mysql:
                creds = parse_mysql_dsn(dsn)
                if not creds.database:
                    try:
                        bare_conn = open_mysql_bare(dsn)
                        self.push_screen(DbPickerScreen(bare_conn, dsn))
                        return
                    except Exception:
                        self.push_screen(OpenDBScreen())
                        return
            try:
                conn = open_connection(dsn)
                schema = load_schema(conn)
                schema.db_path = Path(dsn) if not is_mysql else dsn
                if not is_mysql:
                    VirtualLinks.inject(schema, str(Path(dsn)))
                prefs = UserPreferences(schema.db_path)
                self.push_screen(TablePickerScreen(conn, schema, prefs, dsn))
            except Exception:
                screen = OpenDBScreen()
                self.push_screen(screen)
                screen.set_initial_path(dsn)
        else:
            self.push_screen(OpenDBScreen())

    def _auto_open(self) -> None:
        screen = self.screen
        if isinstance(screen, OpenDBScreen):
            screen.set_initial_path(self.initial_db_path)
            screen._try_open(self.initial_db_path)
