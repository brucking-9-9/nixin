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

    def upstream_remote(self, branch: str | None = None) -> str | None:
        """The remote a branch tracks (branch.<b>.remote; default: the checked-out one)."""
        b = branch or self.branch()
        if b is None:
            return None
        p = self.run("config", "--get", f"branch.{b}.remote")
        return (p.out.strip() or None) if p.ok else None

    def refs(self, *patterns: str) -> list[Ref]:
        """for-each-ref over `patterns` (refs/heads, refs/remotes/<r>), newest commit first."""
        fmt = "%(refname)%00%(objectname)%00%(upstream:short)%00%(committerdate:iso-strict)"
        out = self.check(
            "for-each-ref", "--sort=-committerdate", f"--format={fmt}%00%(subject)", *patterns
        )
        rows = []
        for line in out.splitlines():
            parts = line.split("\0")
            if len(parts) != 5:
                continue
            name, sha, upstream, date, subject = parts
            rows.append(Ref(name, sha, upstream or None, date or None, subject))
        return rows

    def worktrees(self) -> list[Worktree]:
        """`git worktree list --porcelain`: every checkout of this repository."""
        out = self.check("worktree", "list", "--porcelain")
        rows: list[Worktree] = []
        cur: Worktree | None = None
        for line in out.splitlines():
            key, _, val = line.partition(" ")
            if key == "worktree":
                cur = Worktree(Path(val))
                rows.append(cur)
            elif cur is None:
                continue
            elif key == "branch":
                cur.branch = val.removeprefix("refs/heads/")
            elif key == "HEAD":
                cur.head = val
            elif key == "prunable":
                cur.prunable = True
        return rows

    def names_changed(self, rev_range: str) -> list[str]:
        return self.check("diff", "--name-only", "--no-renames", rev_range).splitlines()

    def merge_tree(self, ours: str, theirs: str) -> tuple[str | None, list[str]]:
        """Merge two commits in the object store (no checkout touched): (tree, []) when
        it is clean, (None, conflicting files) when it is not."""
        p = self.run("merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs)
        lines = p.out.splitlines()
        if p.rc == 0 and lines:
            return lines[0], []
        if p.rc == 1 and lines:
            return None, sorted(set(lines[1:]))
        proc.need(p, "git-failed", f"git merge-tree {ours[:12]} {theirs[:12]}")
        raise NixinError("git-failed", f"git merge-tree printed nothing for {theirs[:12]}")

    def commit_tree(self, tree: str, parents: list[str], message: str) -> str:
        """A commit object for `tree` (the repo's own identity, as `commit`); no ref moves."""
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        return self.check(*args, "-m", message, what="git commit-tree").strip()

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

    def commit(self, message: str, also: list[str], exclude: str | None = None) -> str:
        """Stage every tracked change (intent-to-add files included; not `exclude`) plus
        `also`, commit with the repo's own identity, return the new HEAD."""
        if exclude:
            self.check("add", "-u", "--", ".", f":(exclude){exclude}", what="git add -u")
        else:
            self.check("add", "-u", what="git add -u")
        if also:
            self.check("add", "--", *also, what="git add")
        self.check("commit", "-q", "-m", message, what="git commit")
        head = self.head()
        if head is None:
            raise NixinError("git-failed", "git commit left no HEAD")
        return head


@dataclass
class Ref:
    name: str  # refs/heads/x, refs/remotes/origin/x
    commit: str
    upstream: str | None  # origin/x
    date: str | None  # the tip's committer date, ISO 8601
    subject: str


@dataclass
class Worktree:
    path: Path
    branch: str | None = None  # None: detached (also mid-rebase or bisect)
    head: str | None = None
    prunable: bool = False  # its directory is gone; `git worktree prune` drops it
