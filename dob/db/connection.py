"""
dob.db.connection
~~~~~~~~~~~~~~~~~
Single entry-point for opening a SQLite connection.

WAL mode + check_same_thread=False so the live-polling timer thread can
read while the main thread is idle.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_connection(path: str | Path) -> sqlite3.Connection:
    """Return an open SQLite connection ready for multi-thread use."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
