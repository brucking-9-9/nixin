"""The AppStream layer offline: the parsers on recorded metainfo and desktop files, an
output tree read from disk, the installed-apps scan over fake profiles, search.nixos.org's
hint fields, and the `info`/`search --appstream`/`browse` documents on fakes (no nix, no
network)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nixin.appstream import parse

FIX = Path(__file__).parent / "fixtures" / "appstream"


def meta(name: str) -> dict:
    return parse.parse_metainfo((FIX / "metainfo" / name).read_text())


def desktop(name: str) -> dict:
    return parse.parse_desktop((FIX / "desktop" / name).read_text())


# --- metainfo --------------------------------------------------------------------------------


def test_metainfo_kde_appdata():
    m = meta("org.kde.konsole.appdata.xml")
    assert m["id"] == "org.kde.konsole"  # the legacy `.desktop` suffix dropped
    assert m["type"] == "desktop-application"
    assert m["name"] == "Konsole"  # not the German translation kept in the fixture
    assert m["summary"] == "Use the command line interface"
    assert m["description"].startswith("Konsole is a terminal to run a command shell.")
    assert "\n\nFeatures:\n\n• Tabs\n• Multiple profiles\n" in m["description"]
    assert m["categories"] == []  # konsole leaves them to its desktop file
    assert m["license"] == "GPL-2.0+"
    assert m["launchable"] == "org.kde.konsole.desktop"
    assert m["homepage"] == "https://konsole.kde.org/"
    assert m["developer"] == "KDE"
    assert m["screenshots"] == [
        {
            "url": "https://cdn.kde.org/screenshots/konsole/konsole.png",
            "caption": "Konsole being used as a terminal emulator",
            "default": True,
        }
    ]


def test_metainfo_categories_and_source_images():
    m = meta("com.valvesoftware.Steam.metainfo.xml")
    assert m["categories"] == ["Game", "PackageManager"]
    assert m["license"] == "LicenseRef-proprietary"
    assert m["launchable"] == "steam.desktop"
    shots = m["screenshots"]
    assert len(shots) == 3 and shots[0]["default"] and not shots[1]["default"]
    assert all(s["url"].startswith("https://steamcdn-a.akamaihd.net/") for s in shots)
    assert shots[0]["url"].endswith("ac26dea63042eec4886d5fa27854517ce374b11e.jpg")


def test_metainfo_generic_component_with_stock_icon():
    m = meta("io.github.flightlessmango.mangohud.metainfo.xml")
    assert m["type"] == "generic"
    assert m["icon"] == {"name": "io.github.flightlessmango.mangohud"}
    assert m["developer"] == "FlightlessMango"  # <developer_name>
    assert m["categories"] == ["Utility", "Game"]
    assert [s["caption"] for s in m["screenshots"]] == ["Example", "Log uploading walkthrough"]


def test_metainfo_legacy_application_root():
    m = meta("legacy.appdata.xml")
    assert m["id"] == "legacy-viewer" and m["launchable"] == "legacy-viewer.desktop"
    assert m["type"] == "desktop-application"
    assert m["summary"] == "Look at old files"
    assert m["description"] == "Legacy Viewer opens files from the past.\n\n1. Open\n2. Look"
    assert m["license"] == "CC0"
    assert [s["url"] for s in m["screenshots"]] == ["https://example.org/legacy.png"]


@pytest.mark.parametrize(
    "text",
    ["<html/>", "not xml at all", "<components/>", "<?xml version='1.0'?><component><id>x"],
)
def test_metainfo_rejects_non_appstream(text):
    with pytest.raises(ValueError):
        parse.parse_metainfo(text)


def test_metainfo_collection_takes_the_first_component():
    m = parse.parse_metainfo(
        "<components><component type='desktop-application'><id>a.b</id><name>A</name>"
        "</component><component><id>c</id></component></components>"
    )
    assert m["id"] == "a.b" and m["name"] == "A"


# --- desktop entries -------------------------------------------------------------------------


def test_desktop_entry():
    d = desktop("kitty.desktop")
    assert d["name"] == "kitty" and d["generic_name"] == "Terminal emulator"
    assert d["comment"] == "Fast, feature-rich, GPU based terminal"
    assert d["icon"] == "kitty"
    assert d["categories"] == ["System", "TerminalEmulator"]
    assert parse.shown(d)
    assert not parse.shown(desktop("kitty-open.desktop"))  # NoDisplay=true


def test_desktop_entry_skips_translations_and_actions():
    d = desktop("org.kde.konsole.desktop")
    assert d["name"] == "Konsole"  # not "Open New Tab" from a [Desktop Action] group
    assert d["icon"] == "utilities-terminal"
    assert d["categories"] == ["Qt", "KDE", "System", "TerminalEmulator"]
    assert "command prompt" in d["keywords"]


def test_desktop_escapes_and_errors():
    d = parse.parse_desktop(
        "[Desktop Entry]\nType=Application\nName=A\\sB\nCategories=Office\\;Fake;Graphics\n"
        "Hidden=true\n[Desktop Entry]\nName=second\n"
    )
    assert d["name"] == "A B"
    assert d["categories"] == ["Office;Fake", "Graphics"]
    assert d["hidden"] and not parse.shown(d)
    with pytest.raises(ValueError):
        parse.parse_desktop("[Something Else]\nName=x\n")


def test_main_categories():
    cats = ["Qt", "KDE", "System", "TerminalEmulator", "Game", "Utility"]
    assert parse.main_categories(cats) == ["Game", "System", "Utility"]
    assert parse.merge_categories(["Game", "X"], ["X", "Utility"], []) == ["Game", "X", "Utility"]


# --- an output tree ----------------------------------------------------------------------------


def make_tree(root: Path, *, metainfo=(), desktops=(), icons=()) -> Path:
    for name in metainfo:
        dst = root / "share" / "metainfo" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "metainfo" / name, dst)
    for name in desktops:
        dst = root / "share" / "applications" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "desktop" / name, dst)
    for rel, src in icons:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "icons" / src, dst)
    return root


def test_read_output_desktop_only(tmp_path):
    root = make_tree(
        tmp_path / "kitty-0.49.2",
        desktops=["kitty.desktop", "kitty-open.desktop"],
        icons=[
            ("share/icons/hicolor/256x256/apps/kitty.png", "demo.png"),
            ("share/icons/hicolor/scalable/apps/kitty.svg", "demo.svg"),
        ],
    )
    a = parse.read_output(root, ("kitty",))
    assert a is not None
    assert a["id"] == "kitty" and a["name"] == "kitty"
    assert a["summary"] == "Fast, feature-rich, GPU based terminal"
    assert a["description"] is None and a["screenshots"] == []
    assert a["categories"] == ["System", "TerminalEmulator"]
    assert a["launchable"] == "kitty.desktop"
    assert a["desktop_ids"] == ["kitty.desktop"]  # kitty-open is NoDisplay
    assert a["icon"]["name"] == "kitty"
    assert a["icon"]["path"].endswith("hicolor/scalable/apps/kitty.svg")  # svg over png
    assert a["gui"] is True
    assert a["files"] == ["share/applications/kitty.desktop"]


def test_read_output_metainfo_and_desktop(tmp_path):
    root = make_tree(
        tmp_path / "konsole",
        metainfo=["org.kde.konsole.appdata.xml"],
        desktops=["org.kde.konsole.desktop"],
        icons=[("share/icons/breeze/48x48/apps/utilities-terminal.png", "demo.png")],
    )
    a = parse.read_output(root, ("konsole",))
    assert a["id"] == "org.kde.konsole" and a["name"] == "Konsole"
    assert a["summary"] == "Use the command line interface"  # AppStream wins over Comment
    assert a["categories"] == ["Qt", "KDE", "System", "TerminalEmulator"]
    assert a["license"] == "GPL-2.0+" and len(a["screenshots"]) == 1
    assert a["icon"]["path"].endswith("breeze/48x48/apps/utilities-terminal.png")
    assert a["files"] == [
        "share/metainfo/org.kde.konsole.appdata.xml",
        "share/applications/org.kde.konsole.desktop",
    ]


def test_read_output_without_anything(tmp_path):
    (tmp_path / "lib" / "share").mkdir(parents=True)
    assert parse.read_output(tmp_path / "lib") is None
    assert parse.read_output(tmp_path / "missing") is None


def test_read_output_generic_component_without_desktop(tmp_path):
    root = make_tree(
        tmp_path / "mangohud",
        metainfo=["io.github.flightlessmango.mangohud.metainfo.xml"],
        icons=[("share/pixmaps/io.github.flightlessmango.mangohud.svg", "demo.svg")],
    )
    a = parse.read_output(root)
    assert a["gui"] is False  # a component, but nothing to launch from a menu
    assert a["categories"] == ["Utility", "Game"]
    assert a["icon"]["path"].endswith("share/pixmaps/io.github.flightlessmango.mangohud.svg")


def test_resolve_icon_prefers_hicolor_then_size(tmp_path):
    root = make_tree(
        tmp_path / "x",
        icons=[
            ("share/icons/hicolor/32x32/apps/app.png", "demo.png"),
            ("share/icons/hicolor/128x128/apps/app.png", "demo.png"),
            ("share/icons/Other/scalable/apps/app.svg", "demo.svg"),
        ],
    )
    assert parse.resolve_icon(root, "app").endswith("hicolor/128x128/apps/app.png")
    assert parse.resolve_icon(root, "app.png").endswith("hicolor/128x128/apps/app.png")
    assert parse.resolve_icon(root, "missing") is None
    assert parse.resolve_icon(root, str(root / "share/icons/Other/scalable/apps/app.svg"))
    assert parse.resolve_icon(root, None) is None
