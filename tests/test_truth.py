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


# environment.systemPackages as a prefix query reports it (trimmed): which file lists what
SYSTEM_PACKAGES = {
    "name": "environment.systemPackages",
    "type": "list of package",
    "isDefined": True,
    "declarations": [f"{NIXPKGS_STORE}/nixos/modules/config/system-path.nix"],
    "definitions": [f"{CONFIG_STORE}/hosts/x", f"{NIXPKGS_STORE}/nixos/modules/x.nix"],
    "definitionValues": [
        {"file": f"{CONFIG_STORE}/hosts/x", "value": ["<derivation ripgrep-15.2.0>"]},
        {"file": f"{NIXPKGS_STORE}/nixos/modules/x.nix", "value": ["<derivation quiet>"]},
    ],
}


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
                    entries = fixture("prefix-nixos-services.openssh.json")
                    if '"PermitRootLogin"' in tail:  # options.nix follows the rest: `sub`
                        sub = fixture("sub-services.openssh.settings.PermitRootLogin.json")
                        entries[2]["sub"] = sub
                    return json.dumps(entries)
                if "hm = null" in tail and '"environment" "systemPackages"' in tail:
                    return json.dumps([SYSTEM_PACKAGES])
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


def test_run_nix_never_lets_nix_read_stdin(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "{}", ""

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return Done()

    monkeypatch.setattr(configrepo.shutil, "which", lambda exe: f"/bin/{exe}")
    monkeypatch.setattr(configrepo.subprocess, "run", fake_run)
    assert configrepo.run_nix(["eval", "--json", "x"]) == "{}"
    assert seen["stdin"] is configrepo.subprocess.DEVNULL and seen["cmd"][0] == "/bin/nix"


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


def test_directory_module_maps_to_default_nix(tmp_path):
    (tmp_path / "hosts" / "x").mkdir(parents=True)
    (tmp_path / "hosts" / "x" / "default.nix").write_text("{ }")
    repo = ConfigRepo(tmp_path, nix=FakeNix(), _meta={"path": str(tmp_path)})
    loc = repo.locate_path(f"{tmp_path}/hosts/x")  # how nix names `imports = [ ./hosts/x ]`
    assert (loc.source, loc.file) == ("config", "hosts/x/default.nix")


def test_toplevel_failure_is_a_clean_eval_failed(repo):
    with pytest.raises(NixinError) as e:
        repo.eval_raw("nixosConfigurations.mbp13.config.system.build.toplevel.drvPath")
    assert e.value.code == "eval-failed"
    assert "Scripted initrd has been removed." in e.value.message


# --- cache ------------------------------------------------------------------------------


def test_cache_dir_shares_discoverys_root(tmp_path, monkeypatch):
    from nixin.truth.cache import cache_dir

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/cache")  # the XDG spec says ignore it
    assert cache_dir() == tmp_path / ".cache" / "nixin" / "truth"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_dir() == tmp_path / "xdg" / "nixin" / "truth"


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
    summary = options.summarize(q, CONFIG_STORE)
    assert summary["total"] == 3 and summary["excluded"] == 0
    # ports only carries its default (defined in the declaring file): not counted
    assert summary["defined"] == 2 and summary["in_config"] == 2
    assert not options.is_set(options.lookup(q["entries"], "services.openssh.ports"))


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


def test_index_seeds_learns_and_persists_exclusions(repo):
    base, args_seen = repo.nix, []

    def nix(args, **kw):
        if "--apply" not in args:
            return base(args, **kw)
        tail = args[args.index("--apply") + 1].splitlines()[-1]  # `) { ...ARGS }`
        args_seen.append(tail)
        if "hm = null" in tail and '"services.new.thing"' not in tail:
            raise NixinError(
                "eval-failed",
                "boom\nsecond line",
                stderr="error:\n … while evaluating 'nixin-option services.new.thing'\nerror: boom\n",
            )
        return '[{"name": "a.b", "isDefined": true}]'

    repo.nix = nix
    doc = options.index(repo, "19BK", TruthCache(repo, "19BK"))
    assert len(args_seen) == 2 and '"hardware.nvidia.open"' in args_seen[0]  # seeded
    assert len(doc["excluded"]) == len(options.SEED_EXCLUSIONS["nixos"]) + 1
    store = options.ExclusionStore(repo, "19BK")
    assert store.path.name == "exclude-3ec636362e18-c216bf5a1f17-19BK.json"
    saved = json.loads(store.path.read_text())
    assert saved["nixos"]["services.new.thing"] == "boom"  # first line only
    assert saved["nixpkgs"].startswith("3ec6363") and saved["host"] == "19BK"
    assert saved["home"] == options.SEED_EXCLUSIONS["home"]

    args_seen.clear()  # a new config tree (cache miss) on the same lock: one eval
    options.index(repo, "19BK", TruthCache(repo, "19BK", enabled=False))
    assert len(args_seen) == 1 and '"services.new.thing"' in args_seen[0]
    args_seen.clear()
    hm = options.index(repo, "19BK", TruthCache(repo, "19BK"), hm="brucking")
    assert len(args_seen) == 1 and '"programs.radicle.uri.web-rad.browser"' in args_seen[0]
    assert options.summarize(hm)["excluded"] == len(options.SEED_EXCLUSIONS["home"])


def test_exclusions_are_kept_per_host_and_old_files_ignored(repo):
    """An option learned as poisoned on one host is not skipped on another (nvidia on one
    machine only); the pre-host files (one per rev pair, mixing hosts) are not read."""
    a, b = options.ExclusionStore(repo, "19BK"), options.ExclusionStore(repo, "mbp13")
    assert a.path != b.path and a.path.parent == b.path.parent
    legacy = a.path.parent / "exclude-3ec636362e18-c216bf5a1f17.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"nixos": {"from.another.host": "x"}, "home": {}}))
    a.add("nixos", {"hardware.nvidia.open": "expected a set", "only.on.19BK": "boom"})
    assert "only.on.19BK" in a.load("nixos")
    assert "only.on.19BK" not in b.load("nixos") and "from.another.host" not in b.load("nixos")
    assert b.load("nixos") == options.SEED_EXCLUSIONS["nixos"]
    assert options.ExclusionStore(repo, "19 BK/x").path.name.endswith("-19_BK_x.json")


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
    best = scan.best_option_line(NESTED, ["services", "openssh", "settings"])
    assert (best.line, best.score) == (5, 3)  # `settings = {`, not the line inside it
    best = scan.best_option_line(NESTED, ["programs", "kitty"])
    assert (best.line, best.score) == (9, 2)  # `programs.kitty.enable = …` defines it
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
    rc = main(["--json", "--host", "19BK", "status"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] and doc["verb"] == "status"
    assert doc["lock"]["node"] == "nixpkgs_2" and doc["running"]["matches_lock"] is True
    assert doc["eval"]["ok"] and doc["packages"] == {"system": 2, "home": {"brucking": 1}}
    assert doc["hm_users"] == ["brucking"] and doc["options"] == {}  # no index by default
    rc = main(["--json", "--host", "19BK", "status", "--full"])
    full = json.loads(capsys.readouterr().out)["options"]
    assert full["nixos"]["total"] == 0 and full["nixos"]["excluded"] == 8
    saved = json.loads(options.ExclusionStore(repo, "19BK").path.read_text())  # first pass writes it
    assert saved["nixos"] == options.SEED_EXCLUSIONS["nixos"]
    assert set(full["home"]) == {"brucking"}
    n = len(repo.nix.calls)
    rc = main(["--json", "--host", "19BK", "status", "--quick"])  # the default, spelled out
    assert json.loads(capsys.readouterr().out)["options"] == full  # summaries from the cache
    assert not [c for c in repo.nix.calls[n:] if "--apply" in c and "attrNames" not in c[-1]]
    rc = main(["--json", "--host", "mbp13", "status"])
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

    (repo.path / "hosts" / "mbp13").mkdir(parents=True)
    (repo.path / "hosts" / "mbp13" / "default.nix").write_text("{ pkgs, ... }: [ pkgs.ripgrep ]\n")
    (repo.path / "common.nix").write_text("{ pkgs, ... }: [ pkgs.ripgrep ]\n")
    rc = main(["--json", "--host", "19BK", "where", "ripgrep"])
    doc = json.loads(capsys.readouterr().out)
    assert [(p["scope"], p["name"]) for p in doc["installed"]] == [("system", "ripgrep-15.2.0")]
    assert [(d["scope"], d["option"], d["items"]) for d in doc["declared"]] == [
        ("system", "environment.systemPackages", ["<derivation ripgrep-15.2.0>"])
    ]
    assert [m["file"] for m in doc["mentions"]] == ["common.nix"]  # not hosts/mbp13/…

    rc = main(["--json", "--host", "19BK", "where", "no.such.option"])
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not-found"


def test_nix_text_renders_values_as_nix():
    from nixin.truth.render import nix_text

    assert nix_text(True) == "true" and nix_text(None) == "null" and nix_text(22) == "22"
    assert nix_text("a\nb") == '"a…"' and nix_text("<derivation kitty-0.49.2>").startswith("<d")
    assert nix_text(["%h/x", 1]) == '[ "%h/x" 1 ]'
    assert nix_text({"19BK": {"a": False}, "<…>": "3 more attributes"}) == (
        '{ "19BK" = { a = false; }; … 3 more attributes }'
    )


def test_human_rendering(repo, monkeypatch, capsys):
    from nixin.cli import main

    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    assert main(["--host", "19BK", "status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("19BK evaluates") and "options   not indexed" in out
    assert "packages  2 system, 1 home brucking" in out

    assert main(["--host", "19BK", "where", "services.openssh"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "services.openssh  [nixos]  3 options, 2 set beyond their defaults"
    assert out[1].split() == ["enable", "=", "true", "ssh.nix:4"]
    assert out[2].startswith("  settings = { AllowUsers") and out[2].endswith("ssh.nix:5 + nixpkgs")

    assert main(["--host", "19BK", "where", "services.openssh.enable"]) == 0
    out = capsys.readouterr().out
    assert "services.openssh.enable = true  [nixos]" in out
    assert "  declared  nixpkgs nixos/modules/services/networking/ssh/sshd.nix:247" in out
    assert "  set in    ssh.nix:4" in out


# --- docs: type, default, example ---------------------------------------------------------


def test_where_option_has_type_default_example(repo, monkeypatch, capsys):
    from nixin.cli import main

    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    rc = main(["--json", "--host", "19BK", "where", "services.openssh.enable"])
    o = json.loads(capsys.readouterr().out)["scopes"][0]["options"][0]
    assert rc == 0 and o["type_info"] == {"name": "bool", "description": "boolean"}
    assert o["default"] == "false" and o["example"] is None and "sub_description" not in o

    rc = main(["--json", "--host", "19BK", "where", "services.openssh.settings.PermitRootLogin"])
    o = json.loads(capsys.readouterr().out)["scopes"][0]["options"][0]
    assert o["option"] == "services.openssh.settings"  # the real option, its type unchanged
    assert o["type"].startswith("open submodule")
    ti = o["type_info"]  # … and the type of the path asked, followed through the submodule
    assert ti["name"] == "nullOr" and ti["nested"]["elemType"]["values"][-1] == "no"
    assert o["default"] == '"prohibit-password"' and o["example"] is None
    assert o["sub_description"] == "Whether the root user can login using ssh."

    rc = main(["--json", "--host", "19BK", "where", "services.openssh.settings"])
    o = json.loads(capsys.readouterr().out)["scopes"][0]["options"][0]
    assert o["example"] == "{\n  UseDns = true;\n  PasswordAuthentication = false;\n}"

    assert main(["--host", "19BK", "where", "services.openssh.settings.PermitRootLogin"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith('  type      null or one of "yes", "without-password"')
    assert out[2].startswith("  inside    services.openssh.settings (open submodule")
    assert '  default   "prohibit-password"' in out
    assert "  about     Whether the root user can login using ssh." in out
    assert main(["--host", "19BK", "where", "services.openssh.settings"]) == 0
    out = capsys.readouterr().out
    assert "  example   { UseDns = true; PasswordAuthentication = false; }" in out


def test_option_info_modes(repo):
    from nixin.truth import option_info

    o = option_info(repo, "19BK", "services.openssh.ports")
    assert o["mode"] == "exact" and o["scope"] == "nixos" and o["user"] is None
    assert o["type_info"]["nested"]["elemType"]["name"] == "unsignedInt16"
    assert o["default"] == "[ 22 ]" and o["value"] == [22]
    o = option_info(repo, "19BK", ["services", "openssh", "settings", "PermitRootLogin"])
    assert o["mode"] == "sub" and o["value"] == "no" and o["name"].endswith("PermitRootLogin")
    o = option_info(repo, "19BK", "services.openssh")
    assert o["mode"] == "prefix" and len(o["options"]) == 3
    o = option_info(repo, "19BK", "programs.kitty.enable", hm="brucking")
    assert o["scope"] == "home" and o["user"] == "brucking" and o["type"] == "boolean"
    assert option_info(repo, "19BK", "no.such.option") is None


def test_literal_text():
    assert options.literal_text(None) is None
    assert options.literal_text({"text": "pkgs.kitty\n", "kind": "expression"}) == "pkgs.kitty"
    assert options.literal_text({"text": "the *usual*", "kind": "markdown"}) == "the *usual*"
    assert options.literal_text({"value": {"a": [1, "<derivation x>"]}}) == (
        "{ a = [ 1 <derivation x> ]; }"
    )
    assert options.literal_text({"value": None}) == "null"


NIXPKGS_LIB = os.environ.get("NIXIN_TEST_NIXPKGS_LIB")

# the option tree options.nix walks, built by the real module system (lib.evalModules)
DOCS_MODULES = """
{ lib, ... }:
let t = lib.types; in
{
  options.a.enable = lib.mkEnableOption "a";
  options.a.mode = lib.mkOption {
    type = t.enum [ "fast" "slow" ];
    default = "fast";
    example = lib.literalExpression ''"slow"'';
  };
  options.a.ports = lib.mkOption { type = t.listOf t.port; default = [ 22 ]; };
  options.a.dir = lib.mkOption {
    type = t.nullOr t.str;
    default = null;
    defaultText = lib.literalMD "the *state* directory";
  };
  options.a.broken = lib.mkOption { type = t.int; default = throw "no default"; };
  options.a.settings = lib.mkOption {
    default = { };
    type = t.submodule {
      freeformType = t.attrsOf (t.either t.bool t.int);
      options.Port = lib.mkOption { type = t.port; default = 22; description = "The port."; };
      options.log.level = lib.mkOption { type = t.enum [ "INFO" "DEBUG" ]; default = "INFO"; };
    };
  };
  options.a.users = lib.mkOption {
    default = { };
    type = t.attrsOf (t.submodule {
      options.shell = lib.mkOption { type = t.str; default = "bash"; };
    });
  };
  config.a.enable = true;
}
"""


def _options_nix(args: str) -> list[dict]:
    import subprocess

    helper = options.HELPER.read_text()
    expr = (
        f"let lib = import {NIXPKGS_LIB}; "
        f"ev = lib.evalModules {{ modules = [ ({DOCS_MODULES}) ]; }}; "
        f"in ({helper}) {args} {{ inherit lib; inherit (ev) options; }}"
    )
    out = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "--expr", expr],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


@pytest.mark.skipif(
    not NIXPKGS_LIB, reason="NIXIN_TEST_NIXPKGS_LIB (package.nix, the dev shell) is not set"
)
def test_options_nix_docs_on_the_real_module_system(storeless):
    """typeInfo, default/example and `sub` from lib.evalModules + lib.types, storeless."""
    q = '{ prefix = [ "a" ]; values = "all"; docs = true; }'
    got = {e["name"]: e for e in _options_nix(q)}
    assert got["a.enable"]["typeInfo"] == {"name": "bool", "description": "boolean"}
    assert got["a.enable"]["default"] == {"value": False}
    assert got["a.enable"]["example"] == {"value": True}
    assert got["a.mode"]["typeInfo"]["values"] == ["fast", "slow"]
    assert got["a.mode"]["example"] == {"text": '"slow"', "kind": "expression"}
    ports = got["a.ports"]["typeInfo"]
    assert ports["name"] == "listOf" and ports["nested"]["elemType"]["name"] == "unsignedInt16"
    assert got["a.dir"]["default"] == {"text": "the *state* directory", "kind": "markdown"}
    assert got["a.dir"]["typeInfo"]["nested"]["elemType"]["name"] == "str"
    assert "default" not in got["a.broken"]  # it throws: left out, the entry stays
    free = got["a.settings"]["typeInfo"]["nested"]["freeformType"]
    assert free["name"] == "attrsOf" and free["nested"]["elemType"]["name"] == "either"
    assert "sub" not in got["a.settings"]

    def sub(*path: str) -> dict:
        args = '{ prefix = [ "a" ' + " ".join(f'"{p}"' for p in path) + " ]; docs = true; }"
        (entry,) = _options_nix(args)
        return entry["sub"]

    port = sub("settings", "Port")  # a declared sub-option
    assert port["option"] == "a.settings.Port" and port["description"] == "The port."
    assert port["typeInfo"]["name"] == "unsignedInt16" and port["default"] == {"value": 22}
    assert sub("settings", "UseDns")["typeInfo"]["name"] == "either"  # the freeform type
    assert sub("settings", "log", "level")["typeInfo"]["values"] == ["INFO", "DEBUG"]
    assert sub("settings", "log") == {"group": ["level"]}
    assert sub("users", "bob", "shell")["typeInfo"]["name"] == "str"  # attrsOf submodule
    assert sub("users", "bob", "shell")["option"] == "a.users.bob.shell"
    assert sub("users", "bob", "nope") == {"missing": "nope"}
    assert sub("ports", "x") == {"opaque": "listOf"}


@pytest.fixture
def storeless(tmp_path, monkeypatch):
    import shutil

    if shutil.which("nix-instantiate") is None:
        pytest.skip("nix-instantiate not on PATH")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NIX_REMOTE", "dummy://")


# --- list -------------------------------------------------------------------------------

LIST_FLAKE = """{
  outputs = { nixpkgs, home-manager, ... }: {
    nixosConfigurations."19BK" = nixpkgs.lib.nixosSystem {
      modules = [
        ./configuration.nix
        home-manager.nixosModules.home-manager
        { home-manager.users.brucking = import ./home.nix; }
      ];
    };
  };
}
"""


def _list(repo, monkeypatch, capsys, *argv: str) -> tuple[int, dict]:
    from nixin.cli import main

    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    rc = main(["--json", "--host", "19BK", "list", *argv])
    return rc, json.loads(capsys.readouterr().out)


def _package_evals(repo) -> int:
    return sum("--apply" in c and "programsFromOptions" in c[-1] for c in repo.nix.calls)


def test_list_json_contract(repo, monkeypatch, capsys):
    rc, doc = _list(repo, monkeypatch, capsys)
    assert rc == 0 and doc["ok"] and doc["verb"] == "list" and doc["host"] == "19BK"
    assert (doc["scope"], doc["users"], doc["filter"]) == ("all", ["brucking"], None)
    assert [(p["scope"], p["user"], p["name"]) for p in doc["packages"]] == [
        ("home", "brucking", "kitty-0.49.2"),
        ("system", None, "quiet"),
        ("system", None, "ripgrep-15.2.0"),
    ]  # sorted by name across scopes
    assert doc["packages"][2] == {
        "scope": "system",
        "user": None,
        "name": "ripgrep-15.2.0",
        "pname": "ripgrep",
        "version": "15.2.0",
        "declared": [],
    }
    assert doc["count"] == 3 and doc["folded"] == 0 and "programs" not in doc
    # the test tree's flake.nix is `{ }`: no modules for the scanner, said so, not an error
    assert doc["warnings"] and all("declared[] left empty" in w for w in doc["warnings"])
    assert doc["cache"]["misses"] == ["packages"] and _package_evals(repo) == 1

    rc, doc = _list(repo, monkeypatch, capsys, "--home", "brucking")
    assert [p["name"] for p in doc["packages"]] == ["kitty-0.49.2"] and doc["scope"] == "home"
    assert doc["cache"]["hits"] == ["packages"] and _package_evals(repo) == 1  # from the cache
    _, doc = _list(repo, monkeypatch, capsys, "--scope", "system")
    assert [p["pname"] for p in doc["packages"]] == [None, "ripgrep"] and doc["users"] == []
    _, doc = _list(repo, monkeypatch, capsys, "--scope", "home")
    assert [p["pname"] for p in doc["packages"]] == ["kitty"] and doc["users"] == ["brucking"]
    _, doc = _list(repo, monkeypatch, capsys, "--scope", "all", "--home", "brucking")
    assert doc["count"] == 3  # --home narrows the HM users, --scope all keeps the system
    _, doc = _list(repo, monkeypatch, capsys, "--filter", "RIP")
    assert [p["name"] for p in doc["packages"]] == ["ripgrep-15.2.0"] and doc["count"] == 1
    _, doc = _list(repo, monkeypatch, capsys, "--filter", "0.49")  # name, not only pname
    assert [p["pname"] for p in doc["packages"]] == ["kitty"]
    _, doc = _list(repo, monkeypatch, capsys, "--programs")
    assert [(p["name"], p["scope"]) for p in doc["programs"]] == [
        ("bash", "system"),
        ("git", "home"),
        ("kitty", "home"),
        ("zsh", "system"),
    ]
    assert doc["programs"][2] == {
        "scope": "home",
        "user": "brucking",
        "name": "kitty",
        "option": "programs.kitty.enable",
    }
    _, doc = _list(repo, monkeypatch, capsys, "--programs", "--scope", "system", "--filter", "z")
    assert [p["name"] for p in doc["programs"]] == ["zsh"] and doc["packages"] == []

    rc, doc = _list(repo, monkeypatch, capsys, "--refresh")
    assert rc == 0 and doc["cache"]["enabled"] is False and _package_evals(repo) == 2


def test_list_errors(repo, monkeypatch, capsys):
    rc, doc = _list(repo, monkeypatch, capsys, "--home", "bob")
    assert rc == 1 and doc["error"]["code"] == "unknown-user"
    assert "'bob' is not a Home Manager user of 19BK (users: brucking)" in doc["error"]["message"]
    rc, doc = _list(repo, monkeypatch, capsys, "--home", "brucking", "--scope", "system")
    assert rc == 2 and doc["error"]["code"] == "usage"
    rc, doc = _list(repo, monkeypatch, capsys, "kitty")
    assert rc == 2 and doc["error"]["code"] == "usage" and "--filter" in doc["error"]["message"]
    rc, doc = _list(repo, monkeypatch, capsys, "--scope", "user")  # argparse's choices
    assert rc == 2 and doc["error"]["code"] == "usage" and doc["verb"] == "list"
    rc, doc = _list(repo, monkeypatch, capsys, "--host", "nope")
    assert rc == 1 and doc["error"]["code"] == "unknown-host"


def test_list_declared_from_the_scanner(repo, monkeypatch, capsys):
    (repo.path / "flake.nix").write_text(LIST_FLAKE)
    (repo.path / "configuration.nix").write_text(
        "{ pkgs, lib, ... }:\n{\n  imports = [ ./extra.nix ];\n"
        "  environment.systemPackages = with pkgs; [\n    ripgrep\n    quiet\n  ];\n}\n"
    )
    (repo.path / "extra.nix").write_text(
        "{ pkgs, lib, ... }:\n{\n  environment.systemPackages = lib.optionals true [ pkgs.ripgrep ];\n}\n"
    )
    (repo.path / "home.nix").write_text(
        "{ pkgs, ... }:\n{\n  home.packages = [ pkgs.kitty ];\n  programs.kitty.enable = true;\n}\n"
    )
    _, doc = _list(repo, monkeypatch, capsys)
    assert doc["warnings"] == []
    by = {(p["scope"], p["name"]): p["declared"] for p in doc["packages"]}
    assert by["home", "kitty-0.49.2"] == [{"file": "home.nix", "line": 3, "text": "pkgs.kitty"}]
    assert by["system", "quiet"] == [{"file": "configuration.nix", "line": 6, "text": "quiet"}]
    assert by["system", "ripgrep-15.2.0"] == [
        {"file": "configuration.nix", "line": 5, "text": "ripgrep"},
        {"file": "extra.nix", "line": 3, "text": "pkgs.ripgrep", "conditional": True},
    ]
    _, doc = _list(repo, monkeypatch, capsys, "--scope", "system")
    assert doc["users"] == [] and doc["packages"][1]["declared"][0]["line"] == 5


def test_list_rows_fold_repeats_after_the_filter():
    from nixin.truth import listing

    git = {"name": "git-2.56.0", "pname": "git", "version": "2.56.0"}
    lfs = {"name": "git-lfs-3.8.0", "pname": "git-lfs", "version": "3.8.0"}
    pk = {
        "hm_users": ["me"],
        "system": {"packages": [git, lfs, git, lfs, git], "programs": []},
        "home": {"me": {"packages": [git], "programs": []}},
    }
    rows, folded = listing.rows(pk, [None, "me"], None)
    assert [(r["scope"], r["pname"]) for r in rows] == [
        ("system", "git"),
        ("home", "git"),
        ("system", "git-lfs"),
    ]
    assert folded == 3  # the same package in two scopes is two rows, not a repeat
    rows, folded = listing.rows(pk, [None], "LFS")
    assert [r["pname"] for r in rows] == ["git-lfs"] and folded == 1


def test_list_element_names_and_matching():
    from nixin.truth import listing
    from nixin.writer.nixtext import NixFile

    def names(expr: str) -> set[str]:
        nf = NixFile(expr)
        return set(listing.element_names(nf, 0, len(nf.toks)))

    assert names("pkgs.ripgrep") == {"ripgrep"}
    assert names("kdePackages.dolphin") == {"dolphin", "kdePackages-dolphin"}
    assert names("nerd-fonts.jetbrains-mono") >= {"nerd-fonts-jetbrains-mono"}
    assert names("nightSchedule") == {"nightSchedule", "night-schedule"}
    assert names("polkit_gnome") >= {"polkit-gnome"}
    assert names("(pkgs.callPackage ./casual-preloader/package.nix { })") == {"casual-preloader"}
    assert names("(pkgs.callPackage ./tools/gdoc.nix { })") == {"gdoc"}
    assert names("(pkgs.writeShellScriptBin \"hello-x\" ''echo hi'')") == {"hello-x"}
    assert names('(pkgs.writeShellApplication { name = "mc-start"; text = "x"; })') == {"mc-start"}
    assert names("(python3.withPackages (ps: [ ps.requests ]))") == set()

    rows = [
        {"user": None, "name": "night-schedule", "pname": None},
        {"user": None, "name": "ripgrep-15.2.0", "pname": "ripgrep"},
        {"user": None, "name": "git-lfs-3.8.0", "pname": "git-lfs"},
        {"user": "me", "name": "ripgrep-15.2.0", "pname": "ripgrep"},
    ]
    decl = listing.Decl("a.nix", 3, "nightSchedule", frozenset({"night-schedule"}))
    rg = listing.Decl("a.nix", 4, "ripgrep", frozenset({"ripgrep"}))
    git = listing.Decl("a.nix", 5, "git", frozenset({"git"}))
    listing.attach(rows, {None: [decl, rg, git]})
    assert [len(r["declared"]) for r in rows] == [1, 1, 0, 0]  # `git` is not git-lfs; per scope
    assert rows[1]["declared"] == [{"file": "a.nix", "line": 4, "text": "ripgrep"}]


def test_list_human(repo, monkeypatch, capsys):
    from nixin.cli import main

    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    (repo.path / "flake.nix").write_text(LIST_FLAKE)
    (repo.path / "configuration.nix").write_text(
        "{ pkgs, ... }:\n{\n  environment.systemPackages = [\n    pkgs.ripgrep\n  ];\n}\n"
    )
    (repo.path / "home.nix").write_text("{ pkgs, ... }:\n{\n  home.packages = [ pkgs.kitty ];\n}\n")
    monkeypatch.setenv("NIXIN_WIDTH", "120")
    assert main(["--host", "19BK", "list", "--programs"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [
        "home    kitty    0.49.2  (brucking)  home.nix:3",
        "system  quiet",
        "system  ripgrep  15.2.0              configuration.nix:4",
    ]
    assert out[3] == "3 packages on 19BK: 2 system, 1 home brucking  · evaluated in 0.0 s"
    assert out[4:6] == ["", "programs.<name>.enable = true:"]
    assert out[6:] == [
        "system  bash",
        "home    git    (brucking)",
        "home    kitty  (brucking)",
        "system  zsh",
        "4 programs enabled",
    ]

    monkeypatch.setenv("NIXIN_WIDTH", "40")  # the location column no longer fits: dropped
    assert main(["--host", "19BK", "list", "--home", "brucking"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["home  kitty  0.49.2  (brucking)", "1 package on 19BK (home brucking)", "cached"]
    monkeypatch.setenv("NIXIN_WIDTH", "40")  # room for 15 of 19: cut from the left
    assert main(["--host", "19BK", "list", "--filter", "rip"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "system  ripgrep  15.2.0  …guration.nix:4"
    assert out[1:] == ["1 package matching 'rip' on 19BK: 1 system, 0 home brucking", "cached"]


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
