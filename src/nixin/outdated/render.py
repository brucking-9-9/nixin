"""Human text for `nixin outdated`: one aligned table per scope, a summary, what to run."""

from __future__ import annotations

import textwrap
from typing import Any

ARROW = "→"


def _when(rev: str | None, date: str | None) -> str:
    return f"{rev or '?'}" + (f" ({date})" if date else "")


def _table(rows: list[tuple[str, str, str, str]]) -> list[str]:
    """`pname  old → new  note`, columns aligned."""
    if not rows:
        return []
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    w2 = max(len(r[2]) for r in rows)
    out = []
    for name, old, new, note in rows:
        if new:
            line = f"  {name:<{w0}}  {old:>{w1}} {ARROW} {new:<{w2}}"
        else:
            line = f"  {name:<{w0}}  {old:>{w1}}   {'':<{w2}}"
        out.append((line + (f"  {note}" if note else "")).rstrip())
    return out


def _scope_rows(doc: dict[str, Any], scope: str, user: str | None) -> list[tuple]:
    def mine(r: dict[str, Any]) -> bool:
        return r["scope"] == scope and r["user"] == user

    rows: list[tuple[str, str, str, str]] = []
    if doc["mode"] == "lock":
        for r in filter(mine, doc["changed"]):
            rows.append((r["pname"], r["from"], r["to"], _dir_note(r)))
        for r in filter(mine, doc["removed"]):
            rows.append((r["pname"], r["version"], "", "(removed)"))
        for r in filter(mine, doc["added"]):
            rows.append((r["pname"], "", r["version"], "(added)"))
    else:
        for r in filter(mine, doc["changed"]):
            note = [_dir_note(r)] if _dir_note(r) else []
            if r.get("attr") and r["attr"].rsplit(".", 1)[-1] != r["pname"]:  # not kdePackages.x
                note.append(f"as {r['attr']}")
            rows.append((r["pname"], r["installed"], r["published"], "  ".join(note)))
    rows.sort(key=lambda r: r[0].lower())
    return rows


def _dir_note(r: dict[str, Any]) -> str:
    return {"down": "(older)", "same": "(same order)"}.get(r.get("direction") or "", "")


def _kernel(k: dict[str, Any], mode: str) -> str:
    if not k.get("from"):
        return "kernel unknown"
    if mode == "published":
        bits = f"kernel {k['from']}"
        if k.get("to") and k["to"] != k["from"]:
            bits += f" {ARROW} {k['to']}"
        elif k.get("to"):
            bits += " (newest of its series)"
        if k.get("latest") and k["latest"] not in (k["from"], k.get("to")):
            bits += f", latest {k['latest']}"
        return bits
    if k.get("changed"):
        return f"kernel {k['from']} {ARROW} {k['to'] or '?'}"
    return f"kernel {k['from']} (unchanged)"


_WHY = {
    "nix flake update": "write the bump into flake.lock",
    "nix flake lock": "pin nixpkgs to exactly the commit compared",
    "nixin plan": "build it, diff the closure",
    "nixin apply": "commit and switch (sudo)",
    "nixin outdated": "what the lock update itself would change",
}


def _next(commands: list[str]) -> list[str]:
    """The commands with a comment each, aligned; a very long one gets its comment above."""
    w = max((len(c) for c in commands if len(c) <= 64), default=0)
    out = ["next:"]
    for c in commands:
        why = next((v for k, v in _WHY.items() if c.startswith(k)), "")
        if not why:
            out.append(f"  {c}")
        elif len(c) > 64:
            out += [f"  # {why}", f"  {c}"]
        else:
            out.append(f"  {c:<{w}}  # {why}")
    return out


def outdated(doc: dict[str, Any], host: str) -> str:
    lock, target, mode = doc["lock"], doc["target"], doc["mode"]
    have = _when(lock.get("short_rev"), lock.get("date"))
    lines: list[str] = []
    if mode == "lock":
        days = target.get("days_newer")
        newer = f", {days:g} days newer" if days else ""
        lines.append(
            f"{host}: {lock['input']} ({target['flakeref']}) {have} {ARROW} "
            f"{_when(target.get('short_rev'), target.get('date'))}{newer}"
        )
        if doc.get("up_to_date"):
            lines += ["", f"the lock already has {target['short_rev']}: nothing would change"]
            return "\n".join(lines)
        verb = "would change"
    else:
        lines.append(
            f"{host}: installed ({lock['input']} {have}) vs {target['source']} "
            f"{target['channel']} (index {target.get('short_rev') or '?'})"
        )
        verb = f"differ on {target['channel']}"
    for s in doc["scopes"]:
        rows = _scope_rows(doc, s["scope"], s["user"])
        label = "system" if s["user"] is None else f"home {s['user']}"
        extra = []
        if s.get("added"):
            extra.append(f"{s['added']} added")
        if s.get("removed"):
            extra.append(f"{s['removed']} removed")
        tail = f" ({', '.join(extra)})" if extra else ""
        lines += ["", f"{label}: {s['changed']} of {s['total']} {verb}{tail}"]
        lines += _table(rows)
    summary = f"{doc['count']} of {doc['total']} packages {verb}; {_kernel(doc['kernel'], mode)}"
    if mode == "published" and doc.get("unpublished"):
        summary += f"; {len(doc['unpublished'])} not on {target['source']}"
    lines += ["", summary]
    if mode == "lock":  # --published's caveat is in the document; the text says "as attr"
        lines += [textwrap.fill(n, 88) for n in doc.get("notes") or []]
    if doc.get("commands"):
        lines += _next(doc["commands"])
    return "\n".join(lines)
