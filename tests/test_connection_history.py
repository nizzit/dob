"""
tests.test_connection_history
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for ConnectionHistory — storage, deduplication, search.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dob.settings.connection_history import ConnectionHistory, ConnectionEntry


@pytest.fixture
def history(tmp_path: Path) -> ConnectionHistory:
    db = tmp_path / "hist.db"
    h = ConnectionHistory(db_path=db)
    yield h
    h.close()


# ── add / dedup ──────────────────────────────────────────────────────────────


def test_add_single_entry(history: ConnectionHistory) -> None:
    history.add_or_update("mysql://u:p@h/db", "mysql")
    entries = history.get_all()
    assert len(entries) == 1
    assert entries[0].dsn == "mysql://u:p@h/db"
    assert entries[0].db_type == "mysql"


def test_deduplication(history: ConnectionHistory) -> None:
    """Adding the same DSN twice must NOT create a duplicate row."""
    history.add_or_update("./test.db", "sqlite")
    history.add_or_update("./test.db", "sqlite")
    entries = history.get_all()
    assert len(entries) == 1


def test_different_dsns_are_separate(history: ConnectionHistory) -> None:
    history.add_or_update("./a.db", "sqlite")
    history.add_or_update("./b.db", "sqlite")
    assert len(history.get_all()) == 2


def test_label_update(history: ConnectionHistory) -> None:
    history.add_or_update("mysql://u:p@h/db", "mysql", label="prod-db")
    history.add_or_update("mysql://u:p@h/db", "mysql")
    entry = history.get_all()[0]
    assert entry.label == "prod-db"  # label should persist


# ── ordering ─────────────────────────────────────────────────────────────────


def test_most_recent_first(history: ConnectionHistory) -> None:
    history.add_or_update("./old.db", "sqlite")
    history.add_or_update("./new.db", "sqlite")
    entries = history.get_all()
    assert entries[0].dsn == "./new.db"
    assert entries[1].dsn == "./old.db"


def test_bump_last_used_moves_to_top(history: ConnectionHistory) -> None:
    history.add_or_update("./old.db", "sqlite")
    history.add_or_update("./new.db", "sqlite")
    # Re-add old.db to bump its last_used
    history.add_or_update("./old.db", "sqlite")
    entries = history.get_all()
    assert entries[0].dsn == "./old.db"
    assert entries[1].dsn == "./new.db"


# ── search ───────────────────────────────────────────────────────────────────


def test_search_by_dsn(history: ConnectionHistory) -> None:
    history.add_or_update("mysql://user:pass@prod-host/mydb", "mysql")
    history.add_or_update("./local.db", "sqlite")
    results = history.search("prod")
    assert len(results) == 1
    assert "prod-host" in results[0].dsn


def test_search_case_insensitive(history: ConnectionHistory) -> None:
    history.add_or_update("./TestDB.db", "sqlite")
    results = history.search("testdb")
    assert len(results) == 1


def test_search_returns_all_when_no_match(history: ConnectionHistory) -> None:
    history.add_or_update("./abc.db", "sqlite")
    results = history.search("xyz")
    assert len(results) == 0


def test_search_empty_query_returns_all(history: ConnectionHistory) -> None:
    history.add_or_update("mysql://a:b@h/db", "mysql")
    history.add_or_update("./x.db", "sqlite")
    # Empty query → matches LIKE '%%' → all rows
    results = history.search("")
    assert len(results) == 2


# ── remove / clear ───────────────────────────────────────────────────────────


def test_remove(history: ConnectionHistory) -> None:
    history.add_or_update("./a.db", "sqlite")
    history.add_or_update("./b.db", "sqlite")
    history.remove("./a.db")
    assert len(history.get_all()) == 1
    assert history.get_all()[0].dsn == "./b.db"


def test_clear(history: ConnectionHistory) -> None:
    history.add_or_update("./a.db", "sqlite")
    history.add_or_update("./b.db", "sqlite")
    history.clear()
    assert history.get_all() == []


# ── ConnectionEntry display helpers ──────────────────────────────────────────


def test_sanitised_dsn_mysql_masks_password() -> None:
    entry = ConnectionEntry(
        dsn="mysql://alice:secret@db.example.com:3307/mydb",
        label="",
        db_type="mysql",
        last_used=datetime.now(timezone.utc),
    )
    assert "secret" not in entry.sanitised_dsn()
    assert "alice:***@db.example.com" in entry.sanitised_dsn()


def test_sanitised_dsn_sqlite_is_identity() -> None:
    entry = ConnectionEntry(
        dsn="./test.db",
        label="",
        db_type="sqlite",
        last_used=datetime.now(timezone.utc),
    )
    assert entry.sanitised_dsn() == "./test.db"


def test_display_name_uses_label_when_set() -> None:
    entry = ConnectionEntry(
        dsn="mysql://u:p@h/db",
        label="Production",
        db_type="mysql",
        last_used=datetime.now(timezone.utc),
    )
    assert entry.display_name() == "Production"


def test_display_name_falls_back_to_sanitised_dsn() -> None:
    entry = ConnectionEntry(
        dsn="mysql://u:p@h/db",
        label="",
        db_type="mysql",
        last_used=datetime.now(timezone.utc),
    )
    assert entry.display_name() == entry.sanitised_dsn()
