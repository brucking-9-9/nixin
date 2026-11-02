"""The Textual app: global keys, the command palette, and the flows that span screens.

`fetch` is the one way anything gets data: a worker thread calls the backend (the CLI's
verb handler, in-process) and hands the document to a callback on the UI thread. Writer
verbs go through `write` (dry run → diff modal → write), apply through `start_apply`
(dry run → confirm modal → ApplyScreen). Writer, update, plan and apply runs are serialised
by `busy`: two writer runs on one workspace race on installer/state.json (CONTRACT.md).
The `list` and `outdated` documents are kept here, so the Packages screen comes back to
them without another eval until a write or an update makes them stale.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, SystemCommand
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.worker import Worker, get_current_worker

from .. import cli
from ..ui import view
from ..ui.backend import Backend
from .modals import ApplyModal, DiffModal, HelpModal, NoticeModal, SetOptionModal
from .packages import PackagesScreen
from .screens import ApplyScreen, PendingScreen, SearchScreen, StatusScreen, TopBar
from .theme import THEME
from .widgets import LogView, Spinner

Doc = dict[str, Any]

WRITE_VERBS = ("add", "rm", "set", "unset")
NARROW = 80  # cells: HORIZONTAL_BREAKPOINTS' -narrow
# below NARROW the footer has no room for a name per page: the same keys, one group
PAGE_KEYS = Binding.Group("go")
WHERE_VERBS = ("rm", "set", "unset")  # accept --where FILE:LINE (pick a candidate)


class NixinApp(App[str]):
    CSS_PATH = Path(__file__).with_name("nixin.tcss")
    TITLE = "nixin"
    HORIZONTAL_BREAKPOINTS: ClassVar[list[tuple[int, str]]] = [(0, "-narrow"), (80, "-wide")]
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit_safely", "Quit"),
        Binding("question_mark", "help", "Keys"),
        Binding("slash", "search", "Search", show=False),
        # ctrl+4 as in the GUI; a terminal without the kitty keyboard protocol sends ctrl+\
        Binding("i,ctrl+4,ctrl+backslash", "packages", "Packages"),
        Binding("p", "pending", "Pending"),
        Binding("s", "status", "Status"),
        Binding("i,ctrl+4,ctrl+backslash", "go('packages')", "Packages", group=PAGE_KEYS),
        Binding("p", "go('pending')", "Pending", group=PAGE_KEYS),
        Binding("s", "go('status')", "Status", group=PAGE_KEYS),
    ]

    def __init__(self, backend: Backend, *, channel: str = "unstable") -> None:
        super().__init__()
        self.backend = backend
        self.channel = channel
        self.busy = ""  # "writing" | "planning" | "applying" while one runs
        self.writes: list[str] = []  # writer_summary of each write this session
        self.applied: list[Doc] = []  # apply documents this session
        self.status_doc: Doc | None = None
        self.status_stale = True
        self.plan_doc: Doc | None = None
        self.list_doc: Doc | None = None
        self.list_stale = True
        self.outdated_docs: dict[str, Doc] = {}  # mode (lock | published) → document
        self.register_theme(THEME)
        self.theme = THEME.name

    def on_mount(self) -> None:
        self.push_screen(SearchScreen())

    # --- data -------------------------------------------------------------------------------

    def fetch(
        self,
        verb: str,
        *args: str,
        done: Callable[[Doc], None],
        group: str | None = None,
        exclusive: bool = False,
        stream: bool = False,
        log: LogView | None = None,
        busy: str = "",
        spinner: Spinner | None = None,
        label: str = "",
        hint: str = "",
    ) -> Worker | None:
        """Run `verb args` in a worker thread; `done(doc)` on the UI thread unless a newer
        call in the same exclusive group superseded it. `stream`: the verb's log goes to
        `log` while it runs. `busy` marks a run that must not overlap another."""
        if busy:
            if self.busy:
                self.notify(f"wait: {self.busy} is still running", severity="warning")
                return None
            self.set_busy(busy)
        if spinner is not None:
            spinner.start(label or verb, hint)
        if log is not None:
            self.begin_capture_print(log, stdout=False, stderr=True)

        def work() -> Doc:
            try:
                doc = self.backend.call(verb, *args, stream=stream)
            except Exception as exc:  # noqa: BLE001 -- a bug in the shim must not kill the UI
                doc = {"verb": verb, "host": self.backend.host, **cli._internal(exc)}
            if not get_current_worker().is_cancelled:
                self.call_from_thread(finish, doc)
            return doc

        def finish(doc: Doc) -> None:
            if log is not None:
                log.flush_partial()
                self.end_capture_print(log)
            if spinner is not None:
                spinner.stop()
            if busy:
                self.set_busy("")
            done(doc)

        return self.run_worker(
            work, thread=True, group=group or verb, exclusive=exclusive, exit_on_error=False
        )

    def set_busy(self, what: str) -> None:
        self.busy = what
        for bar in self.screen.query(TopBar):
            bar.refresh_bar()

    def cycle_channel(self) -> None:
        chans = view.CHANNELS
        self.channel = (
            chans[(chans.index(self.channel) + 1) % len(chans)]
            if self.channel in chans
            else chans[0]
        )
        for bar in self.screen.query(TopBar):
            bar.refresh_bar()

    def load_status(
        self, done: Callable[[Doc], None], *, full: bool = False, spinner: Spinner | None = None
    ) -> None:
        def got(doc: Doc) -> None:
            if doc.get("ok"):
                self.status_doc = doc
                self.status_stale = False
            done(doc)

        self.fetch(
            "status",
            *(["--full"] if full else []),
            done=got,
            group="status",
            exclusive=True,
            spinner=spinner,
            label=f"status: evaluating {self.backend.config_label}",
            hint="about a minute cold (after an edit), under a second warm"
            + ("; --full adds one to three minutes" if full else ""),
        )

    def hm_users(self, where: Doc | None = None) -> list[str]:
        """Home Manager users of the host: from status or list, else from a `where`."""
        if self.status_doc and self.status_doc.get("hm_users"):
            return list(self.status_doc["hm_users"])
        if self.list_doc and self.list_doc.get("users"):
            return list(self.list_doc["users"])
        users = []
        for s in (where or {}).get("scopes") or []:
            if s.get("scope") == "home" and s.get("user") and s["user"] not in users:
                users.append(s["user"])
        return users

    def load_status_for_users(self, done: Callable[[list[str]], None]) -> None:
        if self.status_doc is not None:
            done(self.hm_users())
            return
        self.notify("loading the host's Home Manager users (status)…")
        self.load_status(lambda doc: done(self.hm_users()))

    # --- writer flow --------------------------------------------------------------------------

    def write(self, verb: str, args: list[str], *, after: Callable[[], None] | None = None) -> None:
        """Preview `verb args --dry-run` in the diff modal; on Write run it for real."""
        assert verb in WRITE_VERBS
        if self.busy:
            self.notify(f"wait: {self.busy} is still running", severity="warning")
            return

        def previewed(doc: Doc) -> None:
            if not doc.get("ok"):
                self._writer_error(verb, args, doc, after)
                return
            title = f"{verb} {' '.join(args)} — preview"
            self.push_screen(
                DiffModal(doc, title), lambda ok: ok and self._really_write(verb, args, after)
            )

        self.fetch(
            verb, *args, "--dry-run", done=previewed, busy="writing", label=f"{verb} --dry-run"
        )

    def _really_write(self, verb: str, args: list[str], after: Callable[[], None] | None) -> None:
        def written(doc: Doc) -> None:
            if not doc.get("ok"):
                self._writer_error(verb, args, doc, after)
                return
            summary = view.writer_summary(doc)
            self.writes.append(summary)
            self.status_stale = True
            self.list_stale = True
            self.plan_doc = None
            if view.writer_eval_failed(doc):
                self.push_screen(DiffModal(doc, f"{verb}: written, but the eval check FAILED"))
            else:
                self.notify(f"{verb}: {summary} · p: pending, plan, apply", title="written")
            if after:
                after()

        self.fetch(verb, *args, done=written, busy="writing", label=verb)

    def _writer_error(self, verb: str, args: list[str], doc: Doc, after) -> None:
        can_pick = verb in WHERE_VERBS and "--where" not in args

        def picked(where: str | None) -> None:
            if where:
                self.write(verb, [*args, "--where", where], after=after)

        self.push_screen(NoticeModal(doc, f"{verb} {' '.join(args)}", can_pick=can_pick), picked)

    def set_option(
        self, option: str, value: str, user: str | None, home: list[str], *, after=None
    ) -> None:
        def entered(new: str | None) -> None:
            if new:
                self.write("set", [option, new, *home], after=after)

        self.push_screen(SetOptionModal(option, value, user), entered)

    # --- packages: list, outdated, update ------------------------------------------------------

    def list_loaded(self, doc: Doc) -> None:
        self.list_doc = doc
        self.list_stale = False

    def updated(self, doc: Doc) -> None:
        """An `update` ran: a moved lock makes every package document stale."""
        if doc.get("ok") and doc.get("changed"):
            self.writes.append(view.update_summary(doc))
            self.outdated_docs.clear()
            self.list_stale = True
            self.status_stale = True
            self.plan_doc = None

    # --- apply flow --------------------------------------------------------------------------

    def start_apply(self) -> None:
        """`apply --dry-run` → confirm (message) → ApplyScreen runs the real apply."""

        def dry(doc: Doc) -> None:
            if not doc.get("ok"):
                self.push_screen(NoticeModal(doc, "apply"))
                return
            self.push_screen(ApplyModal(doc), confirmed)

        def confirmed(message: str | None) -> None:
            if message is not None:
                self.push_screen(ApplyScreen(message))

        self.fetch("apply", "--dry-run", done=dry, busy="checking", label="apply --dry-run")

    def applied_doc(self, doc: Doc) -> None:
        if doc.get("ok"):
            self.applied.append(doc)
            self.status_stale = True
            self.plan_doc = None  # it compared against the system that ran before

    # --- navigation ----------------------------------------------------------------------------

    PAGES: ClassVar[dict[str, type[Screen]]] = {
        "packages": PackagesScreen,
        "pending": PendingScreen,
        "status": StatusScreen,
    }

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """The footer leaves out the screen you are on; the page keys go by name from
        NARROW cells and as one group below (the binding that is off lets the key fall
        through to the other)."""
        page = parameters[0] if action == "go" and parameters else action
        if page not in self.PAGES:
            return True
        if isinstance(self.screen, self.PAGES[page]):
            return False
        return (action == "go") == (self.size.width < NARROW)

    def on_resize(self) -> None:
        self.screen.refresh_bindings()  # across NARROW the page keys change form

    def _go(self, cls: type[Screen]) -> None:
        for i, s in enumerate(self.screen_stack):
            if isinstance(s, cls):  # already open: go back to it
                while len(self.screen_stack) > i + 1:
                    self.pop_screen()
                return
        self.push_screen(cls())

    def action_search(self) -> None:
        self._go(SearchScreen)
        self.screen.query_one("#query").focus()

    def action_pending(self) -> None:
        self._go(PendingScreen)

    def action_packages(self) -> None:
        self._go(PackagesScreen)

    def action_go(self, page: str) -> None:
        self._go(self.PAGES[page])

    def action_status(self) -> None:
        self._go(StatusScreen)

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpModal):
            self.push_screen(HelpModal())

    def action_quit_safely(self) -> None:
        if self.busy in ("writing", "updating", "applying"):
            self.notify(f"{self.busy} is running; ctrl+q quits anyway", severity="warning")
            return
        self.exit(view.farewell(self.writes, self.applied))

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield SystemCommand("Search", "find packages and options", self.action_search)
        yield SystemCommand(
            "Packages", "installed (list), updates (outdated)", self.action_packages
        )
        yield SystemCommand("Pending", "status.pending, plan, apply", self.action_pending)
        yield SystemCommand("Plan", "build the workspace, closure diff", self._palette_plan)
        yield SystemCommand(
            "Apply…", "commit, pull into the authoritative tree, switch", self.start_apply
        )
        yield SystemCommand(
            "Status", "lock, HEAD, running system, counts, caches", self.action_status
        )
        yield SystemCommand("Sync", "git fetch of the hub", self._palette_sync)
        other = "stable" if self.channel == "unstable" else "unstable"
        yield SystemCommand(f"Channel: {other}", "search another channel", self.cycle_channel)
        yield SystemCommand("Keys", "every key", self.action_help)
        yield SystemCommand("Quit", "leave nixin", self.action_quit_safely)

    def _palette_plan(self) -> None:
        self.action_pending()
        self.call_after_refresh(lambda: self.screen.action_plan())  # type: ignore[attr-defined]

    def _palette_sync(self) -> None:
        self.action_pending()
        self.call_after_refresh(lambda: self.screen.action_sync())  # type: ignore[attr-defined]
