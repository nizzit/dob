"""
dob.settings.connection_history
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ConnectionHistory — persistent store of successful database connections.

Uses a local SQLite database (~/.config/dob/connections_history.db) to
remember connection DSNs.  Entries are deduplicated so the same DSN
never appears twice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# ── stored file ──────────────────────────────────────────────────────────────


def _history_db_path() -> Path:
    """Return the path to the connection-history SQLite file."""
    config_dir = Path.home() / ".config" / "dob"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "connections_history.db"


# ── data class ───────────────────────────────────────────────────────────────


@dataclass
class ConnectionEntry:
    """One remembered connection."""

    dsn: str
    label: str
    db_type: str  # "sqlite" | "mysql"
    last_used: datetime

    # -- display helpers ------------------------------------------------

    def display_name(self) -> str:
        """Human-readable label (or sanitised DSN as fallback)."""
        if self.label:
            return self.label
        return self.sanitised_dsn()

    def sanitised_dsn(self) -> str:
        """DSN with passwords masked."""
        if self.dsn.startswith("mysql://"):
            # mysql://user:pass@host/db → mysql://user:***@host/db
            after_scheme = self.dsn[8:]
            if ":" in after_scheme.split("@")[0]:
                user_pass, rest = after_scheme.split("@", 1)
                user = user_pass.split(":", 1)[0]
                return f"mysql://{user}:***@{rest}"
        return self.dsn


# ── manager ──────────────────────────────────────────────────────────────────


_INIT_SQL = """\
CREATE TABLE IF NOT EXISTS connections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    dsn       TEXT    NOT NULL UNIQUE,
    label     TEXT    NOT NULL DEFAULT '',
    db_type   TEXT    NOT NULL,
    last_used TEXT    NOT NULL
);
"""


class ConnectionHistory:
    """Add, search, and retrieve successful connection entries."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _history_db_path()
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute(_INIT_SQL)
        self._conn.commit()

    # -- write -----------------------------------------------------------

    def add_or_update(self, dsn: str, db_type: str, label: str = "") -> None:
        """Insert a new entry or bump *last_used* for an existing one.

        Deduplication is handled by the UNIQUE constraint on *dsn*.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO connections (dsn, label, db_type, last_used)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(dsn) DO UPDATE SET
                last_used = excluded.last_used,
                label     = CASE WHEN excluded.label != ''
                                 THEN excluded.label
                                 ELSE label END
            """,
            (dsn, label, db_type, now),
        )
        self._conn.commit()

    # -- read ------------------------------------------------------------

    def get_all(self) -> list[ConnectionEntry]:
        """Return every entry ordered by most-recently-used first."""
        cur = self._conn.execute(
            "SELECT dsn, label, db_type, last_used "
            "FROM connections ORDER BY last_used DESC"
        )
        return [
            ConnectionEntry(
                dsn=row[0],
                label=row[1],
                db_type=row[2],
                last_used=datetime.fromisoformat(row[3]),
            )
            for row in cur.fetchall()
        ]

    def search(self, query: str) -> list[ConnectionEntry]:
        """Case-insensitive search across dsn and label."""
        q = f"%{query}%"
        cur = self._conn.execute(
            "SELECT dsn, label, db_type, last_used "
            "FROM connections "
            "WHERE dsn LIKE ? OR label LIKE ? "
            "ORDER BY last_used DESC",
            (q, q),
        )
        return [
            ConnectionEntry(
                dsn=row[0],
                label=row[1],
                db_type=row[2],
                last_used=datetime.fromisoformat(row[3]),
            )
            for row in cur.fetchall()
        ]

    def remove(self, dsn: str) -> None:
        """Delete a single entry."""
        self._conn.execute("DELETE FROM connections WHERE dsn = ?", (dsn,))
        self._conn.commit()

    def clear(self) -> None:
        """Remove all entries."""
        self._conn.execute("DELETE FROM connections")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- context manager -------------------------------------------------

    def __enter__(self) -> "ConnectionHistory":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
