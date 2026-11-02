#!/usr/bin/env python3
"""PNG screenshots of the GUI, drawn offscreen (QT_QPA_PLATFORM=offscreen, software renderer).

    nix develop -c python3 scripts/gui-screenshots.py [OUTDIR]
    nix develop -c python3 scripts/gui-screenshots.py --live --config ~/nixos [--host H] [OUTDIR]

Default: the cowsay flow on the recorded documents in tests/fixtures/tui (offline,
reproducible): search, details, the add preview, plan, the apply confirmation,
`needs-sudo`, status, an option with the Set dialog, an error notice. --live drives the
real verb handlers instead (network for discovery, nix against --config): search,
details with `where`, the `add --dry-run` preview, an option's details and Set dialog; it
writes nothing (the dialogs are cancelled). OUTDIR defaults to docs/gui. Files are named
<n>-<step>.png (live-<n>-<step>.png with --live), 1200x780.
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
    g.search.search("cowsay")
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
    g.search.search("services.openssh.enable")
    s.settle()
    names = [h.target for h in g.search.hits]
    g.search.select(names.index("services.openssh.enable"))
    s.settle()
    g.details.editOption()
    s.shot("8-set-option")
    g.writer.cancelSet()
    g.search.setKind("pkg")
    g.search.search("cowsay")
    s.settle()
    g.search.select(0)
    s.settle()
    g.details.remove()  # rm's preview answers `ambiguous`: the notice with candidates
    s.shot("9-notice")
    s.done()


def live(outdir: Path, host: str, config: str, query: str, pkg: str, option: str) -> None:
    gui = Gui(Backend(host=host, config=config))
    s = Shooter(gui, outdir, "live-")
    g = gui
    g.search.search(query)
    s.shot("1-search")
    rows = [h.target for h in g.search.hits]
    g.search.select(rows.index(pkg) if pkg in rows else 0)
    s.shot("2-details")
    g.details.add()
    s.shot("3-add-preview")
    g.writer.cancel()  # nothing is written
    g.search.setKind("opt")
    g.search.search(option)
    s.settle()
    rows = [h.target for h in g.search.hits]
    g.search.select(rows.index(option) if option in rows else 0)
    s.shot("4-option")
    g.details.editOption()
    s.shot("5-set-option")
    g.writer.cancelSet()
    s.done()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=str(ROOT / "docs" / "gui"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--query", default="cowsay")
    ap.add_argument("--package", default="cowsay")
    ap.add_argument("--option", default="services.openssh.enable")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    if a.live:
        live(out, a.host, a.config, a.query, a.package, a.option)
    else:
        fixtures(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
