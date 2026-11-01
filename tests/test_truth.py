"""Truth layer, offline: recorded `nix eval` outputs under tests/fixtures, a fake nix seam.

Live tests (they run the host's nix against $NIXIN_CONFIG / /etc/nixos) are skipped unless
NIXIN_LIVE=1: `nix build` runs this file in a sandbox where nix cannot run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nixin import configrepo
from nixin.configrepo import ConfigRepo, NixinError, nix_value, parse_lock, trim_nix_error
from nixin.truth import options, packages, scan
from nixin.truth.cache import TruthCache

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_STORE = "/nix/store/4naz5g774cwbir62rw4fgdzrjv1m8rgc-source"
NIXPKGS_STORE = "/nix/store/6nwzgfvznah5rg192x0hnl5ykchdgi59-source"
HM_STORE = "/nix/store/9k8yg0sk8ka6krz4qp58n7b6gc9malqk-source"

MBP13_STDERR = """error:
       … while calling the 'head' builtin
         at «github:nixos/nixpkgs/3ec6»/lib/attrsets.nix:1711:13:
         1710|           if length values == 1 then
         1711|             head values
             |             ^

       … while evaluating the option `system.build.toplevel':

       (stack trace truncated; use '--show-trace' to show the full, detailed trace)

       error:
       Failed assertions:
       - The option definition `boot.initrd.systemd.enable' in `/nix/store/x-source/hosts/mbp13' no longer has any effect; please remove it.
       Scripted initrd has been removed.
"""


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeNix:
    """Answers the nix invocations configrepo/truth make, from fixtures; records calls."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        joined = " ".join(args)
        if args[:2] == ["flake", "metadata"]:
            return json.dumps(
                {
                    "path": CONFIG_STORE,
                    "fingerprint": "fp-1",
                    "revision": "4878ba5b8fd854ccef08158b938280d5bfde25c1",
                    "lastModified": 1793345016,
                }
            )
        if args[:2] == ["flake", "archive"]:
            return json.dumps(
                {
                    "path": CONFIG_STORE,
                    "inputs": {
                        "nixpkgs": {"path": NIXPKGS_STORE, "inputs": {}},
                        "home-manager": {"path": HM_STORE, "inputs": {}},
                        "nixos-hardware": {
                            "path": "/nix/store/66r-source",
                            "inputs": {"nixpkgs": {"path": "/nix/store/0pc-source", "inputs": {}}},
                        },
                    },
                }
            )
        if joined.endswith("#nixosConfigurations --apply builtins.attrNames"):
            return '["19BK","mbp13"]'
        if "mbp13" in joined and "toplevel" in joined:
            raise NixinError("eval-failed", trim_nix_error(MBP13_STDERR), stderr=MBP13_STDERR)
        if "toplevel" in joined:
            return "/nix/store/abc-nixos-system-19BK-26.11.drv"
        if "--apply" in args:
            apply = args[args.index("--apply") + 1]
            if "programsFromOptions" in apply:  # packages.nix
                return json.dumps(
                    {
                        "system": {
                            "packages": [
                                {"name": "ripgrep-15.2.0", "pname": "ripgrep", "version": "15.2.0"},
                                {"name": "quiet", "pname": None, "version": None},
                            ],
                            "programs": ["bash", "zsh"],
                        },
                        "home": {
                            "brucking": {
                                "packages": [
                                    {"name": "kitty-0.49.2", "pname": "kitty", "version": "0.49.2"}
                                ],
                                "programs": ["kitty", "git"],
                            }
                        },
                    }
                )
            if "nixin-option" in apply:  # options.nix; the ARGS attrset is the tail
                tail = apply[-400:]
                if 'hm = "brucking"' in tail and '"programs" "kitty"' in tail:
                    return (FIXTURES / "prefix-home-brucking-programs.kitty.json").read_text()
                if "hm = null" in tail and '"services" "openssh"' in tail:
                    return (FIXTURES / "prefix-nixos-services.openssh.json").read_text()
                return "[]"
        raise AssertionError(f"unexpected nix call: {args}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "flake.nix").write_text("{ }")
    (tmp_path / "flake.lock").write_text(json.dumps(fixture("flake.lock.json")))
    (tmp_path / "ssh.nix").write_text(
        "{ config, ... }:\n{\n  services.openssh = {\n    enable = true;\n"
        '    settings.PermitRootLogin = "no";\n  };\n}\n'
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(configrepo.ENV_CONFIG, raising=False)
    return ConfigRepo(tmp_path, nix=FakeNix())


# --- configrepo -------------------------------------------------------------------------


def test_locate_prefers_explicit_then_env(tmp_path, monkeypatch):
    with pytest.raises(NixinError) as e:
        ConfigRepo.locate(str(tmp_path / "nope"), nix=FakeNix())
    assert e.value.code == "config-not-found"
    (tmp_path / "flake.nix").write_text("{ }")
    monkeypatch.setenv(configrepo.ENV_CONFIG, str(tmp_path))
    assert ConfigRepo.locate(nix=FakeNix()).path == tmp_path.resolve()


def test_trim_nix_error_keeps_the_final_block():
    msg = trim_nix_error(MBP13_STDERR)
    assert msg.startswith("Failed assertions:")
    assert msg.endswith("Scripted initrd has been removed.")
    assert "while calling" not in msg and "stack trace" not in msg
    one = trim_nix_error(
        "warning: Git tree '/x' is dirty\ntrace: foo\nerror: flake does not provide attribute 'y'\n"
    )
    assert one == "flake does not provide attribute 'y'"


def test_parse_lock_follows_root_input_to_the_real_node():
    lock = parse_lock(fixture("flake.lock.json"))
    assert lock.node == "nixpkgs_2" and lock.name == "nixpkgs"
    assert lock.rev == "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
    assert lock.ref == "nixos-unstable" and lock.date == "2026-10-23"
    assert lock.flakeref == "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"


def test_nix_value_renders_literals():
    assert nix_value({"prefix": ["a", 'b"c'], "hm": None, "n": 2, "ok": True}) == (
        '{ prefix = [ "a" "b\\"c" ]; hm = null; n = 2; ok = true; }'
    )
    assert configrepo.nix_attr("19BK") == '"19BK"' and configrepo.nix_attr("mbp13") == "mbp13"


def test_source_lock_hosts_and_path_mapping(repo):
    src = repo.source()
    assert (
        src.head.startswith("4878ba5")
        and not src.dirty
        and src.source_hash == "4naz5g774cwbir62rw4fgdzrjv1m8rgc"
    )
    assert repo.hosts() == ["19BK", "mbp13"]
    assert repo.locate_path(f"{CONFIG_STORE}/ssh.nix").doc()["file"] == "ssh.nix"
    assert repo.locate_path(f"{NIXPKGS_STORE}/flake.nix").source == "inline"
    hm = repo.locate_path(f"{HM_STORE}/modules/programs/kitty.nix")
    assert (hm.source, hm.file) == ("home-manager", "modules/programs/kitty.nix")
    assert repo.locate_path("/nix/store/0pc-source/lib/x.nix").source == "nixos-hardware/nixpkgs"
    assert repo.locate_path("<unknown-file>").source == "other"
    assert repo.cache_key("19BK") != repo.cache_key("mbp13")


def test_toplevel_failure_is_a_clean_eval_failed(repo):
    with pytest.raises(NixinError) as e:
        repo.eval_raw("nixosConfigurations.mbp13.config.system.build.toplevel.drvPath")
    assert e.value.code == "eval-failed"
    assert "Scripted initrd has been removed." in e.value.message


# --- cache ------------------------------------------------------------------------------


def test_cache_roundtrip_prune_and_refresh(repo):
    c = TruthCache(repo, "19BK")
    assert c.get("packages") is None and c.misses == ["packages"]
    assert c.put("packages", {"x": 1})
    assert TruthCache(repo, "19BK").get("packages") == {"x": 1}
    stale = c.dir / "19BK-0000000000000000-packages.json"
    stale.write_text("{}")
    c.put("packages", {"x": 2})
    assert not stale.exists()
    off = TruthCache(repo, "19BK", enabled=False)
    assert off.get("packages") is None  # --refresh skips reads …
    assert off.put("packages", {"x": 3}) and c.get("packages") == {"x": 3}  # … but still writes


# --- packages ---------------------------------------------------------------------------


def test_packages_collect_find_programs(repo):
    doc = packages.collect(repo, "19BK", TruthCache(repo, "19BK"))
    assert doc["hm_users"] == ["brucking"]
    assert packages.counts(doc) == {"system": 2, "home": {"brucking": 1}}
    assert [p["scope"] for p in packages.find(doc, "kitty")] == ["home"]
    assert packages.find(doc, "ripgrep")[0]["version"] == "15.2.0"
    assert packages.find(doc, "quiet")[0]["pname"] is None
    assert packages.programs(doc, "kitty") == [
        {"scope": "home", "user": "brucking", "option": "programs.kitty.enable"}
    ]
    n = len(repo.nix.calls)
    packages.collect(repo, "19BK", TruthCache(repo, "19BK"))
    assert len(repo.nix.calls) == n  # second time from the cache


# --- options ----------------------------------------------------------------------------


def test_prefix_query_and_entry_shape(repo):
    cache = TruthCache(repo, "19BK")
    q = options.prefix_query(repo, "19BK", cache, ["services", "openssh"])
    enable = options.lookup(q["entries"], "services.openssh.enable")
    assert enable["value"] is True and enable["isDefined"]
    assert enable["definitionValues"] == [{"file": f"{CONFIG_STORE}/ssh.nix", "value": True}]
    assert enable["declarationPositions"][0]["line"] == 247
    parent, rest = options.longest_parent(
        q["entries"], ["services", "openssh", "settings", "PermitRootLogin"]
    )
    assert parent["name"] == "services.openssh.settings" and rest == ["PermitRootLogin"]
    assert options.dig(parent["value"], rest) == (True, "no")
    assert options.dig(parent["value"], ["Nope"]) == (False, None)
    summary = options.summarize(q)
    assert summary["total"] == 3 and summary["defined"] == 3 and summary["excluded"] == 0


def test_evaluate_excludes_poisoned_options_by_marker(repo):
    calls = {"n": 0}

    def nix(args, **kw):
        calls["n"] += 1
        apply = args[args.index("--apply") + 1]
        if 'exclude = [ "hardware.nvidia.open" ]' not in apply[-300:]:  # the ARGS, not the helper
            raise NixinError(
                "eval-failed",
                "expected a set but found null: null",
                stderr="error:\n … while evaluating list element at index 731\n"
                " … while evaluating 'nixin-option hardware.nvidia.open'\n"
                "error: expected a set but found null: null\n",
            )
        return '[{"name": "hardware.nvidia.enabled", "isDefined": false}]'

    repo.nix = nix
    doc = options.evaluate(repo, "19BK", prefix=["hardware", "nvidia"])
    assert calls["n"] == 2 and doc["retries"] == 1
    assert list(doc["excluded"]) == ["hardware.nvidia.open"]
    assert doc["entries"][0]["name"] == "hardware.nvidia.enabled"


def test_evaluate_gives_up_without_a_marker(repo):
    def nix(args, **kw):
        raise NixinError("eval-failed", "boom", stderr="error: boom\n")

    repo.nix = nix
    with pytest.raises(NixinError) as e:
        options.evaluate(repo, "19BK")
    assert e.value.message == "boom"


# --- scan -------------------------------------------------------------------------------

NESTED = """{ config, pkgs, ... }:
{
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "no";  # comment with enable = false
    };
  };
  programs.kitty.enable = true;
  home.packages = with pkgs; [
    ripgrep
    kitty
  ];
}
"""


def test_find_option_lines_follows_nesting():
    best = scan.best_option_line(NESTED, ["services", "openssh", "enable"])
    assert (best.line, best.score) == (4, 3)
    best = scan.best_option_line(NESTED, ["services", "openssh", "settings", "PermitRootLogin"])
    assert (best.line, best.score) == (6, 3)
    assert scan.best_option_line(NESTED, ["programs", "kitty", "enable"]).line == 9
    assert scan.best_option_line(NESTED, ["programs", "git", "enable"]) is None


def test_find_option_lines_suffix_match_through_function_header():
    text = "{\n  outputs = { self, nixpkgs }: {\n    nixosConfigurations.x = nixpkgs.lib.nixosSystem {\n      modules = [ {\n        home-manager.users.me = ./home.nix;\n      } ];\n    };\n  };\n}\n"
    best = scan.best_option_line(text, ["home-manager", "users", "me"])
    assert best is not None and best.line == 5


def test_grep_package_ranks_bare_and_pkgs_refs_first(tmp_path):
    (tmp_path / "a.nix").write_text(NESTED)
    (tmp_path / "b.nix").write_text('{ x = "kitty --detach"; }\n# kitty\n')
    hits = scan.grep_package(tmp_path, "kitty")
    assert [(h.file, h.line, h.score) for h in hits[:2]] == [("a.nix", 12, 3), ("a.nix", 9, 2)]
    assert hits[-1].score == 0  # the comment


# --- verbs through the CLI --------------------------------------------------------------


def test_status_json_contract(repo, monkeypatch, capsys):
    from nixin.cli import main

    monkeypatch.setattr(configrepo, "run_nix", repo.nix)
    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    monkeypatch.setattr(
        configrepo,
        "running_system",
        lambda root="/run/current-system": {
            "system": None,
            "version": "26.11.20261023.3ec6363",
            "nixpkgs_rev": "3ec6363",
        },
    )
    monkeypatch.setattr("nixin.truth.running_system", configrepo.running_system)
    rc = main(["--json", "--host", "19BK", "status", "--quick"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] and doc["verb"] == "status"
    assert doc["lock"]["node"] == "nixpkgs_2" and doc["running"]["matches_lock"] is True
    assert doc["eval"]["ok"] and doc["packages"] == {"system": 2, "home": {"brucking": 1}}
    assert doc["hm_users"] == ["brucking"] and doc["options"] == {}
    rc = main(["--json", "--host", "mbp13", "status", "--quick"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["ok"] is False and doc["error"]["code"] == "eval-failed"
    assert "Scripted initrd has been removed." in doc["error"]["message"]
    rc = main(["--json", "--host", "nope", "status"])
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown-host"


def test_where_option_and_package(repo, monkeypatch, capsys):
    from nixin.cli import main

    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    rc = main(["--json", "--host", "19BK", "where", "services.openssh.settings.PermitRootLogin"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["kind"] == "option"
    s = doc["scopes"][0]
    assert s["scope"] == "nixos" and s["mode"] == "sub"
    o = s["options"][0]
    assert o["value"] == "no" and o["option"] == "services.openssh.settings"
    assert o["definitions"][0]["file"] == "ssh.nix" and o["definitions"][0]["line"] == 5
    assert o["definitions"][0]["line_heuristic"] is True
    assert o["declarations"][0]["source"] == "nixpkgs" and o["declarations"][0]["line"] == 475

    rc = main(["--json", "--host", "19BK", "where", "programs.kitty.enable"])
    doc = json.loads(capsys.readouterr().out)
    assert [s["scope"] for s in doc["scopes"]] == ["home"]
    d = doc["scopes"][0]["options"][0]["definitions"][0]
    assert (
        d["file"] == "home/modules/terminal/kitty.nix" and "line" not in d
    )  # file absent in the test repo

    rc = main(["--json", "--host", "19BK", "where", "kitty"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "package" and doc["installed"][0]["user"] == "brucking"
    assert doc["programs"][0]["option"] == "programs.kitty.enable"

    rc = main(["--json", "--host", "19BK", "where", "no.such.option"])
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not-found"


# --- live -------------------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("NIXIN_LIVE") != "1", reason="set NIXIN_LIVE=1 to run nix")


@live
def test_live_hosts_and_lock():
    repo = ConfigRepo.locate()
    assert repo.hosts()
    assert repo.lock().rev and repo.source().store_path.startswith("/nix/store/")


@live
def test_live_prefix_query():
    repo = ConfigRepo.locate()
    host = repo.hosts()[0]
    q = options.evaluate(repo, host, prefix=["networking", "hostName"], values="all")
    assert options.lookup(q["entries"], "networking.hostName")["value"]
