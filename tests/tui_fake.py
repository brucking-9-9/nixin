"""A Backend that answers from recorded documents (tests/fixtures/tui), for the TUI tests and
scripts/tui-screenshots.py. No nix, no network, no config.

Routes map "verb [word...]" to a fixture name, a document, or a list of them (successive
calls get successive answers; the last repeats). The most specific route whose words all
appear in the call wins. With stream=True a route's `<name>.log` lines are written to
stderr the way plan/apply tee nix's output in human mode.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from typing import Any

from nixin.ui.backend import Backend, Call

FIXTURES = Path(__file__).parent / "fixtures" / "tui"

COWSAY = {
    "search": "search_pkg_cowsay",
    "info": "info_cowsay",
    "versions": "versions_cowsay",
    "where": ["where_cowsay", "where_cowsay_added"],
    "add --dry-run": "add_cowsay_dry",
    "add": "add_cowsay",
    "status": ["status_clean", "status_added"],
    "plan": "plan_cowsay",
    "apply --dry-run": "apply_dry_cowsay",
    "apply": "apply_needs_sudo",
    "sync": "sync",
}

# the Packages screen, on 19BK's documents (recorded on mbp13 against ~/nixos, trimmed)
PACKAGES = {
    "list": "list_19BK",
    "outdated": "outdated_19BK",
    "outdated --published": "outdated_19BK_published",
    "update --dry-run": "update_dry",
    "update": "update_ok",
    "status": "status_updated",
    "info": "info_cowsay",
    "versions": "versions_cowsay",
    "where": "where_cowsay",
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeBackend(Backend):
    def __init__(self, routes: dict[str, Any], *, host: str = "mbp13", delay: float = 0.0):
        super().__init__(host=host, config="/home/master/nixos")
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.delay = delay
        self.streamed: list[str] = []

    def call(self, verb: str, *args: str, stream: bool = False) -> dict[str, Any]:
        argv = self.argv(verb, *args)
        words = {verb, *args}
        match = [k for k in self.routes if k.split()[0] == verb and set(k.split()) <= words]
        if not match:
            doc = {"ok": False, "error": {"code": "internal", "message": f"no fixture for {argv}"}}
            name = None
        else:
            answers = self.routes[max(match, key=lambda k: len(k.split()))]
            item = answers.pop(0) if len(answers) > 1 else answers[0]
            name = item if isinstance(item, str) else None
            doc = load(item) if isinstance(item, str) else dict(item)
        if self.delay:
            time.sleep(self.delay)
        log = FIXTURES / f"{name}.log" if name else None
        if stream and log and log.exists():
            for line in log.read_text().splitlines(keepends=True):
                sys.stderr.write(line)
                sys.stderr.flush()
                self.streamed.append(line)
        doc = {**doc, "verb": verb, "host": self.host}
        self.calls.append(Call(argv, doc, 0.0))
        return doc

    def verbs(self) -> list[list[str]]:
        """The calls so far, without the --host/--config head."""
        out = []
        for c in self.calls:
            i = next(i for i, a in enumerate(c.argv) if a == c.doc["verb"])
            out.append(c.argv[i:])
        return out


def screen_text(app) -> str:
    """The current screen as plain text (what export_screenshot draws, without the SVG)."""
    from rich.console import Console

    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
    )
    console.print(
        app.screen._compositor.render_update(full=True, screen_stack=app._background_screens)
    )
    return console.export_text()
