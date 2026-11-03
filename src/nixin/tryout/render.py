"""Human text for `nixin try --build-only` (and the lines printed before a handoff)."""

from __future__ import annotations

from typing import Any


def size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, scale in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"


def title(doc: dict[str, Any]) -> str:
    name = doc.get("pname") or doc["attr"]
    return f"{name} {doc['version']}" if doc.get("version") else name


def programs_line(doc: dict[str, Any]) -> str:
    progs = doc.get("programs") or []
    if not progs:
        return "none (nothing in bin/)"
    main = doc.get("program") or doc.get("main_program")
    shown = [f"{p} (runs)" if p == main else p for p in progs[:12]]
    more = f", … {len(progs) - 12} more" if len(progs) > 12 else ""
    return ", ".join(shown) + more


def got(doc: dict[str, Any]) -> str:
    """What this run added to the store."""
    if doc.get("cached"):
        return "already in the store"
    f = doc.get("fetched")
    if not f:
        return "fetched or built just now"
    parts = []
    if f.get("paths"):
        parts.append(f"fetched {f['paths']} paths, {size(f['download'])} download")
    if f.get("built"):
        parts.append(f"built {f['built']} derivations")
    return "; ".join(parts) or "fetched or built just now"


def ready(doc: dict[str, Any]) -> str:
    where = got(doc)
    rows = [
        ("from", doc["nixpkgs"]["label"]),
        ("out", doc.get("out") or "?"),
        ("size", f"{size(doc.get('size'))} with its dependencies, {where}"),
        ("programs", programs_line(doc)),
    ]
    if doc.get("gui"):
        names = ", ".join(e["file"] for e in doc.get("desktop") or [] if not e["hidden"])
        rows.append(("app", f"graphical ({names})"))
    if doc.get("command"):
        rows.append(("run", doc["command"]))
    rows.append(("shell", doc["shell_command"]))
    head = title(doc) + (f": {doc['description']}" if doc.get("description") else "")
    body = [f"  {k:<9} {v}" for k, v in rows]
    tail = "  nothing is installed: the next garbage collection removes it"
    return "\n".join([head, *body, tail])
