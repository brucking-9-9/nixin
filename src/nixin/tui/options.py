"""The Options page: browse NixOS or Home Manager options, see one in full, set or unset it.

OptionsScreen: the query (debounced as on Search), `k` toggles NixOS ↔ Home Manager options
(`search --kind opt|hm`), the results (name, type, and from 60 cells the description; under
the table the highlighted row's description). Enter opens OptionScreen: what the option is
(type, default, example, the full description), its value on this host and the files that
set it (`where`, the Home Manager user's tree for `hm`), where it is declared (the module
on the locked nixpkgs or home-manager, upstream's link). Set (`e`) opens the value dialog
(the current value, the example as a hint) → the writer's `--dry-run` diff → write; Unset
(`u`) the same without the dialog. A write lands on Pending like any other.
"""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Static

from ..ui import view
from .screens import DEBOUNCE, Base
from .widgets import Fields, Line, NoticeBox, Spinner, Title, cell, spans_text


def kind_label(kind: str) -> str:
    return view.OPTION_KIND_LABEL.get(kind, kind)


class OptionsScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "escape", "Back"),
        Binding("k", "kind", "Kind"),
        Binding("f", "focus_query", "Query", show=False),
        Binding("c", "channel", "Channel", show=False),
        Binding("down", "results", "Results", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.kind = "opt"
        self.doc: dict | None = None
        self.hits: list[view.Hit] = []
        self._last: tuple | None = None
        self._jump = False
        self._debounce: Timer | None = None

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        with Horizontal(id="op-bar"):
            yield Input(placeholder="search options… (f)", id="op-query", compact=True)
            yield Button("NixOS (k)", id="op-kind", compact=True)
        yield DataTable(id="op-table", cursor_type="row")
        yield Line("", id="op-preview")
        yield Spinner(id="op-spin")
        yield NoticeBox(id="op-notice")
        yield Line("", id="op-source")
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        last = self.nixin.options_last  # the page as it was left
        if last is not None:
            query, self.kind, doc = last
            self.query_one("#op-query", Input).value = query
            self._last = (query, self.kind, self.nixin.channel)
            self.show(doc)
        else:
            self.show(None)
        self._kind_label()
        self.query_one("#op-query", Input).focus()

    # --- the query

    @on(Input.Changed, "#op-query")
    def _typed(self, event: Input.Changed) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._debounce = self.set_timer(DEBOUNCE, self.search)

    @on(Input.Submitted, "#op-query")
    def _submitted(self, event: Input.Submitted) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._jump = True
        self.search()
        if self.doc is not None and self._last == self._key():
            self._jump = False
            self.query_one("#op-table", DataTable).focus()

    def _key(self) -> tuple:
        return (self.query_one("#op-query", Input).value.strip(), self.kind, self.nixin.channel)

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
        self.nixin.fetch(
            "search",
            *view.option_search_args(query, self.kind, self.nixin.channel),
            done=self.show,
            group="options-search",
            exclusive=True,
            spinner=self.query_one("#op-spin", Spinner),
            label=f"searching {kind_label(self.kind)} options for {query!r}",
        )

    def show(self, doc: dict | None) -> None:
        self.doc = doc
        query = self.query_one("#op-query", Input).value.strip()
        self.nixin.options_last = (query, self.kind, doc) if doc is not None else None
        notice = view.notice(doc) if doc else None
        self.query_one("#op-notice", NoticeBox).show(notice)
        self.hits = view.hits(doc) if doc and doc.get("ok") else []
        self.render_table()
        if self._jump:
            self.query_one("#op-table", DataTable).focus()
        self._jump = False
        src = " · ".join(view.source_lines(doc)) if doc and doc.get("ok") else ""
        self.query_one("#op-source", Line).show(src, "dim")

    def render_table(self) -> None:
        table = self.query_one("#op-table", DataTable)
        table.clear(columns=True)
        width = table.size.width or (self.size.width or 80) - 2
        cols = view.hit_columns(self.kind, width)
        for c in cols:
            table.add_column(c.label, width=c.width, key=c.key)
        for i, h in enumerate(self.hits):
            table.add_row(
                *(cell(self.app, s.text, s.style) for s in view.hit_cells(h, cols)), key=str(i)
            )
        self._preview()

    def on_resize(self) -> None:
        if self.doc is not None:
            self.call_after_refresh(self.render_table)

    @on(DataTable.RowHighlighted, "#op-table")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._preview()

    def _preview(self) -> None:
        prev = self.query_one("#op-preview", Line)
        h = self.current()
        if h is not None:
            prev.show(
                view.clip(h.description, 400) or "(no description)", "" if h.description else "dim"
            )
        elif self.doc and self.doc.get("ok"):
            prev.show("no options match · k: the other kind · f: edit the query", "dim")
        else:
            other = kind_label("hm" if self.kind == "opt" else "opt")
            prev.show(
                f"type to search {kind_label(self.kind)} options · enter: details · k: {other}",
                "dim",
            )

    def current(self) -> view.Hit | None:
        table = self.query_one("#op-table", DataTable)
        row = table.cursor_row
        if not self.hits or row is None or not 0 <= row < len(self.hits):
            return None
        return self.hits[row]

    @on(DataTable.RowSelected, "#op-table")
    def _selected(self, event: DataTable.RowSelected) -> None:
        h = self.current()
        if h is not None:
            self.app.push_screen(OptionScreen(h))

    # --- keys

    def action_kind(self) -> None:
        self.kind = "hm" if self.kind == "opt" else "opt"
        self._kind_label()
        self.search()

    def _kind_label(self) -> None:
        btn = self.query_one("#op-kind", Button)
        btn.label = f"{kind_label(self.kind)} (k)"
        btn.refresh(layout=True)  # an auto width keeps the first label's otherwise
        self.query_one(
            "#op-query", Input
        ).placeholder = f"search {kind_label(self.kind)} options… (f)"
        if not self.query_one("#op-query", Input).value.strip():
            self._preview()

    def action_channel(self) -> None:
        self.nixin.cycle_channel()
        self.search(force=True)

    def action_focus_query(self) -> None:
        self.query_one("#op-query", Input).focus()

    def action_results(self) -> None:
        if self.query_one("#op-query", Input).has_focus and self.hits:
            self.query_one("#op-table", DataTable).focus()

    def action_escape(self) -> None:
        """From the query to the results (then the page keys work); from there back."""
        if self.query_one("#op-query", Input).has_focus:
            self.query_one("#op-table", DataTable).focus()
            return
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "op-kind":
            self.action_kind()


class OptionScreen(Base):
    """One option in full, with Set and Unset, for the system or a Home Manager user."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("e", "set", "Set", show=False),
        Binding("u", "unset", "Unset", show=False),
        Binding("h", "home", "User", show=False),
        Binding("w", "where", "Where again", show=False),
    ]

    def __init__(self, hit: view.Hit, user: str | None = None) -> None:
        super().__init__()
        self.hit = hit
        self.kind = hit.kind  # opt | hm
        self.record = hit.record
        self.where: dict | None = None
        self.user = user  # --home USER, for `hm`
        self.template = view.option_template(hit.target)

    def compose(self) -> ComposeResult:
        h = self.hit
        yield from self.compose_top()
        self.title_spans = [
            view.Span(f"{view.KIND_BADGE.get(h.kind, h.kind)} ", "dim"),
            view.Span(h.name, "option"),
        ]
        yield Static(spans_text(self.app, self.title_spans), id="details-title")
        with VerticalScroll(id="body"), Horizontal(id="panes"):
            with Vertical(id="left", classes="pane"):
                yield Title("option")
                yield Fields(id="op-option", label_width=9)
                yield Title("about")
                yield Fields(id="op-about")
            with Vertical(id="right", classes="pane"):
                yield Title(f"on {self.nixin.backend.host}", id="op-host-title")
                yield Spinner(id="op-where-spin")
                yield Fields(id="op-host", label_width=9)
                yield Title("declared")
                yield Fields(id="op-declared", label_width=9)
        with Horizontal(id="actions"):
            yield Button("Set (e)", id="op-set", variant="primary", compact=True)
            yield Button("Unset (u)", id="op-unset", variant="error", compact=True)
            yield Button("", id="op-home", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        if self.kind == "hm" and self.user is None:
            self.user = (self.nixin.hm_users() or [None])[0]
        self.query_one("#op-unset", Button).disabled = self.template
        self.render_sections()
        if not self.template:
            self.action_where()

    def render_sections(self) -> None:
        host = self.nixin.backend.host
        option, about, on_host, declared = view.option_sections(
            self.record, self.where, host, self.kind, self.user
        )
        self.query_one("#op-option", Fields).section(option)
        self.query_one("#op-about", Fields).show([], about.note, "")
        self.query_one("#op-host", Fields).section(on_host)
        self.query_one("#op-declared", Fields).section(declared)
        cue = view.option_cue(self.where, self.kind, self.user)
        spans = self.title_spans + ([view.Span("  "), cue] if cue else [])
        self.query_one("#details-title", Static).update(spans_text(self.app, spans))
        btn = self.query_one("#op-home", Button)
        btn.display = self.kind == "hm"
        btn.label = f"home: {self.user or 'no HM user'} (h)"

    def action_where(self) -> None:
        if self.template:
            return
        self.nixin.fetch(
            "where",
            self.hit.target,
            "--kind",
            "option",
            done=self._where,
            group=f"where-{id(self)}",
            exclusive=True,
            spinner=self.query_one("#op-where-spin", Spinner),
            label=f"where: evaluating {self.nixin.backend.config_label}",
            hint="a few seconds cold, under one warm",
        )

    def _where(self, doc: dict) -> None:
        self.where = doc
        users = view.option_users(doc)
        if self.kind == "hm" and self.user not in users and users:
            self.user = users[0]
        self.render_sections()

    def users(self) -> list[str]:
        return view.option_users(self.where) or self.nixin.hm_users(self.where)

    def action_home(self) -> None:
        if self.kind != "hm":
            return
        users = self.users()
        if not users:
            self.notify(f"no Home Manager users on {self.nixin.backend.host}", severity="warning")
            return
        i = users.index(self.user) if self.user in users else -1
        self.user = users[(i + 1) % len(users)]
        self.render_sections()

    def _home_args(self) -> list[str] | None:
        if self.kind != "hm":
            return []
        if not self.user:
            self.notify(
                f"Home Manager options need a user; {self.nixin.backend.host} has none",
                severity="error",
            )
            return None
        return ["--home", self.user]

    def action_set(self) -> None:
        home = self._home_args()
        if home is None:
            return
        self.nixin.set_option(
            self.hit.target,
            view.option_prefill(self.record, self.where, self.kind, self.user),
            self.user if home else None,
            home,
            hint=view.option_set_hint(self.record),
            path=self.template,
            after=self.action_where,
        )

    def action_unset(self) -> None:
        if self.template:
            self.notify("a template: unset takes the full name", severity="warning")
            return
        home = self._home_args()
        if home is not None:
            self.nixin.write("unset", [self.hit.target, *home], after=self.action_where)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "home":
            return self.kind == "hm"
        return True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {
            "op-set": self.action_set,
            "op-unset": self.action_unset,
            "op-home": self.action_home,
        }[event.button.id or ""]()

    def action_back(self) -> None:
        self.app.pop_screen()
