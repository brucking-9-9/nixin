"""git on the workspace and (read-only) on the authoritative tree.

Every call goes through `proc.run` with `-c safe.directory=*`: nixin reads the
root-owned authoritative clone as the user, and git would otherwise refuse it as
"dubious ownership". Writes (commit, fetch, pull, push) only ever happen in the
workspace; the authoritative tree is changed by root's `git pull`, never here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..configrepo import NixinError
from . import proc


@dataclass
class Status:
    dirty: list[str]  # tracked files that differ from HEAD (staged or not; intent-to-add too)
    untracked: list[str]  # not in the index: invisible to a git+file flake

    def doc(self) -> dict[str, list[str]]:
        return {"dirty": self.dirty, "untracked": self.untracked}


@dataclass
class Git:
    path: Path

    def _argv(self, *args: str) -> list[str]:
        return ["git", "-c", "safe.directory=*", "-C", str(self.path), *args]

    def run(self, *args: str, stream: bool | str = False) -> proc.Proc:
        p = proc.run(self._argv(*args), stream=stream)
        if p.missing:
            raise NixinError("git-failed", "`git` is not on PATH")
        return p

    def check(self, *args: str, what: str | None = None) -> str:
        p = self.run(*args)
        return proc.need(p, "git-failed", what or f"git {args[0]}").out

    # --- reading ----------------------------------------------------------------------

    def is_repo(self) -> bool:
        """A checkout whose top level is this path (not a directory inside another repo)."""
        p = self.run("rev-parse", "--show-toplevel")
        return p.ok and os.path.realpath(p.out.strip()) == os.path.realpath(self.path)

    def head(self) -> str | None:
        p = self.run("rev-parse", "--verify", "-q", "HEAD")
        return (p.out.strip() or None) if p.ok else None

    def branch(self) -> str | None:
        """The checked-out branch, None when HEAD is detached."""
        p = self.run("symbolic-ref", "--short", "-q", "HEAD")
        return (p.out.strip() or None) if p.ok else None

    def status(self) -> Status:
        out = self.check("status", "--porcelain=v1", "-z", "--untracked-files=normal")
        dirty: list[str] = []
        untracked: list[str] = []
        parts = out.split("\0")
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if len(entry) < 4:
                continue
            xy, path = entry[:2], entry[3:]
            if xy == "??":
                untracked.append(path)
            elif xy != "!!":
                dirty.append(path)
                if "R" in xy or "C" in xy:  # renames carry the old path as the next field
                    i += 1
        return Status(sorted(set(dirty)), sorted(untracked))

    def has_commit(self, rev: str) -> bool:
        return self.run("cat-file", "-e", f"{rev}^{{commit}}").ok

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.run("merge-base", "--is-ancestor", a, b).ok

    def log(self, rev_range: str, limit: int = 50) -> list[dict[str, str]]:
        out = self.check("log", f"--max-count={limit}", "--format=%H%x00%s", rev_range)
        rows = []
        for line in out.splitlines():
            sha, _, subject = line.partition("\0")
            rows.append({"commit": sha, "subject": subject})
        return rows

    def count(self, rev_range: str) -> int:
        return int(self.check("rev-list", "--count", rev_range).strip() or 0)

    def tracked(self, rel: str) -> bool:
        return self.run("ls-files", "--error-unmatch", "--", rel).ok

    def show(self, rev: str, rel: str) -> str | None:
        p = self.run("show", f"{rev}:{rel}")
        return p.out if p.ok else None

    def same_tree(self, a: str, b: str, exclude: str) -> bool:
        """a and b have the same files and contents, `exclude` aside."""
        return self.run("diff", "--quiet", a, b, "--", ".", f":(exclude){exclude}").ok

    def rev(self, ref: str) -> str | None:
        p = self.run("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
        return (p.out.strip() or None) if p.ok else None

    def remote_url(self, name: str) -> str | None:
        p = self.run("remote", "get-url", name)
        return (p.out.strip() or None) if p.ok else None

    def remotes(self) -> list[str]:
        return self.check("remote").split()

    def upstream_remote(self) -> str | None:
        """The remote the current branch tracks (branch.<b>.remote), if any."""
        b = self.branch()
        if b is None:
            return None
        p = self.run("config", "--get", f"branch.{b}.remote")
        return (p.out.strip() or None) if p.ok else None

    # --- writing (workspace only) -----------------------------------------------------

    def restore(self, rev: str, exclude: str) -> None:
        """Index and working tree to rev's files (deleting what rev does not have),
        `exclude` aside; the caller commits."""
        self.check(
            "restore",
            f"--source={rev}",
            "--staged",
            "--worktree",
            "--",
            ".",
            f":(exclude){exclude}",
            what=f"git restore --source={rev[:12]}",
        )

    def commit(self, message: str, also: list[str]) -> str:
        """Stage every tracked change (intent-to-add files included) plus `also`, commit
        with the repo's own identity, return the new HEAD."""
        self.check("add", "-u", what="git add -u")
        if also:
            self.check("add", "--", *also, what="git add")
        self.check("commit", "-q", "-m", message, what="git commit")
        head = self.head()
        if head is None:
            raise NixinError("git-failed", "git commit left no HEAD")
        return head
