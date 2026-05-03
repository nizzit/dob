"""
dob.settings.store
~~~~~~~~~~~~~~~~~~
ProjectSettings — single owner of the <db>.dob.json file.

All read/write goes through this class.  Changes are written atomically
using a temp-file + rename pattern to avoid corruption.

JSON schema:
{
  "links":   [ {from_table, from_col, to_table, to_col}, ... ],
  "sorts":   { "table": ["col", reverse_bool], ... },
  "filters": { "table": ["col", value], ... }
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_EMPTY: dict = {"links": [], "sorts": {}, "filters": {}}


def _settings_path(db_path: str) -> Path:
    return Path(db_path).with_suffix(".dob.json")


class ProjectSettings:
    """In-memory snapshot of the project settings file with atomic save."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._data: dict = self._load()

    # ── public properties ─────────────────────────────────────────────────────

    @property
    def links(self) -> list[dict]:
        return self._data["links"]

    @property
    def sorts(self) -> dict[str, list]:
        return self._data["sorts"]

    @property
    def filters(self) -> dict[str, list]:
        return self._data["filters"]

    # ── mutation helpers ──────────────────────────────────────────────────────

    def patch(self, **kwargs: Any) -> None:
        """Update one or more top-level keys and persist."""
        for key, val in kwargs.items():
            self._data[key] = val
        self._save()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        p = _settings_path(self._db_path)
        if not p.exists():
            return dict(_EMPTY)
        try:
            raw = json.loads(p.read_text())
            return {
                "links": raw.get("links", []),
                "sorts": raw.get("sorts", {}),
                "filters": raw.get("filters", {}),
            }
        except Exception:
            return dict(_EMPTY)

    def _save(self) -> None:
        if not self._db_path:
            return
        p = _settings_path(self._db_path)
        tmp = p.with_suffix(".dob.json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, p)

    # ── class-level helpers (for callers that don't hold an instance) ─────────

    @classmethod
    def load_data(cls, db_path: str) -> dict:
        """Return a plain dict snapshot without creating a settings instance."""
        inst = cls(db_path)
        return dict(inst._data)
