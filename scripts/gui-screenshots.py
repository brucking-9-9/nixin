#!/usr/bin/env python3
"""PNG screenshots of the GUI, drawn offscreen (QT_QPA_PLATFORM=offscreen, software renderer).

    nix develop -c python3 scripts/gui-screenshots.py [OUTDIR]
    nix develop -c python3 scripts/gui-screenshots.py --live --config ~/nixos [--host H] [OUTDIR]

Default: the cowsay flow on the recorded documents in tests/fixtures/tui (offline,
reproducible): search, details, the add preview, plan, the apply confirmation,
`needs-sudo`, status, an option with the Set dialog, an error notice; then Packages on
19BK's documents: installed with a row in details, updates (lock, --published), the
update confirmation; then Options: mbp13's openssh options with one in full, the Set
dialog, its diff, and a Home Manager option on 19BK. --live drives the real verb handlers
instead (network for discovery, nix against --config): search, details with `where`, the
`add --dry-run` preview, an option's details and Set dialog, Packages (`list`,
`outdated`), the Options page (an option, Set, the `set --dry-run` diff; with --hm-host a
Home Manager option there); it writes nothing (the dialogs are cancelled, `update` is
never run). `--only options` takes only the Options part. OUTDIR defaults to docs/gui.
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

    def option_search(self, query: str) -> None:
        field = self.win.find("opQuery")
        assert field is not None
        field.setProperty("text", query)
        self.gui.options.search(query)
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


def options(outdir: Path) -> None:
    from tui_fake import HM_OPTIONS, OPTIONS, FakeBackend

    gui = Gui(FakeBackend(dict(OPTIONS)))
    s = Shooter(gui, outdir)
    g = gui
    g.go("options")
    s.option_search("openssh")
    g.options.select([h.target for h in g.options.hits].index("services.openssh.enable"))
    s.shot("14-options")
    g.options.editOption()
    s.shot("15-option-set")
    g.writer.previewSet("false")
    s.shot("16-option-diff")
    g.writer.cancel()
    s.done()
    gui = Gui(FakeBackend(dict(HM_OPTIONS), host="19BK"))
    s = Shooter(gui, outdir)
    g = gui
    g.go("options")
    g.options.setKind("hm")
    s.option_search("git")
    g.options.select([h.target for h in g.options.hits].index("programs.git.lfs.enable"))
    s.shot("17-options-hm")
    s.done()


def live_options(
    outdir: Path, host: str, config: str, query: str, option: str, value: str, hm: tuple | None
) -> None:
    """The Options page against the real config; the Set preview is cancelled."""
    gui = Gui(Backend(host=host, config=config))
    s = Shooter(gui, outdir, "live-")
    g = gui
    g.go("options")
    s.option_search(query)
    rows = [h.target for h in g.options.hits]
    g.options.select(rows.index(option) if option in rows else 0)
    s.shot("10-options")
    g.options.editOption()
    s.shot("11-option-set")
    g.writer.previewSet(value)
    s.shot("12-option-diff")
    g.writer.cancel()  # nothing is written
    s.done()
    if hm is None:
        return
    hm_host, hm_query, hm_option = hm
    gui = Gui(Backend(host=hm_host, config=config))
    s = Shooter(gui, outdir, "live-")
    g = gui
    g.go("options")
    g.options.setKind("hm")
    s.option_search(hm_query)
    rows = [h.target for h in g.options.hits]
    g.options.select(rows.index(hm_option) if hm_option in rows else 0)
    s.shot("13-options-hm")
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
    ap.add_argument("--config")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--query", default="cowsay")
    ap.add_argument("--package", default="cowsay")
    ap.add_argument("--option", default="services.openssh.enable")
    ap.add_argument("--option-query", default="openssh")
    ap.add_argument("--value", default="false", help="the Set value previewed (never written)")
    ap.add_argument("--hm-host", help="also a Home Manager option on this host (--live)")
    ap.add_argument("--hm-query", default="git lfs")
    ap.add_argument("--hm-option", default="programs.git.lfs.enable")
    ap.add_argument("--only", choices=["all", "options"], default="all")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    if a.live:
        if a.only == "all":
            live(out, a.host, a.config, a.query, a.package, a.option)
        hm = (a.hm_host, a.hm_query, a.hm_option) if a.hm_host else None
        live_options(out, a.host, a.config, a.option_query, a.option, a.value, hm)
    else:
        if a.only == "all":
            fixtures(out)
        options(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
