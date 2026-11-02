"""nixin gui — a desktop UI (Qt 6, QML) over the same documents the CLI prints with --json.

    nixin [--host H] [--config DIR] gui [--channel C] [--self-test]
    nixin-gui [--host H] [--config DIR] [--channel C]

Modules (the data path and the view-model are shared with the TUI: nixin.ui):

    bridge          the state and the flows as Qt objects, one per area: what QML binds to
    models          view-model rows as QAbstractListModels; the streamed log
    jobs            verbs on the thread pool; plan/apply's log routed from sys.stderr
    app             the application, the engine, the window; the self-test
    qml/Nixin       the window, the pages, the dialogs; Theme.qml (the palette)
    qml/NixinStyle  the Qt Quick Controls style (Button, TextField, … in the palette)

PySide6 is imported only when the verb runs, so the other verbs do not pay for it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

HELP = "desktop UI (Qt) over the same documents (search, details, add/rm/set, plan, apply)"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.choices.get("gui")
    if p is None:
        p = sub.add_parser("gui", help=HELP)
        p.add_argument("args", nargs="*")
    p.add_argument("--config", help="config flake / workspace clone (default: $NIXIN_CONFIG)")
    p.add_argument(
        "--channel", default="unstable", metavar="C", help="search channel to start with"
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="load the window, draw one frame and quit (checks the Qt setup)",
    )
    p.set_defaults(run=run)


def has_display() -> bool:
    return any(os.environ.get(v) for v in ("WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM"))


def run(ns: argparse.Namespace) -> dict[str, Any]:
    if getattr(ns, "args", None):
        return _usage(f"gui takes no arguments (got {' '.join(ns.args)!r})")
    if ns.json:
        return _usage("gui is interactive and has no --json form; the other verbs have one")
    if not has_display():
        return _usage(
            "gui needs a display: WAYLAND_DISPLAY or DISPLAY "
            "(QT_QPA_PLATFORM=offscreen draws nowhere, for tests)"
        )
    try:
        from .app import run_app
    except ImportError as exc:
        return {
            "ok": False,
            "error": {"code": "internal", "message": f"the GUI needs PySide6: {exc}"},
        }
    from ..ui.backend import Backend

    # A git fetch (sync) must not wait for a password on a terminal nobody looks at.
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    return run_app(
        Backend(host=ns.host, config=ns.config), channel=ns.channel, self_test=ns.self_test
    )


def _usage(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "usage", "message": message}}


def main(argv: list[str] | None = None) -> int:
    """`nixin-gui ARGS` = `nixin gui ARGS`."""
    from ..cli import main as cli_main

    return cli_main(["gui", *(sys.argv[1:] if argv is None else argv)])
