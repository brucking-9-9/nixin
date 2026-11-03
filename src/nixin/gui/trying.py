"""The Try dialog's state: `try PKG --build-only` with nix's log streaming, then what to do.

Started from the details pane (Try, next to Add). The build runs on the pool like plan
(busy "trying": the stderr router has one sink). Then `view.try_next` decides: a graphical
app is started detached (`setsid`, output to ~/.cache/nixin/try/<program>.log); a CLI
program opens in the person's terminal (`$TERMINAL -e …`, else the first known one: kitty
here) running the `nix run` line, held open until Enter; without a terminal the command
is there to copy. `ambiguous-program` offers the programs; picking one runs try again
with --program (the build is done). Nothing is installed.

A module of its own (bridge.py imports it lazily: it subclasses bridge's VM).
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import Signal, Slot

from ..tryout import launch
from ..ui import view
from . import models
from .bridge import VM, Doc, model, state


class TryVM(VM):
    changed = Signal()
    defaults: ClassVar = {
        "open": False,
        "phase": "",  # building | done | pick | failed
        "target": "",
        "program": "",  # --program, once picked
        "head": "",
        "note": "",  # what happened after the build (launched, opened, or why not)
        "noteStyle": "",
        "isGui": False,
        "canLaunch": False,
        "canTerminal": False,
        "noticeCode": "",
        "noticeTitle": "",
        "noticeMessage": "",
        "noticeSeverity": "",
        "commands": "",  # one per line: run, then shell (or the error's)
        "shellCommand": "",
        "programs": [],
    }
    lists: ClassVar = {"result": models.FIELD}
    logs: ClassVar = ("log",)
    open = state(bool, "open", changed)
    phase = state(str, "phase", changed)
    target = state(str, "target", changed)
    program = state(str, "program", changed)
    head = state(str, "head", changed)
    note = state(str, "note", changed)
    noteStyle = state(str, "noteStyle", changed)
    isGui = state(bool, "isGui", changed)
    canLaunch = state(bool, "canLaunch", changed)
    canTerminal = state(bool, "canTerminal", changed)
    noticeCode = state(str, "noticeCode", changed)
    noticeTitle = state(str, "noticeTitle", changed)
    noticeMessage = state(str, "noticeMessage", changed)
    noticeSeverity = state(str, "noticeSeverity", changed)
    commands = state(str, "commands", changed)
    shellCommand = state(str, "shellCommand", changed)
    programs = state("QVariantList", "programs", changed)
    result = model("result")
    log = model("log")

    def __init__(self, gui: Any) -> None:
        super().__init__(gui)
        self.doc: Doc | None = None
        self.launches: list[dict[str, Any]] = []  # what this dialog started (tests read it)

    @Slot(str)
    def start(self, target: str) -> None:
        if self["open"] or not target:
            return
        self.set(target=target, program="")
        if self._run():
            self.set(open=True)

    @Slot(str)
    def pick(self, program: str) -> None:
        if self["phase"] == "pick" and program:
            self.set(program=program)
            self._run()

    @Slot()
    def retry(self) -> None:
        if self["phase"] == "failed":
            self._run()

    def _run(self) -> bool:
        args = [self["target"], "--build-only"]
        if self["program"]:
            args += ["--program", self["program"]]
        log = self.logmodel("log")
        started = self.gui.fetch(
            "try",
            *args,
            done=self._done,
            stream=True,
            log=log,
            busy="trying",
            label=f"try {self['target']}: nix build into the store (nothing is installed)",
            hint=view.TRY_HINT,
        )
        if started:
            log.clear()
            self.rows("result").reset([])
            self.doc = None
            self.set(
                phase="building",
                head="",
                note="",
                noteStyle="",
                isGui=False,
                canLaunch=False,
                canTerminal=False,
                noticeCode="",
                commands="",
                shellCommand="",
                programs=[],
            )
        return started

    def _done(self, doc: Doc) -> None:
        if not self["open"]:
            return
        self.doc = doc
        n = view.notice(doc)
        term = launch.terminal() is not None
        if n is not None:
            err = doc.get("error") or {}
            shell = str(err.get("shell_command") or "")
            progs = view.try_programs(doc)
            self.set(
                phase="pick" if progs else "failed",
                noticeCode=n.code,
                noticeTitle=n.title,
                noticeMessage=n.brief + "".join(f"\n• {i}" for i in n.items),
                noticeSeverity=n.severity,
                commands="\n".join(n.commands or ([shell] if shell else [])),
                shellCommand=shell,
                programs=progs,
                canTerminal=term and bool(shell),
            )
            return
        self.rows("result").reset(models.fields(view.try_fields(doc)))
        nxt = view.try_next(doc, display=launch.has_display(), terminal=term)
        self.set(
            phase="done",
            head=view.try_head(doc),
            isGui=bool(doc.get("gui")),
            canLaunch=nxt == "launch",
            canTerminal=term and bool(doc.get("command")),
            commands="\n".join(view.try_commands(doc)),
            shellCommand=str(doc.get("shell_command") or ""),
        )
        if nxt == "launch":
            self.launchApp()
        elif nxt == "terminal":
            self.runInTerminal()
        else:
            self.set(
                note="no terminal found to run it in (set $TERMINAL): copy the command",
                noteStyle="warn",
            )

    def _name(self) -> str:
        doc = self.doc or {}
        return str(doc.get("program") or doc.get("attr") or self["target"] or "app")

    def _start(self, argv: list[str], name: str, said: str) -> None:
        try:
            r = launch.detached(argv, name=name)
        except (OSError, LookupError) as e:
            self.set(note=f"could not start {name}: {e}", noteStyle="error")
            return
        self.launches.append(r)
        self.set(note=f"{said} (pid {r['pid']}); its output: {r['log']}", noteStyle="accent")

    @Slot()
    def launchApp(self) -> None:
        doc = self.doc or {}
        if not self["canLaunch"] or not doc.get("exec"):
            return
        name = self._name()
        self._start(list(doc["exec"]), name, f"launched {name}, detached")

    @Slot()
    def runInTerminal(self) -> None:
        doc = self.doc or {}
        if not self["canTerminal"] or not doc.get("command"):
            return
        name = self._name()
        try:
            argv = launch.in_terminal(str(doc["command"]), hold=True, name=name)
        except LookupError as e:
            self.set(note=str(e), noteStyle="warn")
            return
        self._start(argv, f"{name}-terminal", f"opened {name} in {argv[0]}")

    @Slot()
    def shellInTerminal(self) -> None:
        shell = self["shellCommand"]
        if not shell or not self["canTerminal"]:
            return
        try:
            argv = launch.in_terminal(shell, hold=False)
        except LookupError as e:
            self.set(note=str(e), noteStyle="warn")
            return
        name = self._name()
        self._start(argv, f"{name}-shell", f"opened a shell with {self['target']} in {argv[0]}")

    @Slot()
    def copyCommand(self) -> None:
        if self["commands"]:
            self.gui.copy(self["commands"].splitlines()[0])

    @Slot()
    def close(self) -> None:
        if self["phase"] == "building":
            self.gui.notice.say("the build is running; it finishes on its own", "warn")
            return
        self.set(open=False)
