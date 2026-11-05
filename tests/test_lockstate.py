"""Where the nixpkgs lock stands (nixin.lockstate), on real git checkouts in tmp_path: the
working flake.lock against HEAD's, the authoritative tree's and the running system's."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import fixture

from nixin import lockstate
from nixin.configrepo import ConfigRepo
from nixin.lockstate import LockRevs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

LOCK_REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
NEW_REV = "0bb2eedc20e4739f29e8045034f2828ad7054f8f"


def git(path: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True)
    return p.stdout.strip()


def move(path: Path, rev: str) -> None:
    lock = json.loads((path / "flake.lock").read_text())
    lock["nodes"]["nixpkgs_2"]["locked"].update(rev=rev, narHash=f"sha256-{rev[:8]}")
    (path / "flake.lock").write_text(json.dumps(lock, indent=2))


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace clone with its lock at 3ec6363, committed; an authoritative clone of it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "flake.nix").write_text("{ }")
    (ws / "flake.lock").write_text(json.dumps(fixture("flake.lock.json"), indent=2))
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "user.name", "T")
    git(ws, "config", "user.email", "t@example.org")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "lock")
    auth = tmp_path / "etc-nixos"
    subprocess.run(["git", "clone", "-q", str(ws), str(auth)], check=True)
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(auth))
    monkeypatch.setattr(lockstate, "running_system", lambda root="/x": {"nixpkgs_rev": None})
    return ws


def running(monkeypatch, short: str | None) -> None:
    monkeypatch.setattr(lockstate, "running_system", lambda root="/x": {"nixpkgs_rev": short})


def test_clean_lock_has_no_change(ws):
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs", host=None)
    assert revs.lock.rev == revs.committed.rev == revs.applied.rev == LOCK_REV
    assert revs.lock_change() is None and revs.previous() is None
    assert [r["source"] for r in revs.revisions()] == ["lock"]
    assert revs.known("3ec6363").rev == LOCK_REV and revs.known("0bb2eed") is None


def test_uncommitted_move_is_the_change(ws, monkeypatch):
    move(ws, NEW_REV)
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs")
    change = revs.lock_change()
    assert change["committed"] is False and change["running"] is None
    assert change["from"]["rev"] == LOCK_REV and change["to"]["rev"] == NEW_REV
    assert (
        change["from"]["nar_hash"]
        == fixture("flake.lock.json")["nodes"]["nixpkgs_2"]["locked"]["narHash"]
    )
    assert revs.previous().rev == LOCK_REV
    # the running system says which of the full revisions it is built from
    running(monkeypatch, "3ec6363")
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs")
    assert revs.lock_change()["running"] is True
    assert [(r["source"], r["short_rev"], r["running"]) for r in revs.revisions()] == [
        ("lock", "0bb2eed", False),
        ("committed", "3ec6363", True),
    ]
    assert lockstate.lock_change(ConfigRepo(ws))["running"] is True
    # another host's document never looks at this machine's running system
    assert LockRevs.read(ConfigRepo(ws), "nixpkgs", host="elsewhere").running_rev is None


def test_committed_move_not_applied(ws, monkeypatch):
    move(ws, NEW_REV)
    git(ws, "commit", "-qam", "bump")
    running(monkeypatch, "3ec6363")
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs")
    change = revs.lock_change()
    assert change["committed"] is True and change["running"] is True
    assert change["from"]["rev"] == LOCK_REV and change["to"]["rev"] == NEW_REV
    assert revs.previous().rev == LOCK_REV  # the applied one
    assert [r["source"] for r in revs.revisions()] == ["lock", "applied"]
    # the running system is none of the known revisions: listed by its short rev alone
    running(monkeypatch, "abcdef1")
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs")
    assert revs.revisions()[-1] == {
        "source": "running",
        "rev": "abcdef1",
        "short_rev": "abcdef1",
        "date": None,
        "last_modified": None,
        "nar_hash": None,
        "running": True,
    }
    assert revs.previous().rev == LOCK_REV  # still the applied one: the only older revision


def test_prefers_the_running_revision_among_the_older_ones(ws, monkeypatch):
    """Three revisions: applied A (running), committed B, working C → hold at A? No: the
    uncommitted move is B → C, so `from` is B; `previous()` offers the running A."""
    move(ws, NEW_REV)
    git(ws, "commit", "-qam", "bump")
    third = "1d060086d60cb76a40cf37109f979d88a5bd6396"
    move(ws, third)
    running(monkeypatch, "3ec6363")
    revs = LockRevs.read(ConfigRepo(ws), "nixpkgs")
    change = revs.lock_change()
    assert change["committed"] is False and change["from"]["rev"] == NEW_REV
    assert change["running"] is False
    assert revs.previous().rev == LOCK_REV
    assert [r["source"] for r in revs.revisions()] == ["lock", "committed", "applied"]


def test_no_git_and_no_authoritative_tree(tmp_path, monkeypatch):
    (tmp_path / "flake.nix").write_text("{ }")
    (tmp_path / "flake.lock").write_text(json.dumps(fixture("flake.lock.json")))
    revs = LockRevs.read(ConfigRepo(tmp_path), "nixpkgs")
    assert revs.committed is None and revs.applied is None
    assert revs.lock_change() is None and revs.revisions()[0]["source"] == "lock"
    # the authoritative tree is this very tree: not an "applied" revision of its own
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(tmp_path))
    assert LockRevs.read(ConfigRepo(tmp_path), "nixpkgs").applied is None
