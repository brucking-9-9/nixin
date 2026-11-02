"""nixin tui — a terminal UI over the same documents the CLI prints with --json.

    nixin [--host H] [--config DIR] tui [--channel C]
    nixin-tui [--host H] [--config DIR] [--channel C]

Modules:

    view      documents → display rows (pure; the GUI mirrors it)
    backend   the verb handlers, called in-process (the TUI's only data path)
    theme     the palette, as a Textual theme
    app       the App: global keys, command palette, the writer and apply flows
    screens   Search → Details → Pending → Apply, and Status
    modals    diff preview, set option, apply confirm, notices, keys
    widgets   small renderers of view-model data

Textual is imported only when the verb runs, so the other verbs do not pay for it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

HELP = "interactive terminal UI over the same documents (search, details, add/rm/set, plan, apply)"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.choices.get("tui")
    if p is None:
        p = sub.add_parser("tui", help=HELP)
        p.add_argument("args", nargs="*")
    p.add_argument("--config", help="config flake / workspace clone (default: $NIXIN_CONFIG)")
    p.add_argument(
        "--channel", default="unstable", metavar="C", help="search channel to start with"
    )
    p.set_defaults(run=run)


def run(ns: argparse.Namespace) -> dict[str, Any]:
    if getattr(ns, "args", None):
        return _usage(f"tui takes no arguments (got {' '.join(ns.args)!r})")
    if ns.json:
        return _usage("tui is interactive and has no --json form; the other verbs have one")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _usage("tui needs a terminal (stdin and stdout must be a tty)")
    from .app import NixinApp
    from .backend import Backend

    # While the TUI owns the terminal nothing may prompt on it (sync's git fetch).
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    app = NixinApp(Backend(host=ns.host, config=ns.config), channel=ns.channel)
    farewell = app.run()
    return {"ok": True, "message": farewell or "nixin tui: closed"}


def _usage(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "usage", "message": message}}


def main(argv: list[str] | None = None) -> int:
    """`nixin-tui ARGS` = `nixin tui ARGS`."""
    from ..cli import main as cli_main

    return cli_main(["tui", *(sys.argv[1:] if argv is None else argv)])
