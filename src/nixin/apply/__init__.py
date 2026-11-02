"""apply — make it so: `plan`, `apply`, `sync`, and the `pending` block of `status`.

Two checkouts of the config:

    workspace       user-owned clone (--config, $NIXIN_CONFIG): the writer edits it,
                    plan builds it, apply commits in it
    authoritative   root-owned clone (--authoritative, $NIXIN_AUTHORITATIVE, /etc/nixos):
                    the running system is switched from it; nixin never edits it, root
                    only `git pull --ff-only`s the workspace into it

`plan` builds the workspace host's toplevel without root and diffs its closure against
/run/current-system. `apply` commits the workspace, builds (plan's cache), then runs the
two privileged steps (privilege.py): root pulls the commit into the authoritative tree
and `nixos-rebuild switch`es from there; then it checks that /run/current-system is the
out path it built and appends a line to installer/history.jsonl (committed by the next
apply). `sync` moves commits between the workspace and a git remote (the hub).

`register(subparsers)` is the only thing cli.py calls (same pattern as truth/writer).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cache import default_root
from ..configrepo import DEFAULT_CONFIG, ConfigRepo, NixinError, local_hostname
from ..writer.state import STATE_FILE
from ..writer.workspace import Workspace
from . import build, commitmsg, privilege, proc, render, system
from .git import Git, Status
from .system import HISTORY

VERBS = ("plan", "apply", "sync")
ENV_AUTHORITATIVE = "NIXIN_AUTHORITATIVE"


class ApplyError(NixinError):
    """A NixinError whose error object carries extra fields (commands, partial progress)."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(code, message)
        self.extra = extra

    def doc(self) -> dict[str, Any]:
        d = super().doc()
        d["error"].update(self.extra)
        return d


def register(sub: argparse._SubParsersAction) -> None:
    handlers = {"plan": plan, "apply": apply, "sync": sync}
    for verb, handler in handlers.items():
        p = sub.choices.get(verb)
        if p is None:
            p = sub.add_parser(verb)
            p.add_argument("args", nargs="*")
        p.add_argument("--config", help="workspace clone (default: $NIXIN_CONFIG)")
        p.add_argument(
            "--authoritative",
            metavar="DIR",
            help=f"root-owned clone the system switches from (default: ${ENV_AUTHORITATIVE} "
            f"or {DEFAULT_CONFIG})",
        )
        p.set_defaults(run=handler)
    ap = sub.choices["apply"]
    ap.add_argument("-m", "--message", help="commit message (default: generated)")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, run none of them")
    ap.add_argument(
        "--no-switch",
        action="store_true",
        help="nixos-rebuild boot: make it the boot default, activate at the next boot",
    )
    sy = sub.choices["sync"]
    sy.add_argument("--pull", action="store_true", help="git pull --ff-only into the workspace")
    sy.add_argument("--push", action="store_true", help="push the workspace branch")
    sy.add_argument("--remote", metavar="NAME", help="remote (default: the branch's, else origin)")


def _run(ns: argparse.Namespace, fn) -> dict[str, Any]:
    try:
        if getattr(ns, "args", None):
            raise NixinError("usage", f"{ns.verb} takes no arguments (got {' '.join(ns.args)!r})")
        return fn(ns)
    except NixinError as e:
        return e.doc()


# --- the two trees ----------------------------------------------------------------------


@dataclass
class Ctx:
    ns: argparse.Namespace
    path: Path
    repo: ConfigRepo
    git: Git
    branch: str
    auth_path: Path
    auth: Git
    host: str
    local: bool
    cache: build.PlanCache = field(default_factory=build.PlanCache)
    warnings: list[str] = field(default_factory=list)

    @property
    def human(self) -> bool:
        return not self.ns.json


def authoritative_path(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get(ENV_AUTHORITATIVE) or DEFAULT_CONFIG
    return Path(os.path.abspath(Path(raw).expanduser()))


def _context(ns: argparse.Namespace, *, check_host: bool = True) -> Ctx:
    ws = Workspace.open(ns.config, nix=build.nix)  # config-not-found / not-a-workspace
    git = Git(ws.root)
    if not git.is_repo():
        raise NixinError("not-a-workspace", f"{ws.root} is not a git checkout")
    branch = git.branch()
    if branch is None:
        raise NixinError(
            "not-a-workspace", f"{ws.root}: HEAD is detached; check out a branch first"
        )
    auth_path = authoritative_path(getattr(ns, "authoritative", None))
    if auth_path.exists() and auth_path.resolve() == ws.root:
        raise NixinError("not-a-workspace", f"{ws.root} is the authoritative tree itself")
    repo = build.repo_at(ws.root)
    host = ns.host
    if check_host and host not in _text_hosts(ws):  # flake.nix as text, else one eval
        hosts = repo.hosts()
        if host not in hosts:
            raise NixinError(
                "unknown-host", f"{host!r} is not in nixosConfigurations ({', '.join(hosts)})"
            )
    return Ctx(
        ns, ws.root, repo, git, branch, auth_path, Git(auth_path), host, host == local_hostname()
    )


def _text_hosts(ws: Workspace) -> list[str]:
    try:
        return list(ws.flake_hosts())
    except Exception:  # noqa: BLE001 -- the scanner is a shortcut; the eval decides
        return []


def _auth_doc(ctx: Ctx) -> dict[str, Any]:
    if not (ctx.auth_path / ".git").exists():
        return {
            "path": str(ctx.auth_path),
            "exists": False,
            "head": None,
            "branch": None,
            "dirty": [],
        }
    return {
        "path": str(ctx.auth_path),
        "exists": True,
        "head": ctx.auth.head(),
        "branch": ctx.auth.branch(),
        "dirty": ctx.auth.status().dirty,
    }


def _relation(ctx: Ctx, ws_head: str | None, auth_head: str | None) -> dict[str, Any]:
    """Where the authoritative HEAD stands relative to the workspace HEAD."""
    if not ws_head or not auth_head:
        return {"state": "unknown", "pending": None}
    if auth_head == ws_head:
        return {"state": "same", "pending": []}
    if not ctx.git.has_commit(auth_head):
        return {"state": "unknown", "pending": None}
    if ctx.git.is_ancestor(auth_head, ws_head):
        return {"state": "ahead", "pending": ctx.git.log(f"{auth_head}..{ws_head}")}
    if ctx.git.is_ancestor(ws_head, auth_head):
        return {"state": "behind", "pending": []}
    return {"state": "diverged", "pending": None}


def _diverged(ctx: Ctx, rel: dict[str, Any], auth_head: str) -> ApplyError:
    a = auth_head[:7]
    fetch = f"git -C {ctx.path} fetch {ctx.auth_path} HEAD"
    if rel["state"] == "behind":
        what = f"the authoritative tree ({a}) is ahead of the workspace"
        todo = f"{fetch} && git -C {ctx.path} merge --ff-only FETCH_HEAD"
    elif rel["state"] == "unknown":
        what = f"the authoritative HEAD {a} is not a commit the workspace has"
        todo = f"{fetch}, then merge or rebase onto FETCH_HEAD (or `nixin sync --pull`)"
    else:
        what = f"the workspace and the authoritative tree ({a}) have diverged"
        todo = f"{fetch} && git -C {ctx.path} rebase FETCH_HEAD (or merge it)"
    return ApplyError(
        "authoritative-diverged",
        f"{what}; root's `git pull --ff-only` cannot take the workspace. Bring those "
        f"commits into the workspace first: {todo}; then apply again",
        authoritative_head=auth_head,
        state=rel["state"],
    )


def _workspace_doc(ctx: Ctx, st: Status, head: str | None) -> dict[str, Any]:
    return {"path": str(ctx.path), "branch": ctx.branch, "head": head, **st.doc()}


# --- the build, with plan's cache ---------------------------------------------------------


def _target(ctx: Ctx, st: Status, head: str | None, *, dry: bool = False) -> dict[str, Any]:
    """The workspace host's toplevel out path: from the cache, else built (not when dry).

    A tree whose only change is installer/history.jsonl builds what HEAD builds (no module
    reads that file), so the commit map answers it without an eval."""
    real = [f for f in st.dirty if f != HISTORY]
    if not real and head:
        hit = ctx.cache.by_commit(ctx.host, head)
        if hit:
            return {**hit, "cached": True, "cache_key": f"commit {head[:12]}"}
    src = ctx.repo.source()
    hit = ctx.cache.by_source(ctx.host, src.source_hash)
    if hit:
        if not real and head:
            ctx.cache.put_commit(ctx.host, head, {"out": hit["out"], "drv": hit.get("drv")})
        return {**hit, "cached": True, "cache_key": f"source {src.source_hash}"}
    if dry:
        return {"out": None, "drv": None, "cached": False, "cache_key": None}
    b = build.build(ctx.repo, ctx.host, stream=ctx.human)
    entry = {"out": b["out"], "drv": b["drv"]}
    ctx.cache.put_source(ctx.host, src.source_hash, entry)
    if not real and head:
        ctx.cache.put_commit(ctx.host, head, entry)
    return {**b, "cached": False, "cache_key": f"source {src.source_hash}"}


# --- plan ---------------------------------------------------------------------------------


def plan(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _plan)


def _plan(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx = _context(ns)
    st = ctx.git.status()
    head = ctx.git.head()
    auth = _auth_doc(ctx)
    rel = _relation(ctx, head, auth["head"])
    run = system.running()
    target = _target(ctx, st, head)
    out = target["out"]
    diff = build.diff(run["store_path"], out) if ctx.local else None
    if not ctx.local:
        ctx.warnings.append(
            f"{ctx.host} is not this machine ({local_hostname()}): no closure diff "
            "(it would compare against this machine's /run/current-system)"
        )
    if st.untracked:
        ctx.warnings.append(
            "untracked files are not part of the build (git add them if a module needs "
            "them): " + ", ".join(st.untracked[:10])
        )
    doc = {
        "ok": True,
        "out": out,
        "drv": target.get("drv"),
        "cached": target["cached"],
        "cache_key": target["cache_key"],
        "up_to_date": out == run["store_path"],
        "duration_s": round(time.monotonic() - t0, 2),
        "timings": {k: target[k] for k in ("eval_s", "build_s") if k in target},
        "workspace": _workspace_doc(ctx, st, head),
        "authoritative": {**auth, "relation": rel["state"]},
        "running": run,
        "pending_commits": rel["pending"],
        "diff": diff,
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.plan(doc, ctx.host)
    return doc


# --- apply --------------------------------------------------------------------------------


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    """One apply at a time per user (two UIs clicking "apply" must not interleave)."""
    path = default_root() / "apply" / "apply.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = path.open("w")
    except OSError:  # no writable cache dir: run unlocked rather than not at all
        yield
        return
    with fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NixinError("busy", "another nixin apply is running") from None
        yield


def apply(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _apply_locked)


def _apply_locked(ns: argparse.Namespace) -> dict[str, Any]:
    with _lock():
        return _apply(ns)


def _apply(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx = _context(ns)
    mode = "boot" if ns.no_switch else "switch"
    if not ctx.local:
        raise NixinError(
            "host-not-local",
            f"apply switches this machine ({local_hostname()}); {ctx.host!r} is another host. "
            f"Run nixin apply on {ctx.host}, or plan it here with --host {ctx.host}",
        )
    auth = _auth_doc(ctx)
    if not auth["exists"]:
        raise NixinError(
            "authoritative-missing",
            f"{ctx.auth_path} is not a git checkout. Set it up once as root: "
            f"git clone {ctx.path} {ctx.auth_path} (or pass --authoritative DIR)",
        )
    if auth["dirty"]:
        raise ApplyError(
            "authoritative-dirty",
            f"{ctx.auth_path} has uncommitted changes ({', '.join(auth['dirty'][:5])}); nixin "
            "only fast-forwards it. Move those edits into the workspace, then as root: "
            f"git -C {ctx.auth_path} checkout -- . (or git stash)",
            files=auth["dirty"],
        )
    st = ctx.git.status()
    head = ctx.git.head()
    rel = _relation(ctx, head, auth["head"])
    if rel["state"] not in ("same", "ahead"):
        raise _diverged(ctx, rel, auth["head"] or "?")
    if auth["branch"] and auth["branch"] != ctx.branch:
        ctx.warnings.append(
            f"{ctx.auth_path} is on branch {auth['branch']!r}, the workspace on "
            f"{ctx.branch!r}: the pull fast-forwards {auth['branch']!r} to it"
        )
    before = system.running()
    real = [f for f in st.dirty if f != HISTORY]
    add_history = [HISTORY] if HISTORY in st.untracked else []
    to_commit = sorted(set(st.dirty) | set(add_history))
    if not real and not rel["pending"]:
        target = _target(ctx, st, head, dry=ns.dry_run)
        if target["out"] and target["out"] == before["store_path"]:
            raise NixinError(
                "nothing-to-apply",
                f"the workspace HEAD {head[:7] if head else '?'} is already in "
                f"{ctx.auth_path} and /run/current-system is its build "
                f"(generation {before['generation']})",
            )
    message = ns.message or commitmsg.message(
        ctx.git.show("HEAD", STATE_FILE), _read(ctx.path / STATE_FILE), to_commit
    )
    steps = [
        privilege.pull_step(ctx.auth_path, ctx.path, ctx.branch),
        privilege.switch_step(ctx.auth_path, ctx.host, mode),
    ]
    bk = privilege.backends(steps, ctx.auth_path, ctx.local)
    if ns.dry_run:
        return _dry_run(ctx, st, head, rel, before, to_commit, add_history, message, steps, bk, t0)

    committed = None
    t_commit = time.monotonic()
    # plan's entry for the tree as it is now. The commit is this tree plus, the first
    # time, installer/history.jsonl, which no module reads: same out path, so that entry
    # holds for the commit even when the history file changes the source hash.
    pre = _target(ctx, st, head, dry=True) if to_commit else None
    if to_commit:
        committed = ctx.git.commit(message, add_history)
        head = committed
        ctx.repo = build.repo_at(ctx.path)  # the tree nix sees changed (same content though)
        st = ctx.git.status()
    t_build = time.monotonic()
    if pre and pre["out"]:
        target = pre
        ctx.cache.put_commit(ctx.host, head, {"out": pre["out"], "drv": pre.get("drv")})
    else:
        target = _target(ctx, st, head)
    out = target["out"]
    diff = build.diff(before["store_path"], out)
    progress: dict[str, Any] = {"committed": committed, "out": out}
    ran: list[str] = []

    def privileged(step: privilege.Step, remaining: list[privilege.Step]) -> proc.Proc:
        b = bk[step.name]
        why = b.blocked()  # sudo is not probed: its own refusal below is the answer
        if why:
            raise ApplyError("switch-failed", why, privileged_commands=ran, **progress)
        ran.append(proc.shown(b.argv(step)))
        p = b.run(step, stream=ctx.human)
        if isinstance(b, privilege.Sudo) and (p.missing or privilege.needs_password(p)):
            cmds = [bk[s.name].display(s) for s in remaining]
            raise ApplyError(
                "needs-sudo",
                "root is needed and sudo -n cannot get it without a password "
                f"({p.tail(1) or 'no ticket'}). Run:\n  "
                + "\n  ".join(cmds)
                + "\n(or `sudo -v` once, then nixin apply again)",
                commands=cmds,
                privileged_commands=ran,
                **progress,
            )
        return p

    t_pull = time.monotonic()
    p = privileged(steps[0], steps)
    if not p.ok:
        code = "authoritative-diverged" if "fast-forward" in p.err else "git-failed"
        raise ApplyError(
            code,
            f"pulling the workspace into {ctx.auth_path} failed:\n{p.tail(12)}",
            privileged_commands=ran,
            **progress,
        )
    auth_head = ctx.auth.head()
    progress["authoritative_head"] = auth_head
    if auth_head != head:
        raise ApplyError(
            "git-failed",
            f"after the pull {ctx.auth_path} is at {auth_head}, expected {head}",
            privileged_commands=ran,
            **progress,
        )
    t_switch = time.monotonic()
    p = privileged(steps[1], steps[1:])
    if not p.ok:
        raise ApplyError(
            "switch-failed",
            f"nixos-rebuild {mode} failed ({ctx.auth_path} is already at {head[:7]}):\n"
            + p.tail(20),
            privileged_commands=ran,
            **progress,
        )
    t_done = time.monotonic()
    switch_log = p.tail(20)
    now = system.realpath(system.CURRENT if mode == "switch" else system.PROFILE)
    if now != out:
        what = "/run/current-system" if mode == "switch" else system.PROFILE
        raise ApplyError(
            "verify-failed",
            f"{what} is {now}, but nixin built {out} from the same commit; the authoritative "
            "tree evaluated differently (impure config, or a change between plan and apply?)",
            privileged_commands=ran,
            **progress,
        )
    after = system.running()
    generation = system.generation_of(out) if mode == "switch" else after["profile_generation"]
    subject = message.splitlines()[0] if message else ""
    line = {
        "ts": system.now(),
        "host": ctx.host,
        "out": out,
        "generation": generation,
        "commit": head,
        "message": subject,
        "mode": mode,
    }
    try:
        system.append_history(ctx.path, line)
    except OSError as e:
        ctx.warnings.append(f"could not append to {HISTORY}: {e}")
    ctx.cache.put_commit(ctx.host, head, {"out": out, "drv": target.get("drv")})
    doc = {
        "ok": True,
        "committed": committed,
        "commit_message": subject if committed else None,
        "authoritative_head": auth_head,
        "out": out,
        "generation": generation,
        "switched": mode == "switch",
        "mode": mode,
        "previous": before,
        "running": after,
        "privileged_commands": ran,
        "backends": {s.name: bk[s.name].name for s in steps},
        "switch_log": switch_log,
        "diff": diff,
        "cached": target["cached"],
        "history": line,
        "timings": {
            "prepare": round(t_commit - t0, 2),
            "commit": round(t_build - t_commit, 2),
            "build": round(t_pull - t_build, 2),
            "pull": round(t_switch - t_pull, 2),
            "switch": round(t_done - t_switch, 2),
            "total": round(time.monotonic() - t0, 2),
        },
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.apply(doc, ctx.host)
    return doc


def _dry_run(ctx, st, head, rel, before, to_commit, add_history, message, steps, bk, t0):
    """The exact commands apply would run, in order; nothing is run."""
    target = _target(ctx, st, head, dry=True)
    commands: list[dict[str, Any]] = []
    if to_commit:
        g = ["git", "-C", str(ctx.path)]
        commands.append({"step": "commit", "privileged": False, "argv": [*g, "add", "-u"]})
        if add_history:
            commands.append(
                {"step": "commit", "privileged": False, "argv": [*g, "add", "--", *add_history]}
            )
        commands.append(
            {
                "step": "commit",
                "privileged": False,
                "argv": [*g, "commit", "-m", message.splitlines()[0]],
            }
        )
    if target["out"] is None:
        commands.append(
            {"step": "build", "privileged": False, "argv": build.build_command(ctx.repo, ctx.host)}
        )
    for s in steps:
        commands.append(
            {
                "step": s.name,
                "privileged": True,
                "argv": bk[s.name].argv(s),
                "backend": bk[s.name].name,
                "by_hand": bk[s.name].display(s),
            }
        )
    for c in commands:
        c["command"] = proc.shown(c["argv"])
    ready = {s.name: bk[s.name].probe() for s in steps}
    doc = {
        "ok": True,
        "dry_run": True,
        "commands": commands,
        "commit": {"files": to_commit, "message": message} if to_commit else None,
        "out": target["out"],
        "cached": target["cached"],
        "workspace": _workspace_doc(ctx, st, head),
        "authoritative": {"path": str(ctx.auth_path), "relation": rel["state"]},
        "pending_commits": rel["pending"],
        "running": before,
        "privilege": {
            k: {"backend": bk[k].name, "ready": v is None, "why": v} for k, v in ready.items()
        },
        "diff": build.diff(before["store_path"], target["out"]) if target["out"] else None,
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.dry_run(doc, ctx.host)
    return doc


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


# --- sync ---------------------------------------------------------------------------------


def sync(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _sync)


def _sync(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx = _context(ns, check_host=False)
    g = ctx.git
    remotes = g.remotes()
    name = ns.remote or g.upstream_remote() or "origin"
    if name not in remotes:
        raise NixinError(
            "usage",
            f"no remote {name!r} in {ctx.path} (remotes: {', '.join(remotes) or 'none'}; "
            "--remote NAME)",
        )
    done: list[str] = []
    proc.need(g.run("fetch", "--quiet", name), "git-failed", f"git fetch {name}")
    done.append("fetch")
    ref = f"refs/remotes/{name}/{ctx.branch}"
    if ns.pull and g.rev(ref):
        p = g.run("pull", "--ff-only", "--quiet", name, ctx.branch)
        if not p.ok:
            ff = "fast-forward" in p.err or "diverg" in p.err
            raise NixinError(
                "diverged" if ff else "git-failed",
                f"git pull --ff-only {name} {ctx.branch} failed"
                + (
                    ": the workspace and the remote have diverged; merge or rebase by hand"
                    if ff
                    else ""
                )
                + f"\n{p.tail(8)}",
            )
        done.append("pull")
    if ns.pull and "pull" not in done:
        ctx.warnings.append(f"{name} has no branch {ctx.branch!r}: nothing to pull")
    if ns.push:
        p = g.run("push", "--quiet", name, f"HEAD:refs/heads/{ctx.branch}")
        if not p.ok:
            rejected = "rejected" in p.err or "non-fast-forward" in p.err or "fetch first" in p.err
            raise NixinError(
                "diverged" if rejected else "git-failed",
                f"git push {name} {ctx.branch} failed"
                + (
                    ": the remote has commits the workspace does not (sync --pull first)"
                    if rejected
                    else ""
                )
                + f"\n{p.tail(8)}",
            )
        g.run("fetch", "--quiet", name)  # refresh the remote-tracking ref for the report
        done.append("push")
    head = g.head()
    remote_head = g.rev(ref)
    ahead = behind = None
    if remote_head and head:
        counts = g.check("rev-list", "--left-right", "--count", f"HEAD...{ref}").split()
        ahead, behind = int(counts[0]), int(counts[1])
    auth = _auth_doc(ctx)
    rel = _relation(ctx, head, auth["head"])
    doc = {
        "ok": True,
        "done": done,
        "workspace": {
            "path": str(ctx.path),
            "branch": ctx.branch,
            "head": head,
            "ahead": ahead,
            "behind": behind,
            "dirty": g.status().dirty,
        },
        "authoritative": {
            "path": auth["path"],
            "head": auth["head"],
            "relation": rel["state"],
            "behind_workspace": len(rel["pending"]) if rel["pending"] is not None else None,
        },
        "remote": {"name": name, "url": g.remote_url(name), "head": remote_head},
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.sync(doc)
    return doc


# --- status's pending block ---------------------------------------------------------------


def pending(repo: ConfigRepo, host: str) -> dict[str, Any]:
    """What `status` shows as pending: workspace commits the authoritative tree lacks,
    dirty files, and whether the running system is the authoritative HEAD's build (when
    plan's cache knows that build; no nix runs here). Never raises."""
    auth_path = authoritative_path()
    try:
        git = Git(repo.path)
        is_auth = auth_path.exists() and auth_path.resolve() == repo.path
        auth = Git(auth_path)
        auth_head = auth.head() if (auth_path / ".git").exists() else None
        doc: dict[str, Any] = {
            "workspace": None if is_auth else str(repo.path),
            "authoritative": {"path": str(auth_path), "head": auth_head},
            "commits": None,
            "dirty": [],
            "untracked": [],
            "built": None,
            "running_matches": None,
        }
        if not is_auth and git.is_repo():
            st = git.status()
            doc["dirty"], doc["untracked"] = st.dirty, st.untracked
            head = git.head()
            if head and auth_head:
                if head == auth_head:
                    doc["commits"] = []
                elif git.has_commit(auth_head) and git.is_ancestor(auth_head, head):
                    doc["commits"] = git.log(f"{auth_head}..{head}")
        hit = build.PlanCache().by_commit(host, auth_head)
        if hit:
            doc["built"] = hit["out"]
            if host == local_hostname():
                doc["running_matches"] = hit["out"] == system.realpath(system.CURRENT)
        return doc
    except NixinError as e:
        return {"error": {"code": e.code, "message": e.message}}


def annotate_status(doc: dict[str, Any], repo: ConfigRepo, host: str) -> None:
    """truth's status calls this once: adds `pending`, and a line to the human text."""
    if not doc.get("ok"):
        return
    doc["pending"] = pending(repo, host)
    if "message" in doc:
        doc["message"] = doc["message"].rstrip("\n") + "\n" + render.pending(doc["pending"])
