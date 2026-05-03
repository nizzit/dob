"""
dob.domain.observation
~~~~~~~~~~~~~~~~~~~~~~
Observation dataclass — all rows gathered starting from a seed row.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Observation:
    """All rows gathered starting from a seed row."""

    seed_table: str
    seed_row: tuple
    seed_cols: list[str]

    # table → (columns, rows)
    related: dict[str, tuple[list[str], list[tuple]]] = field(default_factory=dict)

    # table → relation kind relative to traversal:
    #   "out"  = table is target of outgoing link from seed
    #   "in"   = table is source of incoming link to seed
    #   "both" = observed in both roles
    related_kind: dict[str, str] = field(default_factory=dict)

    # table → set of table names through which this table was reached
    related_via: dict[str, set[str]] = field(default_factory=dict)
