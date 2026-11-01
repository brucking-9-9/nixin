"""local.py drives `nix`; here `subprocess.run` is replaced by a fake that answers from fixtures."""

import json
import subprocess

import pytest
from conftest import fixture

from nixin.cache import Cache
from nixin.cli import main
from nixin.discovery import DiscoveryError, local

REF = "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"


class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture
def fake_nix(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        assert argv[0] == "nix" and argv[1:3] == local.NIX_FLAGS
        cmd = argv[3:]
        if cmd[:3] == ["flake", "metadata", "--json"]:
            return Done(json.dumps(fixture("local/flake_metadata.json")))
        if cmd[:2] == ["search", "--json"]:
            if cmd[-1] == "nothing-matches":
                return Done("", "error: no results for the given search term(s)!", 1)
            return Done(json.dumps(fixture("local/search_kitty.json")))
        if cmd[:2] == ["eval", "--json"]:
            installable = cmd[2]
            if installable.endswith(".kitty"):
                return Done(json.dumps(fixture("local/eval_kitty.json")))
            if installable.endswith(".nope"):
                return Done(
                    "",
                    f"error: flake '{REF}' does not provide attribute '{installable.split('#')[1]}'",
                    1,
                )
            return Done("", "error: something else broke", 1)
        raise AssertionError(f"unexpected nix call {cmd}")

    monkeypatch.setattr(local.subprocess, "run", run)
    monkeypatch.setattr(local, "current_system", lambda: "x86_64-linux")
    return calls


@pytest.fixture
def cache(cache_dir):
    return Cache()


def test_current_system_maps_machine(monkeypatch):
    monkeypatch.setattr(local.platform, "machine", lambda: "x86_64")
    assert local.current_system() == "x86_64-linux"
    monkeypatch.setattr(local.platform, "machine", lambda: "aarch64")
    assert local.current_system() == "aarch64-linux"
    monkeypatch.setattr(local.platform, "machine", lambda: "riscv64")
    assert local.current_system() == "riscv64-linux"


def test_resolve_reads_flake_metadata(fake_nix, cache):
    meta = local.resolve(cache, REF)
    assert meta["rev"] == REV and meta["path"].startswith("/nix/store/")
    local.resolve(cache, REF)
    assert len(fake_nix) == 1  # cached


def test_search_ranks_and_strips_prefix(fake_nix, cache):
    part = local.search(cache, REF, "kitty", 10)
    assert (
        part.source == "local-nix"
        and part.channel == REF
        and part.rev == REV
        and part.cached is False
    )
    assert [r["name"] for r in part.results] == [
        "kitty",
        "vimPlugins.nvim-treesitter-parsers.kitty",
        "kitty-img",
        "kitty-themes",
    ]
    kitty = part.results[0]
    assert kitty["kind"] == "pkg" and kitty["version"] == "0.49.2" and kitty["pname"] == "kitty"
    assert (
        kitty["description"].startswith("Fast")
        and kitty["license"] == []
        and kitty["unfree"] is None
    )
    assert part.extra == {"system": "x86_64-linux", "total": 4}
    assert fake_nix[-1][3:] == ["search", "--json", f"{REF}#legacyPackages.x86_64-linux", "kitty"]
    assert len(local.search(cache, REF, "kitty", 1).results) == 1
    assert local.search(cache, REF, "kitty", 1).cached is True


def test_search_slow_first_run_gets_a_note(fake_nix, cache, monkeypatch):
    clock = iter([0.0, 40.0])
    monkeypatch.setattr(local.time, "monotonic", lambda: next(clock))
    part = local.search(cache, REF, "kitty", 5)
    assert len(part.notes) == 1 and "40s" in part.notes[0] and "eval cache" in part.notes[0]
    assert local.search(cache, REF, "kitty", 5).notes == []  # cached: no note


def test_search_fast_run_has_no_note(fake_nix, cache, monkeypatch):
    clock = iter([0.0, 3.0])
    monkeypatch.setattr(local.time, "monotonic", lambda: next(clock))
    assert local.search(cache, REF, "kitty", 5).notes == []


def test_search_nix_failure(fake_nix, cache):
    with pytest.raises(DiscoveryError) as e:
        local.search(cache, REF, "nothing-matches", 5)
    assert e.value.code == "nix-failed" and "no results" in e.value.message


def test_info_record(fake_nix, cache):
    part = local.info(cache, REF, "kitty")
    (rec,) = part.results
    assert (
        rec["kind"] == "pkg"
        and rec["source"] == "local-nix"
        and rec["channel"] == REF
        and rec["rev"] == REV
    )
    assert (
        rec["name"] == "kitty" and rec["version"] == "0.49.2" and rec["license"] == ["GPL-3.0-only"]
    )
    assert rec["unfree"] is False and rec["broken"] is False and rec["main_program"] == "kitty"
    assert rec["programs"] == ["kitty"] and "terminfo" in rec["outputs"]
    assert rec["position"] == "pkgs/by-name/ki/kitty/package.nix:345"
    assert rec["homepage"].startswith("https://") and rec["matched_by"] == "attr"
    assert part.extra == {"system": "x86_64-linux"}
    assert fake_nix[-1][3:6] == ["eval", "--json", f"{REF}#legacyPackages.x86_64-linux.kitty"]
    assert fake_nix[-1][6:8] == ["--apply", local.APPLY]
    assert local.info(cache, REF, "kitty").cached is True


def test_info_missing_attribute_is_empty(fake_nix, cache):
    assert local.info(cache, REF, "nope").results == []


def test_info_other_nix_error_surfaces(fake_nix, cache):
    with pytest.raises(DiscoveryError) as e:
        local.info(cache, REF, "broken-eval")
    assert e.value.code == "nix-failed" and "something else broke" in e.value.message


def test_nix_missing_and_timeout(monkeypatch, cache):
    def missing(*a, **k):
        raise FileNotFoundError("nix")

    monkeypatch.setattr(local.subprocess, "run", missing)
    with pytest.raises(DiscoveryError) as e:
        local.resolve(cache, REF)
    assert e.value.code == "nix-missing"

    def slow(argv, **k):
        raise subprocess.TimeoutExpired(argv, k.get("timeout", 0))

    monkeypatch.setattr(local.subprocess, "run", slow)
    with pytest.raises(DiscoveryError) as e:
        local.resolve(cache, REF)
    assert e.value.code == "nix-timeout"


def test_strip_helpers():
    assert local._strip_attr("legacyPackages.x86_64-linux.a.b", "x86_64-linux") == "a.b"
    assert local._strip_attr("a.b", "x86_64-linux") == "a.b"
    assert local._strip_store("/nix/store/" + "a" * 32 + "-source/pkgs/x.nix:1") == "pkgs/x.nix:1"
    assert local._strip_store("/home/me/x.nix:1") == "/home/me/x.nix:1"
    assert local._strip_store(None) is None


def test_cli_search_with_nixpkgs(fake_nix, cache_dir, capsys):
    rc = main(["--json", "search", "kitty", "--nixpkgs", REF, "--limit", "2"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["source"] == "local-nix" and doc["channel"] == REF and doc["rev"] == REV
    assert doc["count"] == 2 and doc["results"][0]["name"] == "kitty"
    assert (
        doc["sources"]["local-nix"]["system"] == "x86_64-linux"
        and doc["sources"]["local-nix"]["total"] == 4
    )


def test_cli_info_with_nixpkgs(fake_nix, cache_dir, capsys):
    rc = main(["--json", "info", "--kind", "pkg", "kitty", "--nixpkgs", REF])
    doc = json.loads(capsys.readouterr().out)
    assert (
        rc == 0
        and doc["results"][0]["version"] == "0.49.2"
        and doc["results"][0]["unfree"] is False
    )
    rc = main(["--json", "info", "--kind", "pkg", "nope", "--nixpkgs", REF])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["error"]["code"] == "not-found"


def test_cli_info_all_with_rev_ref_answers_packages_only(fake_nix, cache_dir, capsys):
    """A bare rev cannot name a channel: options fail with bad-channel per source, the package still answers."""
    rc = main(["--json", "info", "kitty", "--nixpkgs", REF])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] is True and doc["count"] == 1
    assert doc["source"] == "local-nix" and doc["channel"] == REF
    assert doc["sources"]["search.nixos.org"]["error"]["code"] == "bad-channel"
    assert doc["sources"]["home-manager-options"]["error"]["code"] == "bad-channel"


def test_cli_all_attributes_local_failure_to_local_nix(fake_nix, es_transport, cache_dir, capsys):
    """A failing nix search under --kind all is reported under local-nix, not search.nixos.org."""
    rc = main(
        [
            "--json",
            "search",
            "--kind",
            "all",
            "nothing-matches",
            "--nixpkgs",
            REF,
            "--channel",
            "unstable",
        ]
    )
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] is True  # search.nixos.org still answered (with no hits)
    assert doc["sources"]["local-nix"]["error"]["code"] == "nix-failed"
    assert doc["sources"]["local-nix"]["kinds"] == ["pkg"]
    assert doc["sources"]["search.nixos.org"]["error"] is None
