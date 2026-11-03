"""Try in the front ends, offline: the view-model on recorded `try` documents, the TUI's Try
screen driven with App.run_test() (both sizes), and the GUI's Try dialog offscreen.

Documents (tests/fixtures/tui/try_*.json, recorded on mbp13 on 2026-11-02 against
~/nixos's lock, nixpkgs 3ec6363, with `nixin --json try … --config ~/nixos`):

    try_cowsay                 a CLI program, already in the store
    try_gnome_calculator       a GUI app; `cached` set false and `fetched` filled in to match
                               its first run (it was in the store by the recording);
                               try_gnome_calculator.log is that run's streamed lines
    try_coreutils_ambiguous    108 programs, no mainProgram (programs[] cut to 21 of them)
    try_coreutils_ls           the same with --program ls (programs[] cut the same way)
    try_steam_unfree           the unfree refusal

Nothing is launched: launch.SPAWN is replaced by a recorder.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import OptionList, TextArea
from tui_fake import COWSAY, FakeBackend, load, screen_text

from nixin.tryout import launch
from nixin.tui.app import NixinApp
from nixin.tui.screens import DetailsScreen
from nixin.tui.trying import TryScreen
from nixin.tui.widgets import Fields, Line, LogView, NoticeBox
from nixin.ui import view

PHONE = (50, 25)
DESKTOP = (120, 40)
REF = "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
TRY = {
    "try": "try_cowsay",
    "try gnome-calculator": "try_gnome_calculator",
    "try coreutils": "try_coreutils_ambiguous",
    "try coreutils --program": "try_coreutils_ls",
    "try steam": "try_steam_unfree",
}


@pytest.fixture
def spawned(monkeypatch, tmp_path):
    """launch.SPAWN recorded (nothing starts); XDG_CACHE_HOME in tmp; a display by default."""
    seen: list[list[str]] = []
    monkeypatch.setattr(launch, "SPAWN", lambda argv, *, log: seen.append(argv) or 4242)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "no-terminals"))  # no terminal is "installed"
    return seen


# --- view-model ----------------------------------------------------------------------------------


def test_try_view_cli_program():
    doc = load("try_cowsay")
    assert view.try_head(doc) == (
        "ready: cowsay 3.8.4 · 106.9 MiB with its dependencies · already in the store"
    )
    fields = {f.label: f for f in view.try_fields(doc)}
    assert fields["package"].value == "cowsay 3.8.4"
    assert fields["programs"].value == "cowsay (runs), cowthink"
    assert fields["kind"].value == "command-line program"
    assert fields["from"].value == "nixpkgs 3ec6363 (the lock of /home/master/nixos)"
    assert view.try_commands(doc) == [f"nix run {REF}#cowsay", f"nix shell {REF}#cowsay"]
    assert view.try_next(doc, display=True, terminal=False) == "command"
    assert view.try_next(doc, display=True, terminal=True) == "terminal"


def test_try_view_gui_app():
    doc = load("try_gnome_calculator")
    assert "fetched 3 paths, 2.3 MiB download" in view.try_head(doc)
    kind = next(f for f in view.try_fields(doc) if f.label == "kind")
    assert kind.value == "graphical app (Calculator)" and kind.style == "accent"
    assert view.try_next(doc, display=True, terminal=True) == "launch"
    assert view.try_next(doc, display=False, terminal=True) == "terminal"
    assert view.try_next(doc, display=False, terminal=False) == "command"


def test_try_view_errors():
    amb = load("try_coreutils_ambiguous")
    assert "ls" in view.try_programs(amb) and len(view.try_programs(amb)) == 21
    assert view.try_programs(load("try_cowsay")) == []
    unfree = view.notice(load("try_steam_unfree"))
    assert unfree.code == "unfree" and unfree.commands[0] == (
        "NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure"
    )
    assert "Run:" not in unfree.brief and unfree.brief.startswith("steam is unfree")
    missing = view.notice(
        {"ok": False, "error": {"code": "not-found", "message": "m", "suggestions": ["cowsay"]}}
    )
    assert missing.items == ["did you mean cowsay"]


# --- the TUI -------------------------------------------------------------------------------------


def drive(script, routes=None, size=DESKTOP):
    fake = FakeBackend({**COWSAY, **TRY, **(routes or {})}, host="mbp13")
    app = NixinApp(fake)

    async def go():
        async with app.run_test(size=size) as pilot:
            await script(app, pilot, fake)

    asyncio.run(go())
    return app, fake


async def settle(app, pilot, t: float = 0.0):
    await pilot.pause(t)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def open_try(app, pilot, target: str) -> TryScreen:
    app.push_screen(TryScreen(target))
    await settle(app, pilot, 0.05)
    assert isinstance(app.screen, TryScreen)
    return app.screen


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_tui_details_try_cli_program(size, spawned):
    async def script(app, pilot, fake):
        await pilot.press(*"cowsay", "enter")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)
        assert "Try (t)" in screen_text(app)
        await pilot.press("t")
        await settle(app, pilot, 0.05)
        screen = app.screen
        assert isinstance(screen, TryScreen)
        head = screen.query_one("#try-head", Line).value
        assert head.startswith("ready: cowsay 3.8.4")
        assert "cowsay (runs), cowthink" in screen.query_one("#result", Fields).text
        cmds = screen.query_one("#commands", TextArea)
        assert cmds.display and cmds.text.splitlines() == [
            f"nix run {REF}#cowsay",
            f"nix shell {REF}#cowsay",
        ]
        assert "another terminal" in screen.query_one("#try-next", Line).value
        await pilot.press("c")
        assert app.clipboard == f"nix run {REF}#cowsay"
        await pilot.press("l")  # not a GUI app: nothing to launch
        await pilot.press("escape")
        await settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)

    _, fake = drive(script, size=size)
    assert fake.verbs()[-1] == ["try", "cowsay", "--build-only"]
    assert spawned == []


def test_tui_try_gui_app_launches_detached(spawned):
    async def script(app, pilot, fake):
        screen = await open_try(app, pilot, "gnome-calculator")
        log = screen.query_one("#log", LogView)
        assert log.lines_seen[0].startswith("nixin try: fetching gnome-calculator")
        assert "these 3 paths will be fetched" in log.lines_seen[1]
        assert len(log.lines_seen) == 8  # streamed through Textual's stderr capture
        assert not log.display  # folded away once it is built: the head line says it
        nxt = screen.query_one("#try-next", Line).value
        assert nxt.startswith("launched gnome-calculator (pid 4242, detached)")
        assert "try/gnome-calculator.log" in nxt
        await pilot.press("l")  # again
        await settle(app, pilot)
        assert "graphical app (Calculator)" in screen_text(app)

    _, fake = drive(script)
    exe = load("try_gnome_calculator")["exec"]
    assert spawned == [exe, exe]
    assert fake.streamed and fake.verbs() == [["try", "gnome-calculator", "--build-only"]]


def test_tui_try_gui_app_without_a_display(spawned, monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY")

    async def script(app, pilot, fake):
        screen = await open_try(app, pilot, "gnome-calculator")
        assert "no display" in screen.query_one("#try-next", Line).value
        assert screen.query_one("#commands", TextArea).text.startswith("nix run ")
        await pilot.press("l")
        await settle(app, pilot)

    drive(script)
    assert spawned == []


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_tui_try_ambiguous_program_is_picked(size, spawned):
    async def script(app, pilot, fake):
        screen = await open_try(app, pilot, "coreutils")
        notice = screen.query_one("#notice", NoticeBox)
        assert notice.display and notice.notice.code == "ambiguous-program"
        pick = screen.query_one("#programs", OptionList)
        assert pick.display and pick.has_focus and pick.option_count == 21
        assert screen.query_one("#commands", TextArea).text == f"nix shell {REF}#coreutils"
        pick.highlighted = [str(pick.get_option_at_index(i).prompt) for i in range(21)].index("ls")
        await pilot.press("enter")
        await settle(app, pilot, 0.05)
        assert not notice.display and not pick.display
        assert screen.query_one("#commands", TextArea).text.splitlines()[0] == (
            f"nix shell {REF}#coreutils --command ls"
        )
        assert "try coreutils --program ls" in screen_text(app)

    _, fake = drive(script, size=size)
    assert fake.verbs() == [
        ["try", "coreutils", "--build-only"],
        ["try", "coreutils", "--build-only", "--program", "ls"],
    ]


def test_tui_try_unfree_shows_the_commands(spawned):
    async def script(app, pilot, fake):
        screen = await open_try(app, pilot, "steam")
        notice = screen.query_one("#notice", NoticeBox)
        assert notice.notice.code == "unfree"
        cmds = screen.query_one("#commands", TextArea)
        assert cmds.text.splitlines()[0] == "NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure"
        await pilot.press("c")
        assert app.clipboard == "NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure"
        assert "unfree" in screen_text(app)

    drive(script)


def test_tui_try_screenshots(spawned):
    async def script(app, pilot, fake):
        await open_try(app, pilot, "gnome-calculator")
        svg = app.export_screenshot()
        assert svg.startswith("<svg") and "gnome-calculator" in svg

    for size in (PHONE, DESKTOP):
        drive(script, size=size)
