"""
dob.domain.diff
~~~~~~~~~~~~~~~
Compute differences between two Observations (new rows since last poll).
"""

from __future__ import annotations

from dataclasses import dataclass

from .observation import Observation


@dataclass
class TableDiff:
    """New rows that appeared in one table since the last poll."""

    table: str
    cols: list[str]
    new_rows: list[tuple]


def diff_observations(old: Observation, new: Observation) -> list[TableDiff]:
    """Return diffs for every table that gained new rows."""
    diffs: list[TableDiff] = []

    all_tables = set(old.related) | set(new.related)
    for tbl in all_tables:
        new_cols, new_rows = new.related.get(tbl, ([], []))
        old_rows_set = set(old.related[tbl][1]) if tbl in old.related else set()

        added = [r for r in new_rows if r not in old_rows_set]
        if added:
            diffs.append(TableDiff(table=tbl, cols=new_cols, new_rows=added))

    # seed row change (update)
    if old.seed_row != new.seed_row and new.seed_row:
        diffs.insert(
            0,
            TableDiff(
                table=f"{new.seed_table} (seed updated)",
                cols=new.seed_cols,
                new_rows=[new.seed_row],
            ),
        )

    return diffs
