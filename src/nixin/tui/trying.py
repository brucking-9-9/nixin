"""The Try screen: `try PKG --build-only` with nix's output streaming, then what to do with it.

Opened from Details (`t`, the Try button next to Add). The build runs in a worker like plan
does (busy "trying", so its log cannot mix with a plan's); nothing is installed. Then:

- a graphical app (a desktop entry that does not ask for a terminal) is started detached
  (`setsid`, its output in ~/.cache/nixin/try/<program>.log) when this terminal has a
  display; `l` starts it again;
- anything else shows the exact `nix run …` and `nix shell …` lines to copy (`c`): the TUI
  is the terminal, it cannot hand itself over;
- `ambiguous-program` lists the programs: Enter picks one and runs try again with
  `--program` (the build is done, so that takes about a second).
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, OptionList, TextArea

from ..tryout import launch
from ..ui import view
from .screens import Base
from .widgets import Fields, Line, LogView, NoticeBox, Spinner, Title


class TryScreen(Base):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("l", "launch", "Launch", show=False),
        Binding("c", "copy", "Copy command", show=False),
        Binding("r", "retry", "Retry", show=False),
    ]

    def __init__(self, target: str, program: str | None = None) -> None:
        super().__init__()
        self.target = target
        self.program = program
        self.doc: dict | None = None
        self.launched: dict | None = None
        self.running = False

    def compose(self) -> ComposeResult:
        yield from self.compose_top()
        with VerticalScroll(id="body"):
            yield Title(f"try {self.target}", id="try-title")
            yield Spinner(id="try-spin")
            yield LogView(id="log")
            yield NoticeBox(id="notice")
            yield Line("", id="try-pick")
            yield OptionList(id="programs")
            yield Line("", id="try-head")
            yield Fields(id="result", label_width=9)
            yield Line("", id="try-next")
            yield TextArea("", id="commands", read_only=True, soft_wrap=True, compact=True)
        with Horizontal(id="actions"):
            yield Button("Launch (l)", id="launch", variant="primary", compact=True)
            yield Button("Copy (c)", id="copy", compact=True)
            yield Button("Retry (r)", id="retry", compact=True)
            yield Button("Done (esc)", id="done", compact=True)
        yield from self.compose_bottom()

    def on_mount(self) -> None:
        self._buttons(launch=False, copy=False, retry=False)
        self.query_one("#programs", OptionList).display = False
        self.query_one("#commands", TextArea).display = False
        self.run_try()

    def _buttons(self, *, launch: bool, copy: bool, retry: bool) -> None:
        self.query_one("#launch", Button).display = launch
        self.query_one("#copy", Button).display = copy
        self.query_one("#retry", Button).display = retry

    def run_try(self) -> None:
        args = [self.target, "--build-only"]
        if self.program:
            args += ["--program", self.program]
        self.query_one("#try-title", Title).update(f"try {' '.join(args[:1] + args[2:])}")
        self.query_one("#log", LogView).reset()
        self.query_one("#notice", NoticeBox).show(None)
        for line in ("#try-pick", "#try-head", "#try-next"):
            self.query_one(line, Line).show("")
        self.query_one("#programs", OptionList).display = False
        self.query_one("#result", Fields).show([])
        self.query_one("#commands", TextArea).display = False
        self._buttons(launch=False, copy=False, retry=False)
        started = self.nixin.fetch(
            "try",
            *args,
            done=self.show,
            stream=True,
            log=self.query_one("#log", LogView),
            busy="trying",
            spinner=self.query_one("#try-spin", Spinner),
            label=f"try {self.target}: nix build into the store (nothing is installed)",
            hint=view.TRY_HINT,
        )
        self.running = started is not None
        if not self.running:
            self.query_one("#try-next", Line).show("another run is in progress; r retries", "dim")
            self._buttons(launch=False, copy=False, retry=True)

    def show(self, doc: dict) -> None:
        self.running = False
        self.doc = doc
        n = view.notice(doc)
        cmds = self.query_one("#commands", TextArea)
        if n is not None:
            self.query_one("#notice", NoticeBox).show(n)
            err = doc.get("error") or {}
            lines = n.commands or ([err["shell_command"]] if err.get("shell_command") else [])
            cmds.text, cmds.display = "\n".join(lines), bool(lines)
            progs = view.try_programs(doc)
            if progs:
                pick = self.query_one("#programs", OptionList)
                pick.clear_options()
                pick.add_options(progs)
                pick.display = True
                pick.focus()
                self.query_one("#try-pick", Line).show(
                    "pick the program to run (Enter); the build is done", "accent"
                )
            retry = n.code not in ("ambiguous-program", "no-program", "not-found", "unfree")
            self._buttons(launch=False, copy=bool(lines), retry=retry)
            return
        self.query_one("#log", LogView).display = False  # the head line says what it fetched
        self.query_one("#try-head", Line).show(view.try_head(doc), "add")
        self.query_one("#result", Fields).show(view.try_fields(doc))
        cmds.text, cmds.display = "\n".join(view.try_commands(doc)), True
        display = launch.has_display()
        nxt = view.try_next(doc, display=display, terminal=False)
        self._buttons(launch=nxt == "launch", copy=True, retry=False)
        if nxt == "launch":
            self.action_launch()
        elif doc.get("gui"):
            self.query_one("#try-next", Line).show(
                "a graphical app, but this terminal has no display (WAYLAND_DISPLAY, DISPLAY): "
                "run it in your desktop session (c copies):",
                "warn",
            )
        else:
            self.query_one("#try-next", Line).show(
                "run it in another terminal (c copies the first line); nothing is installed",
                "dim",
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.running:
            return
        self.program = str(event.option.prompt)
        self.run_try()

    def action_launch(self) -> None:
        doc = self.doc or {}
        if view.try_next(doc, display=launch.has_display(), terminal=False) != "launch":
            return
        name = doc.get("program") or doc.get("attr") or "app"
        try:
            self.launched = launch.detached(list(doc["exec"]), name=name)
        except OSError as e:
            self.query_one("#try-next", Line).show(f"could not start {name}: {e}", "error")
            return
        self.query_one("#try-next", Line).show(
            f"launched {name} (pid {self.launched['pid']}, detached); "
            f"its output: {self.launched['log']}; l starts another",
            "accent",
        )
        self.notify(f"launched {name}")

    def action_copy(self) -> None:
        text = self.query_one("#commands", TextArea).text
        if text:
            self.app.copy_to_clipboard(text.splitlines()[0])
            self.notify("command copied (OSC 52: your terminal must allow it)")

    def action_retry(self) -> None:
        if not self.running:
            self.run_try()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = {"launch": self.action_launch, "copy": self.action_copy}
        action |= {"retry": self.action_retry, "done": self.action_back}
        action[event.button.id or ""]()

    def action_back(self) -> None:
        if self.running:
            self.notify("the build is running; it finishes on its own", severity="warning")
            return
        self.app.pop_screen()
