#!/usr/bin/env python3
"""SVG screenshots of the TUI at a phone size (50x25) and a desktop size (120x40).

    nix develop -c python3 scripts/tui-screenshots.py [OUTDIR]
    nix develop -c python3 scripts/tui-screenshots.py --live --config ~/nixos [--host H] [OUTDIR]

Default: the cowsay flow on the recorded documents in tests/fixtures/tui (offline,
reproducible): search, details, the add preview, plan, the apply confirmation,
`needs-sudo`, status; then the Packages page on 19BK's documents: installed, a filter,
updates (lock, --published), the update confirmation; then the Options page: mbp13's
openssh options, one in full, the Set dialog, its diff, Pending after the write, and a
Home Manager option on 19BK; then the Try screen on the recorded `try` documents (a CLI
program, a GUI app with its streamed fetch, the program picker; nothing is launched);
then the Branches page on 19BK's recorded `branches` documents: the list with two
branches ticked, the merge confirmation, and the merge's result with its build log.
--live drives the real verb handlers instead (network for discovery, nix against
--config): search, details with `where`, the `add --dry-run` preview, the Packages page
(`list`, `outdated`), and the Options page (an option, the Set dialog and its `set
--dry-run` diff; with --hm-host a Home Manager option there); it writes nothing (the diff
modals are cancelled, `update` is never run). `--only options` takes only the Options
part, --try only the Try shots, --branches only the Branches shots. OUTDIR defaults to docs/tui.
Files are named <size>-<step>.svg (live-<size>-<step>.svg with --live).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from nixin.tui.app import NixinApp
from nixin.tui.theme import THEME
from nixin.ui.backend import Backend

SIZES = {"phone": (50, 25), "desktop": (120, 40)}


async def settle(app, pilot, t: float = 0.1) -> None:
    await pilot.pause(t)
    await app.workers.wait_for_complete()
    await pilot.pause(0.2)


def slim(svg: str) -> str:
    """Paint Rich's window in the theme background and drop the per-cell rectangles of that
    colour (about half the file). Not `simplify=True`: it merges padding into the next
    segment, and SVG viewers then drop its leading spaces (labels run into their values)."""
    bg = THEME.background.lower()
    svg = re.sub(r'(<rect fill=")#292929(" stroke=)', rf"\g<1>{bg}\g<2>", svg, count=1)
    cell = rf'<rect fill="{bg}" x="[^"]*" y="[^"]*" width="[^"]*" height="[^"]*" '
    return re.sub(cell + r'shape-rendering="crispEdges"/>', "", svg)


def shot(app, out: Path, name: str) -> None:
    svg = slim(app.export_screenshot(title=f"nixin tui — {name}"))
    out.write_text(svg)
    print(f"{out}  {len(svg) // 1024} KiB")


async def fixtures(size: str, outdir: Path) -> None:
    from tui_fake import COWSAY, FakeBackend

    app = NixinApp(FakeBackend(dict(COWSAY)))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press(*"cowsay", "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-1-search.svg", "search")
        await pilot.press("enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-2-details.svg", "details")
        await pilot.press("a")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-3-add-preview.svg", "add preview")
        await pilot.press("w")
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("b")
        await settle(app, pilot, 0.3)
        shot(app, outdir / f"{size}-4-plan.svg", "pending + plan")
        await pilot.press("a")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-5-apply-confirm.svg", "apply")
        await pilot.press("enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-6-needs-sudo.svg", "needs-sudo")
        await pilot.press("escape", "s")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-7-status.svg", "status")


async def packages(size: str, outdir: Path) -> None:
    from tui_fake import PACKAGES, FakeBackend

    app = NixinApp(FakeBackend(dict(PACKAGES), host="19BK"))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "i")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-8-packages.svg", "packages: installed")
        await pilot.press("f", *"git", "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-9-packages-filter.svg", "packages: filter")
        await pilot.press("t")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-10-updates.svg", "packages: updates (lock)")
        await pilot.press("m")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-11-updates-published.svg", "packages: updates (--published)")
        await pilot.press("m", "u")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-12-update-confirm.svg", "update nixpkgs")


async def options(size: str, outdir: Path) -> None:
    from tui_fake import HM_OPTIONS, OPTIONS, FakeBackend

    app = NixinApp(FakeBackend(dict(OPTIONS)))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "o", *"openssh", "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-13-options.svg", "options: NixOS")
        await pilot.press("down", "down", "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-14-option.svg", "option: services.openssh.enable")
        await pilot.press("e")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-15-option-set.svg", "option: set")
        await pilot.press(*["backspace"] * 4, *"false", "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-16-option-diff.svg", "option: set --dry-run")
        await pilot.press("w")
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-17-option-pending.svg", "pending after set")
    app = NixinApp(FakeBackend(dict(HM_OPTIONS), host="19BK"))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "o", *"git", "enter", "k")
        await settle(app, pilot)
        rows = [h.target for h in app.screen.hits]
        await pilot.press(*["down"] * rows.index("programs.git.lfs.enable"), "enter")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-18-option-hm.svg", "option: Home Manager")


async def branches(size: str, outdir: Path) -> None:
    from tui_fake import BRANCHES, FakeBackend

    app = NixinApp(FakeBackend(dict(BRANCHES), host="19BK"))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "g")
        await settle(app, pilot)
        await pilot.press("space", "down", "space", "up", "up")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-22-branches.svg", "branches")
        await pilot.press("m")
        await settle(app, pilot)
        shot(app, outdir / f"{size}-23-merge-confirm.svg", "branches: merge")
        await pilot.press("m")
        await settle(app, pilot, 0.3)
        shot(app, outdir / f"{size}-24-merge-result.svg", "branches: merged")


async def live_options(
    size: str, outdir: Path, host: str, config: str, query: str, option: str, value: str
) -> None:
    """Options against the real config: search, the option, Set, its dry-run diff (cancelled)."""
    app = NixinApp(Backend(host=host, config=config))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "o", *query, "enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-8-options.svg", f"options {query}")
        rows = [h.target for h in app.screen.hits]
        await pilot.press(*["down"] * (rows.index(option) if option in rows else 0), "enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-9-option.svg", option)
        await pilot.press("e")
        await settle(app, pilot)
        shot(app, outdir / f"live-{size}-10-option-set.svg", f"set {option}")
        await pilot.press("ctrl+e", "ctrl+u", *value, "enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-11-option-diff.svg", f"set {option} {value} --dry-run")
        await pilot.press("escape")  # cancel: nothing is written


async def live_hm(size: str, outdir: Path, host: str, config: str, query: str, option: str) -> None:
    app = NixinApp(Backend(host=host, config=config))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press("escape", "o", *query, "enter", "k")
        await settle(app, pilot, 0.5)
        rows = [h.target for h in app.screen.hits]
        await pilot.press(*["down"] * (rows.index(option) if option in rows else 0), "enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-12-option-hm.svg", f"{option} on {host}")


async def trying(size: str, outdir: Path) -> None:
    from tui_fake import FakeBackend

    from nixin.tryout import launch
    from nixin.tui.trying import TryScreen

    launch.SPAWN = lambda argv, *, log: 4242  # the shots show "launched", nothing starts
    routes = {
        "try": "try_cowsay",
        "try gnome-calculator": "try_gnome_calculator",
        "try coreutils": "try_coreutils_ambiguous",
    }
    steps = (("19-try-cli", "cowsay"), ("20-try-gui-app", "gnome-calculator"))
    steps += (("21-try-pick", "coreutils"),)
    for step, target in steps:
        app = NixinApp(FakeBackend(routes))
        async with app.run_test(size=SIZES[size]) as pilot:
            app.push_screen(TryScreen(target))
            await settle(app, pilot)
            shot(app, outdir / f"{size}-{step}.svg", f"try {target}")


async def live(size: str, outdir: Path, host: str, config: str, query: str, pkg: str) -> None:
    app = NixinApp(Backend(host=host, config=config))
    async with app.run_test(size=SIZES[size]) as pilot:
        await pilot.press(*query, "enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-1-search.svg", f"search {query}")
        screen = app.screen
        rows = [h.target for h in screen.hits]
        if pkg in rows:  # move the cursor to the package to look at
            for _ in range(rows.index(pkg)):
                await pilot.press("down")
        await pilot.press("enter")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-2-details.svg", f"details {pkg}")
        await pilot.press("a")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-3-add-preview.svg", f"add {pkg} --dry-run")
        await pilot.press("escape")  # cancel: nothing is written
        await settle(app, pilot)
        await pilot.press("escape", "escape", "i")  # Packages: list, then outdated (reads only)
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-4-packages.svg", "packages: installed")
        await pilot.press("t")
        await settle(app, pilot, 0.5)
        shot(app, outdir / f"live-{size}-5-updates.svg", "packages: updates")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=str(ROOT / "docs" / "tui"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--try", dest="try_only", action="store_true", help="only the Try shots")
    ap.add_argument("--branches", action="store_true", help="only the Branches shots")
    ap.add_argument("--config")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--query", default="cowsay")
    ap.add_argument("--package", default="cowsay")
    ap.add_argument("--option-query", default="openssh")
    ap.add_argument("--option", default="services.openssh.enable")
    ap.add_argument("--value", default="false", help="the Set value previewed (never written)")
    ap.add_argument("--hm-host", help="also a Home Manager option on this host (--live)")
    ap.add_argument("--hm-query", default="git lfs")
    ap.add_argument("--hm-option", default="programs.git.lfs.enable")
    ap.add_argument("--only", choices=["all", "options"], default="all")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        if a.try_only:
            asyncio.run(trying(size, out))
        elif a.branches:
            asyncio.run(branches(size, out))
        elif a.live:
            if a.only == "all":
                asyncio.run(live(size, out, a.host, a.config, a.query, a.package))
            asyncio.run(
                live_options(size, out, a.host, a.config, a.option_query, a.option, a.value)
            )
            if a.hm_host:
                asyncio.run(live_hm(size, out, a.hm_host, a.config, a.hm_query, a.hm_option))
        else:
            if a.only == "all":
                asyncio.run(fixtures(size, out))
                asyncio.run(packages(size, out))
            asyncio.run(options(size, out))
            if a.only == "all":
                asyncio.run(trying(size, out))
                asyncio.run(branches(size, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
