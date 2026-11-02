#!/usr/bin/env python3
"""SVG screenshots of the TUI at a phone size (50x25) and a desktop size (120x40).

    nix develop -c python3 scripts/tui-screenshots.py [OUTDIR]
    nix develop -c python3 scripts/tui-screenshots.py --live --config ~/nixos [--host H] [OUTDIR]

Default: the cowsay flow on the recorded documents in tests/fixtures/tui (offline,
reproducible): search, details, the add preview, plan, the apply confirmation,
`needs-sudo`, status. --live drives the real verb handlers instead (network for
discovery, nix against --config): search, details with `where`, and the `add --dry-run`
preview; it writes nothing (the diff modal is cancelled). OUTDIR defaults to docs/tui.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=str(ROOT / "docs" / "tui"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--host", default=socket.gethostname())
    ap.add_argument("--query", default="cowsay")
    ap.add_argument("--package", default="cowsay")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        if a.live:
            asyncio.run(live(size, out, a.host, a.config, a.query, a.package))
        else:
            asyncio.run(fixtures(size, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
