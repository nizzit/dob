"""
dob.ui.screens.observation
~~~~~~~~~~~~~~~~~~~~~~~~~~
ObservationScreen — shows all rows related to a seed row.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from dob.db.lookup import LookupCache
from dob.db.schema import Schema
from dob.domain.diff import diff_observations
from dob.domain.traversal import build_observation
from dob.settings.preferences import UserPreferences
from dob.ui.drilldown import open_observation_for_row
from dob.ui.link_actions import open_link_menu
from dob.ui.live_poller import LivePoller
from dob.ui.sort_mixin import SortableMixin
from dob.ui.widgets.table_block import TableBlock, _build_col_meta


class ObservationScreen(SortableMixin, Screen):
    """Shows the observation graph for a seed row.  Press L to toggle live polling."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Back", show=True),
        Binding("l", "toggle_live", "Live", show=True),
        Binding("f", "expand_focused", "Expand", show=True),
        Binding("k", "link", "Link cols", show=True),
        Binding("s", "sort_column", "Sort", show=True),
    ]

    def __init__(
        self,
        conn: sqlite3.Connection,
        schema: Schema,
        prefs: UserPreferences,
        table: str,
        pk_col: str,
        pk_val: Any,
    ) -> None:
        super().__init__()
        self._conn = conn
        self._schema = schema
        self._prefs = prefs
        self._table = table
        self._pk_col = pk_col
        self._pk_val = pk_val
        self._lookup = LookupCache(conn)

        self._obs = build_observation(conn, schema, prefs, table, pk_col, pk_val, self._lookup)
        self._blocks: dict[str, TableBlock] = {}
        self._poller = LivePoller(self, self._fetch_rows, self._on_new_rows)

    # ── SortableMixin interface ───────────────────────────────────────────────

    @property
    def _sort_prefs(self) -> UserPreferences:
        return self._prefs

    def _resolve_sort_target(self, widget: Any = None) -> tuple[str, list[str]] | None:
        block = self._block_for_widget(widget)
        if block is None:
            return None
        return block.tbl_name, block.cols

    def _after_sort(self) -> None:
        self._reload()

    # ── compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield Label("", id="live-status")
        with VerticalScroll(id="obs-scroll"):
            obs = self._obs

            _pk, _fk = _build_col_meta(self._conn, self._schema, obs.seed_table)
            blk = TableBlock(
                table=obs.seed_table,
                cols=obs.seed_cols,
                rows=[obs.seed_row] if obs.seed_row else [],
                is_seed=True,
                pk_cols=_pk,
                fk_cols=_fk,
                schema=self._schema,
                prefs=self._prefs,
                lookup=self._lookup,
                id="block-seed",
                classes="obs-block",
            )
            self._blocks["seed"] = blk
            yield blk

            if not obs.related:
                yield Label("[dim]No related records found.[/dim]")
            else:
                for tbl_name, (cols, rows) in obs.related.items():
                    _pk, _fk = _build_col_meta(self._conn, self._schema, tbl_name)
                    blk = TableBlock(
                        table=tbl_name,
                        cols=cols,
                        rows=rows,
                        is_seed=False,
                        pk_cols=_pk,
                        fk_cols=_fk,
                        schema=self._schema,
                        prefs=self._prefs,
                        lookup=self._lookup,
                        relation_kind=obs.related_kind.get(tbl_name, ""),
                        relation_via=obs.related_via.get(tbl_name, set()),
                        id=f"block-{tbl_name}",
                        classes="obs-block",
                    )
                    self._blocks[tbl_name] = blk
                    yield blk

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        live = getattr(self.app, "is_table_live", lambda t: False)(self._table)
        if live:
            self._poller.start()
        self.query_one("#obs-scroll").focus()

    def on_screen_resume(self) -> None:
        live = getattr(self.app, "is_table_live", lambda t: False)(self._table)
        if live and not self._poller.is_live:
            self._poller.start()
        elif not live and self._poller.is_live:
            self._poller.stop()

    def on_unmount(self) -> None:
        self._poller.dispose()

    # ── live ─────────────────────────────────────────────────────────────────

    def action_toggle_live(self) -> None:
        self._poller.toggle()
        setter = getattr(self.app, "set_table_live", None)
        if callable(setter):
            setter(self._table, self._poller.is_live)

    def _fetch_rows(self) -> tuple[list[str], list[tuple]]:
        new_obs = build_observation(
            self._conn, self._schema, self._prefs,
            self._table, self._pk_col, self._pk_val, self._lookup,
        )
        # Return seed row list as "rows" — poller doesn't use it directly,
        # we handle diff ourselves in _on_new_rows.
        return self._obs.seed_cols, [self._obs.seed_row] if self._obs.seed_row else []

    def _on_new_rows(self, new_rows: list[tuple], all_rows: list[tuple]) -> None:
        pass  # we override the whole poll logic below

    # ── poll (custom — uses diff_observations) ────────────────────────────────

    def _poll_custom(self) -> None:
        """Called by the poller timer; we bypass the default fetch loop."""
        try:
            new_obs = build_observation(
                self._conn, self._schema, self._prefs,
                self._table, self._pk_col, self._pk_val, self._lookup,
            )
        except Exception:
            return

        diffs = diff_observations(self._obs, new_obs)
        scroll: VerticalScroll = self.query_one("#obs-scroll")

        for diff in diffs:
            real_tbl = diff.table.replace(" (seed updated)", "")
            if real_tbl in self._blocks:
                blk = self._blocks[real_tbl]
                for r in diff.new_rows:
                    blk._flasher.add(str(r))
                if real_tbl == new_obs.seed_table:
                    blk.update_rows([new_obs.seed_row] if new_obs.seed_row else [])
                else:
                    blk.set_relation_kind(new_obs.related_kind.get(real_tbl, ""))
                    blk.set_relation_via(new_obs.related_via.get(real_tbl, set()))
                    blk.update_rows(new_obs.related[real_tbl][1])
            else:
                _pk, _fk = _build_col_meta(self._conn, self._schema, real_tbl)
                blk = TableBlock(
                    table=real_tbl,
                    cols=diff.cols,
                    rows=diff.new_rows,
                    is_seed=False,
                    pk_cols=_pk,
                    fk_cols=_fk,
                    schema=self._schema,
                    prefs=self._prefs,
                    lookup=self._lookup,
                    relation_kind=new_obs.related_kind.get(real_tbl, ""),
                    relation_via=new_obs.related_via.get(real_tbl, set()),
                    id=f"block-{real_tbl}",
                    classes="obs-block",
                )
                self._blocks[real_tbl] = blk
                scroll.mount(blk)

        for blk in self._blocks.values():
            blk.tick_flash()

        self._obs = new_obs

    # ── events ────────────────────────────────────────────────────────────────

    @on(DataTable.HeaderSelected)
    def on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self._sort_from_header_event(event)

    @on(DataTable.CellSelected)
    def row_drilldown(self, event: DataTable.CellSelected) -> None:
        block = self._block_for_widget(event.data_table)
        if block is None or block.is_seed:
            return
        row_index = event.coordinate.row
        if row_index >= len(block.all_rows):
            return
        open_observation_for_row(
            self.app, self._conn, self._schema, self._prefs,
            block.tbl_name, block.cols, block.all_rows[row_index],
        )

    def action_link(self) -> None:
        focused = self.focused
        if not isinstance(focused, DataTable):
            self.notify("Focus a table cell first", severity="warning")
            return
        block = self._block_for_widget(focused)
        if block is None:
            return
        col_index = focused.cursor_column
        if col_index >= len(block.cols):
            return
        open_link_menu(
            self, self._schema, self._schema.db_path,
            block.tbl_name, block.cols[col_index],
            on_changed=self._on_links_changed,
        )

    def action_expand_focused(self) -> None:
        from dob.ui.screens.expanded import ExpandedTableScreen

        focused = self.focused
        target_block = (
            self._block_for_widget(focused) if isinstance(focused, DataTable) else None
        )
        if target_block is None and self._blocks:
            target_block = next(iter(self._blocks.values()))
        if target_block is None:
            return
        _pk, _fk = _build_col_meta(self._conn, self._schema, target_block.tbl_name)
        self.app.push_screen(
            ExpandedTableScreen(
                title=target_block.tbl_name,
                cols=list(target_block.cols),
                rows=list(target_block.all_rows),
                conn=self._conn,
                schema=self._schema,
                prefs=self._prefs,
                tbl_name=target_block.tbl_name,
                pk_cols=_pk,
                fk_cols=_fk,
                seed_table=self._table,
                seed_pk_col=self._pk_col,
                seed_pk_val=self._pk_val,
            )
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _block_for_widget(self, widget: Any) -> TableBlock | None:
        node = widget
        while node is not None:
            if isinstance(node, TableBlock):
                return node
            node = node.parent
        return None

    def _on_links_changed(self) -> None:
        from dob.settings.links import VirtualLinks
        db_path = self._schema.db_path
        if db_path:
            VirtualLinks.inject(self._schema, db_path)
        self._lookup.invalidate()
        self._obs = build_observation(
            self._conn, self._schema, self._prefs,
            self._table, self._pk_col, self._pk_val, self._lookup,
        )
        self._rebuild_blocks()

    def _reload(self) -> None:
        self._obs = build_observation(
            self._conn, self._schema, self._prefs,
            self._table, self._pk_col, self._pk_val, self._lookup,
        )
        for bid, blk in self._blocks.items():
            real_tbl = blk.tbl_name
            if real_tbl == self._obs.seed_table:
                blk.update_rows([self._obs.seed_row] if self._obs.seed_row else [])
            elif real_tbl in self._obs.related:
                blk.set_relation_kind(self._obs.related_kind.get(real_tbl, ""))
                blk.set_relation_via(self._obs.related_via.get(real_tbl, set()))
                blk.update_rows(self._obs.related[real_tbl][1])

    def _rebuild_blocks(self) -> None:
        scroll: VerticalScroll = self.query_one("#obs-scroll")
        obs = self._obs

        seed_blk = self._blocks.get("seed")
        if seed_blk:
            seed_blk.refresh_col_meta(self._conn)
            seed_blk.update_rows([obs.seed_row] if obs.seed_row else [])

        gone = [t for t in list(self._blocks) if t != "seed" and t not in obs.related]
        for tbl in gone:
            blk = self._blocks.pop(tbl)
            blk.remove()

        for tbl_name, (cols, rows) in obs.related.items():
            if tbl_name in self._blocks:
                self._blocks[tbl_name].refresh_col_meta(self._conn)
                self._blocks[tbl_name].set_relation_kind(obs.related_kind.get(tbl_name, ""))
                self._blocks[tbl_name].set_relation_via(obs.related_via.get(tbl_name, set()))
                self._blocks[tbl_name].update_rows(rows)
            else:
                _pk, _fk = _build_col_meta(self._conn, self._schema, tbl_name)
                blk = TableBlock(
                    table=tbl_name,
                    cols=cols,
                    rows=rows,
                    is_seed=False,
                    pk_cols=_pk,
                    fk_cols=_fk,
                    schema=self._schema,
                    prefs=self._prefs,
                    lookup=self._lookup,
                    relation_kind=obs.related_kind.get(tbl_name, ""),
                    relation_via=obs.related_via.get(tbl_name, set()),
                    id=f"block-{tbl_name}",
                    classes="obs-block",
                )
                self._blocks[tbl_name] = blk
                scroll.mount(blk)
