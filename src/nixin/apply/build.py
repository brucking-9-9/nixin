"""Building a host's toplevel from the workspace, the plan cache, and the closure diff.

The build is the workspace as nix sees it: a git+file flake, i.e. tracked files with
their working-tree contents (the writer's new files are intent-to-add, so they count)
and nothing untracked. That is exactly what `apply` commits (`git add -u`) and what the
authoritative clone builds after pulling the commit, so plan's out path is the one
`nixos-rebuild` produces there. (A `path:` flake would also copy `.git` and untracked
files into the store, under another fingerprint.)

Two steps: evaluate `toplevel.drvPath` (failure: `eval-failed`), then build that
derivation (failure: `build-failed`) with `--log-format internal-json`, parsed by the
build tracker (progress.py): a UI reads the tracker through the run's `Cancel` token, human
mode prints nix's readable output and a live status line on stderr, and the final `build`
summary goes into the plan/apply document. The token also stops the build (`cancelled`).

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
import sys
import time
from pathlib import Path
from typing import Any

from ..cache import default_root
from ..configrepo import ConfigRepo, NixinError, trim_nix_error
from . import builders, closure, proc, progress

EVAL_TIMEOUT = 900.0
KEEP = 40  # cache entries per kind and host
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def nix(
    args: list[str],
    *,
    code: str = "eval-failed",
    timeout: float | None = None,
    cancel: proc.Cancel | None = None,
) -> str:
    """ConfigRepo's nix runner, through proc.run (so the apply tests fake one seam)."""
    p = proc.run(["nix", *args, "--option", "warn-dirty", "false"], timeout=timeout, cancel=cancel)
    if cancel is not None:
        cancel.check()
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    if p.rc == 124 and timeout is not None and not p.out:
        raise NixinError("eval-timeout", f"nix did not finish in {timeout:.0f}s")
    if not p.ok:
        msg = trim_nix_error(p.err) or f"nix exited with status {p.rc}"
        raise NixinError(code, msg, stderr=p.err)
    return p.out


def repo_at(path: Path, cancel: proc.Cancel | None = None) -> ConfigRepo:
    """A ConfigRepo whose nix calls the run's token can stop (an eval takes 30 s)."""
    if cancel is None:
        return ConfigRepo(path, nix=nix)

    def runner(args: list[str], **kw: Any) -> str:
        return nix(args, cancel=cancel, **kw)

    return ConfigRepo(path, nix=runner)


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


def build_command(repo: ConfigRepo, host: str, remote: dict[str, Any] | None = None) -> list[str]:
    """What a person would run for the same result (shown by `apply --dry-run`); with a
    remote the builders flags as the build would get them (a spec still to be fetched
    shows as `--builders "$(<command>)"`)."""
    flags = builders.nix_args(remote)
    if remote and not flags and not remote.get("fell_back"):
        flags = ["--builders", f"$({remote.get('command')})", "--builders-use-substitutes"]
        if remote.get("mode") == "all":
            flags += ["--max-jobs", "0"]
    return [
        "nix",
        "build",
        "--no-link",
        "--print-out-paths",
        *flags,
        repo.installable(toplevel_attr(repo, host)),
    ]


def _build_error(stderr: str) -> str:
    """From the first `error: builder for …` block (it has nix's log tail) to the end."""
    lines = progress.ANSI.sub("", stderr).splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*error:.*(builder for|Cannot build|build of)", ln):
            text = "\n".join(lines[i:]).strip()
            return text if len(text) <= 2000 else "…" + text[-1999:]
    return trim_nix_error(stderr) or "nix build failed"


LOG_FORMAT = ["--log-format", "internal-json", "-v"]  # what the tracker parses


def _stderr_tty() -> bool:
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def build(
    repo: ConfigRepo,
    host: str,
    *,
    stream: bool,
    cancel: proc.Cancel | None = None,
    remote: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate the toplevel's drvPath, then build it with the tracker reading nix's
    events. `stream` (human mode): nix's readable output and the status line on stderr
    (no status line when a UI watches the token: it shows the tracker itself). A
    `cancelled` error (with the build summary as `.build`) when the token stops it.
    `remote` (builders.requested): the builders command runs now, right before the
    build; a failure falls back to a local build with a line in `warnings`."""
    token = cancel or proc.Cancel()
    t0 = time.monotonic()
    token.step = "eval"
    drv = repo.eval_raw(f"{toplevel_attr(repo, host)}.drvPath", timeout=EVAL_TIMEOUT).strip()
    token.check()
    if remote is not None:
        remote = builders.resolve(remote, warnings if warnings is not None else [])
        token.check()
        if stream:
            sys.stderr.write(f"nixin: {builders.where(remote)}\n")
            sys.stderr.flush()
    t1 = time.monotonic()
    token.step = "build"
    tracker = progress.Tracker(durations=progress.default_durations(), remote=remote)
    reporter = None
    if stream and not token.watched:
        reporter = progress.Reporter(tracker, tty=_stderr_tty())
        reporter.start()
    token.snapshot = tracker.snapshot

    def on_line(line: str) -> str | None:
        text = tracker.feed_line(line)
        if reporter is not None:
            reporter.line(text)
        return text

    try:
        p = proc.run(
            [
                "nix",
                "build",
                "--no-link",
                "--print-out-paths",
                *builders.nix_args(remote),
                *LOG_FORMAT,
                f"{drv}^out",
            ],
            stream="stderr" if stream and reporter is None else False,
            on_stderr=on_line,
            cancel=token,
        )
    finally:
        if reporter is not None:
            reporter.close()
        token.snapshot = None
    if token.requested:
        tracker.finish("cancelled")
        summary = tracker.summary()
        c = summary["counts"]
        err = NixinError(
            "cancelled",
            f"the build was stopped after {progress.human_time(summary['duration_s'])} "
            f"({c['done']} of {summary['total']} derivations built, "
            f"{summary['downloads']['done']} paths fetched); nothing was applied",
            stderr=p.err,
        )
        err.build = summary  # type: ignore[attr-defined]
        raise err
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    tracker.finish("done" if p.ok else "failed")
    if not p.ok:
        err = NixinError("build-failed", _build_error(p.err), stderr=p.err)
        err.build = tracker.summary()  # type: ignore[attr-defined]
        raise err
    outs = [ln.strip() for ln in p.out.splitlines() if ln.strip()]
    if not outs:
        raise NixinError("build-failed", f"nix build printed no out path for {drv}")
    return {
        "out": outs[-1],
        "drv": drv,
        "eval_s": round(t1 - t0, 2),
        "build_s": round(time.monotonic() - t1, 2),
        "build": tracker.summary(),
    }


def diff(old: str | None, new: str) -> dict[str, Any] | None:
    """The closure diff old -> new; None when there is no old system or nix fails."""
    if not old or not os.path.exists(old):
        return None
    p = proc.run(["nix", "store", "diff-closures", old, new])
    if not p.ok:
        return None
    return closure.parse(p.out)
