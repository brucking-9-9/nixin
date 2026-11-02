"""Modal screens: the writer's diff preview, setting an option, confirming an apply,
error notices (with candidates to pick), and the key help."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..ui import view
from .widgets import Fields, NoticeBox, Spans, Title


class Modal(ModalScreen):
    """A dialog: Esc closes it with None."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Back")]

    def action_close(self) -> None:
        self.dismiss(None)


class DiffModal(Modal):
    """The writer's --dry-run document: the unified diff and what would happen. Write (or
    Enter on the button) dismisses with True."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("w", "write", "Write")]

    def __init__(self, preview: dict, title: str) -> None:
        super().__init__()
        self.preview = preview
        self.heading = title

    def compose(self) -> ComposeResult:
        changed = view.writer_changed(self.preview)
        with Vertical(id="dialog", classes="dialog"):
            yield Label(self.heading, classes="dialog-title")
            with VerticalScroll(id="diff-scroll"):
                yield Fields(id="writer-fields", label_width=10)
                yield Spans(id="diff")
            with Horizontal(classes="buttons"):
                if changed:
                    yield Button("Write (w)", id="write", variant="primary", compact=True)
                yield Button(
                    "Cancel (esc)" if changed else "Close (esc)", id="cancel", compact=True
                )

    def on_mount(self) -> None:
        fields = view.writer_fields(self.preview)
        note = "" if view.writer_changed(self.preview) else "nothing to change"
        self.query_one("#writer-fields", Fields).show(fields, note)
        self.query_one("#diff", Spans).show(view.diff_spans(self.preview))
        btn = self.query("#write")
        (btn.first() if btn else self.query_one("#cancel")).focus()

    def action_write(self) -> None:
        if view.writer_changed(self.preview):
            self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(True if event.button.id == "write" else None)


class SetOptionModal(Modal):
    """`set OPTION VALUE`: the Nix expression, prefilled with the current value."""

    def __init__(self, option: str, value: str, user: str | None) -> None:
        super().__init__()
        self.option, self.value, self.user = option, value, user

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"set {self.option}", classes="dialog-title")
            who = f"Home Manager user {self.user}" if self.user else "NixOS"
            yield Static(f"a Nix expression · {who}", classes="dim")
            yield Input(self.value, placeholder='true, 2, "text", [ … ]', id="value", compact=True)
            with Horizontal(classes="buttons"):
                yield Button("Preview (enter)", id="ok", variant="primary", compact=True)
                yield Button("Cancel (esc)", id="cancel", compact=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._done()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._done()
        else:
            self.dismiss(None)

    def _done(self) -> None:
        value = self.query_one("#value", Input).value.strip()
        self.dismiss(value or None)


class NoticeModal(Modal):
    """An error document. With candidates (`ambiguous`, or `--where` matching nothing) the
    person can pick one: the modal dismisses with its `--where FILE:LINE` value."""

    def __init__(self, doc: dict, heading: str, can_pick: bool = False) -> None:
        super().__init__()
        self.doc = doc
        self.heading = heading
        self.notice = view.notice(doc)
        self.can_pick = can_pick and bool(self.notice and self.notice.candidates)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog notice-dialog"):
            yield Label(self.heading, classes="dialog-title")
            with VerticalScroll():
                yield NoticeBox(id="notice")
                yield Fields(id="progress", label_width=10)
            if self.can_pick:
                yield Static("pick one to retry with --where:", classes="dim")
                yield OptionList(
                    *[
                        Option(view.candidate_label(c), id=view.candidate_where(c))
                        for c in self.notice.candidates
                    ],
                    id="candidates",
                )
            with Horizontal(classes="buttons"):
                yield Button("Close (esc)", id="close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#notice", NoticeBox).show(self.notice)
        self.query_one("#progress", Fields).show(view.progress_fields(self.doc))
        (self.query_one("#candidates") if self.can_pick else self.query_one("#close")).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ApplyModal(Modal):
    """Confirm an apply: the dry run's commit, commands and root readiness, and the commit
    message (empty: apply generates one). Dismisses with the message ("" for generated)."""

    def __init__(self, dry: dict) -> None:
        super().__init__()
        self.dry = dry

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog apply-dialog"):
            yield Label(
                f"apply to {self.dry.get('host')}: commit, pull, switch", classes="dialog-title"
            )
            with VerticalScroll():
                yield Fields(id="dry", label_width=13)
            gen = view.generated_message(self.dry)
            yield Static("commit message (empty: generated)", classes="dim")
            yield Input(placeholder=gen or "nixin: …", id="message", compact=True)
            with Horizontal(classes="buttons"):
                yield Button("Apply (enter)", id="apply", variant="error", compact=True)
                yield Button("Cancel (esc)", id="cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#dry", Fields).show(view.dry_run_fields(self.dry))
        self.query_one("#message", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.dismiss(self.query_one("#message", Input).value.strip())
        else:
            self.dismiss(None)


KEYS = [
    ("everywhere", ""),
    ("q", "quit (ctrl+q while something runs)"),
    ("esc", "back"),
    ("/", "search"),
    ("p", "pending: status.pending, plan, apply"),
    ("s", "status"),
    ("ctrl+p", "command palette"),
    ("?", "these keys"),
    ("search", ""),
    ("enter", "details of the row"),
    ("tab", "query ↔ kinds ↔ results"),
    ("k", "next kind (pkg opt hm program all)"),
    ("c", "channel (unstable / stable)"),
    ("details", ""),
    ("a / r", "add / remove the package"),
    ("e / u", "set / unset the option"),
    ("h", "Home Manager user (packages: off / user)"),
    ("w", "where again (re-evaluate)"),
    ("pending", ""),
    ("b", "plan: build + closure diff"),
    ("a", "apply… (confirm first)"),
    ("y", "sync: fetch the hub"),
    ("r", "refresh status"),
    ("status", ""),
    ("r / f", "refresh / --full (slow)"),
]


class HelpModal(Modal):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("keys", classes="dialog-title")
            with VerticalScroll():
                for key, what in KEYS:
                    if not what:
                        yield Title(key)
                    else:
                        yield Static(f"[$accent]{key:<7}[/] {what}", classes="key-row")
            with Horizontal(classes="buttons"):
                yield Button("Close (esc)", id="close", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
