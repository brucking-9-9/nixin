"""history: this machine's system generations, newest first, with what nixin knows of each.

The generations are the profile links (`/nix/var/nix/profiles/system-N-link`): number,
date (the link's mtime), store path, kernel release, NixOS version, and which one runs
(`current`), which one was booted, which one is the boot default. Each is joined with
the lines of `installer/history.jsonl` whose `out` is its store path: the apply (or
rollback) that made it, with its commit and message. Two generations can share a path
(a rollback to an older system, or a revert that rebuilds an older tree); a line whose
`generation` is this one wins over a line for another generation with the same path.

`config_commit(...)` answers rollback's question "which commit builds this system":
those history lines, then plan's commit cache, over the workspace's ancestry.
"""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from ..configrepo import ConfigRepo, NixinError
from ..truth.render import term_width
from . import build, system
from .git import Git

DEFAULT_LIMIT = 20
ANCESTRY = 200  # first-parent commits rollback searches plan's cache in


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"newest N generations (default {DEFAULT_LIMIT}; 0: all)",
    )


def generation_rows(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Every system generation, newest first, joined with the history lines."""
    gens = system.generations()
    current = system.pick_generation(gens, system.realpath(system.CURRENT))
    booted = system.pick_generation(gens, system.realpath(system.BOOTED), since=system.BOOTED)
    default = system.profile_generation()
    by_out: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_out.setdefault(e["out"], []).append(e)
    rows = []
    for n, path in reversed(gens):
        lines = by_out.get(path, [])
        own = [e for e in lines if e.get("generation") == n]
        pick, match = None, None
        if own:
            pick, match = own[-1], "generation"
        else:
            with_commit = [e for e in lines if e.get("commit")]
            if with_commit:
                pick, match = with_commit[-1], "out"
        rows.append(
            {
                "generation": n,
                "date": system.link_time(n),
                "store_path": path,
                "current": n == current,
                "booted": n == booted,
                "default": n == default,
                "kernel": system.kernel_version(path),
                "nixos_version": system.nixos_version(path),
                "commit": pick.get("commit") if pick else None,
                "message": pick.get("message") if pick else None,
                "mode": pick.get("mode") if pick else None,
                "applied": pick.get("ts") if pick else None,
                "applied_host": pick.get("host") if pick else None,
                "match": match,
                "match_generation": pick.get("generation") if match == "out" else None,
                "entries": own,
            }
        )
    known = {p for _, p in gens}
    return {
        "rows": rows,
        "current": current,
        "booted": booted,
        "default": default,
        "unmatched": sum(1 for e in entries if e["out"] not in known),
    }


def history(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _history(ns)
    except NixinError as e:
        return e.doc()


def _history(ns: argparse.Namespace) -> dict[str, Any]:
    if getattr(ns, "args", None):
        raise NixinError("usage", f"history takes no arguments (got {' '.join(ns.args)!r})")
    if ns.limit < 0:
        raise NixinError("usage", "--limit must be 0 (all) or more")
    from . import authoritative_path, local_hostname  # the package's; no cycle at call time

    repo = ConfigRepo.locate(ns.config)  # read only: /etc/nixos is fine here
    if ns.host != local_hostname():
        raise NixinError(
            "host-not-local",
            f"the generations on this machine are {local_hostname()}'s; run nixin history "
            f"on {ns.host}",
        )
    warnings = []
    auth = authoritative_path(getattr(ns, "authoritative", None))
    if auth.exists() and auth.resolve() == repo.path:
        warnings.append(
            f"{repo.path} is the authoritative tree: its {system.HISTORY} lags the "
            "workspace's by one apply (pass the workspace with --config)"
        )
    hist = repo.path / system.HISTORY
    entries = system.read_history(repo.path)
    if not hist.is_file():
        warnings.append(f"no {system.HISTORY} in {repo.path}: generations without commits")
    j = generation_rows(entries)
    rows = j["rows"]
    doc = {
        "ok": True,
        "generations": rows if ns.limit == 0 else rows[: ns.limit],
        "total": len(rows),
        "current": j["current"],
        "booted": j["booted"],
        "default": j["default"],
        "history": {"path": str(hist), "entries": len(entries), "unmatched": j["unmatched"]},
        "warnings": warnings,
    }
    if not ns.json:
        doc["message"] = render(doc, ns.host)
    return doc


# --- rollback's lookup ------------------------------------------------------------------


def config_commit(
    git: Git, entries: list[dict[str, Any]], host: str, generation: int, out: str
) -> dict[str, Any]:
    """A commit of the workspace's history that builds `out`, newest evidence first:
    history lines for this generation, then for the same out path, then plan's commit
    cache over the first-parent ancestry of HEAD. {commit, source, ...} or {commit: None,
    why}."""
    lines = [e for e in entries if e.get("out") == out and e.get("commit")]
    own = [e for e in lines if e.get("generation") == generation]
    tried = []
    for e in [*reversed(own), *(e for e in reversed(lines) if e not in own)]:
        c = e["commit"]
        if c in tried:
            continue
        tried.append(c)
        if git.has_commit(c) and git.is_ancestor(c, "HEAD"):
            return {
                "commit": c,
                "source": "history",
                "match": "generation" if e in own else "out",
                "entry": e,
            }
    cache = build.PlanCache()
    p = git.run("rev-list", "--first-parent", f"--max-count={ANCESTRY}", "HEAD")
    for c in p.out.split() if p.ok else []:
        hit = cache.by_commit(host, c)
        if hit and hit.get("out") == out:
            return {"commit": c, "source": "plan-cache", "match": "out", "entry": None}
    if tried:
        return {
            "commit": None,
            "why": "not-in-workspace",
            "candidates": tried,
        }
    return {"commit": None, "why": "no-mapping", "candidates": []}


# --- human output -------------------------------------------------------------------------


def _local(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        t = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    except ValueError:
        return ts
    return t.astimezone().strftime("%Y-%m-%d %H:%M")


def flags(r: dict[str, Any]) -> str:
    """`*` runs now, `b` was booted, `d` is the boot default (shown when it is not `*`)."""
    return (
        ("*" if r["current"] else " ")
        + ("b" if r["booted"] else " ")
        + ("d" if r["default"] and not r["current"] else " ")
    )


def _what(r: dict[str, Any]) -> str:
    if not r["commit"] and not r["mode"]:
        return ""
    text = f"{(r['commit'] or '-------')[:7]}  {r['message'] or ''}".rstrip()
    if r["match"] == "out":
        text += f" (= gen {r['match_generation']})"
    elif r["mode"] and r["mode"] != "switch":
        text += f" [{r['mode']}]"
    return text


def render(d: dict[str, Any], host: str) -> str:
    rows = d["generations"]
    more = d["total"] - len(rows)
    head = f"history {host}: {d['total']} generations"
    head += f", newest {len(rows)} shown (--limit 0: all)" if more else ""
    lines = [head]
    if rows:
        kw = max(6, *(len(r["kernel"] or "?") for r in rows))
        vw = max(5, *(len(r["nixos_version"] or "?") for r in rows))
        gw = max(3, *(len(str(r["generation"])) for r in rows))
        lines.append(
            f"{'':3} {'gen':>{gw}}  {'date':<16}  {'kernel':<{kw}}  {'nixos':<{vw}}  "
            "commit   message"
        )
        width = term_width()
        for r in rows:
            line = (
                f"{flags(r)} {r['generation']:>{gw}}  {_local(r['date']):<16}  "
                f"{r['kernel'] or '?':<{kw}}  {r['nixos_version'] or '?':<{vw}}  {_what(r)}"
            ).rstrip()
            lines.append(line if len(line) <= width else line[: width - 1] + "…")
        lines.append("* running now, b booted, d boot default")
    h = d["history"]
    lines.append(
        f"{h['entries']} line(s) in {h['path']}"
        + (f", {h['unmatched']} for generations no longer on disk" if h["unmatched"] else "")
    )
    lines += [f"warning   {w}" for w in d["warnings"]]
    return "\n".join(lines)
