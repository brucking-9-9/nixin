"""The screens: Search → Details → (writer modals) → Pending → Apply, and Status.

Every screen asks the app for documents (`app.fetch`, a worker thread around the backend)
and renders them through view.py. Nothing here reads the config or runs nix itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Input, Static, Tab, Tabs, TextArea

from ..apply.proc import Cancel
from ..ui import view
from .widgets import (
    BuildTracker,
    Fields,
    Line,
    LogView,
    NoticeBox,
    Spinner,
    Title,
    cell,
    spans_text,
)

if TYPE_CHECKING:
    from .app import NixinApp

DEBOUNCE = 0.35  # seconds of quiet typing before a search runs


class TopBar(Static):
    """`nixin · mbp13 · ~/nixos · unstable` and what is running."""

    DEFAULT_CSS = "TopBar { height: 1; width: 100%; }"

    def on_mount(self) -> None:
        self.refresh_bar()

    def refresh_bar(self) -> None:
        app: NixinApp = self.app  # type: ignore[assignment]
        sep = view.Span(" · ", "dim")
        pieces = [
            view.Span("nixin", "head"),
            sep,
            view.Span(app.backend.host, "accent"),
            sep,
            view.Span(app.backend.config_label, "dim"),
            sep,
            view.Span(app.channel, "option"),
        ]
        if app.busy:
            pieces.append(view.Span(f"  [{app.busy}]", "warn"))
        self.update(spans_text(app, pieces))


class Base(Screen):
    """Top bar, footer, the app's global keys."""

    def compose_top(self) -> ComposeResult:
        yield TopBar()

    def compose_bottom(self) -> ComposeResult:
        yield Footer(compact=True, show_command_palette=False)

    @property
    def nixin(self) -> NixinApp:
        return self.app  # type: ignore[return-value]


class BuildScreen(Base):
    """A screen that runs a build (Pending's plan, Apply): the tracker widget `#build` and
    the Stop button `#stop-btn` follow the run's token (`watch_token`) twice a second."""

    STEP_LABEL: ClassVar[dict[str, str]] = {
        "prepare": "reading the trees",
        "eval": "evaluating the config",
        "build": "building",
        "diff": "diffing the closures",
        "commit": "committing (cannot be stopped)",
        "pull": "pulling into the authoritative tree (cannot be stopped)",
        "switch": "switching (cannot be stopped)",
        "verify": "verifying (cannot be stopped)",
    }

    def watch_token(self, token: Cancel | None, label: str) -> None:
        self._token, self._label = token, label
        if getattr(self, "_tick_timer", None) is not None:
            self._tick_timer.stop()  # type: ignore[union-attr]
        self._tick_timer: Timer | None = None
        if token is not None:
            self._tick_timer = self.set_interval(0.5, self._tick)
            self._tick()
        else:
            self.query_one("#build", BuildTracker).hide()
            self.query_one("#stop-btn", Button).display = False

    def _tick(self) -> None:
        token = self._token
        if token is None:
            return
        snapshot = token.snapshot
        self.query_one("#build", BuildTracker).show(snapshot() if snapshot else None)
        btn = self.query_one("#stop-btn", Button)
        btn.display = True
        btn.disabled = not token.stoppable
        btn.label = "Stopping…" if token.requested else "Stop (x)"
        spinner = self.query_one(".run-spin", Spinner)
        step = self.STEP_LABEL.get(token.step)
        if step and spinner.running:
            spinner.relabel(f"{self._label}: {step}")

    def action_stop(self) -> None:
        if self._token is None:
            return
        self.nixin.stop()
        self._tick()


# --- search -------------------------------------------------------------------------------------


class SearchScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "focus_query", "Query"),
        Binding("k", "next_kind", "Kind"),
        Binding("c", "channel", "Channel", show=False),  # the top bar shows the channel
        Binding("escape", "escape", "Back", show=False),
        Binding("down", "results", "Results", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.kind = "pkg"
        self._jump = False  # Enter in the query: move to the results when they arrive
        self.doc: dict | None = None
        self.hits: list[view.Hit] = []
        self._debounce: Timer | None = None
        self._last: tuple | None = None

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        yield Input(placeholder="search packages, options, programs…", id="query", compact=True)
        yield Tabs(*[Tab(k, id=f"kind-{k}") for k in view.SEARCH_KINDS], id="kinds")
        yield DataTable(id="results", cursor_type="row", show_cursor=True)
        yield Line("", id="preview")
        yield Spinner(id="spin")
        yield NoticeBox(id="notice")
        yield Line("", id="source")
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self.query_one("#query", Input).focus()
        self.query_one("#preview", Line).show(
            "type to search · enter: details · k: kind · c: channel · ?: keys", "dim"
        )

    # input → debounced search
    @on(Input.Changed, "#query")
    def _typed(self, event: Input.Changed) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._debounce = self.set_timer(DEBOUNCE, self.search)

    @on(Input.Submitted, "#query")
    def _submitted(self, event: Input.Submitted) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._jump = True
        self.search()
        if self.hits and self.doc is not None and self._last == self._key():
            self._jump = False
            self.query_one("#results", DataTable).focus()

    @on(Tabs.TabActivated, "#kinds")
    def _kind(self, event: Tabs.TabActivated) -> None:
        kind = (event.tab.id or "kind-pkg").removeprefix("kind-")
        if kind != self.kind:
            self.kind = kind
            self.search()

    def action_next_kind(self) -> None:
        kinds = view.SEARCH_KINDS
        nxt = kinds[(kinds.index(self.kind) + 1) % len(kinds)]
        self.query_one("#kinds", Tabs).active = f"kind-{nxt}"

    def action_channel(self) -> None:
        self.nixin.cycle_channel()
        self.search(force=True)

    def action_focus_query(self) -> None:
        self.query_one("#query", Input).focus()

    def action_escape(self) -> None:
        """From the query to the results, so the keys (k, p, s, q) work. Not back: a second
        Esc must not put the person into the text box, where keys become text (/ does)."""
        self.query_one("#results", DataTable).focus()

    def action_results(self) -> None:
        if self.query_one("#query", Input).has_focus and self.hits:
            self.query_one("#results", DataTable).focus()

    def _key(self) -> tuple:
        query = self.query_one("#query", Input).value.strip()
        return (query, self.kind, self.nixin.channel)

    def search(self, force: bool = False) -> None:
        key = self._key()
        query = key[0]
        if not query:
            self._last = None
            self.show(None)
            return
        if key == self._last and not force:
            return
        self._last = key
        args = ["--kind", self.kind, "--channel", self.nixin.channel, "--", query]
        self.nixin.fetch(
            "search",
            *args,
            done=self.show,
            group="search",
            exclusive=True,
            spinner=self.query_one("#spin", Spinner),
            label=f"searching {self.kind} for {query!r}",
        )

    def show(self, doc: dict | None) -> None:
        self.doc = doc
        notice = view.notice(doc) if doc else None
        self.query_one("#notice", NoticeBox).show(notice)
        self.hits = view.hits(doc) if doc and doc.get("ok") else []
        self.render_table()
        if self._jump:  # also with no hits: k (another kind) must work right away
            self.query_one("#results", DataTable).focus()
        self._jump = False
        src = self.query_one("#source", Line)
        src.show(" · ".join(view.source_lines(doc)) if doc and doc.get("ok") else "", "dim")

    def render_table(self) -> None:
        table = self.query_one("#results", DataTable)
        table.clear(columns=True)
        width = table.size.width or self.size.width or 80
        cols = view.hit_columns(self.kind, width)
        for c in cols:
            table.add_column(c.label, width=c.width, key=c.key)
        for i, h in enumerate(self.hits):
            cells = [cell(self.app, s.text, s.style) for s in view.hit_cells(h, cols)]
            table.add_row(*cells, key=str(i))
        self._preview()

    def on_resize(self) -> None:
        if self.doc is not None:
            self.call_after_refresh(self.render_table)

    @on(DataTable.RowHighlighted, "#results")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._preview()

    def _preview(self) -> None:
        prev = self.query_one("#preview", Line)
        h = self.current()
        if h is None:
            if self.doc and self.doc.get("ok") and not self.hits:
                prev.show("no results · k: other kinds · /: edit the query", "dim")
            return
        if h.description:
            prev.show(view.clip(h.description, 400))
        else:
            prev.show("(no description)", "dim")

    def current(self) -> view.Hit | None:
        table = self.query_one("#results", DataTable)
        if not self.hits or table.cursor_row is None or table.cursor_row >= len(self.hits):
            return None
        return self.hits[table.cursor_row]

    @on(DataTable.RowSelected, "#results")
    def _selected(self, event: DataTable.RowSelected) -> None:
        h = self.current()
        if h is not None:
            self.app.push_screen(DetailsScreen(h))


# --- details ------------------------------------------------------------------------------------

INFO_KIND = {"pkg": "pkg", "program": "pkg", "opt": "opt", "hm": "hm", "fn": "fn"}


class DetailsScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("a", "add", "Add", show=False),
        Binding("t", "try", "Try", show=False),
        Binding("r", "remove", "Remove", show=False),
        Binding("e", "set", "Set", show=False),
        Binding("u", "unset", "Unset", show=False),
        Binding("h", "home", "Home", show=False),
        Binding("w", "where", "Where again", show=False),
    ]

    def __init__(self, hit: view.Hit, user: str | None = None) -> None:
        super().__init__()
        self.hit = hit
        self.record = hit.record
        self.where: dict | None = None
        self.user = user  # --home USER (an installed row opens with its own)

    def compose(self) -> ComposeResult:
        h = self.hit
        yield from self.compose_top()
        badge = view.KIND_BADGE.get(h.kind, h.kind)
        title = [
            view.Span(f"{badge} ", "dim"),
            view.Span(h.name, "option" if h.is_option else "accent"),
        ]
        if h.target != h.name:
            title.append(view.Span(f"  ({h.target})", "dim"))
        self.title_spans = title
        yield Static(spans_text(self.app, title), id="details-title")
        with VerticalScroll(id="body"), Horizontal(id="panes"):
            with Vertical(id="left", classes="pane"):
                yield Fields(id="info", label_width=9)
                yield Spinner(id="info-spin")
                if h.is_package:
                    yield Title("versions")
                    yield Spinner(id="versions-spin")
                    yield Fields(id="versions", label_width=12)
            with Vertical(id="right", classes="pane"):
                yield Title(f"on {self.nixin.backend.host}", id="where-title")
                yield Spinner(id="where-spin")
                yield Fields(id="where", label_width=9)
        with Horizontal(id="actions"):
            if h.is_package:
                yield Button("Add (a)", id="add", variant="primary", compact=True)
                yield Button("Try (t)", id="try", compact=True)
                yield Button("Remove (r)", id="remove", variant="error", compact=True)
            elif h.is_option:
                yield Button("Set (e)", id="set", variant="primary", compact=True)
                yield Button("Unset (u)", id="unset", variant="error", compact=True)
            yield Button("", id="home", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        h = self.hit
        if h.kind == "hm":
            self.user = (self.nixin.hm_users() or [None])[0]
        self._home_label()
        self.query_one("#info", Fields).show(view.info_fields(self.record))
        if h.kind in INFO_KIND:
            self.nixin.fetch(
                "info",
                h.target,
                "--kind",
                INFO_KIND[h.kind],
                "--channel",
                self.nixin.channel,
                done=self._info,
                group=f"info-{id(self)}",
                spinner=self.query_one("#info-spin", Spinner),
                label="info",
            )
        if h.is_package:
            self.nixin.fetch(
                "versions",
                h.target,
                "--limit",
                "8",
                done=self._versions,
                group=f"versions-{id(self)}",
                spinner=self.query_one("#versions-spin", Spinner),
                label="versions (nixhub)",
            )
        self.action_where()

    def _info(self, doc: dict) -> None:
        if not doc.get("ok"):
            n = view.notice(doc)
            assert n is not None
            self.query_one("#info", Fields).show(
                view.info_fields(self.record), f"info: {n.code}: {n.message}", "error"
            )
            return
        want = self.hit.target
        rec = next((r for r in doc.get("results") or [] if r.get("name") == want), None)
        if rec is not None:
            if self.hit.kind == "program":  # keep the binary in view
                rec = {**rec, "kind": "program", "package": want, "name": self.hit.name}
            self.record = rec
        self.query_one("#info", Fields).show(view.info_fields(self.record))

    def _versions(self, doc: dict) -> None:
        f = self.query_one("#versions", Fields)
        if not doc.get("ok"):
            n = view.notice(doc)
            assert n is not None
            f.show([], f"{n.code}: {n.message}", "dim")
            return
        f.show(view.version_rows(doc), "" if doc.get("results") else "no history")

    def action_where(self) -> None:
        h = self.hit
        kind = "option" if h.is_option else "package"
        self.nixin.fetch(
            "where",
            h.where_target,
            "--kind",
            kind,
            done=self._where,
            group=f"where-{id(self)}",
            exclusive=True,
            spinner=self.query_one("#where-spin", Spinner),
            label=f"where: evaluating {self.nixin.backend.config_label}",
            hint="a few seconds cold, under one warm",
        )

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("add", "remove", "try"):
            return self.hit.is_package
        if action in ("set", "unset"):
            return self.hit.is_option
        if action == "home":
            return self.hit.is_package or self.hit.kind == "hm"
        return True

    def _where(self, doc: dict) -> None:
        self.where = doc
        self._title(doc)
        if self.hit.kind == "hm" and self.user is None:
            self.user = (self.nixin.hm_users(doc) or [None])[0]
            self._home_label()
        self.query_one("#where", Fields).section(view.where_section(doc, self.nixin.backend.host))

    def _title(self, where: dict | None) -> None:
        cue = view.where_cue(where) if where else None
        spans = self.title_spans + ([view.Span("  "), cue] if cue else [])
        self.query_one("#details-title", Static).update(spans_text(self.app, spans))

    # --- actions
    def _home_label(self) -> None:
        btn = self.query_one("#home", Button)
        if self.hit.kind == "hm":
            btn.label = f"home: {self.user or 'no HM user'}"
        else:
            btn.label = f"home: {self.user or 'off'} (h)"
        btn.display = self.hit.is_package or self.hit.kind == "hm"

    def action_home(self) -> None:
        users = self.nixin.hm_users(self.where)
        if not users:
            self.nixin.load_status_for_users(self._users_loaded)
            return
        choices: list[str | None] = list(users) if self.hit.kind == "hm" else [None, *users]
        i = choices.index(self.user) if self.user in choices else -1
        self.user = choices[(i + 1) % len(choices)]
        self._home_label()

    def _users_loaded(self, users: list[str]) -> None:
        if not users:
            self.notify(f"no Home Manager users on {self.nixin.backend.host}", severity="warning")
            return
        self.action_home()

    def _home_args(self) -> list[str] | None:
        if self.hit.kind == "hm":
            if not self.user:
                self.notify(
                    f"Home Manager options need a user; {self.nixin.backend.host} has none",
                    severity="error",
                )
                return None
            return ["--home", self.user]
        return ["--home", self.user] if self.user else []

    def action_add(self) -> None:
        home = self._home_args() if self.hit.is_package else None
        if home is not None:
            self.nixin.write("add", [self.hit.target, *home], after=self.action_where)

    def action_try(self) -> None:
        """Build it into the store and run it, without installing (the Try screen)."""
        if self.hit.is_package:
            from .trying import TryScreen  # it imports this module

            self.app.push_screen(TryScreen(self.hit.target))

    def action_remove(self) -> None:
        home = self._home_args() if self.hit.is_package else None
        if home is not None:
            self.nixin.write("rm", [self.hit.target, *home], after=self.action_where)

    def action_set(self) -> None:
        if not self.hit.is_option:
            return
        home = self._home_args()
        if home is None:
            return
        value = view.current_value(self.where) or str(self.record.get("default") or "")
        self.nixin.set_option(
            self.hit.target, value, self.user if home else None, home, after=self.action_where
        )

    def action_unset(self) -> None:
        if not self.hit.is_option:
            return
        home = self._home_args()
        if home is not None:
            self.nixin.write("unset", [self.hit.target, *home], after=self.action_where)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = {"add": self.action_add, "remove": self.action_remove, "set": self.action_set}
        action["try"] = self.action_try
        action |= {"unset": self.action_unset, "home": self.action_home}
        action[event.button.id or ""]()

    def action_back(self) -> None:
        self.app.pop_screen()


# --- pending + plan -----------------------------------------------------------------------------


class PendingScreen(BuildScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("b", "plan", "Plan", show=False),
        Binding("a", "apply", "Apply", show=False),
        Binding("y", "sync", "Sync", show=False),
        Binding("x", "stop", "Stop", show=False),
        Binding("z", "undo", "Undo lock move", show=False),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        with VerticalScroll(id="body"):
            yield Title("pending")
            yield Spinner(id="status-spin")
            yield Fields(id="pending", label_width=10)
            yield Title("this session")
            yield Fields(id="session", label_width=10)
            yield Title("plan", id="plan-title")
            yield Spinner(id="plan-spin", classes="run-spin")
            yield BuildTracker(id="build")
            yield LogView(id="log")
            yield NoticeBox(id="notice")
            yield Fields(id="plan", label_width=10)
            yield Line("", id="closure-head")
            yield DataTable(id="closure", cursor_type="none", show_cursor=False)
            yield Fields(id="sync", label_width=10)
        with Horizontal(id="actions"):
            yield Button("Plan (b)", id="plan-btn", variant="primary", compact=True)
            yield Button("Stop (x)", id="stop-btn", variant="warning", compact=True)
            yield Button("Apply… (a)", id="apply-btn", variant="error", compact=True)
            yield Button("Sync (y)", id="sync-btn", compact=True)
            yield Button("Undo lock move (z)", id="undo-btn", variant="warning", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self.query_one("#closure", DataTable).display = False
        self.query_one("#stop-btn", Button).display = False
        self.query_one("#undo-btn", Button).add_class("hidden")
        self._token: Cancel | None = None

    def on_screen_resume(self) -> None:  # also on the first push
        self.render_session()
        self.render_plan()
        self.action_refresh(force=False)

    def render_session(self) -> None:
        writes = [view.Field("wrote", w, "accent") for w in self.nixin.writes]
        applied = [
            view.Field("applied", f"generation {d.get('generation')}", "ok")
            for d in self.nixin.applied
        ]
        self.query_one("#session", Fields).show(
            writes + applied, "" if writes or applied else "no writes yet"
        )

    def action_refresh(self, force: bool = True) -> None:
        doc = self.nixin.status_doc
        if doc is not None and not force and not self.nixin.status_stale:
            self._status(doc)
            return
        self.nixin.load_status(self._status, spinner=self.query_one("#status-spin", Spinner))

    def _status(self, doc: dict) -> None:
        f = self.query_one("#pending", Fields)
        if not doc.get("ok"):
            n = view.notice(doc)
            assert n is not None
            f.show([], f"status: {n.code}: {n.message}", "error")
            return
        undo = view.can_undo(doc.get("lock_change"))
        f.show(view.pending_fields(doc.get("pending"), doc.get("lock_change"), "z" if undo else ""))
        self.query_one("#undo-btn", Button).set_class(not undo, "hidden")  # a phone: `z`

    def action_undo(self) -> None:
        doc = self.nixin.status_doc
        if doc is None or not view.can_undo(doc.get("lock_change")):
            self.notify("nothing to undo: flake.lock is as committed", severity="warning")
            return
        self.nixin.undo_update(after=lambda: self.action_refresh(force=True))

    def action_plan(self) -> None:
        log = self.query_one("#log", LogView)
        log.reset()
        self.query_one("#notice", NoticeBox).show(None)
        token = Cancel(watched=True)
        started = self.nixin.fetch(
            "plan",
            done=self.show_plan,
            stream=True,
            log=log,
            busy="planning",
            spinner=self.query_one("#plan-spin", Spinner),
            label="plan: building the workspace",
            hint="38 s for nothing new, minutes for new packages, hours for a kernel; x stops",
            cancel=token,
        )
        if started is not None:
            self.watch_token(token, "plan")

    def show_plan(self, doc: dict) -> None:
        self.watch_token(None, "")
        self.nixin.plan_doc = doc
        self.render_plan()
        # the result is the bottom of this screen: bring it into view (a phone shows ~15 rows)
        body = self.query_one("#body", VerticalScroll)
        self.set_timer(0.05, lambda: body.scroll_end(animate=False))

    def render_plan(self) -> None:
        """The app's last plan, or the hint when there is none (a write or an apply since
        made it stale)."""
        doc = self.nixin.plan_doc
        table = self.query_one("#closure", DataTable)
        head = self.query_one("#closure-head", Line)
        if doc is None:
            if self.nixin.busy != "planning":
                self.query_one("#log", LogView).reset()
                self.query_one("#log", LogView).display = False
            self.query_one("#notice", NoticeBox).show(None)
            self.query_one("#plan", Fields).show(
                [], "b: build the workspace and diff it against the running system", "dim"
            )
            head.show("")
            table.display = False
            return
        notice = view.notice(doc)
        self.query_one("#notice", NoticeBox).show(notice)
        if notice is not None:
            self.query_one("#plan", Fields).show([])
            head.show("")
            table.display = False
            return
        self.query_one("#plan", Fields).show(view.plan_fields(doc))
        head.show(view.closure_head(doc.get("diff")), "head")
        rows = view.closure_rows(doc.get("diff"))
        table.clear(columns=True)
        table.add_column("", width=1, key="sign")
        table.add_column("package", key="name")
        table.add_column("version", key="version")
        table.add_column("size", key="size")
        for r in rows:
            table.add_row(
                *(cell(self.app, x, r.style) for x in (r.sign, r.name, r.version, r.size))
            )
        table.display = bool(rows)

    def action_apply(self) -> None:
        self.nixin.start_apply()

    def action_sync(self) -> None:
        self.nixin.fetch(
            "sync",
            done=self._synced,
            spinner=self.query_one("#plan-spin", Spinner),
            label="sync: git fetch of the hub",
        )

    def _synced(self, doc: dict) -> None:
        f = self.query_one("#sync", Fields)
        n = view.notice(doc)
        if n is not None:
            f.show([], f"sync: {n.code}: {n.message}", "error")
        else:
            f.show(view.sync_fields(doc))
        f.scroll_visible()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "plan-btn": self.action_plan,
            "stop-btn": self.action_stop,
            "apply-btn": self.action_apply,
            "sync-btn": self.action_sync,
            "undo-btn": self.action_undo,
        }[event.button.id or ""]()

    def action_back(self) -> None:
        self.app.pop_screen()


# --- apply ----------------------------------------------------------------------------------------


class ApplyScreen(BuildScreen):
    """Runs `apply [-m MESSAGE]` with its log streaming; then the result, or the notice —
    for `needs-sudo` the exact commands to run, selectable and copyable. Stop (x) stops the
    build; from the pull on it is refused."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("c", "copy", "Copy commands", show=False),
        Binding("r", "retry", "Retry", show=False),
        Binding("x", "stop", "Stop", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message
        self.doc: dict | None = None

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        with VerticalScroll(id="body"):
            yield Title(f"apply → {self.nixin.backend.host}")
            yield Spinner(id="apply-spin", classes="run-spin")
            yield BuildTracker(id="build")
            yield LogView(id="log")
            yield NoticeBox(id="notice")
            yield Line("", id="sudo-help")
            yield TextArea("", id="commands", read_only=True, soft_wrap=True, compact=True)
            yield Fields(id="result", label_width=10)
            yield Line("", id="closure-head")
        with Horizontal(id="actions"):
            yield Button("Stop (x)", id="stop-btn", variant="warning", compact=True)
            yield Button("Copy commands (c)", id="copy", compact=True)
            yield Button("Retry (r)", id="retry", compact=True)
            yield Button("Done (esc)", id="done", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self.query_one("#commands", TextArea).display = False
        self.query_one("#copy", Button).display = False
        self.query_one("#retry", Button).display = False
        self.query_one("#stop-btn", Button).display = False
        self._token: Cancel | None = None
        self.run_apply()

    def run_apply(self) -> None:
        args = ["-m", self.message] if self.message else []
        self.query_one("#log", LogView).reset()
        token = Cancel(watched=True)
        started = self.nixin.fetch(
            "apply",
            *args,
            done=self.show,
            stream=True,
            log=self.query_one("#log", LogView),
            busy="applying",
            spinner=self.query_one("#apply-spin", Spinner),
            label="apply: commit, build, pull, switch",
            hint="about 30 s after a plan; nixos-rebuild evaluates again as root; "
            "x stops the build (not the switch)",
            cancel=token,
        )
        if started is not None:
            self.watch_token(token, "apply")

    def show(self, doc: dict) -> None:
        self.watch_token(None, "")
        self.doc = doc
        self.nixin.applied_doc(doc)
        n = view.notice(doc)
        self.query_one("#notice", NoticeBox).show(n)
        cmds = self.query_one("#commands", TextArea)
        help_ = self.query_one("#sudo-help", Line)
        if n is not None:
            self.query_one("#result", Fields).show(view.progress_fields(doc))
            cmds.display = bool(n.commands)
            self.query_one("#copy", Button).display = bool(n.commands)
            self.query_one("#retry", Button).display = n.severity != "info" or n.code == "cancelled"
            if n.commands:
                cmds.text = "\n".join(n.commands)
                help_.show(
                    "run these as root (c copies them), or `sudo -v` once and r to retry; "
                    "the commit and the build are done",
                    "dim",
                )
            return
        help_.show("")
        cmds.display = False
        self.query_one("#result", Fields).show(view.apply_fields(doc))
        self.query_one("#closure-head", Line).show(view.closure_head(doc.get("diff")), "head")

    def action_copy(self) -> None:
        n = view.notice(self.doc) if self.doc else None
        if n and n.commands:
            self.app.copy_to_clipboard("\n".join(n.commands))
            self.notify("commands copied (OSC 52: your terminal must allow it)")

    def action_retry(self) -> None:
        if self.nixin.busy:
            return
        self.query_one("#notice", NoticeBox).show(None)
        self.run_apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "copy": self.action_copy,
            "retry": self.action_retry,
            "done": self.action_back,
            "stop-btn": self.action_stop,
        }[event.button.id or ""]()

    def action_back(self) -> None:
        if self.nixin.busy == "applying":
            self.notify("apply is running; it finishes on its own", severity="warning")
            return
        self.app.pop_screen()


# --- status ---------------------------------------------------------------------------------------


class StatusScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("f", "full", "Full"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        yield Spinner(id="status-spin")
        yield NoticeBox(id="notice")
        with VerticalScroll(id="body"), Vertical(id="sections"):
            for name in ("config", "lock", "running", "pending", "counts", "caches"):
                with Vertical(classes="section", id=f"sec-{name}"):
                    yield Title(name)
                    yield Fields(id=f"f-{name}", label_width=10)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        doc = self.nixin.status_doc
        if doc is not None and not self.nixin.status_stale:
            self.show(doc)
        else:
            self.action_refresh()

    def action_refresh(self, full: bool = False) -> None:
        self.nixin.load_status(
            self.show, full=full, spinner=self.query_one("#status-spin", Spinner)
        )

    def action_full(self) -> None:
        self.action_refresh(full=True)

    def show(self, doc: dict) -> None:
        n = view.notice(doc)
        self.query_one("#notice", NoticeBox).show(n)
        if n is not None:
            return
        running = (self.nixin.plan_doc or {}).get("running")
        if self.nixin.applied:
            running = self.nixin.applied[-1].get("running") or running
        for s in view.status_sections(doc, running):
            self.query_one(f"#f-{s.title}", Fields).section(s)

    def action_back(self) -> None:
        self.app.pop_screen()
