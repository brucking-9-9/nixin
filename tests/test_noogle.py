import json
import re

import pytest

from nixin.cache import Cache
from nixin.cli import main
from nixin.discovery import noogle
from nixin.http import NetworkError


@pytest.fixture
def cache(cache_dir):
    return Cache()


@pytest.fixture
def noogle_transport(transport):
    transport.add("GET", r"noogle\.dev/api/v1/data$", fixture_file="noogle/data.json")
    return transport


def test_load_trims_and_caches(noogle_transport, cache):
    fns, cached = noogle.load(cache)
    assert cached is False and len(fns) == 4
    assert set(fns[0]) == {"name", "aliases", "signature", "description", "position", "primop"}
    by_name = {f["name"]: f for f in fns}
    cat = by_name["builtins.concatStringsSep"]
    assert (
        cat["primop"] is True
        and cat["signature"] == "concatStringsSep :: String -> [String] -> String"
    )
    assert (
        cat["description"] == "Concatenate a list of strings with a separator between each element"
    )
    assert "lib.strings.concatStringsSep" in cat["aliases"] and "lib.join" in cat["aliases"]
    assert re.fullmatch(r"lib/strings\.nix:\d+", cat["position"])
    _f, cached = noogle.load(cache)
    assert cached is True and noogle_transport.count("api/v1/data") == 1


def test_load_offline_is_network(transport, cache):
    with pytest.raises(NetworkError):
        noogle.load(cache)


def test_search_ranks_name_over_alias_over_description(noogle_transport, cache):
    part = noogle.search(cache, "concatStringsSep", 10)
    assert part.source == "noogle.dev" and part.channel is None
    names = [r["name"] for r in part.results]
    assert names[0] == "builtins.concatStringsSep"
    assert "lib.strings.concatStringsSep" in names and "lib.attrsets.mapAttrs" not in names
    assert part.results[0]["kind"] == "fn" and part.results[0]["rev"] is None
    assert [r["name"] for r in noogle.search(cache, "mapAttrs", 5).results] == [
        "lib.attrsets.mapAttrs"
    ]
    assert noogle.search(cache, "zzz-nothing", 5).results == []
    assert len(noogle.search(cache, "lib", 2).results) == 2


def test_search_multiword_requires_every_word(noogle_transport, cache):
    names = [r["name"] for r in noogle.search(cache, "builtins map", 5).results]
    assert (
        names[0] == "builtins.map"
    )  # lib.attrsets.mapAttrs follows via its builtins.mapAttrs alias
    assert noogle.search(cache, "builtins zzz", 5).results == []


def test_info_by_name_and_alias(noogle_transport, cache):
    (rec,) = noogle.info(cache, "builtins.map").results
    assert rec["name"] == "builtins.map"
    (rec,) = noogle.info(cache, "lib.join").results
    assert rec["name"] == "builtins.concatStringsSep"
    (rec,) = noogle.info(cache, "LIB.ATTRSETS.MAPATTRS").results
    assert rec["name"] == "lib.attrsets.mapAttrs"
    assert noogle.info(cache, "lib.nope").results == []


def test_helpers_tolerate_sparse_docs():
    assert noogle._path({"meta": {"title": "x"}}) == "x"
    assert noogle._path({}) == ""
    assert noogle._aliases({"meta": {"aliases": [["a", "b"], "c"]}}) == ["a.b", "c"]
    assert noogle._signature({"content": {"signature": " a ->\n b "}}) == "a -> b"
    assert noogle._signature({}) is None
    assert (
        noogle._description(
            {"content": {"content": "\n\n# Heading\n\nfirst line\nsecond\n\nthird"}}
        )
        == "first line second"
    )
    assert noogle._description({"content": {"content": ""}}) is None
    assert (
        noogle._position(
            {
                "meta": {
                    "attr_position": {
                        "file": "/nix/store/" + "a" * 32 + "-source/lib/x.nix",
                        "line": 3,
                    }
                }
            }
        )
        == "lib/x.nix:3"
    )
    assert noogle._position({"meta": {"attr_position": {"file": "/tmp/y.nix"}}}) == "/tmp/y.nix"
    assert noogle._position({}) is None


def test_cli_search_fn(noogle_transport, cache_dir, capsys):
    rc = main(["--json", "search", "--kind", "fn", "concatStringsSep", "--limit", "2"])
    doc = json.loads(capsys.readouterr().out)
    assert (
        rc == 0 and doc["source"] == "noogle.dev" and doc["channel"] is None and doc["count"] == 2
    )
    assert doc["results"][0]["kind"] == "fn"


def test_cli_info_fn(noogle_transport, cache_dir, capsys):
    rc = main(["--json", "info", "--kind", "fn", "lib.join"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["results"][0]["name"] == "builtins.concatStringsSep"
    rc = main(["--json", "info", "--kind", "fn", "lib.nope"])
    doc = json.loads(capsys.readouterr().out)
    assert (
        rc == 1
        and doc["error"]["code"] == "not-found"
        and doc["error"]["message"] == "no function named 'lib.nope'"
    )
