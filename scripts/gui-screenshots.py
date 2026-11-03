#!/usr/bin/env python3
"""PNG screenshots of the GUI, drawn offscreen (QT_QPA_PLATFORM=offscreen, software renderer).

    nix develop -c python3 scripts/gui-screenshots.py [OUTDIR]
    nix develop -c python3 scripts/gui-screenshots.py --live --config ~/nixos [--host H] [OUTDIR]

Default: the cowsay flow on the recorded documents in tests/fixtures/tui (offline,
reproducible): search, details, the add preview, plan, the apply confirmation,
`needs-sudo`, status, an option with the Set dialog, an error notice; then Packages on
19BK's documents: installed with a row in details, updates (lock, --published), the
update confirmation. --live drives the real verb handlers instead (network for
discovery, nix against --config): search, details with `where`, the `add --dry-run`
preview, an option's details and Set dialog, Packages (`list`, `outdated`); it writes
nothing (the dialogs are cancelled, `update` is never run). OUTDIR defaults to docs/gui.
--try: only the Try dialog on the recorded `try` documents (a CLI program opened in the
terminal, a GUI app launched, the program picker); nothing is started.
Files are named <n>-<step>.png (live-<n>-<step>.png with --live), 1200x780.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from nixin.gui.app import Window, application
from nixin.gui.bridge import Gui
from nixin.ui.backend import Backend

SIZE = (1200, 780)


class Shooter:
    def __init__(self, gui: Gui, outdir: Path, prefix: str = "") -> None:
        self.app = application()
        self.gui = gui
        self.win = Window(gui)
        assert self.win.window is not None, self.win.errors
        self.win.window.resize(*SIZE)
        self.outdir, self.prefix = outdir, prefix

    def settle(self, t: float = 0.3, limit: float = 300.0) -> None:
        """Until every verb has answered, then `t` seconds more for the transitions."""
        start = time.monotonic()
        while not self.gui.idle and time.monotonic() - start < limit:
            self.app.processEvents()
            time.sleep(0.02)
        end = time.monotonic() + t
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def search(self, query: str) -> None:
        """As typed: the query in the field, then the search."""
        field = self.win.find("query")
        assert field is not None
        field.setProperty("text", query)
        self.gui.search.search(query)
        self.settle()

    def shot(self, name: str) -> None:
        self.settle()
        path = self.outdir / f"{self.prefix}{name}.png"
        self.win.grab().save(str(path), "PNG", 0)
        print(f"{path}  {path.stat().st_size // 1024} KiB")

    def done(self) -> None:
        if self.win.errors:
            print("QML warnings:", *self.win.errors, sep="\n  ", file=sys.stderr)
        self.win.close()
        self.gui.close()


def fixtures(outdir: Path) -> None:
    from tui_fake import COWSAY, FakeBackend

    routes = {
        **COWSAY,
        "search --kind opt": "search_all_openssh",
        "info --kind opt": "info_openssh_enable",
        "where --kind option": "where_openssh_enable",
        "rm --dry-run": "rm_git_ambiguous",
    }
    gui = Gui(FakeBackend(routes))
    s = Shooter(gui, outdir)
    g = gui
    s.search("cowsay")
    s.shot("1-search")
    g.search.select(0)
    s.shot("2-details")
    g.details.add()
    s.shot("3-add-preview")
    g.writer.write()
    s.settle()
    g.go("pending")
    s.settle()
    g.pending.startPlan()
    s.shot("4-plan")
    g.apply.start()
    s.shot("5-apply-confirm")
    g.apply.confirm("")
    s.shot("6-needs-sudo")
    g.apply.close()
    g.notice.dismiss()
    g.go("status")
    s.shot("7-status")
    g.go("search")
    g.search.setKind("opt")
    s.search("services.openssh.enable")
    names = [h.target for h in g.search.hits]
    g.search.select(names.index("services.openssh.enable"))
    s.settle()
    g.details.editOption()
    s.shot("8-set-option")
    g.writer.cancelSet()
    g.search.setKind("pkg")
    s.search("cowsay")
    g.search.select(0)
    s.settle()
    g.details.remove()  # rm's preview answers `ambiguous`: the notice with candidates
    s.shot("9-notice")
    s.done()
    packages(outdir)


def packages(outdir: Path) -> None:
    from tui_fake import PACKAGES, FakeBackend

    gui = Gui(FakeBackend(dict(PACKAGES), host="19BK"))
    s = Shooter(gui, outdir)
    g = gui
    g.go("packages")
    s.settle()
    names = [r.name for r in g.packages.shown]
    g.packages.select(names.index("ripgrep"))
    s.shot("10-packages")
    g.packages.setTab("updates")
    s.settle()
    s.shot("11-updates")
    g.packages.setMode("published")
    s.shot("12-updates-published")
    g.packages.setMode("lock")
    s.settle()
    g.packages.startUpdate()
    s.shot("13-update-confirm")
    g.packages.cancelUpdate()
    s.done()


def trying(outdir: Path) -> None:
    from tui_fake import COWSAY, FakeBackend

    from nixin.tryout import launch

    launch.SPAWN = lambda argv, *, log: 4242  # the shots say "launched", nothing starts
    routes = {
        **COWSAY,
        "try": "try_cowsay",
        "try gnome-calculator": "try_gnome_calculator",
        "try coreutils": "try_coreutils_ambiguous",
    }
    gui = Gui(FakeBackend(routes))
    s = Shooter(gui, outdir)
    g = gui
    s.search("cowsay")
    g.search.select(0)
    s.settle()
    for step, target in (("14-try-cli", "cowsay"), ("15-try-gui-app", "gnome-calculator")):
        g.trying.start(target)
        s.shot(step)
        g.trying.close()
        s.settle()
    g.trying.start("coreutils")
    s.shot("16-try-pick")
    g.trying.close()
    s.done()


def live(outdir: Path, host: str, config: str, query: str, pkg: str, option: str) -> None:
    gui = Gui(Backend(host=host, config=config))
    s = Shooter(gui, outdir, "live-")
    g = gui
    s.search(query)
    s.shot("1-search")
    rows = [h.target for h in g.search.hits]
    g.search.select(rows.index(pkg) if pkg in rows else 0)
    s.shot("2-details")
    g.details.add()
    s.shot("3-add-preview")
    g.writer.cancel()  # nothing is written
    g.search.setKind("opt")
    s.search(option)
    rows = [h.target for h in g.search.hits]
    g.search.select(rows.index(option) if option in rows else 0)
    s.shot("4-option")
    g.details.editOption()
    s.shot("5-set-option")
    g.writer.cancelSet()
    g.go("packages")  # list, then outdated: they read the config, nothing more
    s.settle(1.0)
    if g.packages.shown:
        g.packages.select(0)
    s.shot("6-packages")
    g.packages.setTab("updates")
    s.shot("7-updates")
    s.done()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=str(ROOT / "docs" / "gui"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--try", dest="try_only", action="store_true", help="only the Try shots")
    ap.add_argument("--config")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--query", default="cowsay")
    ap.add_argument("--package", default="cowsay")
    ap.add_argument("--option", default="services.openssh.enable")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    if a.try_only:
        trying(out)
    elif a.live:
        live(out, a.host, a.config, a.query, a.package, a.option)
    else:
        fixtures(out)
        trying(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
