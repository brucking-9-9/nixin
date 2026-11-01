"""`--kind all` fans out to several sources and survives one of them failing."""

import json

from nixin.cli import main


def run_json(capsys, *argv):
    rc = main(["--json", "--host", "t", *argv])
    out = capsys.readouterr().out
    return rc, json.loads(out)


def test_all_merges_four_kinds(es_transport, cache_dir, capsys):
    es_transport.add("GET", r"/data/options-master\.json$", fixture_file="hm/options-master.json")
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox", "--limit", "3")
    assert rc == 0 and doc["ok"] is True
    assert doc["source"] == "all" and doc["channel"] == "unstable" and doc["rev"] is None
    kinds = [r["kind"] for r in doc["results"]]
    assert kinds[:3] == ["pkg", "pkg", "pkg"] and "opt" in kinds and "program" in kinds
    assert set(doc["sources"]) == {"search.nixos.org", "home-manager-options"}
    es = doc["sources"]["search.nixos.org"]
    assert (
        es["kinds"] == ["pkg", "opt", "program"]
        and es["error"] is None
        and es["count"] == len(kinds) - kinds.count("hm")
    )
    hm = doc["sources"]["home-manager-options"]
    assert (
        hm["kinds"] == ["hm"] and hm["count"] == 0 and hm["channel"] == "unstable" and hm["updated"]
    )
    assert doc["cached"] is False


def test_all_with_one_source_down_is_partial_success(es_transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox")
    assert rc == 0 and doc["ok"] is True
    assert doc["source"] == "search.nixos.org"  # the only source that answered
    hm = doc["sources"]["home-manager-options"]
    assert hm["error"]["code"] == "network" and hm["kinds"] == ["hm"] and hm["count"] == 0
    assert all(r["kind"] != "hm" for r in doc["results"])


def test_all_with_every_source_down_fails(transport, cache_dir, capsys):
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox")
    assert rc == 1 and doc["ok"] is False and doc["error"]["code"] == "network"


def test_all_human_footer_names_the_failed_source(es_transport, cache_dir, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "120")
    assert main(["search", "--kind", "all", "firefox", "--limit", "2"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("KIND")
    assert (
        "home-manager-options FAILED:" in lines[-1]
        and "search.nixos.org unstable@1c89083e" in lines[-1]
    )


def test_info_all_returns_every_exact_match(es_transport, cache_dir, capsys):
    es_transport.add("GET", r"/data/options-master\.json$", fixture_file="hm/options-master.json")
    rc, doc = run_json(capsys, "info", "programs.kitty.enable")
    assert rc == 0 and [r["kind"] for r in doc["results"]] == ["hm"]
    assert doc["sources"]["search.nixos.org"]["count"] == 0
    rc, doc = run_json(capsys, "info", "nothing-anywhere")
    assert rc == 1 and doc["error"]["code"] == "not-found"
    assert (
        doc["error"]["message"]
        == "no package or NixOS option or Home Manager option named 'nothing-anywhere' on unstable"
    )
