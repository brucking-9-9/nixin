"""The Packages screen: what the host has installed (`list`), and what a nixpkgs update would
change (`outdated`), with the button that takes it (`update`, then Pending).

Installed: one `list` (every scope) loaded on entry; the filter box and the scope toggle
narrow it here, with `list --filter`/`--scope` semantics (view.filter_packages), so they
need no second eval. Enter opens the Details screen for the row (info, versions, where,
Remove), for the row's Home Manager user. Updates: `outdated` (lock mode, `m` for
`--published`) on first view; `u` previews `update --dry-run`, confirms, runs `update`, and
goes to Pending, where Plan builds it as for any other change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Tab, Tabs

from ..ui import view
from .modals import NoticeModal, UpdateModal
from .screens import Base, DetailsScreen
from .widgets import Line, NoticeBox, Spinner, cell

if TYPE_CHECKING:
    from .app import NixinApp

TABS = ("installed", "updates")
MODES = ("lock", "published")
OUTDATED_HINT = {
    "lock": "two package-list evals: seconds; about a minute when nixpkgs has a new commit",
    "published": "one eval and a few search.nixos.org queries: seconds",
}


class PackagesScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "escape", "Back"),
        Binding("t", "tab('updates')", "Updates"),
        Binding("t", "tab('installed')", "Installed"),
        Binding("f", "focus_filter", "Filter", show=False),
        Binding("h", "next_scope", "For", show=False),
        Binding("m", "mode", "Mode", show=False),
        Binding("u", "update", "Update lock", show=False),
        Binding("r", "refresh", "Refresh", show=False),  # ? lists it; a phone has no room
    ]

    def __init__(self, tab: str = "installed") -> None:
        super().__init__()
        self.tab = tab
        self.scope = "all"
        self.rows: list[view.PackageRow] = []  # the whole list
        self.shown: list[view.PackageRow] = []  # what the filter and the scope leave
        self.updates: list[view.UpdateRow] = []
        self.mode = "lock"
        self._planned: dict[str, int] = {}  # table id → the width its columns were planned for

    @property
    def nixin(self) -> NixinApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        yield Tabs(*[Tab(t, id=f"pk-tab-{t}") for t in TABS], id="pk-tabs")
        with ContentSwitcher(initial=f"pk-{self.tab}", id="pk-switch"):
            with Vertical(id="pk-installed"):
                with Horizontal(id="pk-bar"):
                    yield Input(placeholder="filter (f)…", id="pk-filter", compact=True)
                    yield Button("for: all (h)", id="pk-scope", compact=True)
                yield Spinner(id="pk-spin")
                yield NoticeBox(id="pk-notice")
                yield DataTable(id="pk-table", cursor_type="row")
                yield Line("", id="pk-detail")
                yield Line("", id="pk-footer")
            with Vertical(id="pk-updates"):
                yield Line("", id="up-head")
                yield Spinner(id="up-spin")
                yield NoticeBox(id="up-notice")
                yield DataTable(id="up-table", cursor_type="row")
                yield Line("", id="up-kernel")
                yield Line("", id="up-summary")
                yield Line("", id="up-notes")
                with Horizontal(id="actions"):
                    yield Button(
                        "Update nixpkgs lock (u)", id="up-update", variant="primary", compact=True
                    )
                    yield Button("mode: lock (m)", id="up-mode", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self.query_one("#pk-tabs", Tabs).active = f"pk-tab-{self.tab}"

    def on_screen_resume(self) -> None:  # also on the first push
        app = self.nixin
        if app.list_doc is None or app.list_stale:
            self.load_list()
        else:
            self.show_list(app.list_doc)
        if self.tab == "updates":
            self.load_updates()
        self._focus_table()

    # --- tabs ---------------------------------------------------------------------------

    @on(Tabs.TabActivated, "#pk-tabs")
    def _tab(self, event: Tabs.TabActivated) -> None:
        tab = (event.tab.id or "pk-tab-installed").removeprefix("pk-tab-")
        self.tab = tab
        self.query_one("#pk-switch", ContentSwitcher).current = f"pk-{tab}"
        if tab == "updates":
            self.load_updates()
        self._focus_table()
        self.refresh_bindings()
        self.call_after_refresh(self._replan)

    def action_tab(self, tab: str) -> None:
        self.query_one("#pk-tabs", Tabs).active = f"pk-tab-{tab}"

    def _focus_table(self) -> None:
        self.query_one("#pk-table" if self.tab == "installed" else "#up-table").focus()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "tab":  # `t` names the other tab
            return parameters[0] != self.tab
        if action in ("focus_filter", "next_scope"):
            return self.tab == "installed"
        if action == "update":
            return self.tab == "updates"
        return True

    def action_escape(self) -> None:
        if self.query_one("#pk-filter", Input).has_focus:
            self.query_one("#pk-table", DataTable).focus()
            return
        self.app.pop_screen()

    # --- installed ----------------------------------------------------------------------

    def load_list(self, refresh: bool = False) -> None:
        self.nixin.fetch(
            "list",
            *(["--refresh"] if refresh else []),
            done=self.show_list,
            group="list",
            exclusive=True,
            spinner=self.query_one("#pk-spin", Spinner),
            label=f"list: evaluating {self.nixin.backend.config_label}",
            hint="a few seconds cold, under a second warm",
        )

    def show_list(self, doc: dict) -> None:
        app = self.nixin
        notice = view.notice(doc)
        self.query_one("#pk-notice", NoticeBox).show(notice)
        if notice is None:
            app.list_loaded(doc)
            self.rows = view.package_rows(doc)
        else:
            self.rows = []
        if self.scope not in {s.key for s in self.scopes()}:
            self.scope = "all"
        self.render_list()

    def scopes(self) -> list[view.Scope]:
        doc = self.nixin.list_doc or {}
        return view.package_scopes(list(doc.get("users") or []))

    def current_scope(self) -> view.Scope:
        return next((s for s in self.scopes() if s.key == self.scope), self.scopes()[0])

    def render_list(self) -> None:
        text = self.query_one("#pk-filter", Input).value
        self.shown = view.filter_packages(self.rows, text, self.scope)
        table = self.query_one("#pk-table", DataTable)
        table.clear(columns=True)
        width = self._width(table)
        tagged = self.scope == "all" and len(self.scopes()) > 2
        cols = view.package_columns(width, tagged)
        for c in cols:
            table.add_column(c.label, width=c.width, key=c.key)
        for i, r in enumerate(self.shown):
            table.add_row(
                *(cell(self.app, s.text, s.style) for s in view.package_cells(r, cols)), key=str(i)
            )
        self.query_one("#pk-scope", Button).label = f"for: {self.current_scope().label} (h)"
        doc = self.nixin.list_doc
        foot = self.query_one("#pk-footer", Line)
        if doc is not None and self.rows:
            foot.show(view.list_footer(doc, len(self.shown), text, self.current_scope()), "dim")
        else:
            foot.show("")
        self._list_detail()

    @on(Input.Changed, "#pk-filter")
    def _filtered(self, event: Input.Changed) -> None:
        self.render_list()

    @on(Input.Submitted, "#pk-filter")
    def _filter_done(self, event: Input.Submitted) -> None:
        self.query_one("#pk-table", DataTable).focus()

    def on_key(self, event) -> None:
        if event.key == "down" and self.query_one("#pk-filter", Input).has_focus:
            self.query_one("#pk-table", DataTable).focus()
            event.stop()

    def action_focus_filter(self) -> None:
        self.query_one("#pk-filter", Input).focus()

    def action_next_scope(self) -> None:
        keys = [s.key for s in self.scopes()]
        self.scope = keys[(keys.index(self.scope) + 1) % len(keys)] if self.scope in keys else "all"
        self.render_list()

    def _width(self, table: DataTable) -> int:
        """The table's width; a table not laid out yet (hidden, or just shown) gets a guess
        and its columns are planned again once it has one (`_replan`)."""
        if table.size.width:
            width = table.size.width
        else:
            width = (self.size.width or 84) - 4  # margin 0 1, the scrollbar
            self.call_after_refresh(self._replan)
        self._planned[table.id or ""] = width
        return width

    def _replan(self) -> None:
        for tid, render in (("pk-table", self.render_list), ("up-table", self.render_updates)):
            w = self.query_one(f"#{tid}", DataTable).size.width
            if w and w != self._planned.get(tid):
                render()

    def on_resize(self) -> None:
        self.call_after_refresh(self._replan)

    @on(DataTable.RowHighlighted, "#pk-table")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._list_detail()

    def _list_detail(self) -> None:
        line = self.query_one("#pk-detail", Line)
        r = self._current(self.query_one("#pk-table", DataTable), self.shown)
        if r is not None:
            line.show(view.package_detail(r), "dim")
        elif self.nixin.list_doc is not None and not self.shown:
            line.show("nothing matches · f: filter · h: for whom", "dim")
        else:
            line.show("")

    @staticmethod
    def _current(table: DataTable, rows: list):
        row = table.cursor_row
        if not rows or row is None or not 0 <= row < len(rows):
            return None
        return rows[row]

    @on(DataTable.RowSelected, "#pk-table")
    def _selected(self, event: DataTable.RowSelected) -> None:
        r = self._current(self.query_one("#pk-table", DataTable), self.shown)
        if r is not None:
            self.app.push_screen(DetailsScreen(view.package_hit(r), user=r.user))

    # --- updates ------------------------------------------------------------------------

    def load_updates(self, refresh: bool = False) -> None:
        app = self.nixin
        doc = app.outdated_docs.get(self.mode)
        if doc is not None and not refresh:
            self.show_updates(doc)
            return
        mode = self.mode
        args = ["--published"] if mode == "published" else []
        self.query_one("#up-notice", NoticeBox).show(None)
        self.nixin.fetch(
            "outdated",
            *args,
            *(["--refresh"] if refresh else []),
            done=lambda d, m=mode: self._outdated(m, d),
            group="outdated",
            exclusive=True,
            spinner=self.query_one("#up-spin", Spinner),
            label=f"outdated{' --published' if mode == 'published' else ''}: evaluating",
            hint=OUTDATED_HINT[mode],
        )

    def _outdated(self, mode: str, doc: dict) -> None:
        if doc.get("ok"):
            self.nixin.outdated_docs[mode] = doc
        if mode == self.mode:
            self.show_updates(doc)

    def show_updates(self, doc: dict) -> None:
        notice = view.notice(doc)
        self.query_one("#up-notice", NoticeBox).show(notice)
        mode = self.query_one("#up-mode", Button)
        mode.label = f"mode: {self.mode} (m)"
        mode.refresh(layout=True)  # an auto width keeps the first label's otherwise
        lines = ("#up-head", "#up-kernel", "#up-summary", "#up-notes")
        if notice is not None:
            self.updates = []
            for name in lines:
                self.query_one(name, Line).show("")
            self.render_updates()
            return
        self.updates = view.update_rows(doc)
        host = self.nixin.backend.host
        self.query_one("#up-head", Line).show(view.outdated_head(doc, host), "dim")
        k = view.kernel_line(doc)
        self.query_one("#up-kernel", Line).show(k.text, k.style)
        self.query_one("#up-summary", Line).show(view.outdated_summary(doc), "head")
        notes = view.outdated_notes(doc)
        self.query_one("#up-notes", Line).show(" · ".join(notes), "dim")
        self.query_one("#up-update", Button).disabled = view.update_args(doc) is None
        self.render_updates()

    def render_updates(self) -> None:
        table = self.query_one("#up-table", DataTable)
        table.clear(columns=True)
        width = self._width(table)
        tagged = len({(r.scope, r.user) for r in self.updates}) > 1
        cols = view.update_columns(width, tagged)
        for c in cols:
            table.add_column(c.label, width=c.width, key=c.key)
        for i, r in enumerate(self.updates):
            table.add_row(
                *(cell(self.app, s.text, s.style) for s in view.update_cells(r, cols)), key=str(i)
            )
        table.display = bool(self.updates)

    def action_mode(self) -> None:
        if self.tab != "updates":
            self.query_one("#pk-tabs", Tabs).active = "pk-tab-updates"
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self.load_updates()

    @on(DataTable.RowSelected, "#up-table")
    def _update_selected(self, event: DataTable.RowSelected) -> None:
        r = self._current(self.query_one("#up-table", DataTable), self.updates)
        if r is None:
            return
        row = view.PackageRow(r.scope, r.user, r.pname, r.pname, r.old, "", r.pname)
        self.app.push_screen(DetailsScreen(view.package_hit(row), user=r.user))

    def action_refresh(self) -> None:
        if self.tab == "installed":
            self.load_list(refresh=True)
        else:
            self.load_updates(refresh=True)

    # --- update -------------------------------------------------------------------------

    def action_update(self) -> None:
        """`update --dry-run` → confirm → `update` → Pending."""
        doc = self.nixin.outdated_docs.get(self.mode)  # only ok documents are kept
        args = view.update_args(doc) if doc is not None else []
        if args is None:
            self.notify("nothing to update: the lock already has the target", severity="warning")
            return

        def dry(d: dict) -> None:
            if not d.get("ok"):
                self.app.push_screen(NoticeModal(d, "update"))
                return
            lock_doc = doc if doc is not None and doc.get("mode") == "lock" else None
            self.app.push_screen(UpdateModal(d, lock_doc), lambda ok: ok and run())

        def run() -> None:
            self.nixin.fetch(
                "update",
                *args,
                done=self._updated,
                busy="updating",
                spinner=self.query_one("#up-spin", Spinner),
                label="update: " + " ".join(["nix flake update nixpkgs", *args]).strip(),
                hint="nix fetches the new nixpkgs: about a minute the first time",
            )

        self.nixin.fetch(
            "update", *args, "--dry-run", done=dry, busy="checking", label="update --dry-run"
        )

    def _updated(self, doc: dict) -> None:
        if not doc.get("ok"):
            self.app.push_screen(NoticeModal(doc, "update"))
            return
        self.nixin.updated(doc)
        summary = view.update_summary(doc)
        if not doc.get("changed"):
            self.notify(view.update_fields(doc)[0].value, title="update")
            return
        self.notify(f"{summary} · b: plan, a: apply", title="updated")
        self.nixin.action_pending()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "pk-scope": self.action_next_scope,
            "up-update": self.action_update,
            "up-mode": self.action_mode,
        }[event.button.id or ""]()
