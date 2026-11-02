"""The discovery verbs through the real CLI: argument parsing, the --json envelope, errors."""

import json

import pytest

from nixin.cli import main

ENVELOPE = {
    "ok",
    "verb",
    "host",
    "results",
    "count",
    "source",
    "channel",
    "rev",
    "cached",
    "sources",
}


def run_json(capsys, *argv):
    rc = main(["--json", "--host", "t", *argv])
    out, err = capsys.readouterr()
    assert err == ""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    doc = json.loads(out)
    assert out.strip().startswith("{") and lines[-1] == "}"  # exactly one document
    return rc, doc


def test_search_pkg_json(es_transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "search", "firefox", "--limit", "4")
    assert rc == 0 and doc["ok"] is True and doc["verb"] == "search" and doc["host"] == "t"
    assert ENVELOPE <= set(doc)
    assert doc["query"] == "firefox" and doc["kind"] == "pkg" and doc["limit"] == 4
    assert (
        doc["source"] == "search.nixos.org"
        and doc["channel"] == "unstable"
        and doc["cached"] is False
    )
    assert doc["rev"] == "1c89083e451e891152365b799595cf7790a43325"
    assert doc["count"] == 4 and doc["results"][0]["name"] == "firefox"
    assert doc["sources"] == {
        "search.nixos.org": {
            "kinds": ["pkg"],
            "channel": "unstable",
            "rev": doc["rev"],
            "cached": False,
            "count": 4,
            "error": None,
        }
    }


def test_search_second_run_is_cached(es_transport, cache_dir, capsys):
    run_json(capsys, "search", "firefox")
    _rc, doc = run_json(capsys, "search", "firefox")
    assert doc["cached"] is True and doc["sources"]["search.nixos.org"]["cached"] is True


def test_search_no_cache_and_refresh_flags(es_transport, cache_dir, capsys):
    run_json(capsys, "search", "firefox", "--no-cache")
    assert not (cache_dir / "es").exists()
    _rc, doc = run_json(capsys, "search", "firefox")
    assert doc["cached"] is False
    _rc, doc = run_json(capsys, "search", "firefox", "--refresh")
    assert doc["cached"] is False
    _rc, doc = run_json(capsys, "search", "firefox")
    assert doc["cached"] is True


def test_search_opt_and_program_kinds(es_transport, cache_dir, capsys):
    _rc, doc = run_json(capsys, "search", "--kind", "opt", "services.openssh")
    assert {r["kind"] for r in doc["results"]} == {"opt"}
    _rc, doc = run_json(capsys, "search", "--kind", "program", "rg")
    assert doc["results"][0] == {
        "kind": "program",
        "source": "search.nixos.org",
        "channel": "unstable",
        "rev": doc["rev"],
        "name": "rg",
        "package": "ripgrep",
        "pname": "ripgrep",
        "version": "15.2.0",
        "description": doc["results"][0]["description"],
        "exact": True,
    }


def test_search_channel_variants(es_transport, cache_dir, capsys):
    _rc, doc = run_json(capsys, "search", "firefox", "--channel", "stable")
    assert doc["channel"] == "26.05"
    _rc, doc = run_json(capsys, "search", "firefox", "--channel", "nixos-25.11")
    assert doc["channel"] == "25.11" and doc["rev"] == "b6018f87da91d19d0ab4cf979885689b469cdd41"
    # a branch-named --nixpkgs implies the channel for option search
    _rc, doc = run_json(
        capsys,
        "search",
        "--kind",
        "opt",
        "services.openssh",
        "--nixpkgs",
        "github:nixos/nixpkgs/nixos-25.11",
    )
    assert doc["channel"] == "25.11"


def test_search_rev_nixpkgs_without_channel_is_bad_channel_for_options(
    es_transport, cache_dir, capsys
):
    rc, doc = run_json(
        capsys,
        "search",
        "--kind",
        "opt",
        "x",
        "--nixpkgs",
        "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504",
    )
    assert rc == 1 and doc["ok"] is False and doc["error"]["code"] == "bad-channel"
    assert "--channel" in doc["error"]["message"]


def test_search_errors(es_transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "search", "firefox", "--channel", "99.99")
    assert rc == 1 and doc["error"]["code"] == "bad-channel" and doc["verb"] == "search"
    rc, doc = run_json(capsys, "search", "   ")
    assert doc["error"]["code"] == "usage"
    rc, doc = run_json(capsys, "search", "firefox", "--limit", "0")
    assert doc["error"]["code"] == "usage"


def test_search_offline_is_network(transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "search", "firefox")
    assert rc == 1 and doc["ok"] is False and doc["error"]["code"] == "network"
    assert "cannot reach" in doc["error"]["message"]


def test_search_needs_a_query(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--json", "search"])
    assert e.value.code == 2


def test_info_pkg(es_transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "info", "--kind", "pkg", "kitty")
    assert rc == 0 and doc["verb"] == "info" and doc["name"] == "kitty" and doc["kind"] == "pkg"
    (rec,) = doc["results"]
    assert rec["name"] == "kitty" and rec["version"] == "0.49.2" and rec["matched_by"] == "attr"


def test_info_opt(es_transport, cache_dir, capsys):
    _rc, doc = run_json(capsys, "info", "--kind", "opt", "services.openssh.enable")
    (rec,) = doc["results"]
    assert rec["kind"] == "opt" and rec["type"] == "boolean"


def test_info_not_found(es_transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "info", "--kind", "pkg", "no-such-package")
    assert rc == 1 and doc["error"]["code"] == "not-found"
    assert "no package named 'no-such-package' on unstable" == doc["error"]["message"]


def test_human_output_is_a_table(es_transport, cache_dir, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "100")
    rc = main(["search", "firefox"])
    out, err = capsys.readouterr()
    assert rc == 0 and err == ""
    lines = out.splitlines()
    assert lines[0].startswith("PACKAGE") and lines[1].startswith("firefox ")
    assert lines[-1].startswith("# 4 result(s) · search.nixos.org unstable@1c89083e")


def test_human_error_goes_to_stderr(es_transport, cache_dir, capsys):
    rc = main(["search", "firefox", "--channel", "99.99"])
    out, err = capsys.readouterr()
    assert rc == 1 and out == "" and err.startswith("nixin search: unknown channel '99.99'")
