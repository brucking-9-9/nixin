"""Human (non --json) output of plan, apply --dry-run, apply, sync and status's pending
line. The --json document has everything; this is a short read of it."""

from __future__ import annotations

from typing import Any

from ..truth.render import row, sub
from .closure import human_size

SHOWN = 25  # closure rows printed per kind


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def _store(p: str | None) -> str:
    return p or "-"


def _gen(run: dict[str, Any]) -> str:
    g = run.get("generation")
    return f"generation {g}" if g is not None else "not a profile generation"


def diff_lines(diff: dict[str, Any] | None) -> list[str]:
    if diff is None:
        return [row("closure", "no diff (no running system to compare, or another host)")]
    a, r, c = diff["added"], diff["removed"], diff["changed"]
    if not (a or r or c):
        return [row("closure", "no change")]
    head = f"{len(a)} added, {len(r)} removed, {len(c)} changed, {human_size(diff['size_delta'])}"
    lines = [row("closure", head)]
    for x in a[:SHOWN]:
        lines.append(sub("+", f"{x['name']} {x['version']} {human_size(x['size'])}".rstrip()))
    for x in r[:SHOWN]:
        lines.append(sub("-", f"{x['name']} {x['version']} {human_size(x['size'])}".rstrip()))
    for x in c[:SHOWN]:
        ver = f"{x['from']} → {x['to']}" if x["from"] is not None or x["to"] is not None else ""
        lines.append(sub("~", f"{x['name']} {ver} {human_size(x['size_delta'])}".strip()))
    more = max(0, len(a) - SHOWN) + max(0, len(r) - SHOWN) + max(0, len(c) - SHOWN)
    if more:
        lines.append(sub("…", f"{more} more (--json has all)"))
    return lines


def _workspace(w: dict[str, Any]) -> str:
    dirty = f", {len(w['dirty'])} changed: {', '.join(w['dirty'][:4])}" if w["dirty"] else ""
    return f"{w['path']} {w['branch']} @ {_short(w['head'])}{dirty}"


def _pending(commits: list[dict] | None) -> str:
    if commits is None:
        return "unknown (the authoritative HEAD is not in the workspace)"
    if not commits:
        return "none"
    return f"{len(commits)}: " + "; ".join(
        f"{_short(c['commit'])} {c['subject']}" for c in commits[:3]
    )


def plan(d: dict[str, Any], host: str) -> str:
    built = "cached" if d["cached"] else f"built in {d['duration_s']} s"
    lines = [
        f"plan {host}: {_store(d['out'])} ({built})",
        row("workspace", _workspace(d["workspace"])),
        row(
            "authorit.",
            f"{d['authoritative']['path']} @ {_short(d['authoritative']['head'])} "
            f"({d['authoritative']['relation']})",
        ),
        row("pending", _pending(d["pending_commits"])),
        row("running", f"{_gen(d['running'])} {_store(d['running']['store_path'])}"),
        *diff_lines(d["diff"]),
    ]
    lines += [row("warning", w) for w in d["warnings"]]
    lines.append("up to date: nothing to apply" if d["up_to_date"] else "next: nixin apply")
    return "\n".join(lines)


def dry_run(d: dict[str, Any], host: str) -> str:
    lines = [f"apply {host} --dry-run: would run, in order (nothing was run)"]
    for c in d["commands"]:
        tag = "root" if c["privileged"] else "user"
        lines.append(sub(f"[{tag}]", c["command"]))
    if d["commit"]:
        lines.append(row("commit", d["commit"]["message"].splitlines()[0]))
    lines.append(row("out", f"{_store(d['out'])}" + (" (cached)" if d["cached"] else "")))
    for step, p in d["privilege"].items():
        state = "ready" if p["ready"] else f"not ready: {p['why']}"
        lines.append(row(step, f"{p['backend']} ({state})"))
    if d["diff"] is not None:
        lines += diff_lines(d["diff"])
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)


def apply(d: dict[str, Any], host: str) -> str:
    how = "switched to" if d["switched"] else "boot default is"
    lines = [f"apply {host}: {how} generation {d['generation']} {_store(d['out'])}"]
    if d["committed"]:
        lines.append(row("committed", f"{_short(d['committed'])} {d['commit_message']}"))
    lines.append(row("authorit.", f"@ {_short(d['authoritative_head'])}"))
    for c in d["privileged_commands"]:
        lines.append(sub("[root]", c))
    lines += diff_lines(d["diff"])
    t = d["timings"]
    lines.append(
        row(
            "took",
            f"{t['total']} s (build {t['build']} s, pull {t['pull']} s, switch {t['switch']} s)",
        )
    )
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)


def sync(d: dict[str, Any]) -> str:
    w, a, r = d["workspace"], d["authoritative"], d["remote"]
    ab = (
        f"{w['ahead']} ahead, {w['behind']} behind {r['name']}/{w['branch']}"
        if w["ahead"] is not None
        else f"{r['name']} has no {w['branch']}"
    )
    behind = a["behind_workspace"]
    lines = [
        f"sync: {', '.join(d['done'])}",
        row("workspace", f"{w['path']} {w['branch']} @ {_short(w['head'])}: {ab}"),
        row("remote", f"{r['name']} {r['url']} @ {_short(r['head'])}"),
        row(
            "authorit.",
            f"{a['path']} @ {_short(a['head'])} ({a['relation']}"
            + (f", {behind} commit(s) to apply" if behind else "")
            + ")",
        ),
    ]
    lines += [row("warning", x) for x in d["warnings"]]
    return "\n".join(lines)


def pending(p: dict[str, Any]) -> str:
    if "error" in p:
        return row("pending", f"? ({p['error']['message']})")
    if p["workspace"] is None:
        text = "(this is the authoritative tree; pass the workspace with --config)"
    else:
        parts = [f"commits {_pending(p['commits'])}"]
        if p["dirty"]:
            parts.append(f"{len(p['dirty'])} uncommitted file(s)")
        text = "; ".join(parts)
    if p["running_matches"] is not None:
        text += (
            "; running = authoritative build"
            if p["running_matches"]
            else ("; running is NOT the authoritative HEAD's build")
        )
    return row("pending", text)
