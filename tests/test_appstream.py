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


# --- search.nixos.org's hint -------------------------------------------------------------------

from conftest import fixture, has

from nixin.appstream import curated, hint, images, installed, store
from nixin.cache import Cache
from nixin.cli import main


def es_doc(name: str) -> dict:
    return fixture(f"appstream/es/info_{name}.json")["hits"]["hits"][0]["_source"]


def test_hint_from_desktop_data():
    a = hint.from_es(es_doc("kitty"))
    assert a["source"] == "search.nixos.org" and a["gui"] is True
    assert a["name"] == "kitty"  # the listed entry, not "kitty URL Launcher" (NoDisplay)
    assert a["summary"] == "Fast, feature-rich, GPU based terminal"
    assert a["categories"] == ["System", "TerminalEmulator"]
    assert a["icon"] == {
        "name": "kitty",
        "path": None,
        "url": "https://search.nixos.org/icons/b937ae4fea91793b992a59ed39c1f472.svg",
    }
    assert a["notes"] == []


def test_hint_screenshots_and_component_id():
    a = hint.from_es(es_doc("prismlauncher"))
    assert a["id"] == "org.prismlauncher.PrismLauncher"
    assert a["categories"][0] == "Game"
    s = a["screenshots"][0]
    assert s["url"] == "https://prismlauncher.org/img/screenshots/LauncherDark.png"
    assert s["caption"] == "The main Prism Launcher window" and s["default"]
    assert s["thumbnail"].startswith(hint.SCREENSHOT_URL) and s["thumbnail"].endswith(".webp")
    assert s["large"].startswith(hint.SCREENSHOT_URL)
    assert not a["screenshots"][1]["default"]


def test_hint_falls_back_to_the_shipped_list():
    a = hint.from_es(es_doc("firefox"))  # search.nixos.org has no desktop data for firefox
    assert a["source"] == "curated" and a["gui"] is True and a["categories"] == ["Network"]
    assert "nixin's own list" in a["notes"][0]


def test_hint_nothing_to_launch_and_unknown():
    zlib = hint.from_es(es_doc("zlib"))
    assert zlib["source"] == "search.nixos.org" and zlib["gui"] is False
    rg = hint.from_es(
        {**es_doc("zlib"), "package_attr_name": "ripgrep", "package_programs": ["rg"]}
    )
    assert rg["gui"] is None  # binaries, but nothing says whether one is graphical
    old = hint.from_es({"package_attr_name": "ripgrep", "package_programs": ["rg"]})
    assert old["source"] is None and old["gui"] is None
    assert "no desktop data" in old["notes"][0]  # an index before generation 52


def test_curated_categories():
    assert [c.key for c in curated.CATEGORIES][:4] == [
        "AudioVideo",
        "Development",
        "Education",
        "Game",
    ]
    assert curated.category("games").key == "Game"
    assert curated.category("Internet").key == "Network"
    assert curated.category("audioVIDEO").key == "AudioVideo"
    assert curated.category("nope") is None
    assert curated.of("kitty") == ["System"]
    assert [c.key for c in curated.for_categories(["Audio", "Player"])] == ["AudioVideo"]
    every = [a for xs in curated.WELL_KNOWN.values() for a in xs]
    assert len(every) == len(set(every))  # one category each
    assert set(curated.WELL_KNOWN) == {c.key for c in curated.CATEGORIES}


# --- installed applications (fake profiles in a fake store) -------------------------------------

HASH = "0" * 31


@pytest.fixture
def fake_store(tmp_path, monkeypatch):
    """A /nix/store stand-in under tmp_path and a `link(owner, path)` that puts a store
    path's share/ files into that owner's profile, as buildEnv does."""
    root = tmp_path / "nix" / "store"
    root.mkdir(parents=True)
    monkeypatch.setattr(installed, "STORE", str(root) + "/")
    profiles: dict[str, Path] = {}
    n = iter(range(10))

    def path(name: str, **tree) -> Path:
        return make_tree(root / f"{next(n)}{HASH}-{name}", **tree)

    def link(owner: str, store_path: Path) -> None:
        prof = profiles.setdefault(owner, tmp_path / "profiles" / owner)
        for f in store_path.glob("share/*/*"):
            dst = prof / f.relative_to(store_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(f)

    monkeypatch.setattr(installed, "profiles", lambda home=None: list(profiles.items()))
    return SimpleNamespace(path=path, link=link, root=root)


from types import SimpleNamespace


def test_parse_store_name():
    assert installed.parse_store_name("konsole-26.08.2") == ("konsole", "26.08.2")
    assert installed.parse_store_name("steam-unwrapped-1.0.0.87") == ("steam-unwrapped", "1.0.0.87")
    assert installed.parse_store_name("hello") == ("hello", "")


def test_scan_profiles(fake_store):
    kitty = fake_store.path(
        "kitty-0.49.2",
        desktops=["kitty.desktop", "kitty-open.desktop"],
        icons=[("share/icons/hicolor/scalable/apps/kitty.svg", "demo.svg")],
    )
    steam = fake_store.path(
        "steam-unwrapped-1.0.0.87", metainfo=["com.valvesoftware.Steam.metainfo.xml"]
    )
    fake_store.link("system", kitty)
    fake_store.link("brucking", kitty)
    fake_store.link("system", steam)
    apps = installed.scan()
    assert [(a.name, a.pname, a.version, a.owners) for a in apps] == [
        ("kitty", "kitty", "0.49.2", ("system", "brucking"))  # kitty-open is NoDisplay
    ]
    paths = installed.store_paths()
    assert paths == {str(kitty): ("system", "brucking"), str(steam): ("system",)}
    assert installed.find("steam", paths) == [(str(steam), ("system",))]  # the -unwrapped one
    assert installed.find("kitty", paths, "0.49.2") == [(str(kitty), ("system", "brucking"))]
    assert installed.find("alacritty", paths) == []


# --- the store lookup -------------------------------------------------------------------------


def test_dry_run_cost():
    err = (
        "these 2 derivations will be built:\n  /nix/store/x.drv\n"
        "these 6 paths will be fetched (690.9 MiB download, 819.5 MiB unpacked):\n"
        "this path will be fetched (323.2 KiB download, 1.6 MiB unpacked):\n"
    )
    build, paths, size = store.dry_run_cost(err)
    assert (build, paths) == (2, 7)
    assert size == int(690.9 * 1024**2) + int(323.2 * 1024)
    assert store.dry_run_cost("") == (0, 0, 0)
    assert store.human(int(1.5 * 1024**2)) == "1.5 MiB"


def test_ordered_outputs():
    assert store.ordered_outputs({"lib": "/l", "dev": "/d", "out": "/o", "bin": "/b"}) == [
        "/o",
        "/b",
        "/l",
        "/d",
    ]


def test_read_caches_by_store_path(tmp_path, cache_dir):
    root = make_tree(tmp_path / "x", desktops=["kitty.desktop"])
    cache = Cache()
    assert store.read(cache, str(root), ("kitty",))["name"] == "kitty"
    (root / "share" / "applications" / "kitty.desktop").unlink()
    assert store.read(cache, str(root), ("kitty",))["name"] == "kitty"  # a store path never changes
    assert store.read(cache, str(tmp_path / "missing")) is None


KITTY = {
    "kind": "pkg",
    "name": "kitty",
    "pname": "kitty",
    "version": "0.49.2",
    "main_program": "kitty",
}


def test_for_package_installed_output_over_the_hint(fake_store, cache_dir):
    kitty = fake_store.path(
        "kitty-0.49.2",
        desktops=["kitty.desktop"],
        icons=[("share/icons/hicolor/scalable/apps/kitty.svg", "demo.svg")],
    )
    fake_store.link("system", kitty)
    a = appstream.for_package(Cache(), KITTY, base=hint.from_es(es_doc("kitty")), flakeref="x")
    assert a["source"] == "installed" and a["store_path"] == str(kitty)
    assert a["installed_for"] == ["system"]
    assert a["icon"]["path"].endswith("kitty.svg")
    assert a["icon"]["url"].startswith(hint.ICON_URL)  # the hint's, for a UI without the file
    assert a["files"] == ["share/applications/kitty.desktop"] and a["notes"] == []


def test_for_package_from_the_locked_nixpkgs(tmp_path, cache_dir, monkeypatch):
    out = make_tree(
        tmp_path / "konsole-26.08.2",
        metainfo=["org.kde.konsole.appdata.xml"],
        desktops=["org.kde.konsole.desktop"],
    )
    asked = []

    def out_paths(cache, flakeref, attr):
        asked.append((flakeref, attr))
        return {"out": str(out), "dev": str(tmp_path / "absent")}

    monkeypatch.setattr(store, "out_paths", out_paths)
    rec = {"kind": "pkg", "name": "kdePackages.konsole", "pname": "konsole"}
    a = appstream.for_package(Cache(), rec, flakeref="github:nixos/nixpkgs/3ec6363")
    assert asked == [("github:nixos/nixpkgs/3ec6363", "kdePackages.konsole")]
    assert a["source"] == "store" and a["store_path"] == str(out)
    assert a["license"] == "GPL-2.0+" and a["description"].startswith("Konsole is")
    assert "installed_for" not in a  # not installed: found in the store


def test_for_package_not_in_the_store_keeps_the_hint(tmp_path, cache_dir, monkeypatch):
    monkeypatch.setattr(store, "out_paths", lambda c, f, a: {"out": str(tmp_path / "absent")})
    base = hint.from_es(es_doc("prismlauncher"))
    rec = {"kind": "pkg", "name": "prismlauncher", "pname": "prismlauncher"}
    a = appstream.for_package(Cache(), rec, flakeref="ref", base=base)
    assert a["source"] == "search.nixos.org" and a["gui"] is True
    assert a["notes"] == ["not in the local store (--appstream fetch substitutes it when cheap)"]
    assert appstream.for_package(Cache(), rec, flakeref="ref", base=base, mode="hint") == base


def test_for_package_fetch_when_cheap(tmp_path, cache_dir, monkeypatch):
    out = tmp_path / "gnome-mines-50.0"
    monkeypatch.setattr(store, "out_paths", lambda c, f, a: {"out": str(out)})

    def fetch(flakeref, attr):
        make_tree(out, desktops=["kitty.desktop"])
        return [str(out)], "fetched 1 path(s), 323.2 KiB, from the binary cache"

    monkeypatch.setattr(store, "fetch_cheaply", fetch)
    a = appstream.for_package(Cache(), KITTY, flakeref="ref", mode="fetch")
    assert a["source"] == "store" and a["notes"] == [
        "fetched 1 path(s), 323.2 KiB, from the binary cache"
    ]


def test_for_package_outputs_without_anything_is_not_an_app(tmp_path, cache_dir, monkeypatch):
    out = tmp_path / "ripgrep-15.1.0"
    (out / "bin").mkdir(parents=True)
    monkeypatch.setattr(store, "out_paths", lambda c, f, a: {"out": str(out)})
    rec = {"kind": "pkg", "name": "ripgrep", "pname": "ripgrep", "programs": ["rg"]}
    a = appstream.for_package(Cache(), rec, flakeref="ref")
    assert a["gui"] is False and a["source"] is None
    assert a["notes"][0] == "its outputs in the store ship no desktop entry or AppStream file"


def test_for_package_without_a_lock_or_nix(cache_dir, monkeypatch):
    a = appstream.for_package(Cache(), KITTY)
    assert a["notes"][0].startswith("no locked nixpkgs to look in")
    a = appstream.for_package(Cache(), KITTY, flakeref="ref")  # conftest: unit tests run no nix
    assert a["notes"][0] == "the out paths: nix-missing: unit tests run no nix"


from nixin import appstream

# --- the verbs, on recorded search.nixos.org answers --------------------------------------------


@pytest.fixture
def es_app(transport, cache_dir):
    """search.nixos.org with the desktop-data fields (requests that ask for them)."""

    def wants_hint(b) -> bool:
        return "package_desktop_entries" in (b or {}).get("_source", [])

    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    for attr in ("kitty", "prismlauncher", "firefox", "zlib"):
        transport.add(
            "POST",
            r"/_search$",
            fixture_file=f"appstream/es/info_{attr}.json",
            when=lambda b, a=attr: wants_hint(b) and has(b, "package_attr_name", a),
        )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="appstream/es/search_firefox.json",
        when=lambda b: wants_hint(b) and has(b, "query", "firefox"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="appstream/es/browse_overview.json",
        when=lambda b: "aggs" in (b or {}),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="appstream/es/browse_game_names.json",
        when=lambda b: has(b, "minimum_should_match", 1),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="appstream/es/browse_game_popular.json",
        when=lambda b: "package_categories_set" in str(b),
    )
    # the same requests without the desktop-data fields: discovery's own recordings
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/info_pkg_kitty.json",
        when=lambda b: has(b, "package_attr_name", "kitty"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/search_pkg_firefox.json",
        when=lambda b: has(b, "query", "firefox"),
    )
    transport.add("POST", r"/_search$", fixture_file="es/empty.json")
    return transport


def run(capsys, *argv) -> tuple[int, dict | str]:
    rc = main(list(argv))
    out = capsys.readouterr().out
    return rc, (__import__("json").loads(out) if "--json" in argv else out)


def test_info_has_appstream(es_app, capsys):
    rc, doc = run(capsys, "--json", "info", "kitty", "--kind", "pkg")
    assert rc == 0
    (rec,) = doc["results"]
    a = rec["appstream"]
    assert a["source"] == "search.nixos.org" and a["gui"] is True  # no profiles, no lock here
    assert a["notes"] == [
        "no locked nixpkgs to look in (no flake.lock; pass --config or --nixpkgs)"
    ]
    assert set(a) >= {"store_path", "description", "screenshots", "license", "launchable", "files"}
    rc, doc = run(capsys, "--json", "info", "kitty", "--kind", "pkg", "--appstream", "off")
    assert "appstream" not in doc["results"][0]
    assert not any("package_desktop_entries" in str(b) for _m, _u, b in es_app.calls[-1:])


def test_info_human_block(es_app, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "100")
    rc, out = run(capsys, "info", "prismlauncher", "--kind", "pkg")
    assert rc == 0
    assert "  appstream: an application, from search.nixos.org's desktop data" in out
    assert "    categories   Game, ActionGame" in out
    assert "    screenshots  https://search.nixos.org/screenshots/" in out


def test_search_appstream_marks_gui_apps(es_app, capsys, monkeypatch):
    rc, doc = run(capsys, "--json", "search", "--appstream", "firefox", "--limit", "4")
    assert rc == 0
    marks = {r["name"]: r["appstream"]["gui"] for r in doc["results"]}
    assert marks["firefox"] is True  # the shipped list: search.nixos.org has nothing for it
    assert doc["results"][0]["appstream"]["categories"] == ["Network"]
    monkeypatch.setenv("NIXIN_WIDTH", "100")
    rc, out = run(capsys, "search", "--appstream", "firefox", "--limit", "4")
    head, first = out.splitlines()[:2]
    assert head.split() == ["PACKAGE", "VERSION", "APP", "DESCRIPTION"]
    assert first.split()[:3] == ["firefox", "159.0", "Network"]
    rc, doc = run(capsys, "--json", "search", "firefox", "--limit", "4")  # without the flag
    assert "appstream" not in doc["results"][0]


def test_browse_overview(es_app, capsys, fake_store):
    fake_store.link("system", fake_store.path("kitty-0.49.2", desktops=["kitty.desktop"]))
    rc, doc = run(capsys, "--json", "browse")
    assert rc == 0 and doc["category"] is None and doc["installed"] == 1
    cats = {c["key"]: c for c in doc["categories"]}
    assert list(cats) == [c.key for c in curated.CATEGORIES]
    assert cats["System"]["installed"] == 1 and cats["Game"]["installed"] == 0
    assert cats["Game"]["published"] == 498 and cats["Game"]["curated"] == 10
    assert doc["sources"]["search.nixos.org"]["error"] is None


def test_browse_a_category(es_app, capsys, fake_store):
    steam = fake_store.path(
        "steam-unwrapped-1.0.0.87",
        metainfo=["com.valvesoftware.Steam.metainfo.xml"],
        desktops=["kitty.desktop"],
    )
    # the fixture's desktop entry says System;TerminalEmulator: make it a game
    f = steam / "share" / "applications" / "kitty.desktop"
    f.write_text(f.read_text().replace("Categories=System;TerminalEmulator;", "Categories=Game;"))
    fake_store.link("system", steam)
    rc, doc = run(capsys, "--json", "browse", "games", "--limit", "5")
    assert rc == 0 and doc["category"]["key"] == "Game"
    rows = doc["results"]
    first = rows[0]
    assert first["name"] == "steam"  # the wrapper's attribute, not steam-unwrapped
    assert first["installed"] and first["installed_for"] == ["system"]
    assert first["curated"] and first["listed_by"] == ["installed", "curated"]
    assert first["appstream"]["source"] == "installed"
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names))
    assert sum(1 for r in rows if r["listed_by"] == ["search.nixos.org"]) <= 5
    assert "steam" not in names[1:]


def test_browse_without_the_network(transport, cache_dir, capsys, fake_store):
    rc, doc = run(capsys, "--json", "browse", "Office")
    assert rc == 0
    assert doc["sources"]["search.nixos.org"]["error"]["code"] == "network"
    assert [r["name"] for r in doc["results"]] == list(curated.WELL_KNOWN["Office"])
    assert all(r["listed_by"] == ["curated"] and r["version"] is None for r in doc["results"])


def test_browse_usage(es_app, capsys):
    rc, doc = run(capsys, "--json", "browse", "nonsense")
    assert (
        rc == 2
        and doc["error"]["code"] == "usage"
        and "known: AudioVideo" in doc["error"]["message"]
    )
    rc, doc = run(capsys, "--json", "browse", "Game", "--limit", "0")
    assert rc == 2


def test_browse_human(es_app, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "120")
    rc, out = run(capsys, "browse")
    assert rc == 0
    assert out.splitlines()[0].split() == [
        "CATEGORY",
        "KEY",
        "INSTALLED",
        "WELL-KNOWN",
        "SEARCH.NIXOS.ORG",
    ]
    _rc, out = run(capsys, "browse", "Game", "--limit", "3")
    assert out.startswith("Games (Game) · ● installed  ★ well-known\n")
    assert "★   steam" in out or "★  steam" in out


# --- images ----------------------------------------------------------------------------------


class ImageResponse:
    def __init__(self, status=200, ctype="image/png", body=b"\x89PNG....", length=True):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        if length:
            self.headers["Content-Length"] = str(len(body))
        self.body = body

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i : i + n]

    def close(self):
        pass


class ImageSession:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.responses[url]


@pytest.fixture
def image_session():
    from nixin import http

    def make(responses):
        s = ImageSession(responses)
        http.set_session(s)  # type: ignore[arg-type]
        return s

    yield make
    from nixin import http as h

    h.set_session(None)


def test_image_fetch_and_cache(tmp_path, image_session):
    url = "https://search.nixos.org/screenshots/a.webp"
    s = image_session({url: ImageResponse(ctype="image/webp", body=b"RIFF0000WEBP")})
    p = images.fetch(url, tmp_path)
    assert p.parent == tmp_path / "images" and p.suffix == ".webp"
    assert p.read_bytes() == b"RIFF0000WEBP"
    assert images.fetch(url, tmp_path) == p and s.calls == [url]  # the second from the cache


@pytest.mark.parametrize(
    "resp, code",
    [
        (ImageResponse(body=b"x" * 50), "too-large"),
        (ImageResponse(body=b"x" * 50, length=False), "too-large"),
        (ImageResponse(ctype="text/html"), "not-an-image"),
        (ImageResponse(status=404), "network"),
    ],
)
def test_image_refusals(tmp_path, image_session, resp, code):
    url = "https://example.org/shot.png"
    image_session({url: resp})
    with pytest.raises(images.ImageError) as e:
        images.fetch(url, tmp_path, cap=40)
    assert e.value.code == code
    assert not list((tmp_path / "images").glob("*")) if (tmp_path / "images").exists() else True
    with pytest.raises(images.ImageError):
        images.fetch("file:///etc/passwd", tmp_path)


def test_image_prune_keeps_the_newest(tmp_path):
    d = tmp_path / "images"
    d.mkdir()
    for i in range(4):
        f = d / f"{i}.png"
        f.write_bytes(b"x" * 100)
        __import__("os").utime(f, (1000 + i, 1000 + i))
    images.prune(tmp_path, total=250)
    assert sorted(f.name for f in d.iterdir()) == ["2.png", "3.png"]
