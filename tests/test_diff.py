"""Tests for dob.domain.diff — diff_observations."""

from __future__ import annotations

from dob.domain.diff import diff_observations
from dob.domain.observation import Observation


def _obs(seed_row=(), related=None):
    return Observation(
        seed_table="t",
        seed_row=seed_row,
        seed_cols=["id", "val"],
        related=related or {},
    )


def test_no_changes():
    rows = [(1, "a"), (2, "b")]
    o = _obs(related={"x": (["id", "v"], rows)})
    diffs = diff_observations(o, o)
    assert diffs == []


def test_new_rows_detected():
    old = _obs(related={"x": (["id", "v"], [(1, "a")])})
    new = _obs(related={"x": (["id", "v"], [(1, "a"), (2, "b")])})
    diffs = diff_observations(old, new)
    assert len(diffs) == 1
    assert diffs[0].table == "x"
    assert diffs[0].new_rows == [(2, "b")]


def test_new_table_appears():
    old = _obs()
    new = _obs(related={"y": (["id"], [(5,)])})
    diffs = diff_observations(old, new)
    assert len(diffs) == 1
    assert diffs[0].table == "y"


def test_seed_update_prepended():
    old = _obs(seed_row=(1, "old"))
    new = _obs(seed_row=(1, "new"))
    diffs = diff_observations(old, new)
    assert len(diffs) == 1
    assert "seed updated" in diffs[0].table
    assert diffs[0].new_rows == [(1, "new")]


def test_seed_update_with_related_changes():
    old = _obs(seed_row=(1, "old"), related={"x": (["id"], [(1,)])})
    new = _obs(seed_row=(1, "new"), related={"x": (["id"], [(1,), (2,)])})
    diffs = diff_observations(old, new)
    # seed update first, then table diff
    assert diffs[0].table.endswith("(seed updated)")
    assert any(d.table == "x" for d in diffs)
