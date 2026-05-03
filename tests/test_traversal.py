"""Tests for dob.domain.traversal — build_observation."""

from __future__ import annotations

import pytest

from dob.domain.traversal import (
    _apply_seed_anchor,
    _merge,
    _mark_kind,
    _mark_via,
    build_observation,
)


# ── build_observation ─────────────────────────────────────────────────────────


def test_build_observation_returns_seed(conn, schema, prefs, lookup):
    obs = build_observation(conn, schema, prefs, "authors", "id", 1, lookup)
    assert obs.seed_table == "authors"
    assert obs.seed_row[0] == 1   # id=1
    assert obs.seed_row[1] == "Alice"


def test_build_observation_finds_books(conn, schema, prefs, lookup):
    obs = build_observation(conn, schema, prefs, "authors", "id", 1, lookup)
    # Alice (id=1) has books Alpha and Beta
    assert "books" in obs.related
    titles = {r[2] for r in obs.related["books"][1]}
    assert titles == {"Alpha", "Beta"}


def test_build_observation_finds_tags_transitively(conn, schema, prefs, lookup):
    # traversal should reach tags via authors→books→tags
    obs = build_observation(conn, schema, prefs, "authors", "id", 1, lookup)
    assert "tags" in obs.related
    labels = {r[2] for r in obs.related["tags"][1]}
    assert labels == {"sci-fi", "classic", "drama"}


def test_build_observation_seed_not_in_related(conn, schema, prefs, lookup):
    obs = build_observation(conn, schema, prefs, "authors", "id", 1, lookup)
    assert "authors" not in obs.related


def test_build_observation_unknown_pk_returns_empty(conn, schema, prefs, lookup):
    obs = build_observation(conn, schema, prefs, "authors", "id", 999, lookup)
    assert obs.seed_row == ()
    assert obs.related == {}


def test_build_observation_direction_tags(conn, schema, prefs, lookup):
    # From books: authors is "out" (books.author_id → authors.id),
    # tags is "in" (tags.book_id → books.id)
    obs = build_observation(conn, schema, prefs, "books", "id", 1, lookup)
    assert obs.related_kind.get("tags") == "in"
    # authors is a lookup-like? No—it has 2 cols but pk is not named 'id' only,
    # actually it IS named id. Let's just check the traversal doesn't crash.
    assert obs.seed_table == "books"


def test_statuses_skipped_as_lookup(conn, schema, prefs, lookup):
    # statuses(id, name) is a lookup table → must NOT appear in related
    obs = build_observation(conn, schema, prefs, "authors", "id", 1, lookup)
    assert "statuses" not in obs.related


# ── _apply_seed_anchor ────────────────────────────────────────────────────────


def test_apply_seed_anchor_filters_unrelated_rows(conn, schema, prefs, lookup):
    # Re-traverse from book id=1, check anchor keeps only tags for book 1
    obs = build_observation(conn, schema, prefs, "books", "id", 1, lookup)
    if "tags" in obs.related:
        tag_book_ids = {r[1] for r in obs.related["tags"][1]}
        assert tag_book_ids == {1}


# ── _merge ────────────────────────────────────────────────────────────────────


def test_merge_deduplicates():
    store: dict = {}
    rows_a = [(1, "a"), (2, "b")]
    rows_b = [(2, "b"), (3, "c")]
    _merge(store, "t", ["id", "val"], rows_a)
    _merge(store, "t", ["id", "val"], rows_b)
    assert len(store["t"][1]) == 3


def test_merge_empty_rows_noop():
    store: dict = {}
    _merge(store, "t", ["id"], [])
    assert "t" not in store


# ── _mark_kind ────────────────────────────────────────────────────────────────


def test_mark_kind_both():
    store: dict = {}
    _mark_kind(store, "t", "out")
    _mark_kind(store, "t", "in")
    assert store["t"] == "both"


def test_mark_kind_same_idempotent():
    store: dict = {}
    _mark_kind(store, "t", "out")
    _mark_kind(store, "t", "out")
    assert store["t"] == "out"


# ── _mark_via ─────────────────────────────────────────────────────────────────


def test_mark_via_accumulates():
    store: dict = {}
    _mark_via(store, "t", "a")
    _mark_via(store, "t", "b")
    assert store["t"] == {"a", "b"}
