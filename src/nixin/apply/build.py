"""Building a host's toplevel from the workspace, the plan cache, and the closure diff.

The build is the workspace as nix sees it: a git+file flake, i.e. tracked files with
their working-tree contents (the writer's new files are intent-to-add, so they count)
and nothing untracked. That is exactly what `apply` commits (`git add -u`) and what the
authoritative clone builds after pulling the commit, so plan's out path is the one
`nixos-rebuild` produces there. (A `path:` flake would also copy `.git` and untracked
files into the store, under another fingerprint.)

Two steps: evaluate `toplevel.drvPath` (failure: `eval-failed`), then build that
derivation (failure: `build-failed`); nix's progress streams to stderr in human mode.

The cache maps the flake's *source store path* (a content hash of that tree, dirty edits
included) to the out path. nix's fingerprint changes when the same tree is committed;
the source hash does not, so `plan` on a dirty tree and `apply`'s build right after its
own commit share one entry. A second map, commit -> out, answers "what does this commit
build to" for `status` and for a tree whose only change is installer/history.jsonl.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..cache import default_root
from ..configrepo import ConfigRepo, NixinError, trim_nix_error
from . import closure, proc

EVAL_TIMEOUT = 900.0
KEEP = 40  # cache entries per kind and host
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def nix(args: list[str], *, code: str = "eval-failed", timeout: float | None = None) -> str:
    """ConfigRepo's nix runner, through proc.run (so the apply tests fake one seam)."""
    p = proc.run(["nix", *args, "--option", "warn-dirty", "false"], timeout=timeout)
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    if p.rc == 124 and timeout is not None and not p.out:
        raise NixinError("eval-timeout", f"nix did not finish in {timeout:.0f}s")
    if not p.ok:
        msg = trim_nix_error(p.err) or f"nix exited with status {p.rc}"
        raise NixinError(code, msg, stderr=p.err)
    return p.out


def repo_at(path: Path) -> ConfigRepo:
    return ConfigRepo(path, nix=nix)


# --- cache ------------------------------------------------------------------------------


class PlanCache:
    def __init__(self, root: Path | None = None) -> None:
        self.dir = (root or default_root()) / "apply"

    def _path(self, kind: str, host: str, key: str) -> Path:
        return self.dir / f"{kind}-{_SAFE.sub('_', host)}-{_SAFE.sub('_', key)}.json"

    def _get(self, kind: str, host: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        try:
            doc = json.loads(self._path(kind, host, key).read_text())
        except (OSError, ValueError):
            return None
        out = doc.get("out") if isinstance(doc, dict) else None
        if not out or not os.path.exists(out):  # garbage-collected since
            return None
        return doc

    def _put(self, kind: str, host: str, key: str | None, doc: dict[str, Any]) -> None:
        if not key:
            return
        p = self._path(kind, host, key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc, sort_keys=True))
            os.replace(tmp, p)
            olds = sorted(
                self.dir.glob(f"{kind}-{_SAFE.sub('_', host)}-*.json"),
                key=lambda f: f.stat().st_mtime,
            )
            for old in olds[:-KEEP]:
                old.unlink(missing_ok=True)
        except OSError:
            pass  # best effort, like every nixin cache

    def by_source(self, host: str, source_hash: str | None) -> dict[str, Any] | None:
        return self._get("out", host, source_hash)

    def put_source(self, host: str, source_hash: str | None, doc: dict[str, Any]) -> None:
        self._put("out", host, source_hash, doc)

    def by_commit(self, host: str, commit: str | None) -> dict[str, Any] | None:
        return self._get("commit", host, commit)

    def put_commit(self, host: str, commit: str | None, doc: dict[str, Any]) -> None:
        self._put("commit", host, commit, doc)


# --- build ------------------------------------------------------------------------------


def toplevel_attr(repo: ConfigRepo, host: str) -> str:
    return f"{repo.host_attr(host)}.config.system.build.toplevel"


def build_command(repo: ConfigRepo, host: str) -> list[str]:
    """What a person would run for the same result (shown by `apply --dry-run`)."""
    return [
        "nix",
        "build",
        "--no-link",
        "--print-out-paths",
        repo.installable(toplevel_attr(repo, host)),
    ]


def _build_error(stderr: str) -> str:
    """From the first `error: builder for …` block (it has nix's log tail) to the end."""
    lines = stderr.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*error:.*(builder for|Cannot build|build of)", ln):
            text = "\n".join(lines[i:]).strip()
            return text if len(text) <= 2000 else "…" + text[-1999:]
    return trim_nix_error(stderr) or "nix build failed"


def build(repo: ConfigRepo, host: str, *, stream: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    drv = repo.eval_raw(f"{toplevel_attr(repo, host)}.drvPath", timeout=EVAL_TIMEOUT).strip()
    t1 = time.monotonic()
    p = proc.run(
        ["nix", "build", "--no-link", "--print-out-paths", f"{drv}^out"],
        stream="stderr" if stream else False,
    )
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    if not p.ok:
        raise NixinError("build-failed", _build_error(p.err), stderr=p.err)
    outs = [ln.strip() for ln in p.out.splitlines() if ln.strip()]
    if not outs:
        raise NixinError("build-failed", f"nix build printed no out path for {drv}")
    return {
        "out": outs[-1],
        "drv": drv,
        "eval_s": round(t1 - t0, 2),
        "build_s": round(time.monotonic() - t1, 2),
    }


def diff(old: str | None, new: str) -> dict[str, Any] | None:
    """The closure diff old -> new; None when there is no old system or nix fails."""
    if not old or not os.path.exists(old):
        return None
    p = proc.run(["nix", "store", "diff-closures", old, new])
    if not p.ok:
        return None
    return closure.parse(p.out)
