"""A Backend that answers from recorded documents (tests/fixtures/tui), for the TUI tests and
scripts/tui-screenshots.py. No nix, no network, no config.

Routes map "verb [word...]" to a fixture name, a document, or a list of them (successive
calls get successive answers; the last repeats). The most specific route whose words all
appear in the call wins. With stream=True a route's `<name>.log` lines are written to
stderr the way plan/apply tee nix's output in human mode.

A call with a `cancel` token (plan, apply from the UIs) shows a build in progress the way
the real verbs do: `LIVE` names the snapshot the token answers while the call runs
(`plan_building`) and the document a stopped run answers (`plan_cancelled`); the call
waits `build_delay` seconds for the token (0: it returns at once, as before). The
snapshot's `remote` block follows the call's build flags (`--remote [--all|--split]`),
as the real tracker's does; without them it is a local build (`remote` null).
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
    "info": "info_ripgrep",
    "versions": "versions_ripgrep",
    "where": "where_ripgrep_19BK",
    "pins": "pins_19BK",
    "pins --check": "pins_19BK_check",
    "pin kernel --dry-run": "pin_kernel_dry",
    "pin kernel": "pin_kernel",
    "pin --dry-run": "pin_firefox_dry",
    "pin": "pin_firefox",
    "unpin --dry-run": "unpin_kernel_dry",
    "unpin": "unpin_kernel",
}
# the Packages screen once something is held: `outdated` answers with holds[] after a pin
HOLDS = {**PACKAGES, "outdated": ["outdated_19BK", "outdated_19BK_holds"]}


# the Branches page: 19BK's ~/nixos branches (recorded on a clone of it, 2026-11-12): the
# list, then the list after merging accent-surfaces + feat/spotify-player-page
BRANCHES = {
    "branches": ["branches_19BK", "branches_19BK_merged"],
    "branches merge --dry-run": "branches_merge_dry",
    "branches merge": "branches_merge",
    "branches merge clash": "branches_merge_conflict",
    "branches prune --dry-run": "branches_prune_dry",
    "branches prune": "branches_prune",
    "status": "status_clean",
}


# the Options page: NixOS options on mbp13 (services.openssh.*, recorded against ~/nixos, the
# writes in a throwaway clone of it) and Home Manager options on 19BK (programs.git.*, user
# brucking, read-only against ~/nixos)
OPTIONS = {
    "search": "options_search_openssh",
    "where": ["options_where_openssh_enable", "options_where_openssh_set"],
    "where services.openssh.ports": "options_where_openssh_ports",
    "set --dry-run": "options_set_dry_openssh",
    "set": "options_set_openssh",
    "unset --dry-run": "options_unset_dry_openssh",
    "unset": "options_unset_openssh",
    "status": "options_status_set",
}
HM_OPTIONS = {
    "search": "options_search_hm_git",
    "where": "options_where_hm_git_lfs",
    "set --dry-run": "options_set_dry_hm_git_lfs",
}


# verb → (the tracker's snapshot while the call runs, the document when it is stopped)
LIVE = {"plan": ("plan_building", "plan_cancelled"), "apply": ("plan_building", "apply_cancelled")}


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SPEC = "ssh-ng://nixremote@100.97.50.80 x86_64-linux /etc/nix/mbp13-builder 2 1 big-parallel"


def remote_block(args: tuple[str, ...]) -> dict[str, Any] | None:
    """The tracker's `remote` for a call's flags, as builders.resolve would fill it."""
    if "--remote" not in args and not any(a.startswith("--builders") for a in args):
        return None
    mode = "all" if "--all" in args else "split" if "--split" in args else "big"
    return {
        "mode": mode,
        "name": "mbp13",
        "builders": SPEC,
        "command": "mbp13-builders" + (f" --{mode}" if mode != "big" else ""),
        "fell_back": False,
    }


class FakeBackend(Backend):
    def __init__(self, routes: dict[str, Any], *, host: str = "mbp13", delay: float = 0.0):
        super().__init__(host=host, config="/home/master/nixos")
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.delay = delay
        self.build_delay = 0.0  # how long a plan/apply with a token stays "building"
        self.streamed: list[str] = []
        self.tokens: list[Any] = []  # the Cancel tokens the UI handed in

    def call(
        self, verb: str, *args: str, stream: bool = False, cancel: Any = None
    ) -> dict[str, Any]:
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
        if cancel is not None and verb in LIVE and "--dry-run" not in args:
            self.tokens.append(cancel)
            building, stopped = LIVE[verb]
            snapshot = {**load(building), "remote": remote_block(args)}
            cancel.step = "build"
            cancel.snapshot = lambda: snapshot
            try:
                if cancel.wait(self.build_delay) and cancel.requested:
                    doc = load(stopped)
            finally:
                cancel.snapshot = None
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


# Browse and the details' app block, on mbp13's documents (recorded 2026-11-02, trimmed)
BROWSE = {
    "browse": "browse_overview",
    "browse AudioVideo": "browse_audiovideo",
    "browse Game": "browse_game",
    "info": "info_prismlauncher",
    "versions": {"ok": True, "results": [], "count": 0, "total": 0, "package": "?"},
    "where": "where_cowsay",
}
