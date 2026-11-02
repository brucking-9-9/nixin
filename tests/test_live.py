"""Live smoke test of the discovery verbs against the real services (`NIXIN_LIVE=1`).

Each case runs the CLI in-process and checks only the contract: exactly one JSON
document on stdout, nothing on stderr, the exit code matching `ok`, and the
expected source or error code. `--no-cache` keeps nixin's cache untouched.
The `--nixpkgs` cases also need `NIXIN_LIVE_NIXPKGS=<flakeref>` (ideally the
locked rev, already in the store); they run the host's nix, and the first
search on a rev takes about a minute.
"""

import json
import os

import pytest
from conftest import live

from nixin.cli import main

NIXPKGS = os.environ.get("NIXIN_LIVE_NIXPKGS")


def case(*argv, source=None, code=None):
    return pytest.param(list(argv), source, code, id=" ".join(argv))


CASES = [
    case("search", "firefox", source="search.nixos.org"),
    case("search", "--kind", "opt", "services.openssh", source="search.nixos.org"),
    case("search", "--kind", "hm", "programs.kitty", source="home-manager-options"),
    case("search", "--kind", "program", "rg", source="search.nixos.org"),
    case("search", "--kind", "fn", "concatStringsSep", source="noogle.dev"),
    case("search", "--kind", "all", "kitty", "--limit", "3", source="all"),
    case("search", "--channel", "99.99", "x", code="bad-channel"),
    case("info", "--kind", "pkg", "kitty", source="search.nixos.org"),
    case("info", "--kind", "pkg", "firefox", "--channel", "stable", source="search.nixos.org"),
    case("info", "programs.kitty.enable", source="all"),
    case("info", "no-such-thing-xyz", code="not-found"),
    case("versions", "firefox", "--limit", "5", source="nixhub.io"),
    case("versions", "no-such-pkg-xyz", code="not-found"),
]

LOCAL_CASES = [
    case("search", "kitty", source="local-nix"),
    case("info", "--kind", "pkg", "kitty", source="local-nix"),
    case("info", "--kind", "pkg", "no-such-attr-xyz", code="not-found"),
]


def _check(capsys, argv, source, code):
    rc = main(["--json", "--host", "live", *argv, "--no-cache"])
    out, err = capsys.readouterr()
    doc, end = json.JSONDecoder().raw_decode(out)
    assert out[end:].strip() == "" and err == ""  # one document, no noise
    assert rc == (0 if doc["ok"] else 1)
    if code:
        assert doc["ok"] is False and doc["error"]["code"] == code
    else:
        assert doc["ok"] is True and doc["source"] == source and doc["count"] > 0
        assert all(r["source"] in doc["sources"] for r in doc["results"])
    return doc


@live
@pytest.mark.parametrize("argv, source, code", CASES)
def test_live(argv, source, code, capsys):
    _check(capsys, argv, source, code)


@live
@pytest.mark.skipif(not NIXPKGS, reason="set NIXIN_LIVE_NIXPKGS=<flakeref> for nix cases")
@pytest.mark.parametrize("argv, source, code", LOCAL_CASES)
def test_live_nixpkgs(argv, source, code, capsys):
    doc = _check(capsys, [*argv, "--nixpkgs", str(NIXPKGS)], source, code)
    if not code:
        assert doc["channel"] == NIXPKGS and doc["rev"]
