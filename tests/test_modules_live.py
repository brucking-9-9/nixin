"""nixosModules.default and homeModules.default, evaluated for real (NIXIN_LIVE=1): one
`nix eval` of tests/nix/default.nix on minimal configs, from the worktree as a `path:`
flake (uncommitted files included). Home Manager's source is the one the config flake
locks ($NIXIN_CONFIG, else ~/nixos), fetched by its narHash, so the store copy serves.
About 20 s and 2 GiB on mbp13. Nothing is built."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import live

ROOT = Path(__file__).resolve().parent.parent


def _home_manager() -> str:
    config = Path(os.environ.get("NIXIN_CONFIG") or Path.home() / "nixos")
    node = json.loads((config / "flake.lock").read_text())["nodes"]["home-manager"]["locked"]
    return " ".join(f'{k} = "{node[k]}";' for k in ("type", "owner", "repo", "rev", "narHash"))


@pytest.fixture(scope="module")
def facts():
    expr = (
        f'let nixin = builtins.getFlake "path:{ROOT}"; in import "${{nixin}}/tests/nix" '
        f"{{ inherit nixin; homeManager = builtins.fetchTree {{ {_home_manager()} }}; }}"
    )
    out = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--expr", expr],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


@live
def test_nixos_module(facts):
    n = facts["nixos"]
    assert n["packages"] == ["nixin"] and n["etc"] == "nixin-options-nixos.json"
    h = n["header"]
    assert h["format"] == "nixin-options/1" and h["scope"] == "nixos"
    assert h["host"] == "nixin-test" and h["user"] is None and h["count"] > 10000
    assert {r["source"] for r in h["roots"]} == {"nixin", "nixpkgs"}  # the tests live in nixin
    assert n["openssh"]["declarations"] == [
        {
            "source": "nixpkgs",
            "file": "nixos/modules/services/networking/ssh/sshd.nix",
            "line": n["openssh"]["declarations"][0]["line"],
        }
    ]
    assert n["openssh"]["default"] == "false" and n["openssh"]["type"] == "boolean"
    assert n["greeting"]["default"] == '"hello"' and n["greeting"]["example"] == '"hej"'
    assert n["rootedGreeting"] == [{"source": "config", "file": "nixos-config.nix", "line": 5}]
    assert n["enable"]["declarations"][0]["source"] == "nixin"
    assert n["enable"]["declarations"][0]["file"] == "modules/options.nix"
    assert n["template"]["name"] == "users.users.<name>.home"
    assert n["moduleArgs"] == 0  # no submodule _module.* internals


@live
def test_nixos_module_switches(facts):
    n = facts["nixos"]
    t = n["timers"]
    assert t["outdated"] == {"OnCalendar": "*-*-* 09:00:00", "OnStartupSec": "10min"}
    assert t["outdatedExec"].endswith(
        "/bin/nixin-state outdated --timeout 30000 --quiet-hours 23-8"
    )
    assert t["outdatedEnv"]["NIXIN_AUTHORITATIVE"] == "/etc/nixos"
    assert t["outdatedEnv"]["NIXIN_CONFIG"] == "/home/alice/nixos"
    assert t["outdatedEnv"]["PATH"].startswith("/run/current-system/sw/bin:")
    assert t["condition"] == "alice" and t["statusExec"].endswith("/bin/nixin-state status")
    assert t["statusPaths"][-1] == "/home/alice/nixos/.git/refs/heads"
    assert t["none"] == []  # not without outdated.enable / pending.enable
    assert n["noIndex"] == {"etc": False, "file": None, "packages": ["nixin"]}
    assert n["off"] == {"etc": False, "packages": []}


@live
def test_config_root_detection(facts):
    assert facts["configRoot"] == {
        "flake": "/nix/store/cfg-source",
        "inline": "/nix/store/cfg-source",  # nixpkgs' flake.nix (inline modules) skipped
        "etc": "/etc/nixos",
        "plain": "/home/a/cfg/hosts",
        "none": None,
        "given": "/srv/cfg",
    }


@live
def test_home_module(facts):
    h = facts["home"]
    assert h["packages"] == ["nixin"] and h["file"] == "nixin-options-home.json"
    hd = h["header"]
    assert hd["scope"] == "home" and hd["user"] == "alice" and hd["host"] is None
    assert hd["home_manager"]["release"] and hd["count"] > 1000
    assert h["git"]["declarations"][0]["source"] == "home-manager"
    assert h["git"]["declarations"][0]["file"] == "modules/programs/git.nix"
    assert h["colour"]["type"] == 'one of "cyan", "pink"'
    assert h["enable"]["declarations"][0]["source"] == "nixin"
    assert h["timer"] == {"OnCalendar": "*-*-* 09:00:00", "OnStartupSec": "10min"}
    assert h["exec"] == [h["exec"][0]] and h["exec"][0].endswith("nixin-state outdated --no-notify")
    assert h["status"] is False


@live
def test_home_manager_as_a_nixos_module(facts):
    w = facts["nixosWithHm"]
    assert w["bobFile"] == "nixin-options-home.json"
    assert w["bobHeader"]["user"] == "bob" and w["bobHeader"]["host"] == "nixin-test"
    assert "config" not in {r["source"] for r in w["bobHeader"]["roots"]}  # not nixpkgs
    assert w["systemHasUsers"] is True and w["systemHasHmTree"] == 0  # pruned
    assert w["bobPrefixed"] == 0  # names without home-manager.users.bob.
    assert w["bobEnable"]["loc"] == ["programs", "nixin", "enable"]
    assert w["bobEnable"]["declarations"][0]["line"] is not None  # found by the short path
    assert w["bobGit"]["declarations"][0]["source"] == "home-manager"
