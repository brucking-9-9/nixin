import pytest

from nixin.discovery import format as fmt


@pytest.fixture(autouse=True)
def fixed_width(monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "80")


def pkg(name, version="1.0", desc="desc"):
    return {
        "kind": "pkg",
        "source": "search.nixos.org",
        "channel": "unstable",
        "rev": "a" * 40,
        "name": name,
        "pname": name,
        "version": version,
        "description": desc,
        "homepage": "https://x",
        "license": ["MIT"],
        "unfree": None,
        "programs": [name],
        "main_program": name,
        "platforms": ["x86_64-linux"],
        "outputs": ["out"],
        "position": "pkgs/a.nix:1",
    }


def doc(results, **kw):
    d = {
        "ok": True,
        "results": results,
        "count": len(results),
        "source": "search.nixos.org",
        "channel": "unstable",
        "rev": "a" * 40,
        "cached": False,
        "sources": {},
    }
    d.update(kw)
    return d


def test_width_env_override(monkeypatch):
    assert fmt.width() == 80
    monkeypatch.delenv("NIXIN_WIDTH")
    assert fmt.width() > 0


def test_table_aligns_and_clips_last_column():
    out = fmt.table(["A", "B", "C"], [["x", "yy", "z" * 200], ["long-name", "", "short"]], total=40)
    lines = out.splitlines()
    assert lines[0].startswith("A          B   C")
    assert all(len(line) <= 40 for line in lines)
    assert lines[1].endswith("…")
    assert lines[2].rstrip().endswith("short")


def test_table_clips_wide_fixed_columns():
    out = fmt.table(["A", "B"], [["n" * 100, "d"]], total=40)
    first = out.splitlines()[1]
    assert len(first) <= 40 and "…" in first


def test_render_search_single_kind_has_kind_headers():
    out = fmt.render("search", doc([pkg("firefox", "159.0", "Web browser")]))
    lines = out.splitlines()
    assert lines[0].startswith("PACKAGE") and "VERSION" in lines[0] and "DESCRIPTION" in lines[0]
    assert lines[1].startswith("firefox") and "159.0" in lines[1] and "Web browser" in lines[1]
    assert lines[-1] == "# 1 result(s) · search.nixos.org unstable@aaaaaaaa"


def test_render_search_mixed_kinds_adds_kind_column():
    opt = {"kind": "opt", "name": "services.x.enable", "type": "boolean", "description": "Whether"}
    hm = {"kind": "hm", "name": "programs.x.enable", "type": "boolean", "description": "HM"}
    prog = {
        "kind": "program",
        "name": "rg",
        "package": "ripgrep",
        "version": "15",
        "description": "grep",
    }
    fn = {"kind": "fn", "name": "lib.id", "signature": "a -> a", "description": "identity"}
    d = doc(
        [pkg("x"), opt, hm, prog, fn],
        source="all",
        cached=True,
        sources={
            "search.nixos.org": {
                "channel": "unstable",
                "rev": "b" * 40,
                "cached": True,
                "error": None,
            },
            "home-manager-options": {
                "channel": "unstable",
                "rev": None,
                "cached": False,
                "error": None,
            },
            "noogle": {
                "channel": None,
                "rev": None,
                "cached": False,
                "error": {"code": "network", "message": "down"},
            },
        },
        notes=["first nix search on a rev is slow"],
    )
    out = fmt.render("search", d)
    lines = out.splitlines()
    assert lines[0].startswith("KIND")
    assert (
        lines[1].startswith("pkg") and lines[2].startswith("opt") and lines[4].startswith("program")
    )
    assert "ripgrep 15" in lines[4] and "a -> a" in lines[5]
    footer = lines[-2]
    assert "search.nixos.org unstable@bbbbbbbb (cached)" in footer
    assert "home-manager-options unstable" in footer and "noogle FAILED: down" in footer
    assert lines[-1] == "# note: first nix search on a rev is slow"


def test_render_search_empty_is_just_footer():
    out = fmt.render("search", doc([], cached=True))
    assert out == "# 0 result(s) · search.nixos.org unstable@aaaaaaaa (cached)"


def test_render_info_block_orders_fields_and_lists():
    rec = pkg("kitty", "0.49", "Terminal")
    rec["platforms"] = [f"p{i}" for i in range(20)]
    rec["alternatives"] = ["kitty-bin"]
    rec["example"] = "line1\nline2"
    out = fmt.render("info", doc([rec]))
    lines = out.splitlines()
    assert lines[0] == "pkg: kitty"
    assert lines[1] == "  description: Terminal"
    assert lines[2] == "  example:" and lines[3] == "    line1" and lines[4] == "    line2"
    body = "\n".join(lines)
    assert (
        body.index("pname")
        < body.index("version")
        < body.index("homepage")
        < body.index("alternatives")
    )
    assert "… (20 total)" in body
    assert "kind:" not in body and lines[-1].startswith("# 1 result(s)")


def test_render_versions_table():
    d = doc(
        [
            {
                "kind": "version",
                "version": "159.0",
                "date": "2026-10-20",
                "rev": "c" * 40,
                "attr": "firefox",
                "platforms": ["x86_64-linux"],
            },
            {
                "kind": "version",
                "version": "158.0",
                "date": None,
                "rev": None,
                "attr": None,
                "platforms": [],
            },
        ],
        source="nixhub.io",
        channel=None,
        rev=None,
        package="firefox",
        total=210,
        summary="Web browser",
    )
    out = fmt.render("versions", d)
    lines = out.splitlines()
    assert lines[0] == "firefox · Web browser · 2 of 210 versions"
    assert lines[1].startswith("VERSION") and "NIXPKGS REV" in lines[1]
    assert lines[2].startswith("159.0") and "cccccccccccc" in lines[2] and "firefox" in lines[2]
    assert lines[3].startswith("158.0")
    assert lines[-1] == "# 2 result(s) · nixhub.io"
