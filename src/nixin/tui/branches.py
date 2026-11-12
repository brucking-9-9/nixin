"""The Branches page: the config's git branches against main (`branches`), merging the
ticked ones into it (`branches merge`, built first) and pruning the merged (`branches
prune`).

The list is one `branches` document (git only, under a second), kept on the app until a
merge or a prune makes it stale. `space` ticks a clean, unmerged row; `m` merges the
ticked rows (or the highlighted one) after a confirm dialog (skip the build, push to the
hub). The merge builds the host from the merged commit with nix's output streaming, as
plan does; `x` (the Stop button) stops that build. `x` otherwise prunes: a `--dry-run`
in the confirm dialog first (with "also on the hub"), then the real prune. A merge or a
prune makes status and the plan stale. `C` copies the run's error and build log.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, DataTable, Label

from ..apply.proc import Cancel
from ..ui import view
from .modals import Modal, NoticeModal
from .screens import BuildScreen
from .widgets import BuildTracker, Fields, Line, LogView, NoticeBox, Spinner, Title, cell

Doc = dict[str, Any]

TICK = view.Column("tick", "", 1)


WIDE_MIN = 79  # branch_columns' wide set: 68 cells + a subject of 10 + the scrollbar


def tick_columns(width: int) -> list[view.Column]:
    """branch_columns with a one-cell tick column in front, fitted to `width` (its gaps of
    2 and the scrollbar included). Between view.NARROW and WIDE_MIN the wide set does not
    fit: the narrow one, its name widened."""
    inner = width - 5  # the tick and its gap; branch_columns' sets come out 1-2 cells wide
    if inner >= WIDE_MIN or inner < view.NARROW:
        return [TICK, *view.branch_columns(inner)]
    cols = view.branch_columns(view.NARROW - 1)
    name = replace(cols[0], width=width - 6 - sum(c.width + 2 for c in cols[1:]))
    return [TICK, name, *cols[1:]]


def without_hub(doc: Doc) -> Doc:
    """A `prune --hub --dry-run` document as the run without --hub would have it."""
    return {
        **doc,
        "hub": None,
        "deleted": [x for x in doc.get("deleted") or [] if x.get("where") != "hub"],
        "skipped": [x for x in doc.get("skipped") or [] if x.get("where") != "hub"],
    }


class MergeModal(Modal):
    """Confirm a merge: the branches, what happens (builds HOST, then moves main), and the
    toggles: skip the build, push to the hub. Dismisses with (build, push)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("m", "merge", "Merge"),
        Binding("n", "toggle('#no-build')", "No build", show=False),
        Binding("u", "toggle('#push')", "Push", show=False),
    ]

    def __init__(self, rows: list[view.BranchRow], into: str, host: str, hub: str | None):
        super().__init__()
        self.rows, self.into, self.host, self.hub = rows, into, host, hub

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog merge-dialog"):
            yield Label(f"merge into {self.into}", classes="dialog-title")
            with VerticalScroll():
                yield Fields(id="merge-fields", label_width=8)
            yield Checkbox("skip the build (--no-build) (n)", id="no-build", compact=True)
            if self.hub:
                yield Checkbox(f"push {self.into} to {self.hub} (u)", id="push", compact=True)
            with Horizontal(classes="buttons"):
                yield Button("Merge (m)", id="merge", variant="primary", compact=True)
                yield Button("Cancel (esc)", id="cancel", compact=True)

    def on_mount(self) -> None:
        self.render_fields()
        self.query_one("#merge", Button).focus()

    @property
    def build(self) -> bool:
        return not self.query_one("#no-build", Checkbox).value

    @property
    def push(self) -> bool:
        box = self.query("#push")
        return bool(box) and box.first(Checkbox).value

    def render_fields(self) -> None:
        fields = [
            view.Field(
                "branch",
                f"{r.name}: {r.ahead} commit(s), {r.files} file(s), {r.behind} behind",
                "accent",
            )
            for r in self.rows
        ]
        if self.build:
            then = f"builds {self.host} from the merged commit, then moves {self.into}"
        else:
            then = f"moves {self.into} without building it"
        fields.append(view.Field("then", then, ""))
        fields.append(view.Field("", "a conflict or a failed build changes nothing", "dim"))
        if self.push:
            fields.append(view.Field("push", f"{self.hub}/{self.into}", "warn"))
        fields.append(view.Field("next", f"Plan, then Apply from {self.into}", "dim"))
        self.query_one("#merge-fields", Fields).show(fields)

    @on(Checkbox.Changed)
    def _changed(self, event: Checkbox.Changed) -> None:
        self.render_fields()

    def action_toggle(self, box: str) -> None:
        found = self.query(box)
        if found:
            found.first(Checkbox).toggle()

    def action_merge(self) -> None:
        self.dismiss((self.build, self.push))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "merge":
            self.action_merge()
        else:
            self.dismiss(None)


class PruneModal(Modal):
    """Confirm a prune: the dry run's deletions; "also on the hub" adds the hub's. The dry
    run came with --hub when there is a hub, so the toggle only filters what it shows.
    Dismisses with the --hub choice."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("x", "prune", "Prune"),
        Binding("u", "toggle_hub", "Hub", show=False),
    ]

    def __init__(self, dry: Doc) -> None:
        super().__init__()
        self.dry = dry
        self.hub = dry.get("hub")

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog prune-dialog"):
            yield Label(f"prune the branches {self.dry.get('into')} has", classes="dialog-title")
            with VerticalScroll():
                yield Fields(id="prune-fields", label_width=8)
            if self.hub:
                yield Checkbox(f"also on the hub, {self.hub} (u)", id="hub", compact=True)
            with Horizontal(classes="buttons"):
                yield Button("Prune (x)", id="prune", variant="error", compact=True)
                yield Button("Cancel (esc)", id="cancel", compact=True)

    @property
    def with_hub(self) -> bool:
        box = self.query("#hub")
        return bool(box) and box.first(Checkbox).value

    def shown(self) -> Doc:
        return self.dry if self.with_hub else without_hub(self.dry)

    def on_mount(self) -> None:
        self.render_fields()
        self.query_one("#prune", Button).focus()

    def render_fields(self) -> None:
        doc = self.shown()
        self.query_one("#prune-fields", Fields).show(view.prune_fields(doc))
        self.query_one("#prune", Button).disabled = not doc.get("deleted")

    @on(Checkbox.Changed)
    def _changed(self, event: Checkbox.Changed) -> None:
        self.render_fields()

    def action_toggle_hub(self) -> None:
        box = self.query("#hub")
        if box:
            box.first(Checkbox).toggle()

    def action_prune(self) -> None:
        if self.shown().get("deleted"):
            self.dismiss(self.with_hub)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "prune":
            self.action_prune()
        else:
            self.dismiss(None)


class BranchesScreen(BuildScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("space", "tick", "Tick"),
        Binding("m", "merge", "Merge"),
        Binding("x", "prune", "Prune"),  # while a merge builds: Stop
        Binding("r", "refresh", "Refresh", show=False),
        Binding("C,shift+c", "copy_log", "Copy", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.doc: Doc | None = None  # the list
        self.rows: list[view.BranchRow] = []
        self.ticked: list[str] = []  # names, in the order they were ticked
        self.result: Doc | None = None  # the last merge or prune document
        self._planned = 0

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        yield Line("", id="br-head")
        yield Spinner(id="br-spin")
        yield NoticeBox(id="br-list-notice")
        yield DataTable(id="br-table", cursor_type="row")
        with VerticalScroll(id="br-lower"):
            yield Line("", id="br-detail")
            yield Title("merge", id="br-result-title")
            yield Spinner(id="br-run-spin", classes="run-spin")
            yield BuildTracker(id="build")
            yield LogView(id="log")
            yield NoticeBox(id="br-notice")
            yield Fields(id="br-result", label_width=8)
        with Horizontal(id="actions"):
            yield Button("Merge… (m)", id="br-merge", variant="primary", compact=True)
            yield Button("Stop (x)", id="stop-btn", variant="warning", compact=True)
            yield Button("Prune… (x)", id="br-prune", compact=True)
            yield Button("Refresh (r)", id="br-refresh", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self._token: Cancel | None = None
        self.query_one("#stop-btn", Button).display = False
        self.query_one("#br-result-title", Title).display = False

    def on_screen_resume(self) -> None:  # also on the first push
        doc = self.nixin.branches_doc
        if doc is None:
            self.action_refresh()
        else:
            self.show_list(doc)
        self.query_one("#br-table", DataTable).focus()

    # --- the list ---------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.nixin.fetch(
            "branches",
            done=self._listed,
            group="branches",
            exclusive=True,
            spinner=self.query_one("#br-spin", Spinner),
            label="branches: git, against the target",
            hint="under a second (git only, no network)",
        )

    def _listed(self, doc: Doc) -> None:
        if doc.get("ok"):
            self.nixin.branches_doc = doc
        self.show_list(doc)

    def show_list(self, doc: Doc) -> None:
        self.doc = doc
        notice = view.notice(doc)
        self.query_one("#br-list-notice", NoticeBox).show(notice)
        head = self.query_one("#br-head", Line)
        if notice is not None:
            self.rows = []
            head.show("")
        else:
            self.rows = view.branch_rows(doc)
            text = view.branches_head(doc)
            warnings = " · ".join(doc.get("warnings") or [])
            head.show(text + (f"\n{warnings}" if warnings else ""), "head")
        names = {r.name for r in self.rows if r.mergeable}
        self.ticked = [n for n in self.ticked if n in names]
        self.render_table()

    def _width(self, table: DataTable) -> int:
        if table.size.width:
            return table.size.width
        self.call_after_refresh(self._replan)
        return (self.size.width or 84) - 4  # margin 0 1, the scrollbar

    def _replan(self) -> None:
        w = self.query_one("#br-table", DataTable).size.width
        if w and w != self._planned and self.doc is not None:
            self.render_table()

    def on_resize(self) -> None:
        self.call_after_refresh(self._replan)

    def render_table(self) -> None:
        table = self.query_one("#br-table", DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        width = self._width(table)
        self._planned = width
        cols = tick_columns(width)
        for c in cols:
            table.add_column(c.label, width=c.width, key=c.key)
        for r in self.rows:
            cells = [cell(self.app, s.text, s.style) for s in view.branch_cells(r, cols[1:])]
            table.add_row(self._tick_cell(r), *cells, key=r.name)
        table.display = bool(self.rows)
        if row and row < len(self.rows):
            table.move_cursor(row=row)
        self._detail()
        self._buttons()

    def _tick_cell(self, r: view.BranchRow):
        return cell(self.app, "●" if r.name in self.ticked else " ", "accent")

    def current(self) -> view.BranchRow | None:
        row = self.query_one("#br-table", DataTable).cursor_row
        if not self.rows or row is None or not 0 <= row < len(self.rows):
            return None
        return self.rows[row]

    @on(DataTable.RowHighlighted, "#br-table")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._detail()

    @on(DataTable.RowSelected, "#br-table")
    def _selected(self, event: DataTable.RowSelected) -> None:
        self.action_tick()

    def _detail(self) -> None:
        line = self.query_one("#br-detail", Line)
        r = self.current()
        if r is None:
            empty = self.doc is not None and self.doc.get("ok") and not self.rows
            line.show("no branches but the target" if empty else "", "dim")
            return
        into = str((self.doc or {}).get("into") or "main")
        hint = "space: tick · m: merge · x: prune merged · C: copy"
        line.show(view.branch_detail(r, into) + "\n" + hint, "dim")

    def _buttons(self) -> None:
        mergeable = any(r.mergeable for r in self.rows)
        self.query_one("#br-merge", Button).disabled = not mergeable
        self.query_one("#br-prune", Button).disabled = not any(r.merged for r in self.rows)
        n = len(self.ticked)
        button = self.query_one("#br-merge", Button)
        button.label = f"Merge {n}… (m)" if n else "Merge… (m)"
        button.refresh(layout=True)  # an auto width keeps the first label's otherwise

    def action_tick(self) -> None:
        r = self.current()
        if r is None:
            return
        if not r.mergeable:
            why = {
                "merged": "is merged already: x prunes it",
                "conflict": "does not merge cleanly: rebase it, or merge it by hand",
            }
            self.notify(f"{r.name} {why.get(r.state, 'cannot be merged')}", severity="warning")
            return
        if r.name in self.ticked:
            self.ticked.remove(r.name)
        else:
            self.ticked.append(r.name)
        table = self.query_one("#br-table", DataTable)
        table.update_cell(r.name, "tick", self._tick_cell(r))
        self._buttons()
        if table.cursor_row is not None and table.cursor_row + 1 < len(self.rows):
            table.move_cursor(row=table.cursor_row + 1)

    # --- merge ------------------------------------------------------------------------

    def action_merge(self) -> None:
        if self.nixin.busy:
            self.notify(f"wait: {self.nixin.busy} is still running", severity="warning")
            return
        by_name = {r.name: r for r in self.rows}
        picked = [by_name[n] for n in self.ticked if n in by_name]
        if not picked:
            r = self.current()
            if r is None or not r.mergeable:
                self.notify("tick a clean, unmerged branch (space) to merge", severity="warning")
                return
            picked = [r]
        doc = self.doc or {}
        into = str(doc.get("into") or "main")
        modal = MergeModal(picked, into, self.nixin.backend.host, doc.get("hub"))
        names = [r.name for r in picked]
        self.app.push_screen(modal, lambda answer: answer and self.run_merge(names, *answer))

    def run_merge(self, names: list[str], build: bool, push: bool) -> None:
        self._result_title("merge " + " ".join(names))
        log = self.query_one("#log", LogView)
        log.reset()
        log.display = False
        token = Cancel(watched=True)
        host = self.nixin.backend.host
        started = self.nixin.fetch(
            "branches",
            *view.merge_args(names, build=build, push=push),
            done=self.show_merge,
            stream=True,
            log=log,
            busy="merging",
            spinner=self.query_one("#br-run-spin", Spinner),
            label=f"merge: building {host}" if build else "merge",
            hint="as long as a plan: seconds when cached, minutes for new packages; x stops"
            if build
            else "git only: under a second",
            cancel=token,
        )
        if started is not None:
            self.watch_token(token, "merge")

    def show_merge(self, doc: Doc) -> None:
        self.watch_token(None, "")
        self.result = doc
        notice = view.notice(doc)
        self.query_one("#br-notice", NoticeBox).show(notice)
        fields = self.query_one("#br-result", Fields)
        if notice is not None:
            fields.show([], "C copies the error and the build log", "dim")
            self.notify(f"merge: {notice.code}", severity="error")
        else:
            fields.show(view.merge_fields(doc))
            merged = doc.get("merged") or []
            if merged and not doc.get("dry_run"):
                self.ticked = []
                self.nixin.branches_changed()
                self.action_refresh()
            self.notify(f"merged {len(merged)} branch(es) into {doc.get('into')}", title="branches")
        self._scroll_result()

    # --- prune ------------------------------------------------------------------------

    def action_prune(self) -> None:
        if self._token is not None:  # a merge builds: x is Stop
            self.action_stop()
            return
        if self.nixin.busy:
            self.notify(f"wait: {self.nixin.busy} is still running", severity="warning")
            return
        hub = bool((self.doc or {}).get("hub"))

        def dry(d: Doc) -> None:
            if not d.get("ok"):
                self.app.push_screen(NoticeModal(d, "branches prune --dry-run"))
                return
            self.app.push_screen(PruneModal(d), lambda h: h is not None and self.run_prune(h))

        self.nixin.fetch(
            "branches",
            *view.prune_args(hub=hub, dry=True),
            done=dry,
            busy="checking",
            spinner=self.query_one("#br-run-spin", Spinner),
            label="prune --dry-run",
        )

    def run_prune(self, hub: bool) -> None:
        self._result_title("prune" + (" --hub" if hub else ""))
        self.query_one("#log", LogView).display = False
        self.nixin.fetch(
            "branches",
            *view.prune_args(hub=hub),
            done=self.show_prune,
            busy="pruning",
            spinner=self.query_one("#br-run-spin", Spinner),
            label="prune" + (f": git push --delete to {self.doc.get('hub')}" if hub else ""),
            hint="network (the hub)" if hub else "git only",
        )

    def show_prune(self, doc: Doc) -> None:
        self.result = doc
        notice = view.notice(doc)
        self.query_one("#br-notice", NoticeBox).show(notice)
        fields = self.query_one("#br-result", Fields)
        if notice is not None:
            fields.show([], "C copies the error", "dim")
        else:
            fields.show(view.prune_fields(doc))
            self.nixin.branches_changed()
            self.action_refresh()
            n = len(doc.get("deleted") or [])
            self.notify(f"deleted {n} branch(es)", title="branches")
        self._scroll_result()

    # --- shared -----------------------------------------------------------------------

    def _result_title(self, text: str) -> None:
        title = self.query_one("#br-result-title", Title)
        title.update(text)
        title.display = True
        self.query_one("#br-notice", NoticeBox).show(None)
        self.query_one("#br-result", Fields).show([])

    def _scroll_result(self) -> None:
        lower = self.query_one("#br-lower", VerticalScroll)
        self.set_timer(0.05, lambda: lower.scroll_end(animate=False))

    def copy_doc(self) -> Doc | None:
        return self.result if self.result is not None else self.doc

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "br-merge": self.action_merge,
            "stop-btn": self.action_stop,
            "br-prune": self.action_prune,
            "br-refresh": self.action_refresh,
        }[event.button.id or ""]()

    def action_back(self) -> None:
        self.app.pop_screen()
