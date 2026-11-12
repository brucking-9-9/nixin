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
from ..apply.proc import Cancel
from ..ui import view
from ..ui.backend import Backend
from .branches import BranchesScreen
from .modals import ApplyModal, DiffModal, HelpModal, NoticeModal, SetOptionModal, UpdateModal
from .options import OptionsScreen
from .packages import PackagesScreen
from .screens import ApplyScreen, PendingScreen, SearchScreen, StatusScreen, TopBar
from .theme import THEME
from .widgets import LogView, Spinner

Doc = dict[str, Any]

WRITE_VERBS = ("add", "rm", "set", "unset", "pin", "unpin")
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
        # ctrl+5: ctrl+] without the kitty protocol
        Binding("o,ctrl+5,ctrl+right_square_bracket", "options", "Options"),
        Binding("p", "pending", "Pending"),
        Binding("s", "status", "Status"),
        # ctrl+7: a terminal without the kitty keyboard protocol sends 0x1f, ctrl+_
        Binding("g,ctrl+7,ctrl+underscore", "branches", "Branches"),
        # C: the last run's error and log, as text to paste (screens with a log copy theirs)
        Binding("C,shift+c", "copy_last", "Copy", show=False),
        Binding("i,ctrl+4,ctrl+backslash", "go('packages')", "Packages", group=PAGE_KEYS),
        Binding("o,ctrl+5,ctrl+right_square_bracket", "go('options')", "Options", group=PAGE_KEYS),
        Binding("p", "go('pending')", "Pending", group=PAGE_KEYS),
        Binding("s", "go('status')", "Status", group=PAGE_KEYS),
        Binding("g,ctrl+7,ctrl+underscore", "go('branches')", "Branches", group=PAGE_KEYS),
    ]

    def __init__(self, backend: Backend, *, channel: str = "unstable") -> None:
        super().__init__()
        self.backend = backend
        self.channel = channel
        self.busy = ""  # "writing" | "planning" | "applying" while one runs
        self.cancel_token: Cancel | None = None  # the busy run's stop token (plan, apply)
        self.writes: list[str] = []  # writer_summary of each write this session
        self.applied: list[Doc] = []  # apply documents this session
        self.status_doc: Doc | None = None
        self.status_stale = True
        self.plan_doc: Doc | None = None
        self.list_doc: Doc | None = None
        self.list_stale = True
        self.outdated_docs: dict[str, Doc] = {}  # mode (lock | published) → document
        self.pins_doc: Doc | None = None  # the Pins tab's last ok `pins` document
        self.branches_doc: Doc | None = None  # the Branches page's last ok list
        # the last run's document and its streamed log (plain): what C copies anywhere
        self.last_run: tuple[Doc, str] | None = None
        self.last_failure: tuple[Doc, str] | None = None
        # where plan and apply build (Pending's `w`): a mode of view.build_choices, for the session
        self.build_choices = view.build_choices(backend.config)
        self.build_where = self.build_choices[0][0]
        self.options_last: tuple[str, str, Doc] | None = None  # the Options page's query, kind, doc
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
        cancel: Cancel | None = None,
    ) -> Worker | None:
        """Run `verb args` in a worker thread; `done(doc)` on the UI thread unless a newer
        call in the same exclusive group superseded it. `stream`: the verb's log goes to
        `log` while it runs. `busy` marks a run that must not overlap another. `cancel`:
        the run's stop token (plan, apply): the screen reads the build tracker from it and
        `stop()` asks it to stop."""
        if busy:
            if self.busy:
                self.notify(f"wait: {self.busy} is still running", severity="warning")
                return None
            self.set_busy(busy)
        self.cancel_token = cancel if busy else self.cancel_token
        if spinner is not None:
            spinner.start(label or verb, hint)
        if log is not None:
            self.begin_capture_print(log, stdout=False, stderr=True)

        def work() -> Doc:
            try:
                doc = self.backend.call(verb, *args, stream=stream, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 -- a bug in the shim must not kill the UI
                doc = {"verb": verb, "host": self.backend.host, **cli._internal(exc)}
            if not get_current_worker().is_cancelled:
                self.call_from_thread(finish, doc)
            return doc

        def finish(doc: Doc) -> None:
            if log is not None:
                log.flush_partial()
                self.end_capture_print(log)
            self.last_run = (doc, log.plain if log is not None else "")
            if not doc.get("ok"):
                self.last_failure = self.last_run
            if spinner is not None:
                spinner.stop()
            if busy:
                self.set_busy("")
                self.cancel_token = None
            done(doc)

        return self.run_worker(
            work, thread=True, group=group or verb, exclusive=exclusive, exit_on_error=False
        )

    def set_busy(self, what: str) -> None:
        self.busy = what
        for bar in self.screen.query(TopBar):
            bar.refresh_bar()

    def stop(self) -> None:
        """Stop the running plan or apply (its build); says why when that is refused."""
        token = self.cancel_token
        if token is None:
            self.notify("nothing is running that could be stopped", severity="information")
            return
        if token.request():
            self.notify("stopping: waiting for nix to clean up…", severity="warning")
        else:
            self.notify(token.refused, severity="warning")

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
            if verb in ("pin", "unpin"):
                self.outdated_docs.clear()
                self.pins_doc = None
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
        self,
        option: str,
        value: str,
        user: str | None,
        home: list[str],
        *,
        hint: list[view.Field] | None = None,
        path: bool = False,
        after=None,
    ) -> None:
        """The value dialog (`hint`: the type and example under it; `path`: the option name is
        a template to fill in), then the writer flow for `set`."""

        def entered(answer: tuple[str, str] | None) -> None:
            if answer:
                self.write("set", [answer[0], answer[1], *home], after=after)

        self.push_screen(SetOptionModal(option, value, user, hint=hint, path=path), entered)

    # --- packages: list, outdated, update ------------------------------------------------------

    def list_loaded(self, doc: Doc) -> None:
        self.list_doc = doc
        self.list_stale = False

    def updated(self, doc: Doc) -> None:
        """An `update` ran: a moved lock makes every package document stale."""
        if doc.get("ok") and doc.get("changed"):
            self.writes.append(view.update_summary(doc))
            self.outdated_docs.clear()
            self.pins_doc = None  # stale pins are no longer stale
            self.list_stale = True
            self.status_stale = True
            self.plan_doc = None

    def undo_update(self, *, after: Callable[[], None] | None = None) -> None:
        """`update --undo --dry-run` → confirm → `update --undo`: flake.lock back to HEAD's."""
        if self.busy:
            self.notify(f"wait: {self.busy} is still running", severity="warning")
            return

        def dry(d: Doc) -> None:
            if not d.get("ok"):
                self.push_screen(NoticeModal(d, "update --undo"))
                return
            self.push_screen(UpdateModal(d), lambda ok: ok and run())

        def run() -> None:
            self.fetch(
                "update",
                "--undo",
                done=done,
                busy="updating",
                label="update --undo: git checkout HEAD -- flake.lock",
                hint="instant",
            )

        def done(doc: Doc) -> None:
            if not doc.get("ok"):
                self.push_screen(NoticeModal(doc, "update --undo"))
                return
            self.updated(doc)
            self.notify(view.update_summary(doc), title="undone")
            if after:
                after()

        self.fetch(
            "update",
            "--undo",
            "--dry-run",
            done=dry,
            busy="checking",
            label="update --undo --dry-run",
        )

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

        self.fetch(
            "apply",
            "--dry-run",
            *self.build_args(),
            done=dry,
            busy="checking",
            label="apply --dry-run",
        )

    # --- copy: errors and logs out of the TUI ------------------------------------------------

    def copy_report(self, doc: Doc | None, log: str = "") -> bool:
        """Put view.copy_text(doc, log) on the clipboard (OSC 52); False when it is empty."""
        text = view.copy_text(doc, log)
        if not text.strip():
            return False
        self.copy_to_clipboard(text)
        n = len(text.splitlines())
        self.notify(f"copied {n} line{'s' if n != 1 else ''} (OSC 52: the terminal must allow it)")
        return True

    def action_copy_last(self) -> None:
        """The most recent failure (its error document and log), else the last run's log."""
        for run in (self.last_failure, self.last_run):
            if run is not None and self.copy_report(*run):
                return
        self.notify("nothing to copy yet: no error, no log", severity="information")

    # --- where the build runs -------------------------------------------------------------

    @property
    def build_where_label(self) -> str:
        return dict(self.build_choices)[self.build_where]

    def build_args(self) -> list[str]:
        """The plan/apply flags of the selected build location (none for local)."""
        return view.build_flags(self.build_where)

    def next_build_where(self) -> str:
        """Cycle the location: local → the remote's big jobs → everything → split."""
        modes = [m for m, _ in self.build_choices]
        self.build_where = modes[(modes.index(self.build_where) + 1) % len(modes)]
        return self.build_where_label

    def applied_doc(self, doc: Doc) -> None:
        if doc.get("ok"):
            self.applied.append(doc)
            self.status_stale = True
            self.plan_doc = None  # it compared against the system that ran before

    # --- navigation ----------------------------------------------------------------------------

    PAGES: ClassVar[dict[str, type[Screen]]] = {
        "packages": PackagesScreen,
        "options": OptionsScreen,
        "pending": PendingScreen,
        "status": StatusScreen,
        "branches": BranchesScreen,
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

    def action_options(self) -> None:
        self._go(OptionsScreen)

    def action_go(self, page: str) -> None:
        self._go(self.PAGES[page])

    def action_status(self) -> None:
        self._go(StatusScreen)

    def action_branches(self) -> None:
        self._go(BranchesScreen)

    def branches_changed(self) -> None:
        """A merge or a prune ran: the list, status and the plan are stale."""
        self.branches_doc = None
        self.status_stale = True
        self.list_stale = True
        self.plan_doc = None

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpModal):
            self.push_screen(HelpModal())

    def action_quit_safely(self) -> None:
        if self.busy in ("writing", "updating", "applying", "merging", "pruning"):
            self.notify(f"{self.busy} is running; ctrl+q quits anyway", severity="warning")
            return
        self.exit(view.farewell(self.writes, self.applied))

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield SystemCommand("Search", "find packages and options", self.action_search)
        yield SystemCommand(
            "Packages", "installed (list), updates (outdated), pins", self.action_packages
        )
        yield SystemCommand(
            "Options", "NixOS and Home Manager options: browse, set, unset", self.action_options
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
        yield SystemCommand(
            "Branches", "the config's branches: merge into main, prune", self.action_branches
        )
        yield SystemCommand("Copy", "the last error and log, to paste", self.action_copy_last)
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
