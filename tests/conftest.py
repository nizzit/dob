"""
Shared pytest fixtures for the domain layer tests.

Uses an in-memory SQLite database with a minimal schema:

  authors(id PK, name)
  books(id PK, author_id FK→authors.id, title)
  tags(id PK, book_id FK→books.id, label)
  statuses(id PK, name)          ← lookup table (2 columns)
"""

from __future__ import annotations

import sqlite3

import pytest

from dob.db.lookup import LookupCache
from dob.db.schema import load_schema
from dob.settings.preferences import UserPreferences


# ── in-memory DB fixture ──────────────────────────────────────────────────────


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.executescript("""
        CREATE TABLE authors (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE books (
            id        INTEGER PRIMARY KEY,
            author_id INTEGER REFERENCES authors(id),
            title     TEXT NOT NULL
        );
        CREATE TABLE tags (
            id      INTEGER PRIMARY KEY,
            book_id INTEGER REFERENCES books(id),
            label   TEXT NOT NULL
        );
        CREATE TABLE statuses (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        INSERT INTO authors VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO books   VALUES (1, 1, 'Alpha'), (2, 1, 'Beta'), (3, 2, 'Gamma');
        INSERT INTO tags    VALUES (1, 1, 'sci-fi'), (2, 1, 'classic'), (3, 2, 'drama');
        INSERT INTO statuses VALUES (1, 'active'), (2, 'inactive');
    """)
    yield c
    c.close()


@pytest.fixture
def schema(conn):
    return load_schema(conn)


@pytest.fixture
def prefs(tmp_path):
    db_file = tmp_path / "test.db"
    db_file.touch()
    return UserPreferences(str(db_file))


@pytest.fixture
def lookup(conn):
    return LookupCache(conn)
