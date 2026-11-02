"""Human (non --json) output of plan, apply --dry-run, apply, sync, rollback and status's
pending line (history renders itself). The --json document has everything; this is a
short read of it."""

from __future__ import annotations

from typing import Any

from ..truth.render import row, sub
from .closure import human_size
from .safety import reboot_text, space_text

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


def boot_lines(b: dict[str, Any] | None) -> list[str]:
    """`reboot    needs a reboot: kernel 7.2.12 → 7.3.0, initrd` and `/boot     +57.3 MiB
    of 80.4 MiB free (71 %)`; nothing when neither applies."""
    if not b:
        return []
    lines = []
    if b["reboot_needed"]:
        lines.append(row("reboot", f"needs a reboot: {reboot_text(b)}"))
    elif b["pending_reboot"]:
        lines.append(row("reboot", "pending already (the running kernel is not the booted one)"))
    for spec in b["specialisations"]:
        if spec.get("kernel"):
            k = spec["kernel"]
            lines.append(sub("~", f"specialisation {spec['name']}: kernel {k['from']} → {k['to']}"))
    s = b["boot_space"]
    if s["needed"] or s["state"] not in ("ok", "unknown"):
        tag = {"warn": " (tight)", "full": " (does not fit: refused)"}.get(s["state"], "")
        lines.append(row(s["path"], space_text(s) + tag))
    if b.get("boot_entry") is False:
        lines += [row("warning", w) for w in b["warnings"] if "no boot entry" in w]
    return lines


def _warnings(d: dict[str, Any]) -> list[str]:
    """warnings[] minus the boot ones boot_lines already shows."""
    shown = set((d.get("boot") or {}).get("warnings") or [])
    return [row("warning", w) for w in d["warnings"] if w not in shown]


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
        *boot_lines(d["boot"]),
    ]
    lines += _warnings(d)
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
    lines += boot_lines(d["boot"])
    lines += _warnings(d)
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
    lines += boot_lines(d["boot"])
    t = d["timings"]
    lines.append(
        row(
            "took",
            f"{t['total']} s (build {t['build']} s, pull {t['pull']} s, switch {t['switch']} s)",
        )
    )
    lines += _warnings(d)
    return "\n".join(lines)


def _config(c: dict[str, Any], dry: bool) -> str:
    target = _short(c["commit"])
    src = {"history": "history.jsonl", "plan-cache": "plan's cache"}.get(c["source"], "?")
    if c["state"] == "untouched":
        return "untouched: no commit known to build it (see the warning)"
    if c["state"] == "in-sync":
        return f"in sync: the authoritative tree already builds it ({target}, from {src})"
    n = len(c["reverted"])
    if dry:
        what = f"revert {n} commit(s) back to {target}" if n else f"pull {target}"
        return f"would {what} (from {src}) and pull it into the authoritative tree"
    made = f"{_short(c['revert_commit'])} reverts {n} commit(s), " if c["revert_commit"] else ""
    return f"{made}back to {target} (from {src}); authoritative @ {_short(c['authoritative_head'])}"


def rollback(d: dict[str, Any], host: str) -> str:
    head = f"rollback {host}: generation {d['from_generation']} → {d['generation']} "
    head += _store(d["out"]) if d["switched"] else "(it already ran; config only)"
    lines = [head, row("config", _config(d["config"], dry=False))]
    for c in d["privileged_commands"]:
        lines.append(sub("[root]", c))
    if d["diff"] is not None:
        lines += diff_lines(d["diff"])
    lines += boot_lines(d["boot"])
    t = d["timings"]
    lines.append(row("took", f"{t['total']} s (switch {t['switch']} s, pull {t['pull']} s)"))
    lines += _warnings(d)
    return "\n".join(lines)


def rollback_dry_run(d: dict[str, Any], host: str) -> str:
    gens = f"generation {d['from_generation']} → {d['generation']}"
    lines = [f"rollback {host} --dry-run: {gens}; would run, in order (nothing was run)"]
    for c in d["commands"]:
        lines.append(sub("[root]" if c["privileged"] else "[user]", c["command"]))
    lines.append(row("config", _config(d["config"], dry=True)))
    for step, p in d["privilege"].items():
        state = "ready" if p["ready"] else f"not ready: {p['why']}"
        lines.append(row(step, f"{p['backend']} ({state})"))
    if d["diff"] is not None:
        lines += diff_lines(d["diff"])
    lines += boot_lines(d["boot"])
    lines += _warnings(d)
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
