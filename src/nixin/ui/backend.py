"""The front ends' only data path: the CLI's verb handlers, called in-process.

`Backend.call("search", "firefox", "--kind", "all")` builds the same argv a person would
type, parses it with the CLI's own parser and runs the verb through `cli.document`, so
the result is the document `nixin --json …` prints (envelope keys included), with no
subprocess and no second implementation. Calls block: the UIs run them in worker
threads (Textual workers in the TUI, QThreadPool runnables in the GUI).

`stream=True` runs the verb in human mode. plan, apply and try then tee nix's build output
and nixos-rebuild's log to `sys.stderr` (try is called with --build-only: it must not hand
the UI's terminal to the program); the UI routes that into its log view (the TUI with
App.begin_capture_print, the GUI with a sys.stderr proxy). The document is the same apart
from `message`, the human rendering, which the UIs ignore.

`cancel=` hands plan/apply a `Cancel` token (nixin.apply.proc): the verb puts the build
tracker's snapshot and its current step on it while it runs, and `request()` stops the
build (the Stop button). A token made with `watched=True` tells the verb a UI shows the
tracker, so no status line goes to stderr.

Tests replace `Backend` with a fake that answers from recorded documents.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .. import cli
from ..apply.proc import Cancel

# verbs whose human mode streams a log worth showing (CONTRACT.md: the slow ones)
STREAMING = frozenset({"plan", "apply", "try"})
# verbs that read --config (discovery ignores it)
CONFIG_VERBS = frozenset(
    {"status", "where", "list", "add", "rm", "set", "unset", "plan", "apply", "sync"}
    | {"outdated", "update", "try", "info"}  # info: the lock its AppStream lookup evaluates
)


@dataclass
class Call:
    """One finished call, for the session log and the tests."""

    argv: list[str]
    doc: dict[str, Any]
    seconds: float


@dataclass
class Backend:
    host: str
    config: str | None = None
    calls: list[Call] = field(default_factory=list)

    @property
    def config_label(self) -> str:
        """The config the truth/writer/apply verbs will use, as CONTRACT.md resolves it."""
        raw = self.config or os.environ.get("NIXIN_CONFIG") or "/etc/nixos"
        home = os.path.expanduser("~")
        path = os.path.abspath(os.path.expanduser(raw))
        return "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path

    def argv(self, verb: str, *args: str) -> list[str]:
        head = ["--host", self.host]
        if self.config and verb in CONFIG_VERBS:
            head += ["--config", self.config]
        return [*head, verb, *args]

    def call(
        self, verb: str, *args: str, stream: bool = False, cancel: Cancel | None = None
    ) -> dict[str, Any]:
        argv = self.argv(verb, *args)
        t0 = time.monotonic()
        doc = run(argv, stream=stream, cancel=cancel)
        self.calls.append(Call(argv, doc, round(time.monotonic() - t0, 2)))
        return doc


def run(argv: list[str], *, stream: bool = False, cancel: Cancel | None = None) -> dict[str, Any]:
    """What `nixin --json ARGV` prints, as a dict. `stream`: human mode (see the module doc);
    `cancel`: the run's stop token (plan, apply)."""
    try:
        ns = cli._parser().parse_args(argv)
    except cli.UsageError as e:
        verb = next((a for a in argv if a in cli.VERBS), None)
        host = argv[argv.index("--host") + 1] if "--host" in argv else None
        return {"ok": False, "verb": verb, "host": host, "error": usage(str(e))}
    ns.json = not stream
    if cancel is not None:
        ns.cancel = cancel
    doc = cli.document(ns)
    return {"verb": ns.verb, "host": ns.host, **doc}


def usage(message: str) -> dict[str, str]:
    return {"code": "usage", "message": message}


def shell(argv: list[str]) -> str:
    """The CLI call a person would type for argv (shown in the UI next to slow work)."""
    from ..apply.proc import shown

    return "nixin " + shown(argv)
