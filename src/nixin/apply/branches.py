"""branches — the workspace's git branches against the default branch (main).

    nixin branches                      list: ahead/behind, merged, files, clean merge?
    nixin branches merge NAME...        merge them into main (built first), main moves
    nixin branches prune [--hub]        delete the branches main already contains

`merge` works in the object store: `git merge-tree --write-tree` merges, `commit-tree`
makes the merge commit (the hub's `--no-ff` style: one merge commit per branch), and no
checkout is touched. So it runs from any branch and with a dirty tree; the workspace
stays as it is. Before main moves, the merged commit is built for the host from a
detached worktree in the cache (a `git+file` flake, like plan's; removed again), and its
out path goes into plan's commit cache. Then main moves with `git fetch . REF:main`:
fast-forward only (main moving meanwhile is `diverged`), and git refuses a branch that a
worktree has checked out (mid-rebase and bisect included): `branch-checked-out`. When
the workspace itself is on main, `merge --ff-only` moves its files with it. Nothing is
switched: `nixin apply` (from main) does that.

`prune` deletes local branches whose tip main contains (`branch -D` after nixin's own
ancestry check: `-d` asks the branch's upstream instead), never main, the checked-out
branch or one a worktree has checked out; `--hub` also deletes them on the hub (the
target branch's remote, else origin) and never on any other remote.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..cache import default_root
from ..configrepo import NixinError
from ..truth.render import cap, row, sub
from . import build, proc
from .git import Git, Ref, Worktree

HELP = "the config's git branches against main: list, merge (built first), prune merged"
ACTIONS = ("list", "merge", "prune")
FILES_SHOWN = 200  # files[] per branch in the document (files_total has the count)
TMP_REF = "refs/nixin/branches-merge"


def add_arguments(p: argparse.ArgumentParser) -> None:
    for a in p._actions:  # cli.py's generic positional: the action, then branch names
        if a.dest == "args":
            a.metavar, a.help = "ACTION [BRANCH...]", "list (default), merge BRANCH..., prune"
    p.add_argument("--into", metavar="BRANCH", help="target branch (default: the hub's HEAD, main)")
    p.add_argument("--no-build", action="store_true", help="merge: do not build the result first")
    p.add_argument("--push", action="store_true", help="merge: push the target branch to the hub")
    p.add_argument("--hub", action="store_true", help="prune: also delete them on the hub")
    p.add_argument("--dry-run", action="store_true", help="merge/prune: show it, change nothing")


def branches(ns: argparse.Namespace) -> dict[str, Any]:
    from . import _lock  # the package's; no cycle at call time

    try:
        action, names = _action(ns)
        if action == "list":
            return _list(ns)
        if ns.dry_run:
            return _merge(ns, names) if action == "merge" else _prune(ns)
        with _lock():
            return _merge(ns, names) if action == "merge" else _prune(ns)
    except NixinError as e:
        return e.doc()


def _action(ns: argparse.Namespace) -> tuple[str, list[str]]:
    args = list(getattr(ns, "args", None) or [])
    action = args.pop(0) if args else "list"
    if action not in ACTIONS:
        raise NixinError("usage", f"branches ACTION is one of {', '.join(ACTIONS)}, not {action!r}")
    if action == "merge" and not args:
        raise NixinError("usage", "branches merge needs at least one BRANCH")
    if action != "merge" and args:
        raise NixinError("usage", f"branches {action} takes no branch names (got {' '.join(args)})")
    flags = {"--push": ns.push, "--no-build": ns.no_build, "--hub": ns.hub}
    allowed = {"merge": ("--push", "--no-build"), "prune": ("--hub",), "list": ()}[action]
    bad = [f for f, on in flags.items() if on and f not in allowed]
    if ns.dry_run and action == "list":
        bad.append("--dry-run")
    if bad:
        raise NixinError("usage", f"{', '.join(bad)} does not apply to branches {action}")
    return action, args


# --- where things are ---------------------------------------------------------------------


def hub_of(g: Git, branch: str | None = None) -> str | None:
    """The hub remote: what `branch` tracks, else origin; None without either."""
    remotes = g.remotes()
    name = g.upstream_remote(branch) if branch else None
    if name in remotes:
        return name
    return "origin" if "origin" in remotes else None


def default_branch(g: Git, explicit: str | None = None) -> str:
    """--into, else the hub's HEAD (refs/remotes/origin/HEAD), else main."""
    if explicit:
        if not g.rev(f"refs/heads/{explicit}"):
            raise NixinError("unknown-branch", f"there is no local branch {explicit!r}")
        return explicit
    hub = hub_of(g)
    if hub:
        p = g.run("symbolic-ref", "--short", "-q", f"refs/remotes/{hub}/HEAD")
        name = p.out.strip().removeprefix(f"{hub}/") if p.ok else ""
        if name and g.rev(f"refs/heads/{name}"):
            return name
    for name in ("main", "master"):
        if g.rev(f"refs/heads/{name}"):
            return name
    raise NixinError("unknown-branch", "no main branch (pass --into BRANCH)")


def _checkouts(g: Git) -> dict[str, Worktree]:
    """branch -> the live worktree that has it checked out (stale entries ignored)."""
    return {w.branch: w for w in g.worktrees() if w.branch and not w.prunable}


def _workspace_of(ns: argparse.Namespace):
    from . import _context

    return _context(ns, check_host=False)


# --- list ---------------------------------------------------------------------------------


def _entry(
    g: Git, into: str, ref: Ref, where: str, hub_tip: str | None, wt: Worktree | None
) -> dict[str, Any]:
    name = ref.name.split("/", 3)[-1] if where == "hub" else ref.name.removeprefix("refs/heads/")
    ahead = g.count(f"{into}..{ref.commit}")
    behind = g.count(f"{ref.commit}..{into}")
    merged = ahead == 0
    files: list[str] = []
    clean: bool | None = None
    conflicts: list[str] = []
    if not merged:
        files = g.names_changed(f"{into}...{ref.commit}")
        tree, conflicts = g.merge_tree(into, ref.commit)
        clean = tree is not None
    return {
        "name": name,
        "where": where,  # local | hub (on the hub only)
        "commit": ref.commit,
        "subject": ref.subject,
        "date": ref.date,
        "upstream": ref.upstream,
        "hub_commit": hub_tip,  # the hub's tip of the same name (None: not on the hub)
        "ahead": ahead,
        "behind": behind,
        "merged": merged,
        "files": files[:FILES_SHOWN],
        "files_total": len(files),
        "merges_clean": clean,
        "conflicts": conflicts,
        "checked_out_in": str(wt.path) if wt else None,
    }


def _list(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx = _workspace_of(ns)
    g = ctx.git
    into = default_branch(g, ns.into)
    hub = hub_of(g, into)
    outs = _checkouts(g)
    hub_refs = {}
    if hub:
        prefix = f"refs/remotes/{hub}/"
        for r in g.refs(prefix.rstrip("/")):
            short = r.name.removeprefix(prefix)
            if short != "HEAD":
                hub_refs[short] = r
    rows = []
    local_names = set()
    for r in g.refs("refs/heads"):
        name = r.name.removeprefix("refs/heads/")
        local_names.add(name)
        if name == into:
            continue
        h = hub_refs.get(name)
        rows.append(_entry(g, into, r, "local", h.commit if h else None, outs.get(name)))
    for short, r in hub_refs.items():
        if short in local_names or short == into:
            continue
        rows.append(_entry(g, into, r, "hub", r.commit, None))
    rows.sort(key=lambda b: (b["merged"], -_ts(b["date"])))
    stale = [str(w.path) for w in g.worktrees() if w.prunable]
    warnings = list(ctx.warnings)
    if stale:
        warnings.append(f"stale worktrees (directory gone; merge/prune drop them): {len(stale)}")
    doc = {
        "ok": True,
        "into": into,
        "into_head": g.rev(f"refs/heads/{into}"),
        "hub": hub,
        "workspace": {"path": str(ctx.path), "branch": ctx.branch},
        "branches": rows,
        "counts": {
            "unmerged": sum(1 for b in rows if not b["merged"]),
            "merged": sum(1 for b in rows if b["merged"]),
        },
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": warnings,
    }
    if ctx.human:
        doc["message"] = render_list(doc)
    return doc


def _ts(date: str | None) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(date).timestamp() if date else 0.0
    except ValueError:
        return 0.0


# --- merge --------------------------------------------------------------------------------


def _resolve(g: Git, name: str, hub: str | None) -> tuple[str, str]:
    """A branch name (or hub/name) -> (shown name, commit)."""
    sha = g.rev(f"refs/heads/{name}")
    if sha:
        return name, sha
    if hub:
        short = name.removeprefix(f"{hub}/")
        sha = g.rev(f"refs/remotes/{hub}/{short}")
        if sha:
            return short, sha
    raise NixinError("unknown-branch", f"there is no branch {name!r} (nixin branches lists them)")


def _message(g: Git, name: str, into: str, tip: str, sha: str) -> str:
    head = f"Merge branch '{name}'" + ("" if into in ("main", "master") else f" into {into}")
    subjects = [c["subject"] for c in reversed(g.log(f"{tip}..{sha}", limit=40))]
    return head + ("\n\n" + "\n".join(f"* {s}" for s in subjects) if subjects else "")


def _merge(ns: argparse.Namespace, names: list[str]) -> dict[str, Any]:
    from . import ApplyError, _context

    t0 = time.monotonic()
    ctx = _context(ns, check_host=not (ns.no_build or ns.dry_run))
    g = ctx.git
    if not ns.dry_run:
        g.run("worktree", "prune")  # entries whose directory is gone would block nothing,
        # but `worktree add` refuses a path a stale entry still names
    into = default_branch(g, ns.into)
    hub = hub_of(g, into)
    old = g.rev(f"refs/heads/{into}")
    assert old is not None  # default_branch checked it
    tip = old
    merged: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for raw in names:
        name, sha = _resolve(g, raw, hub)
        if name == into:
            raise NixinError("usage", f"{name} is the target branch")
        if g.is_ancestor(sha, tip):
            skipped.append({"branch": name, "reason": f"{into} already contains it"})
            continue
        tree, conflicts = g.merge_tree(tip, sha)
        if tree is None:
            raise ApplyError(
                "merge-conflict",
                f"{name} does not merge cleanly into {into}"
                + (f" (after {', '.join(m['branch'] for m in merged)})" if merged else "")
                + f": {', '.join(conflicts)}. Nothing was changed; merge it by hand "
                f"(git switch {into} && git merge {name}) or rebase {name} first",
                branch=name,
                files=conflicts,
            )
        files = g.names_changed(f"{tip}...{sha}")
        commits = g.count(f"{tip}..{sha}")
        new = (
            None
            if ns.dry_run
            else g.commit_tree(tree, [tip, sha], _message(g, name, into, tip, sha))
        )
        merged.append(
            {"branch": name, "commit": sha, "merge": new, "commits": commits, "files": files}
        )
        if new:
            tip = new
    doc: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(ns.dry_run),
        "into": into,
        "old": old,
        "new": tip if merged and not ns.dry_run else old,
        "merged": merged,
        "skipped": skipped,
        "build": None,
        "pushed": False,
        "hub": hub,
        "workspace": {"path": str(ctx.path), "branch": ctx.branch},
        "warnings": ctx.warnings,
    }
    if merged and not ns.dry_run:
        held = _checkouts(g).get(into)
        if held and ctx.branch != into:  # before a build that could not land anyway
            raise _held(into, str(held.path))
        if not ns.no_build:
            doc["build"] = _build(ctx, tip)
        _move(ctx, into, old, tip)
        if ns.push:
            _push(g, hub, into)
            doc["pushed"] = True
        if ctx.branch != into:
            ctx.warnings.append(
                f"the workspace is on {ctx.branch}, not {into}: `git switch {into}` before "
                "nixin apply (uncommitted changes come along)"
            )
    doc["duration_s"] = round(time.monotonic() - t0, 2)
    if ctx.human:
        doc["message"] = render_merge(doc)
    return doc


def _build(ctx, tip: str) -> dict[str, Any]:
    """Build the host's toplevel from `tip` in a detached worktree (a git+file flake, as
    plan builds the workspace); the out path goes into plan's commit cache."""
    from . import ApplyError

    wt = default_root() / "branches" / tip[:12]
    wt.parent.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        ctx.git.run("worktree", "remove", "--force", str(wt))
    ctx.git.check("worktree", "add", "--detach", "--quiet", str(wt), tip, what="git worktree add")
    try:
        repo = build.repo_at(wt, ctx.cancel)
        try:
            b = build.build(
                repo,
                ctx.host,
                stream=ctx.human,
                cancel=ctx.cancel,
                remote=None,
                warnings=ctx.warnings,
            )
        except NixinError as e:
            raise ApplyError(
                e.code,
                f"{e.message}\n(the merged {ctx.host} does not build; nothing was changed)",
                build=getattr(e, "build", None),
            ) from None
    finally:
        ctx.git.run("worktree", "remove", "--force", str(wt))
    entry = {"out": b["out"], "drv": b["drv"]}
    ctx.cache.put_commit(ctx.host, tip, entry)
    return {
        "host": ctx.host,
        "out": b["out"],
        "drv": b["drv"],
        "eval_s": b.get("eval_s"),
        "build_s": b.get("build_s"),
    }


def _move(ctx, into: str, old: str, tip: str) -> None:
    """Fast-forward `into` to tip: in the workspace when it has `into` checked out,
    else as a ref (git refuses one a worktree has checked out)."""
    g = ctx.git
    if ctx.branch == into:
        p = g.run("merge", "--ff-only", "--quiet", tip)
        if not p.ok:
            raise NixinError(
                "git-failed",
                f"the merges are built but {into} did not move: git merge --ff-only in the "
                f"workspace failed (uncommitted changes in the merged files?)\n{p.tail(8)}",
            )
        return
    g.check("update-ref", TMP_REF, tip, what="git update-ref")
    try:
        p = g.run("fetch", str(ctx.path), f"{TMP_REF}:refs/heads/{into}")
    finally:
        g.run("update-ref", "-d", TMP_REF)
    if p.ok:
        return
    if "checked out" in p.err or "refusing to fetch" in p.err:
        w = _checkouts(g).get(into)  # None: a worktree mid-rebase/bisect on it
        raise _held(into, str(w.path) if w else _quoted_path(p.err) or "another worktree")
    if "non-fast-forward" in p.err or "rejected" in p.err:
        raise NixinError(
            "diverged",
            f"{into} moved while merging (was {old[:7]}); run nixin branches merge again",
        )
    proc.need(p, "git-failed", f"moving {into}")


def _held(into: str, where: str) -> NixinError:
    from . import ApplyError

    return ApplyError(
        "branch-checked-out",
        f"{into} is checked out in {where}, so it cannot move under it. Remove that "
        f"worktree (git worktree remove {where}) or merge there; nothing was changed",
        path=where,
    )


def _quoted_path(err: str) -> str | None:
    """The path in git's "refusing to fetch into branch '…' checked out at '<path>'"."""
    _, sep, rest = err.partition("checked out at '")
    return rest.split("'", 1)[0] if sep else None


def _push(g: Git, hub: str | None, into: str) -> None:
    if hub is None:
        raise NixinError("usage", "--push: the workspace has no hub remote (origin)")
    p = g.run("push", "--quiet", hub, f"refs/heads/{into}:refs/heads/{into}")
    if p.ok:
        g.run("fetch", "--quiet", hub)
        return
    rejected = "rejected" in p.err or "non-fast-forward" in p.err or "fetch first" in p.err
    raise NixinError(
        "diverged" if rejected else "git-failed",
        f"the merges are in {into}, but git push {hub} {into} failed"
        + (": the hub has commits the workspace does not (sync --pull first)" if rejected else "")
        + f"\n{p.tail(8)}",
    )


# --- prune --------------------------------------------------------------------------------


def _prune(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    ctx = _workspace_of(ns)
    g = ctx.git
    if not ns.dry_run:
        g.run("worktree", "prune")
    into = default_branch(g, ns.into)
    hub = hub_of(g, into) if ns.hub else None
    if ns.hub and hub is None:
        raise NixinError("usage", "--hub: the workspace has no hub remote (origin)")
    outs = _checkouts(g)
    deleted: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    kept = 0
    held: set[str] = set()

    def why_not(name: str) -> str | None:
        if name == ctx.branch:
            return "the workspace has it checked out"
        w = outs.get(name)
        return f"checked out in {w.path}" if w else None

    for r in g.refs("refs/heads"):
        name = r.name.removeprefix("refs/heads/")
        if name == into:
            continue
        if not g.is_ancestor(r.commit, into):
            kept += 1
            continue
        reason = why_not(name)
        if reason:
            skipped.append({"branch": name, "where": "local", "reason": reason})
            held.add(name)
            continue
        if not ns.dry_run:
            g.check("branch", "-D", "--quiet", name, what=f"git branch -D {name}")
        deleted.append({"branch": name, "where": "local", "commit": r.commit})
    if hub:
        prefix = f"refs/remotes/{hub}/"
        gone = []
        for r in g.refs(prefix.rstrip("/")):
            name = r.name.removeprefix(prefix)
            if name in ("HEAD", into) or not g.is_ancestor(r.commit, into):
                continue
            if name in held:
                skipped.append({"branch": name, "where": "hub", "reason": "kept locally (above)"})
                continue
            gone.append((name, r.commit))
        if gone and not ns.dry_run:
            deleted += _delete_on_hub(g, hub, gone, skipped)
        else:
            deleted += [{"branch": n, "where": "hub", "commit": c} for n, c in gone]
    doc = {
        "ok": True,
        "dry_run": bool(ns.dry_run),
        "into": into,
        "hub": hub,
        "deleted": deleted,
        "skipped": skipped,
        "unmerged": kept,
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render_prune(doc)
    return doc


def _delete_on_hub(
    g: Git, hub: str, gone: list[tuple[str, str]], skipped: list[dict[str, str]]
) -> list[dict[str, str]]:
    """One `git push --delete` for all of them; the porcelain says which went."""
    p = g.run("push", "--porcelain", hub, "--delete", *(n for n, _ in gone))
    status: dict[str, tuple[str, str]] = {}
    for line in p.out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1].startswith(":refs/heads/"):
            status[parts[1].removeprefix(":refs/heads/")] = (parts[0], parts[2])
    if not status:
        proc.need(p, "git-failed", f"git push {hub} --delete")
    out = []
    for name, sha in gone:
        flag, summary = status.get(name, ("?", "no answer"))
        if flag == "-" or "does not exist" in summary:
            out.append({"branch": name, "where": "hub", "commit": sha})
        else:
            skipped.append({"branch": name, "where": "hub", "reason": summary.strip("[] ")})
    g.run("fetch", "--quiet", "--prune", hub)
    return out


# --- human output -------------------------------------------------------------------------


def _age(date: str | None) -> str:
    ts = _ts(date)
    if not ts:
        return "?"
    days = (time.time() - ts) / 86400
    return f"{days * 24:.0f}h" if days < 1 else f"{days:.0f}d"


def _state(b: dict[str, Any]) -> str:
    if b["merged"]:
        return "merged"
    if b["merges_clean"] is False:
        return "conflict"
    return "clean"


def render_list(d: dict[str, Any]) -> str:
    lines = [
        f"branches against {d['into']} @ {(d['into_head'] or '?')[:7]}"
        + (f" (hub: {d['hub']})" if d["hub"] else "")
        + f": {d['counts']['unmerged']} unmerged, {d['counts']['merged']} merged"
    ]
    for b in d["branches"]:
        if b["merged"]:
            continue
        where = "" if b["where"] == "local" else " (hub only)"
        lines.append(
            row(
                _state(b),
                f"{b['name']}{where}: +{b['ahead']} -{b['behind']}, "
                f"{b['files_total']} file(s), {_age(b['date'])} — {cap(b['subject'], 60)}",
            )
        )
        if b["conflicts"]:
            lines.append(sub("conflicts", ", ".join(b["conflicts"])))
        if b["checked_out_in"] and b["checked_out_in"] != d["workspace"]["path"]:
            lines.append(sub("worktree", b["checked_out_in"]))
    merged = [b["name"] for b in d["branches"] if b["merged"]]
    if merged:
        lines.append(row("prunable", f"{len(merged)}: {cap(', '.join(merged), 200)}"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)


def render_merge(d: dict[str, Any]) -> str:
    dry = " (dry run)" if d["dry_run"] else ""
    if not d["merged"]:
        lines = [f"branches merge{dry}: nothing to merge into {d['into']}"]
    else:
        lines = [
            f"branches merge{dry}: {d['into']} {d['old'][:7]} -> "
            + ("(not moved)" if d["dry_run"] else d["new"][:7])
        ]
    for m in d["merged"]:
        lines.append(
            row("merged", f"{m['branch']}: {m['commits']} commit(s), {len(m['files'])} file(s)")
        )
    for s in d["skipped"]:
        lines.append(row("skipped", f"{s['branch']}: {s['reason']}"))
    b = d["build"]
    if b:
        lines.append(row("built", f"{b['host']}: {b['out']}"))
    elif d["merged"] and not d["dry_run"]:
        lines.append(row("built", "no (--no-build)"))
    if d["pushed"]:
        lines.append(row("pushed", f"{d['hub']}/{d['into']}"))
    if d["merged"] and not d["dry_run"]:
        lines.append(row("next", "nixin apply (switches to it; plan shows the diff first)"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)


def render_prune(d: dict[str, Any]) -> str:
    verb = "would delete" if d["dry_run"] else "deleted"
    lines = [f"branches prune: {len(d['deleted'])} {verb}, {d['unmerged']} unmerged kept"]
    for x in d["deleted"]:
        lines.append(row(verb.split()[-1], f"{x['branch']} ({x['where']}, {x['commit'][:7]})"))
    for x in d["skipped"]:
        lines.append(row("kept", f"{x['branch']} ({x['where']}): {x['reason']}"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)
