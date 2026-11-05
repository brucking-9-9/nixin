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


def kernel(k: dict[str, Any], mode: str) -> str:
    """`kernel 7.2.12 → 7.2.14`, `kernel 7.2.12 (unchanged)`, … (the UIs show it too)."""
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
    held = k.get("held")
    if held:
        avail = held.get("available")
        if avail and avail != k["from"]:
            return f"kernel {k['from']} (held by pin {held['pin']}; {avail} available)"
        return f"kernel {k['from']} (held by pin {held['pin']})"
    return f"kernel {k['from']} (unchanged)"


def holds(doc: dict[str, Any]) -> list[str]:
    """`holds:` block: what each pin keeps back, and whether it holds anything."""
    items = doc.get("holds") or []
    if not items:
        return []
    out = ["holds (nixin pins):"]
    for h in items:
        attr = h["attrs"][0]
        avail = (h.get("available") or {}).get(attr)
        line = f"  {h['name']}: {attr} held at {h.get('version') or '?'} (nixpkgs {h.get('short_rev') or '?'})"
        if h.get("behind"):
            line += f"; the target has {avail}"
        elif avail:
            line += f"; the target has the same {avail}"
        if h.get("stale"):
            line += f"  (stale: {h.get('stale_why')})"
        out.append(line)
    return out


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


def header(doc: dict[str, Any], host: str) -> str:
    """The first line: what was compared with what."""
    lock, target = doc["lock"], doc["target"]
    have = _when(lock.get("short_rev"), lock.get("date"))
    if doc["mode"] == "lock":
        days = target.get("days_newer")
        newer = f", {days:g} days newer" if days else ""
        return (
            f"{host}: {lock['input']} ({target['flakeref']}) {have} {ARROW} "
            f"{_when(target.get('short_rev'), target.get('date'))}{newer}"
        )
    return (
        f"{host}: installed ({lock['input']} {have}) vs {target['source']} "
        f"{target['channel']} (index {target.get('short_rev') or '?'})"
    )


def _verb(doc: dict[str, Any]) -> str:
    return "would change" if doc["mode"] == "lock" else f"differ on {doc['target']['channel']}"


def scope_line(doc: dict[str, Any], s: dict[str, Any]) -> str:
    """`home brucking: 9 of 187 would change (1 removed)` for one of `scopes[]`."""
    label = "system" if s["user"] is None else f"home {s['user']}"
    extra = [f"{s[k]} {k}" for k in ("added", "removed") if s.get(k)]
    tail = f" ({', '.join(extra)})" if extra else ""
    return f"{label}: {s['changed']} of {s['total']} {_verb(doc)}{tail}"


def summary(doc: dict[str, Any]) -> str:
    """`13 of 325 packages would change; kernel 7.2.12 → 7.2.14`."""
    if doc.get("up_to_date"):
        return f"the lock already has {doc['target']['short_rev']}: nothing would change"
    text = f"{doc['count']} of {doc['total']} packages {_verb(doc)}; "
    text += kernel(doc["kernel"], doc["mode"])
    if doc["mode"] == "published" and doc.get("unpublished"):
        text += f"; {len(doc['unpublished'])} not on {doc['target']['source']}"
    return text


def outdated(doc: dict[str, Any], host: str) -> str:
    mode = doc["mode"]
    lines = [header(doc, host)]
    if doc.get("up_to_date"):
        return "\n".join(
            [*lines, "", summary(doc), *(["", *holds(doc)] if doc.get("holds") else [])]
        )
    for s in doc["scopes"]:
        lines += ["", scope_line(doc, s)]
        lines += _table(_scope_rows(doc, s["scope"], s["user"]))
    lines += ["", summary(doc)]
    if doc.get("holds"):
        lines += ["", *holds(doc)]
    if mode == "lock":  # --published's caveat is in the document; the text says "as attr"
        lines += [textwrap.fill(n, 88) for n in doc.get("notes") or []]
    if doc.get("commands"):
        lines += _next(doc["commands"])
    return "\n".join(lines)
