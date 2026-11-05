"""Human text for `nixin pins` and the pin lines other verbs print."""

from __future__ import annotations

from typing import Any


def pin_row(p: dict[str, Any]) -> tuple[str, str, str, str, str]:
    attrs = ", ".join(p.get("attrs") or [])
    held = f"{p.get('short_rev') or '?'}" + (f" ({p['date']})" if p.get("date") else "")
    version = str(p.get("version") or "?")
    note = []
    if p.get("stale"):
        note.append(f"(stale: {p.get('stale_why') or 'holds nothing'})")
    if p.get("why"):
        note.append(p["why"])
    return (p["name"], attrs, version, held, "  ".join(note))


def table(pins: list[dict[str, Any]]) -> list[str]:
    if not pins:
        return []
    rows = [pin_row(p) for p in pins]
    head = ("pin", "attrs", "held at", "nixpkgs", "")
    rows.insert(0, head)
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    out = []
    for r in rows:
        line = "  ".join(f"{r[j]:<{widths[j]}}" for j in range(4)).rstrip()
        if r[4]:
            line += f"  {r[4]}"
        out.append("  " + line)
    return out


def checks(items: list[dict[str, Any]]) -> list[str]:
    return [f"{c['level']}: {c['text']}" for c in items]


def pins(doc: dict[str, Any]) -> str:
    lines = [f"{doc['host']}: {summary(doc)}"]
    lines += table(doc.get("pins") or [])
    if doc.get("checks"):
        lines += ["", *checks(doc["checks"])]
    if doc.get("pins"):
        lines += ["", "drop one: nixin unpin NAME; all: nixin unpin --all"]
    return "\n".join(lines)


def summary(doc: dict[str, Any]) -> str:
    n = len(doc.get("pins") or [])
    if not n:
        return "no pins"
    stale = sum(1 for p in doc["pins"] if p.get("stale"))
    lock = doc.get("lock") or {}
    text = f"{n} pin{'s' if n != 1 else ''}"
    if stale:
        text += f", {stale} stale"
    if lock.get("short_rev"):
        text += f" (flake.lock: nixpkgs {lock['short_rev']})"
    return text
