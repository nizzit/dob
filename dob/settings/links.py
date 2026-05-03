"""
dob.settings.links
~~~~~~~~~~~~~~~~~~
VirtualLinks — manages user-defined FK-like column associations stored in
the project settings file.

Virtual links are injected into the Schema FK graph at load time and
whenever the user creates / removes a link.
"""

from __future__ import annotations

from dob.db.schema import FKInfo, Schema

from .store import ProjectSettings


class VirtualLinks:
    """CRUD for virtual (user-defined) column links."""

    @classmethod
    def add(
        cls, db_path: str, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> None:
        s = ProjectSettings(db_path)
        entry = dict(
            from_table=from_table, from_col=from_col, to_table=to_table, to_col=to_col
        )
        if entry not in s.links:
            s.links.append(entry)
            s.patch(links=s.links)

    @classmethod
    def remove(
        cls, db_path: str, from_table: str, from_col: str, to_table: str, to_col: str
    ) -> None:
        s = ProjectSettings(db_path)
        entry = dict(
            from_table=from_table, from_col=from_col, to_table=to_table, to_col=to_col
        )
        new_links = [ln for ln in s.links if ln != entry]
        s.patch(links=new_links)

    @classmethod
    def inject(cls, schema: Schema, db_path: str) -> None:
        """Add virtual FKInfo entries to an already-loaded Schema in-place."""
        if not db_path:
            return
        data = ProjectSettings.load_data(db_path)
        for entry in data["links"]:
            ft, fc = entry["from_table"], entry["from_col"]
            tt, tc = entry["to_table"], entry["to_col"]
            if ft not in schema.fk_from:
                schema.fk_from[ft] = []
            if tt not in schema.fk_to:
                schema.fk_to[tt] = []
            fk = FKInfo(
                from_table=ft, from_col=fc, to_table=tt, to_col=tc, virtual=True
            )
            existing = {(f.from_col, f.to_table, f.to_col) for f in schema.fk_from[ft]}
            if (fc, tt, tc) not in existing:
                schema.fk_from[ft].append(fk)
                schema.fk_to[tt].append(fk)
