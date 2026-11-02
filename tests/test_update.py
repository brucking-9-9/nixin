"""`nixin update`, offline: a fake nix that writes flake.lock the way `nix flake update` /
`nix flake lock --override-input` do, on a copy of the recorded lock (tests/fixtures/
flake.lock.json: nixpkgs is node `nixpkgs_2`, github:nixos/nixpkgs/nixos-unstable at
3ec6363). The live test (NIXIN_LIVE=1) runs on a throwaway clone of $NIXIN_CONFIG.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import fixture, live

from nixin import configrepo
from nixin.cli import main
from nixin.configrepo import ConfigRepo, NixinError, trim_nix_error

LOCK_REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
TARGET_REV = "0bb2eedc20e4739f29e8045034f2828ad7054f8f"
TARGET_TIME = 1793515703  # 2026-11-01
NETWORK_STDERR = """error:
       … while updating the lock file of flake 'git+file:///home/master/nixos-ui-test'

       error: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-unstable': Could not resolve host: api.github.com (6)
"""
BAD_REF_STDERR = """error:
       … while updating the flake input 'nixpkgs'

       error: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-unstabel': HTTP error 422
"""


class FakeNix:
    """`flake update INPUT --flake DIR` and `flake lock DIR --override-input INPUT REF`: moves
    the input's lock node to `self.rev` (or the commit in REF); records the calls."""

    def __init__(self, path) -> None:
        self.path = path
        self.calls: list[list[str]] = []
        self.rev = TARGET_REV
        self.stderr: str | None = None

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        if self.stderr is not None:
            raise NixinError(kw.get("code", "x"), trim_nix_error(self.stderr), stderr=self.stderr)
        if args[:2] == ["flake", "update"]:
            name, rev = args[2], self.rev
        elif args[:2] == ["flake", "lock"]:
            name, ref = args[4], args[5]
            rev = ref.rsplit("/", 1)[-1] if len(ref.rsplit("/", 1)[-1]) == 40 else self.rev
        else:
            raise AssertionError(f"unexpected nix call: {args}")
        lock_file = self.path / "flake.lock"
        lock = json.loads(lock_file.read_text())
        node = lock["nodes"]["root"]["inputs"][name]
        locked = lock["nodes"][node]["locked"]
        if locked["rev"] != rev:
            locked.update(rev=rev, lastModified=TARGET_TIME, narHash=f"sha256-{rev[:8]}")
        lock_file.write_text(json.dumps(lock, indent=2))
        return ""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "flake.nix").write_text("{ }")
    (tmp_path / "flake.lock").write_text(json.dumps(fixture("flake.lock.json")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    r = ConfigRepo(tmp_path, nix=FakeNix(tmp_path))
    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: r)
    )
    return r


def run(capsys, repo, *argv: str, json_: bool = True) -> tuple[int, dict | str]:
    head = ["--json"] if json_ else []
    rc = main([*head, "--host", "19BK", "update", "--config", str(repo.path), *argv])
    out, err = capsys.readouterr()
    if not json_:
        return rc, out
    assert err == ""
    return rc, json.loads(out)


def locked_rev(repo) -> str:
    lock = json.loads((repo.path / "flake.lock").read_text())
    return lock["nodes"]["nixpkgs_2"]["locked"]["rev"]


def test_update_runs_nix_flake_update_on_the_nixpkgs_input(repo, capsys):
    rc, doc = run(capsys, repo)
    assert rc == 0 and doc["ok"] and doc["verb"] == "update" and doc["host"] == "19BK"
    assert repo.nix.calls == [["flake", "update", "nixpkgs", "--flake", str(repo.path)]]
    assert doc["command"] == f"nix flake update nixpkgs --flake {repo.path}"
    assert (doc["input"], doc["node"]) == ("nixpkgs", "nixpkgs_2")
    assert doc["to_ref"] == doc["original"] == "github:nixos/nixpkgs/nixos-unstable"
    assert doc["from"]["rev"] == LOCK_REV and doc["from"]["date"] == "2026-10-23"
    assert doc["to"]["rev"] == TARGET_REV and doc["to"]["short_rev"] == "0bb2eed"
    assert doc["to"]["date"] == "2026-11-01"
    assert doc["changed"] is True and doc["dry_run"] is False
    assert doc["commands"] == [
        f"nixin plan --config {repo.path} --host 19BK",
        f"nixin apply --config {repo.path} --host 19BK",
    ]
    assert locked_rev(repo) == TARGET_REV


def test_update_to_a_commit_pins_it_as_outdated_prints(repo, capsys):
    rc, doc = run(capsys, repo, "--to", TARGET_REV)
    pin = f"github:nixos/nixpkgs/{TARGET_REV}"
    assert rc == 0 and doc["to_ref"] == pin
    assert repo.nix.calls == [["flake", "lock", str(repo.path), "--override-input", "nixpkgs", pin]]
    assert doc["command"] == f"nix flake lock {repo.path} --override-input nixpkgs {pin}"
    assert doc["to"]["rev"] == TARGET_REV and doc["changed"]
    # a flakeref goes to --override-input as given
    rc, doc = run(capsys, repo, "--to", "github:nixos/nixpkgs/nixos-26.05")
    assert repo.nix.calls[-1][-1] == "github:nixos/nixpkgs/nixos-26.05"


def test_update_with_nothing_new_changes_nothing(repo, capsys):
    repo.nix.rev = LOCK_REV
    before = (repo.path / "flake.lock").read_text()
    rc, doc = run(capsys, repo)
    assert rc == 0 and doc["changed"] is False and doc["commands"] == []
    assert doc["to"]["rev"] == doc["from"]["rev"] == LOCK_REV
    assert json.loads((repo.path / "flake.lock").read_text()) == json.loads(before)
    _, text = run(capsys, repo, json_=False)
    assert "nixpkgs (github:nixos/nixpkgs/nixos-unstable): already at 3ec6363 (2026-10-23)" in text


def test_update_dry_run_runs_nothing(repo, capsys):
    rc, doc = run(capsys, repo, "--dry-run")
    assert rc == 0 and repo.nix.calls == [] and locked_rev(repo) == LOCK_REV
    assert doc["dry_run"] is True and doc["to"] is None and doc["changed"] is None
    assert doc["commands"] == [f"nix flake update nixpkgs --flake {repo.path}"]
    _, text = run(capsys, repo, "--dry-run", json_=False)
    assert text.splitlines() == [
        "nixpkgs (github:nixos/nixpkgs/nixos-unstable): at 3ec6363 (2026-10-23)",
        f"would run: nix flake update nixpkgs --flake {repo.path}",
    ]


def test_update_another_input(repo, capsys):
    rc, doc = run(capsys, repo, "--input", "home-manager")
    assert rc == 0 and doc["input"] == "home-manager"
    assert repo.nix.calls == [["flake", "update", "home-manager", "--flake", str(repo.path)]]
    assert doc["from"]["rev"].startswith("c216bf5") and doc["to"]["rev"] == TARGET_REV
    assert locked_rev(repo) == LOCK_REV  # nixpkgs did not move


def test_update_human_output(repo, capsys):
    rc, text = run(capsys, repo, json_=False)
    assert rc == 0
    assert text.splitlines() == [
        "nixpkgs (github:nixos/nixpkgs/nixos-unstable): 3ec6363 (2026-10-23) → 0bb2eed (2026-11-01)",
        f"ran: nix flake update nixpkgs --flake {repo.path}",
        "next:",
        f"  nixin plan --config {repo.path} --host 19BK",
        f"  nixin apply --config {repo.path} --host 19BK",
    ]


@pytest.mark.parametrize(
    "argv, code, words",
    [
        (["extra"], "usage", "takes no arguments"),
        (["--input", "nope"], "not-found", "has no input 'nope'"),
        (["--input", "fprintd-src"], "not-found", "has no input 'fprintd-src'"),  # no node
    ],
)
def test_update_errors(repo, capsys, argv, code, words):
    rc, doc = run(capsys, repo, *argv)
    assert rc == (2 if code == "usage" else 1)
    assert doc["error"]["code"] == code and words in doc["error"]["message"]
    assert repo.nix.calls == []


def test_update_bare_commit_needs_a_repository_input(repo, capsys):
    lock = json.loads((repo.path / "flake.lock").read_text())
    lock["nodes"]["nixpkgs_2"]["locked"] = {"type": "tarball", "url": "https://x/nixpkgs.tar.gz"}
    (repo.path / "flake.lock").write_text(json.dumps(lock))
    rc, doc = run(capsys, repo, "--to", "0bb2eed")
    assert (
        rc == 2 and doc["error"]["code"] == "usage" and "give a flakeref" in doc["error"]["message"]
    )


@pytest.mark.parametrize(
    "stderr, code", [(NETWORK_STDERR, "network"), (BAD_REF_STDERR, "bad-channel")]
)
def test_update_nix_failures(repo, capsys, stderr, code):
    repo.nix.stderr = stderr
    rc, doc = run(capsys, repo)
    assert rc == 1 and doc["error"]["code"] == code
    assert locked_rev(repo) == LOCK_REV
    repo.nix.stderr = "error: something else entirely"
    rc, doc = run(capsys, repo)
    assert doc["error"]["code"] == "nix-failed" and "something else" in doc["error"]["message"]


def test_update_needs_a_workspace(repo, capsys, tmp_path):
    rc, doc = run(capsys, repo, "--config", "/etc/nixos")
    assert rc == 1 and doc["error"]["code"] == "not-a-workspace"
    if os.geteuid() != 0:  # root can write anywhere
        repo.path.chmod(0o555)
        try:
            rc, doc = run(capsys, repo)
        finally:
            repo.path.chmod(0o755)
        assert (
            doc["error"]["code"] == "not-a-workspace" and "not writable" in doc["error"]["message"]
        )
    assert repo.nix.calls == []


# --- live: a throwaway clone of $NIXIN_CONFIG, never the config itself --------------------


@live
def test_live_update_on_a_clone(tmp_path, capsys):
    src = os.path.expanduser(os.environ.get("NIXIN_CONFIG") or "")
    if not src or shutil.which("git") is None:
        pytest.skip("NIXIN_CONFIG (a git checkout) is needed")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", src, str(clone)], check=True)
    original = (Path(src) / "flake.lock").read_text()
    before = (clone / "flake.lock").read_text()
    assert main(["--json", "update", "--config", str(clone), "--dry-run"]) == 0
    assert (clone / "flake.lock").read_text() == before
    assert main(["--json", "update", "--config", str(clone)]) == 0
    assert (Path(src) / "flake.lock").read_text() == original  # the config is untouched
