"""Where the nixpkgs lock stands: the working tree's flake.lock against the committed one
(HEAD), the applied one (the authoritative tree's flake.lock) and the running system.

`nixin update` writes flake.lock without committing, so between a click on "Update nixpkgs
lock" and an apply the workspace has three revisions of the same input: what runs, what is
committed, what the file says now. `LockRevs` reads all three (git and the file system only,
no nix) and answers two questions the documents carry:

- `lock_change()`: the move that is not applied yet — `{from, to, committed, running}`, or
  None when the lock is as committed and applied. `committed: false` is the uncommitted move
  (`nixin update --undo` takes it back); `committed: true` a move committed in the workspace
  but not applied (the authoritative tree still has the old lock).
- `revisions()`: the distinct revisions the host knows (the lock's, the committed, the
  applied, the running system's), for a revision chooser (`nixin pin NAME --rev`).

The running system only names its nixpkgs by the short revision in `nixos-version`; it is
used to say which full revision is the running one, never passed to nix on its own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configrepo import ConfigRepo, LockedInput, local_hostname, parse_lock, running_system


def _git_show(path: Path, rev: str, rel: str) -> str | None:
    """`git show REV:REL` in a checkout, None when git, the checkout or the file is missing
    (a root-owned tree is read with safe.directory=*, like `ConfigRepo.git_head`)."""
    if shutil.which("git") is None or not (path / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(path), "show", f"{rev}:{rel}"],
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return proc.stdout if proc.returncode == 0 else None


def _node(text: str | None, name: str) -> LockedInput | None:
    if not text:
        return None
    try:
        return parse_lock(json.loads(text), name)
    except (ValueError, AttributeError):
        return None


def side(lk: LockedInput | None) -> dict[str, Any]:
    """One revision as the documents spell it (update's `from`/`to` shape)."""
    if lk is None:
        return {"rev": None, "short_rev": None, "date": None, "last_modified": None}
    return {
        "rev": lk.rev,
        "short_rev": lk.short_rev,
        "date": lk.date,
        "last_modified": lk.last_modified,
        "nar_hash": lk.nar_hash,
    }


@dataclass(frozen=True)
class LockRevs:
    input: str
    lock: LockedInput | None  # the working tree's flake.lock
    committed: LockedInput | None  # HEAD:flake.lock (None: no git, or the lock is untracked)
    applied: (
        LockedInput | None
    )  # the authoritative tree's flake.lock (None: this tree, or unreadable)
    running_rev: str | None  # /run/current-system's nixpkgs, short (this machine only)

    @classmethod
    def read(cls, repo: ConfigRepo, name: str = "nixpkgs", host: str | None = None) -> LockRevs:
        from .apply import authoritative_path  # late: apply imports truth.render

        lock = repo.lock(name)
        committed = _node(_git_show(repo.path, "HEAD", "flake.lock"), name)
        applied = None
        auth = authoritative_path()
        try:
            same = auth.exists() and auth.resolve() == repo.path.resolve()
        except OSError:
            same = False
        if not same:
            try:
                applied = _node((auth / "flake.lock").read_text(), name)
            except OSError:
                applied = None
        running = None
        if host is None or host == local_hostname():
            running = running_system().get("nixpkgs_rev")
        return cls(name, lock, committed, applied, running)

    # --- questions -----------------------------------------------------------------------

    def is_running(self, lk: LockedInput | None) -> bool | None:
        """Whether `lk` is what the running system was built from; None when unknown."""
        if lk is None or not lk.rev or not self.running_rev:
            return None
        return lk.rev.startswith(self.running_rev)

    def lock_change(self) -> dict[str, Any] | None:
        lock, head, applied = self.lock, self.committed, self.applied
        if lock is None or not lock.rev:
            return None
        if head is not None and head.rev and _differs(head, lock):
            return {
                "from": side(head),
                "to": side(lock),
                "committed": False,
                "running": self.is_running(head),
            }
        if applied is not None and applied.rev and _differs(applied, lock):
            return {
                "from": side(applied),
                "to": side(lock),
                "committed": True,
                "running": self.is_running(applied),
            }
        return None

    def previous(self) -> LockedInput | None:
        """The revision to hold at when the lock moved: the committed one, else the applied
        one, preferring whichever the running system was built from; None when the lock is
        where everything else is."""
        lock = self.lock
        if lock is None:
            return None
        older = [
            lk for lk in (self.committed, self.applied) if lk and lk.rev and _differs(lk, lock)
        ]
        if not older:
            return None
        running = [lk for lk in older if self.is_running(lk)]
        return (running or older)[0]

    def revisions(self) -> list[dict[str, Any]]:
        """Distinct revisions, the lock's first: `{source, rev, short_rev, date, nar_hash,
        running}`; `source` is lock, committed, applied or running (the running system's
        short revision alone, when none of the full ones is it)."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source, lk in (
            ("lock", self.lock),
            ("committed", self.committed),
            ("applied", self.applied),
        ):
            if lk is None or not lk.rev or lk.rev in seen:
                continue
            seen.add(lk.rev)
            out.append({"source": source, **side(lk), "running": self.is_running(lk)})
        if self.running_rev and not any(r["running"] for r in out):
            out.append(
                {
                    "source": "running",
                    "rev": self.running_rev,
                    "short_rev": self.running_rev[:7],
                    "date": None,
                    "last_modified": None,
                    "nar_hash": None,
                    "running": True,
                }
            )
        return out

    def known(self, rev: str) -> LockedInput | None:
        """A full revision the trees know (its narHash and date come for free then)."""
        for lk in (self.lock, self.committed, self.applied):
            if lk is not None and lk.rev and (lk.rev == rev or lk.rev.startswith(rev)):
                return lk
        return None


def _differs(a: LockedInput, b: LockedInput) -> bool:
    return (a.rev, a.nar_hash) != (b.rev, b.nar_hash)


def lock_change(repo: ConfigRepo, name: str = "nixpkgs", host: str | None = None) -> dict | None:
    """`LockRevs.read(...).lock_change()`; None on anything unreadable (never raises)."""
    try:
        return LockRevs.read(repo, name, host).lock_change()
    except (OSError, ValueError):
        return None


__all__ = ["LockRevs", "lock_change", "side"]
