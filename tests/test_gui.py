"""The GUI, offline and headless (QT_QPA_PLATFORM=offscreen, Qt Quick's software renderer).

Every QML file compiles and passes qmllint; the Theme singleton loads; the view-model
objects fill their models from recorded documents; then the window is driven with real
key presses and mouse clicks against the backend the TUI tests use (tests/tui_fake.py:
answers from tests/fixtures/tui, no nix, no network, no config): search → details → add →
the diff dialog → write → pending → plan (log streamed) → apply → `needs-sudo`, error
notices, candidates, the Home Manager user, the keys.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from tui_fake import COWSAY, PACKAGES, FakeBackend, load

from nixin.cli import main
from nixin.gui import app as gui_app
from nixin.gui import jobs, models
from nixin.gui.app import QML_DIR, STYLE, Window, run_app
from nixin.gui.bridge import Gui
from nixin.ui import view

pytestmark = pytest.mark.skipif(
    not os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"),
    reason="the GUI tests run offscreen",
)

QML_FILES = sorted(QML_DIR.glob("*/*.qml"))
FADE_MS = 200  # Theme.fadeMs and a little: dialogs fade in and out
SUDO = (
    "sudo git -C /etc/nixos pull --ff-only /home/master/nixos main\n"
    "sudo nixos-rebuild switch --flake /etc/nixos#mbp13"
)


# --- helpers -------------------------------------------------------------------------------


@pytest.fixture
def ui(qapp, qtbot):
    """`ui(routes)` → a window on a FakeBackend: .gui, .win, .fake, and helpers."""
    made: list[SimpleNamespace] = []

    def make(routes=None, delay: float = 0.0, host: str = "mbp13") -> SimpleNamespace:
        fake = FakeBackend(dict(COWSAY if routes is None else routes), delay=delay, host=host)
        gui = Gui(fake)
        win = Window(gui)
        assert win.window is not None, win.errors
        win.window.resize(1200, 780)
        u = SimpleNamespace(gui=gui, win=win, fake=fake, qtbot=qtbot)
        made.append(u)
        settle(u)
        return u

    yield make
    for u in made:
        u.gui.pool.waitForDone()
        u.win.close()
        u.gui.close()
    assert all(not u.win.errors for u in made), [u.win.errors for u in made]


def settle(u: SimpleNamespace, ms: int = 60) -> None:
    """Every verb answered, its callback run, the bindings and transitions done."""
    u.qtbot.waitUntil(lambda: u.gui.idle, timeout=10_000)
    u.qtbot.wait(ms)
    u.qtbot.waitUntil(lambda: u.gui.idle, timeout=10_000)
    u.qtbot.wait(FADE_MS)


def item(u: SimpleNamespace, name: str) -> QObject:
    obj = u.win.find(name)
    assert obj is not None, f"no QML object {name!r}"
    return obj


def prop(u: SimpleNamespace, name: str, p: str):
    return item(u, name).property(p)


def shown(u: SimpleNamespace, name: str) -> bool:
    obj = u.win.find(name)
    return bool(obj is not None and obj.property("visible"))


def click(u: SimpleNamespace, name: str) -> None:
    """A real mouse click in the middle of the item (it must be visible and enabled)."""
    obj = item(u, name)
    assert obj.property("visible"), f"{name} is not visible"
    assert obj.property("enabled"), f"{name} is disabled"
    w, h = obj.property("width"), obj.property("height")
    pt = obj.mapToScene(QPointF(w / 2, h / 2)).toPoint()
    QTest.mouseClick(u.win.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    settle(u)


def keys(u: SimpleNamespace, *seq, mods=Qt.KeyboardModifier.NoModifier) -> None:
    """Key presses on the window: Qt.Key values, or strings typed a character at a time."""
    for k in seq:
        for one in k if isinstance(k, str) else [k]:
            QTest.keyClick(u.win.window, one, mods)
    settle(u)


def search_cowsay(u: SimpleNamespace) -> None:
    """Type the query in the field and press Enter (the focus starts there)."""
    assert prop(u, "query", "activeFocus")
    keys(u, "cowsay", Qt.Key.Key_Return)


def open_details(u: SimpleNamespace) -> None:
    search_cowsay(u)
    assert prop(u, "results", "activeFocus")  # Enter moved there
    keys(u, Qt.Key.Key_Return)  # the first row
    assert u.gui.details["present"] and u.gui.details["name"] == "cowsay"


# --- QML: compiles, lints, the theme --------------------------------------------------------


@pytest.mark.parametrize("path", QML_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_qml_file_compiles(qapp, path):
    if QQuickStyle.name() != STYLE:
        QQuickStyle.setStyle(STYLE)
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    c = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    assert c.isReady(), c.errorString()


def test_qmllint_is_clean():
    """qmllint over every file, every warning an error. --bare and absolute paths: with the
    current directory as an implicit import it sees Theme.qml outside its module."""
    qmllint = shutil.which("qmllint")
    paths = [p for p in os.environ.get("NIXPKGS_QT6_QML_IMPORT_PATH", "").split(":") if p]
    if qmllint is None or not paths:
        pytest.skip("qmllint (qt6.qtdeclarative) and NIXPKGS_QT6_QML_IMPORT_PATH are needed")
    argv = [qmllint, "--bare", "--max-warnings", "0", "-I", str(QML_DIR)]
    for p in paths:
        argv += ["-I", p]
    argv += [str(p.resolve()) for p in QML_FILES]
    r = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_theme_singleton(qapp):
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    c = QQmlComponent(engine)
    c.setData(
        b"import QtQuick\nimport Nixin\nQtObject {\n"
        b"  property color bg: Theme.bg; property color cyan: Theme.cyan\n"
        b"  property color pink: Theme.pink; property color purple: Theme.purple\n"
        b"  property color del: Theme.style('del'); property color opt: Theme.style('option')\n"
        b"  property color dim: Theme.style('dim'); property color plain: Theme.style('')\n"
        b"  property color action: Theme.severity('action')\n}",
        QUrl("file:///theme-test.qml"),
    )
    o = c.create()
    assert o is not None, c.errorString()
    colour = {k: QColor(o.property(k)).name() for k in ("bg", "cyan", "pink", "purple")}
    assert colour == {"bg": "#120b10", "cyan": "#00ffc8", "pink": "#ff0055", "purple": "#d57bff"}
    assert QColor(o.property("del")).name() == "#ff0055"
    assert QColor(o.property("opt")).name() == "#d57bff"
    assert QColor(o.property("action")).name() == "#d57bff"
    assert QColor(o.property("dim")) != QColor(o.property("plain"))


def test_window_is_the_style_and_the_palette(ui):
    u = ui()
    w = u.win.window
    assert w.property("minimumWidth") >= 900 and w.property("minimumHeight") >= 600
    assert w.title() == "nixin · mbp13"
    img = u.win.grab()
    assert (img.width(), img.height()) == (1200, 780)
    assert img.pixelColor(1190, 770).name() == "#120b10"  # the theme's background
    assert QQuickStyle.name() == STYLE
    for page in ("details", "pendingPage", "statusPage", "diffDialog", "applyDialog"):
        assert u.win.find(page) is not None, page


# --- the view-model objects -----------------------------------------------------------------


def test_models_from_documents(qapp):
    rows = models.Rows(models.HIT)
    rows.reset(models.hits(view.hits(load("search_all_openssh"))))
    assert rows.count == len(rows.rows) > 3
    names = set(rows.roleNames().values())
    assert {bytes(n) for n in names} == {k.encode() for k in models.HIT}
    opt = next(r for r in rows.rows if r["name"] == "services.openssh.enable")
    assert opt["isOption"] and opt["badge"] == "opt" and opt["detail"] == "boolean"
    assert rows.get(0)["name"] == rows.rows[0]["name"] and rows.get(99) == {}
    f = models.fields(view.plan_fields(load("plan_cowsay")))
    assert {"label": "running", "value": "generation 112", "style": "dim"} in f
    sec = models.section(view.status_sections(load("status_clean"))[1])
    assert sec["title"] == "lock" and sec["fields"][0]["value"] == "3ec636362e18 (nixos-unstable)"


def test_log_model_lines(qapp):
    log = models.LogModel()
    log.feed("these 39 deriv")
    log.feed("ations will be built:\n\x1b[1mbuilding\x1b[0m 'a'\r[1/3 built]\r[2/3 built]\npar")
    assert log.lines == ["these 39 derivations will be built:", "[2/3 built]"]
    log.flush_partial()
    assert log.lines[-1] == "par" and log.count == 3
    log.clear()
    assert log.count == 0


def test_stderr_router_sends_a_streaming_run_to_its_log(qapp, qtbot):
    real = []
    sink = SimpleNamespace(write=real.append, flush=lambda: None)
    router = jobs.StderrRouter(sink)  # type: ignore[arg-type]
    router.write("passes through\n")
    log = models.LogModel()
    router.attach(log.incoming.emit)
    t = threading.Thread(target=lambda: router.write("from a pump thread\n"))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: log.count == 1)
    router.detach()
    router.write("after\n")
    assert log.lines == ["from a pump thread"]
    assert real == ["passes through\n", "after\n"]


def test_a_search_from_elsewhere_shows_its_query(ui):
    u = ui()
    u.gui.go("status")  # the query field has no focus there
    settle(u)
    u.gui.search.search(" cowsay ")
    settle(u)
    assert prop(u, "query", "text") == "cowsay"


def test_a_newer_search_supersedes_an_older_one(ui):
    first = {**load("search_pkg_firefox")}
    second = load("search_pkg_cowsay")
    u = ui({"search": [first, second]}, delay=0.05)
    u.gui.search.search("firefox")
    u.gui.search.search("cowsay")
    settle(u)
    assert [h.name for h in u.gui.search.hits] == ["cowsay", "neo-cowsay"]
    assert u.gui.search["count"] == 2 and not u.gui.search["loading"]


# --- the window, driven ---------------------------------------------------------------------


def test_search_details_add_diff_write(ui):
    u = ui()
    search_cowsay(u)
    results = u.gui.search.rows("results")
    assert results.column("name") == ["cowsay", "neo-cowsay"]
    assert prop(u, "source", "text") == "2 result(s) · search.nixos.org unstable@1c89083e"
    keys(u, Qt.Key.Key_Return)
    d = u.gui.details
    assert d["cue"] == "○ not installed" and prop(u, "cue", "text") == "○ not installed"
    assert ("3.8.4", "2026-10-02  55ba7f49") == tuple(d.rows("versions").rows[0].values())[:2]
    assert {"label": "version", "value": "3.8.4", "style": "accent"} in d.rows("info").rows
    assert d["whereNote"] == "not installed on mbp13"

    add = item(u, "addButton")  # the style's Button: highlighted is filled with the accent
    assert QColor(add.property("background").property("color")).name() == "#00ffc8"
    remove = item(u, "removeButton")  # a DangerButton: the accent is pink
    assert QColor(remove.property("contentItem").property("color")).name() == "#ff0055"
    click(u, "addButton")
    assert u.fake.verbs()[-1] == ["add", "cowsay", "--dry-run"]
    assert shown(u, "diffDialog") and u.gui.writer["phase"] == "preview"
    assert {"text": "+    pkgs.cowsay", "style": "add"} in u.gui.writer.rows("diff").rows
    assert prop(u, "diffCommand", "text") == "nixin add cowsay --dry-run"
    click(u, "diffWrite")
    assert u.fake.verbs()[-3:] == [
        ["add", "cowsay", "--dry-run"],
        ["add", "cowsay"],
        ["where", "cowsay", "--kind", "package"],
    ]
    assert not shown(u, "diffDialog")
    assert d["cue"] == "● installed [system]"
    assert u.gui.writes == [
        "+cowsay (hosts/mbp13/default.nix, installer/mbp13.nix, installer/state.json)"
    ]
    n = u.gui.notice
    assert n["visible"] and n["transient"] and n["message"].startswith("written: +cowsay")
    assert u.gui.farewell().endswith("not applied yet (nixin plan, nixin apply)")


def test_escape_cancels_the_preview_and_writes_nothing(ui):
    u = ui()
    open_details(u)
    click(u, "addButton")
    assert shown(u, "diffDialog")
    keys(u, Qt.Key.Key_Escape)
    assert not shown(u, "diffDialog") and not u.gui.writer["open"]
    assert ["add", "cowsay"] not in u.fake.verbs() and u.gui.writes == []


def test_pending_plan_apply_needs_sudo(ui, qapp):
    u = ui()
    keys(u, Qt.Key.Key_2, mods=Qt.KeyboardModifier.ControlModifier)
    assert u.gui["page"] == "pending" and shown(u, "pendingPage")
    p = u.gui.pending
    assert {"label": "authority", "value": "/etc/nixos @ 7da2b4c", "style": "dim"} in p.rows(
        "pending"
    ).rows
    click(u, "planButton")
    log = p.logmodel("log")
    assert log.lines[0] == "these 39 derivations will be built:" and log.count == 5
    assert p["closureHead"] == "1 added, 0 removed, 0 changed, +51.2 KiB"
    assert p.rows("closure").rows == [
        {"sign": "+", "name": "cowsay", "version": "3.8.4", "size": "+51.2 KiB", "style": "add"}
    ]
    click(u, "applyButton")
    a = u.gui.apply
    assert shown(u, "applyDialog") and a["phase"] == "confirm"
    dry = {r["label"]: r["value"] for r in a.rows("dry").rows}
    assert dry["switch (root)"] == "sudo nixos-rebuild switch --flake /etc/nixos#mbp13"
    assert a["placeholder"] == "nixin: mbp13: +cowsay"
    assert prop(u, "applyMessage", "activeFocus")
    keys(u, Qt.Key.Key_Return)  # an empty message: the generated one
    assert a["phase"] == "needs-sudo" and a["noticeCode"] == "needs-sudo"
    assert prop(u, "sudoCommands", "text") == SUDO and shown(u, "sudoCommands")
    click(u, "copyCommands")
    assert QGuiApplication.clipboard().text() == SUDO
    assert [r["label"] for r in a.rows("result").rows] == ["committed", "built", "ran"]
    assert u.fake.verbs() == [["status"], ["plan"], ["apply", "--dry-run"], ["apply"]]
    assert u.gui.applied == []
    click(u, "applyClose")
    assert not shown(u, "applyDialog")


def test_apply_with_message_and_result(ui):
    u = ui({**COWSAY, "apply": "apply_ok"})
    u.gui.go("pending")
    settle(u)
    click(u, "planButton")
    assert u.gui.pending["planned"]
    click(u, "applyButton")
    keys(u, "test: cowsay", Qt.Key.Key_Return)
    a = u.gui.apply
    assert a["phase"] == "done"
    result = {r["label"]: r["value"] for r in a.rows("result").rows}
    assert result["system"] == "switched to generation 113"
    assert a.logmodel("log").lines[-1] == "reloading user units for master..."
    assert u.fake.verbs()[-1] == ["apply", "-m", "test: cowsay"]
    p = u.gui.pending  # the plan compared against the system before: gone
    assert u.gui.plan_doc is None and not p["planned"] and p["planNote"].startswith("Plan builds")
    assert {"label": "applied", "value": "generation 113", "style": "ok"} in p.rows("session").rows
    assert u.gui.farewell() == "nixin gui: applied: generation 113"


def test_nothing_to_apply_is_a_calm_notice(ui):
    u = ui({**COWSAY, "apply --dry-run": "apply_nothing"})
    u.gui.go("pending")
    settle(u)
    click(u, "applyButton")
    n = u.gui.notice
    assert not shown(u, "applyDialog")
    assert n["visible"] and n["code"] == "nothing-to-apply" and n["severity"] == "info"
    assert shown(u, "noticeBar") and prop(u, "noticeCode", "text") == "nothing-to-apply"


def test_error_documents_become_a_notice(ui):
    network = {"ok": False, "error": {"code": "network", "message": "search.nixos.org: timed out"}}
    u = ui({"search": network})
    keys(u, "x", Qt.Key.Key_Return)
    assert shown(u, "noticeBar")
    assert prop(u, "noticeCode", "text") == "network"
    assert prop(u, "noticeMessage", "text") == "search.nixos.org: timed out"
    assert u.gui.notice["severity"] == "error" and not u.gui.notice["transient"]
    click(u, "noticeDismiss")
    assert not u.gui.notice["visible"]


def test_info_error_shows_in_details(ui):
    u = ui({**COWSAY, "info": "info_notfound"})
    open_details(u)
    assert u.gui.details["infoNote"].startswith("info: not-found: no package")


def test_ambiguous_write_offers_the_candidates(ui):
    routes = {
        "search": "search_pkg_firefox",
        "info": "info_firefox",
        "versions": "versions_cowsay",
        "where": "where_git",
        "rm --dry-run": ["rm_git_ambiguous", "add_cowsay_dry"],
    }
    u = ui(routes)
    keys(u, "git", Qt.Key.Key_Return)
    keys(u, Qt.Key.Key_Return)
    click(u, "removeButton")
    n = u.gui.notice
    assert n["code"] == "ambiguous" and not shown(u, "diffDialog")
    assert [c["where"] for c in n["candidates"]] == [
        "hosts/mbp13/default.nix:203",
        "configuration.nix:406",
    ]
    click(u, "candidate-configuration.nix:406")
    assert u.fake.verbs()[-2:] == [
        ["rm", "firefox", "--dry-run"],
        ["rm", "firefox", "--where", "configuration.nix:406", "--dry-run"],
    ]
    assert shown(u, "diffDialog") and not n["visible"]


def test_home_manager_user(ui):
    status = {**load("status_clean"), "hm_users": ["brucking"]}
    u = ui({**COWSAY, "status": status})
    open_details(u)
    d = u.gui.details
    assert d["canHome"] and not d["homeKnown"] and shown(u, "homeLoad")
    click(u, "homeLoad")  # the users come from status
    assert ["status"] in u.fake.verbs()
    assert d["homeKnown"] and d["homeChoices"] == ["", "brucking"] and shown(u, "homeBox")
    d.setHome("brucking")
    click(u, "addButton")
    assert u.fake.verbs()[-1] == ["add", "cowsay", "--home", "brucking", "--dry-run"]
    w = u.gui.writer
    assert w["canHome"] and w["homeUser"] == "brucking" and shown(u, "diffHome")
    w.setHome("")  # the dialog's picker: preview again, for the system
    settle(u)
    assert u.fake.verbs()[-1] == ["add", "cowsay", "--dry-run"]
    assert d["homeUser"] == ""  # the details' picker follows


def test_home_manager_option_needs_its_user(ui):
    hm_where = {
        "ok": True,
        "kind": "option",
        "name": "programs.kitty.actionAliases",
        "cache": {},
        "scopes": [
            {
                "scope": "home",
                "user": "brucking",
                "mode": "exact",
                "options": [
                    {
                        "name": "programs.kitty.actionAliases",
                        "option": "programs.kitty.actionAliases",
                        "type": "attribute set of string",
                        "value": True,
                        "value_found": True,
                        "declarations": [],
                        "definitions": [],
                    }
                ],
            }
        ],
    }
    routes = {
        "search": "search_hm_kitty",
        "info": {"ok": False, "error": {"code": "not-found", "message": "no"}},
        "where": hm_where,
        "set --dry-run": "add_cowsay_dry",
    }
    u = ui(routes)
    keys(u, "kitty", Qt.Key.Key_Return)
    keys(u, Qt.Key.Key_Return)
    d = u.gui.details
    assert d["kind"] == "hm" and d["homeUser"] == "brucking"  # from where
    click(u, "setButton")
    assert shown(u, "setDialog") and prop(u, "setValue", "text") == "true"
    assert prop(u, "setValue", "activeFocus")
    keys(u, "false", Qt.Key.Key_Return)  # the value was selected: typing replaces it
    assert not shown(u, "setDialog") and shown(u, "diffDialog")
    assert u.fake.verbs()[-1] == [
        "set",
        "programs.kitty.actionAliases",
        "false",
        "--home",
        "brucking",
        "--dry-run",
    ]


def test_status_page(ui):
    u = ui()
    keys(u, Qt.Key.Key_3, mods=Qt.KeyboardModifier.ControlModifier)
    assert u.gui["page"] == "status" and shown(u, "statusPage")
    titles = [s["title"] for s in u.gui.status["sections"]]
    assert titles == ["config", "lock", "running", "pending", "counts", "caches"]
    assert shown(u, "section-lock")
    click(u, "statusFull")
    assert u.fake.verbs()[-1] == ["status", "--full"]


def test_keys_slash_and_ctrl_q(ui):
    u = ui()
    u.gui.go("status")
    settle(u)
    keys(u, Qt.Key.Key_Slash)
    assert u.gui["page"] == "search" and prop(u, "query", "activeFocus")
    keys(u, "a/b")  # in the query "/" is a letter
    assert prop(u, "query", "text") == "a/b"
    keys(u, Qt.Key.Key_Q, mods=Qt.KeyboardModifier.ControlModifier)
    assert not u.win.window.isVisible()


def test_quit_while_writing_takes_a_second_try(ui):
    u = ui(delay=0.4)
    open_details(u)
    u.gui.details.add()  # the dry run takes 0.4 s: busy = writing
    assert u.gui["busy"] == "writing"
    QTest.keyClick(u.win.window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    assert u.win.window.isVisible() and "quit again" in u.gui.notice["message"]
    QTest.keyClick(u.win.window, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    assert not u.win.window.isVisible()
    settle(u)


# --- packages: installed (`list`), updates (`outdated`), `update` --------------------------


def open_packages(u: SimpleNamespace) -> None:
    keys(u, Qt.Key.Key_4, mods=Qt.KeyboardModifier.ControlModifier)
    assert u.gui["page"] == "packages" and shown(u, "packagesPage")


def test_packages_installed_filter_scope_details_remove(ui):
    u = ui(PACKAGES, host="19BK")
    open_packages(u)
    vm = u.gui.packages
    assert prop(u, "pkList", "count") == 78 and prop(u, "pkList", "activeFocus")
    assert prop(u, "pkFooter", "text").startswith(
        "78 packages: 26 system, 52 home brucking · 67 repeats folded"
    )
    assert vm["scopeLabels"] == ["all", "system", "home brucking"]
    click(u, "pkFilter")
    keys(u, "zsh")
    assert prop(u, "pkList", "count") == 2
    vm.setScope("home:brucking")
    settle(u)
    assert prop(u, "pkScope", "displayText") == "for: home brucking"
    assert vm.rows("packages").rows == [
        {
            "name": "zsh",
            "drv": vm.shown[0].drv,
            "version": vm.shown[0].version,
            "tag": "brucking",
            "where": vm.shown[0].where,
            "isHome": True,
        }
    ]
    keys(u, Qt.Key.Key_Down, Qt.Key.Key_Return)  # filter → the list → the row
    d = u.gui.details
    assert d["owner"] == "packages" and d["name"] == "zsh" and d["homeUser"] == "brucking"
    assert d["homeKnown"] and d["homeChoices"] == ["", "brucking"]  # from list's users[]
    assert shown(u, "detailsName") and prop(u, "homeBox", "displayText") == "home: brucking"
    click(u, "removeButton")
    assert u.fake.verbs()[-1] == ["rm", "zsh", "--home", "brucking", "--dry-run"]
    keys(u, Qt.Key.Key_Escape)
    keys(u, Qt.Key.Key_1, mods=Qt.KeyboardModifier.ControlModifier)
    assert u.gui["page"] == "search" and not shown(u, "detailsName")  # Search's own pane
    verbs = u.fake.verbs()
    assert verbs[0] == ["list"] and verbs.count(["list"]) == 1
    assert ["where", "zsh", "--kind", "package"] in verbs


def test_packages_updates_then_update_lock_to_pending(ui):
    u = ui(PACKAGES, host="19BK")
    open_packages(u)
    click(u, "pk-tab-updates")
    vm = u.gui.packages
    assert vm["tab"] == "updates" and prop(u, "upList", "count") == 14
    assert prop(u, "upKernel", "text") == "kernel 7.2.12 → 7.2.14"
    assert prop(u, "upSummary", "text") == "13 of 325 packages would change; kernel 7.2.12 → 7.2.14"
    assert prop(u, "upHead", "text").startswith(
        "19BK: nixpkgs (github:nixos/nixpkgs/nixos-unstable)"
    )
    fuse = next(r for r in vm.rows("updates").rows if r["name"] == "fuse")
    assert (fuse["before"], fuse["after"], fuse["style"]) == ("2.9.9", "removed", "del")
    vm.setMode("published")
    settle(u)
    assert prop(u, "upList", "count") == 13 and vm["kernel"].endswith("latest 7.3")
    assert prop(u, "upMode", "displayText") == "vs search.nixos.org"
    assert vm["notes"].startswith("search.nixos.org has many attributes per pname")
    vm.setMode("lock")  # kept: not evaluated again
    settle(u)
    keys(u, Qt.Key.Key_Return)  # the first row, in details
    assert u.gui.details["owner"] == "packages" and u.gui.details["name"] == "firefox"
    click(u, "updateButton")
    assert shown(u, "updateDialog") and vm["updatePhase"] == "confirm"
    fields = {r["label"]: r["value"] for r in vm.rows("updateFields").rows}
    assert fields["runs"] == "nix flake update nixpkgs --flake /home/master/nixos"
    assert fields["to"].startswith("0bb2eed (2026-11-01), as compared: 13 of 325")
    click(u, "updateConfirm")
    assert not shown(u, "updateDialog")
    assert u.gui["page"] == "pending" and shown(u, "pendingPage")
    assert u.gui.writes == ["nixpkgs 3ec6363 → 0bb2eed (flake.lock)"]
    assert u.gui.notice["message"].startswith("updated: nixpkgs 3ec6363 → 0bb2eed")
    assert {
        "label": "wrote",
        "value": "nixpkgs 3ec6363 → 0bb2eed (flake.lock)",
        "style": "accent",
    } in (u.gui.pending.rows("session").rows)
    open_packages(u)  # the lock moved: both documents again, in two jobs
    verbs = u.fake.verbs()
    assert verbs[:3] == [["list"], ["outdated"], ["outdated", "--published"]]
    assert sorted(verbs[3:6]) == [  # the details' three jobs, in any order
        ["info", "firefox", "--kind", "pkg", "--channel", "unstable"],
        ["versions", "firefox", "--limit", "8"],
        ["where", "firefox", "--kind", "package"],
    ]
    assert verbs[6:-2] == [["update", "--dry-run"], ["update"], ["status"]]
    assert sorted(verbs[-2:]) == [["list"], ["outdated"]]


def test_packages_nothing_to_update_and_errors(ui):
    routes = {
        **PACKAGES,
        "outdated": {**load("outdated_19BK"), "up_to_date": True, "changed": [], "count": 0},
        "outdated --published": {
            "ok": False,
            "error": {"code": "network", "message": "search.nixos.org: timed out"},
        },
    }
    u = ui(routes, host="19BK")
    open_packages(u)
    click(u, "pk-tab-updates")
    assert not prop(u, "updateButton", "enabled")
    assert u.gui.packages["summary"].endswith("nothing would change")
    u.gui.packages.setMode("published")
    settle(u)
    vm = u.gui.packages
    assert vm["updatesNote"] == "outdated: network: search.nixos.org: timed out"
    assert prop(u, "upList", "count") == 0 and prop(u, "updateButton", "enabled")
    assert u.fake.verbs() == [["list"], ["outdated"], ["outdated", "--published"]]


def test_every_page_and_dialog_grabs(ui, tmp_path):
    u = ui()
    open_details(u)
    sizes = []
    for step in ("details", "diff", "pending", "status", "packages", "updates", "update"):
        if step == "packages":
            u.fake.routes |= {k: [v] for k, v in PACKAGES.items() if k not in u.fake.routes}
            u.gui.go("packages")
        elif step == "updates":
            u.gui.packages.setTab("updates")
        elif step == "update":
            u.gui.packages.startUpdate()
        elif step == "diff":
            u.gui.details.add()
        elif step == "pending":
            u.gui.writer.cancel()
            u.gui.go("pending")
        elif step == "status":
            u.gui.go("status")
        settle(u)
        img = u.win.grab()
        path = tmp_path / f"{step}.png"
        assert img.save(str(path), "PNG", 0)
        sizes.append(path.stat().st_size)
    assert all(2_000 < s < 200_000 for s in sizes), sizes


# --- the verb -------------------------------------------------------------------------------


def test_gui_verb_has_no_json_form(capsys, monkeypatch):
    assert main(["--json", "--host", "testhost", "gui"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"] == {
        "code": "usage",
        "message": "gui is interactive and has no --json form; the other verbs have one",
    }
    for var in ("WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(var, raising=False)
    assert main(["gui"]) == 2
    assert "needs a display" in capsys.readouterr().err


def test_self_test(qapp):
    doc = run_app(FakeBackend(dict(COWSAY)), self_test=True)
    assert doc["ok"], doc
    assert doc["message"].startswith("nixin gui: self-test ok (offscreen, 1200x780")


def test_self_test_reports_qml_errors(qapp, monkeypatch, tmp_path):
    broken = tmp_path / "qml"
    shutil.copytree(QML_DIR, broken)
    (broken / "Nixin" / "Main.qml").write_text("import QtQuick\nItem { nonsense: 1 }\n")
    monkeypatch.setattr(gui_app, "QML_DIR", Path(broken))
    doc = run_app(FakeBackend(dict(COWSAY)), self_test=True)
    assert doc["ok"] is False and doc["error"]["code"] == "internal"
    assert doc["error"]["message"].startswith("QML: ")
