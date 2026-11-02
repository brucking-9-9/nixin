"""`--kind all` fans out to several sources and survives one of them failing."""

import json

from conftest import has

from nixin.cli import main

REV_UNSTABLE = "1c89083e451e891152365b799595cf7790a43325"


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


def _es_failing_for(transport, doc_type):
    """ES rules where every search of `doc_type` ("package"/"option") answers 503."""
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    transport.add("POST", r"/_search$", None, status=503, when=lambda b: has(b, "type", doc_type))
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/search_pkg_firefox.json",
        when=lambda b: has(b, "query", "firefox"),
    )
    transport.add("POST", r"/_search$", fixture_file="es/empty.json")


def test_all_keeps_a_sources_answers_when_another_of_its_kinds_fails(transport, cache_dir, capsys):
    # pkg and program answer, opt fails, Home Manager is down: still a result, not an error
    _es_failing_for(transport, "option")
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox", "--limit", "3")
    assert rc == 0 and doc["ok"] is True
    assert doc["source"] == "search.nixos.org" and doc["rev"] == REV_UNSTABLE
    es = doc["sources"]["search.nixos.org"]
    assert es["kinds"] == ["pkg", "opt", "program"] and es["error"]["code"] == "network"
    assert es["channel"] == "unstable" and es["count"] == doc["count"] > 0
    assert {r["kind"] for r in doc["results"]} == {"pkg", "program"}
    assert doc["sources"]["home-manager-options"]["error"]["code"] == "network"


def test_all_source_failing_first_then_answering_keeps_channel_and_rev(
    transport, cache_dir, capsys
):
    _es_failing_for(transport, "package")  # pkg (first kind) and program fail, opt answers
    transport.add("GET", r"/data/options-master\.json$", fixture_file="hm/options-master.json")
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox")
    assert rc == 0 and doc["ok"] is True and doc["source"] == "all"
    es = doc["sources"]["search.nixos.org"]
    assert es["channel"] == "unstable" and es["rev"] == REV_UNSTABLE
    assert es["error"]["code"] == "network" and es["cached"] is False


def test_all_reports_the_concrete_channel(es_transport, cache_dir, capsys):
    es_transport.add(
        "GET", r"/data/options-release-26\.05\.json$", fixture_file="hm/options-master.json"
    )
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox", "--channel", "stable")
    assert rc == 0 and doc["source"] == "all"
    assert doc["channel"] == "26.05"  # not "stable": the document names what it searched
    assert {s["channel"] for s in doc["sources"].values()} == {"26.05"}


def test_info_not_found_names_only_the_kinds_that_were_asked(es_transport, cache_dir, capsys):
    # no Home Manager rule: that source is unreachable, so it cannot vouch for "not found"
    rc, doc = run_json(capsys, "info", "nothing-anywhere")
    assert rc == 1 and doc["error"]["code"] == "not-found"
    assert doc["error"]["message"] == (
        "no package or NixOS option named 'nothing-anywhere' on unstable;"
        " not asked: Home Manager option (network)"
    )


def test_unexpected_exception_is_an_internal_error_document(monkeypatch, cache_dir, capsys):
    from nixin.discovery import home_manager

    def boom(*a, **k):
        raise KeyError("options")

    monkeypatch.setattr(home_manager, "search", boom)
    rc = main(["--json", "--host", "t", "search", "--kind", "hm", "x"])
    out, err = capsys.readouterr()
    doc = json.loads(out)  # still exactly one document on stdout, no traceback
    assert rc == 1 and err == "" and doc["ok"] is False
    assert doc["error"]["code"] == "internal" and "KeyError" in doc["error"]["message"]


def test_unexpected_exception_in_one_source_spares_the_others(
    es_transport, monkeypatch, cache_dir, capsys
):
    from nixin.discovery import home_manager

    def boom(*a, **k):
        raise TypeError("shape changed")

    monkeypatch.setattr(home_manager, "search", boom)
    rc, doc = run_json(capsys, "search", "--kind", "all", "firefox")
    assert rc == 0 and doc["ok"] is True and doc["source"] == "search.nixos.org"
    assert doc["sources"]["home-manager-options"]["error"]["code"] == "internal"
