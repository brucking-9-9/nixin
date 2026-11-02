"""`nix store diff-closures OLD NEW`, parsed.

nix prints one line per package name whose versions or size changed:

    cowsay: ∅ → 3.8.4, 68.2 KiB          added (no version before)
    aalib: 1.4rc5 → ∅, -208.0 KiB         removed
    attica: 6.30.0 → 6.31.0               changed (size delta under nix's 8 KiB cut-off)
    akonadi: 267.0 KiB                    same versions, different size
    breeze: 6.7.5, 6.7.5-qt5 → 6.8.1, 6.8.1-qt5, -2.4 MiB

Versions are comma-separated sets, `∅` is the empty set, `ε` an empty version string.
Sizes carry a sign only when negative and are ANSI-coloured; both are normalised here.
"""

from __future__ import annotations

import re
from typing import Any

ARROW = " → "
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SIZE = re.compile(r"^([+-]?\d+(?:\.\d+)?) (B|KiB|MiB|GiB|TiB)$")
_UNIT = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def size_bytes(text: str) -> int | None:
    m = _SIZE.match(text.strip())
    if not m:
        return None
    return round(float(m.group(1)) * _UNIT[m.group(2)])


def _versions(text: str) -> str | None:
    """nix's version set as one string ("" for ε), None for ∅."""
    t = text.strip()
    if t == "∅":
        return None
    return ", ".join("" if v == "ε" else v for v in t.split(", "))


def parse_line(line: str) -> dict[str, Any] | None:
    line = _ANSI.sub("", line).rstrip()
    name, sep, rest = line.partition(": ")
    if not sep or not name:
        return None
    delta: int | None = None
    if ARROW not in rest:
        delta = size_bytes(rest)
        if delta is None:
            return None
        return {"kind": "changed", "name": name, "from": None, "to": None, "size_delta": delta}
    old, new = rest.split(ARROW, 1)
    head, _, last = new.rpartition(", ")
    if head and size_bytes(last) is not None:
        new, delta = head, size_bytes(last)
    elif size_bytes(new) is not None:  # "∅ → 12 KiB" cannot happen, but keep it total
        delta = size_bytes(new)
    before, after = _versions(old), _versions(new)
    if before is None and after is not None:
        return {"kind": "added", "name": name, "version": after, "size": delta}
    if after is None and before is not None:
        return {"kind": "removed", "name": name, "version": before, "size": delta}
    return {"kind": "changed", "name": name, "from": before, "to": after, "size_delta": delta}


def parse(text: str) -> dict[str, Any]:
    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unparsed: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        row = parse_line(raw)
        if row is None:
            unparsed.append(_ANSI.sub("", raw))
            continue
        kind = row.pop("kind")
        {"added": added, "removed": removed, "changed": changed}[kind].append(row)
    total = sum(r.get("size") or r.get("size_delta") or 0 for r in [*added, *removed, *changed])
    doc: dict[str, Any] = {
        "added": added,
        "removed": removed,
        "changed": changed,
        "size_delta": total,
    }
    if unparsed:
        doc["unparsed"] = unparsed
    return doc


def human_size(n: int | None) -> str:
    if n is None:
        return ""
    sign = "-" if n < 0 else "+"
    v = abs(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if v < 1024 or unit == "GiB":
            return f"{sign}{v:.1f} {unit}" if unit != "B" else f"{sign}{v} B"
        v /= 1024
    return f"{sign}{v:.1f} TiB"  # pragma: no cover
