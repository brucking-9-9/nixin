# ruff: noqa: F811 -- `ui` is test_gui's fixture, imported; the tests take it as a parameter
"""Try in the GUI, offline and headless: the details pane's Try → the dialog with nix's log
streamed → a GUI app launched detached, a CLI program opened in the terminal, a program
picked, an unfree refusal. The backend is test_gui's (tests/tui_fake.py on the recorded
`try` documents, see test_try_ui.py); nothing starts: launch.SPAWN is a recorder, and the
only terminal on PATH is a fake `kitty`.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtGui import QGuiApplication
from test_gui import click, open_details, settle, shown, ui  # noqa: F401
from tui_fake import COWSAY, load

from nixin.tryout import launch

pytestmark = pytest.mark.skipif(
    not os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="the GUI tests run offscreen",
)

REF = "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
ROUTES = {
    **COWSAY,
    "try": "try_cowsay",
    "try gnome-calculator": "try_gnome_calculator",
    "try coreutils": "try_coreutils_ambiguous",
    "try coreutils --program": "try_coreutils_ls",
    "try steam": "try_steam_unfree",
}


@pytest.fixture
def spawned(monkeypatch, tmp_path):
    """launch.SPAWN recorded; a fake kitty first on PATH; XDG_CACHE_HOME in tmp."""
    seen: list[list[str]] = []
    monkeypatch.setattr(launch, "SPAWN", lambda argv, *, log: seen.append(argv) or 4242)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "kitty").write_text("#!/bin/sh\n")
    (bin_ / "kitty").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ.get('PATH', '')}")
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    return seen


def test_gui_try_cli_program_opens_in_the_terminal(ui, spawned):
    u = ui(ROUTES)
    open_details(u)
    assert shown(u, "tryButton")
    click(u, "tryButton")
    t = u.gui.trying
    assert shown(u, "tryDialog") and t["phase"] == "done"
    assert u.fake.verbs()[-1] == ["try", "cowsay", "--build-only"]
    assert t["head"].startswith("ready: cowsay 3.8.4")
    assert t["commands"].splitlines() == [f"nix run {REF}#cowsay", f"nix shell {REF}#cowsay"]
    # opened at once: the nix run line in kitty, held open until Enter
    assert spawned[0][:3] == ["kitty", "sh", "-c"]
    assert spawned[0][3].startswith(f"nix run {REF}#cowsay\n") and spawned[0][4] == "cowsay"
    assert t["note"].startswith("opened cowsay in kitty (pid 4242)")
    assert not shown(u, "tryLaunch") and shown(u, "tryTerminal")
    click(u, "tryShell")
    assert spawned[1] == ["kitty", "sh", "-c", f"nix shell {REF}#cowsay"]
    click(u, "tryCopy")
    assert QGuiApplication.clipboard().text() == f"nix run {REF}#cowsay"
    click(u, "tryClose")
    assert not shown(u, "tryDialog") and not t["open"]


def test_gui_try_gui_app_launches_detached(ui, spawned):
    u = ui(ROUTES)
    u.gui.trying.start("gnome-calculator")
    settle(u)
    t = u.gui.trying
    lines = t.logmodel("log").lines
    assert lines[0].startswith("nixin try: fetching gnome-calculator") and len(lines) == 8
    exe = load("try_gnome_calculator")["exec"]
    assert spawned == [exe]
    assert t["note"].startswith("launched gnome-calculator, detached (pid 4242)")
    assert t["isGui"] and t["canLaunch"]
    assert "fetched 3 paths, 2.3 MiB download" in t["head"]
    click(u, "tryLaunch")
    assert spawned == [exe, exe]
    click(u, "tryTerminal")  # a GUI app's nix run line in a terminal, to see its output
    assert spawned[2][0] == "kitty"


def test_gui_try_without_a_terminal_offers_the_command(ui, spawned, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    u = ui(ROUTES)
    open_details(u)
    click(u, "tryButton")
    t = u.gui.trying
    assert spawned == [] and not t["canTerminal"]
    assert "no terminal found" in t["note"]
    assert shown(u, "tryCopy") and not shown(u, "tryTerminal") and not shown(u, "tryShell")


def test_gui_try_picks_the_program(ui, spawned):
    u = ui(ROUTES)
    u.gui.trying.start("coreutils")
    settle(u)
    t = u.gui.trying
    assert t["phase"] == "pick" and t["noticeCode"] == "ambiguous-program"
    assert len(t["programs"]) == 21 and shown(u, "tryPrograms")
    assert t["commands"] == f"nix shell {REF}#coreutils" and shown(u, "tryShell")
    assert spawned == []  # nothing runs until a program is picked
    click(u, "tryProgram-cat")
    assert u.fake.verbs()[-2:] == [
        ["try", "coreutils", "--build-only"],
        ["try", "coreutils", "--build-only", "--program", "cat"],
    ]
    assert t["phase"] == "done" and t["program"] == "cat"
    assert spawned[0][3].startswith(f"nix shell {REF}#coreutils --command ls")  # the fixture's


def test_gui_try_unfree_says_how(ui, spawned):
    u = ui(ROUTES)
    u.gui.trying.start("steam")
    settle(u)
    t = u.gui.trying
    assert t["phase"] == "failed" and t["noticeCode"] == "unfree"
    assert t["commands"].splitlines()[0] == "NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure"
    assert "Run:" not in t["noticeMessage"]
    assert shown(u, "tryRetry") and shown(u, "tryCopy") and spawned == []


def test_gui_try_waits_for_a_running_plan(ui, spawned):
    u = ui(ROUTES)
    u.gui.set(busy="planning")
    u.gui.trying.start("cowsay")
    settle(u)
    assert not u.gui.trying["open"] and ["try", "cowsay", "--build-only"] not in u.fake.verbs()
