"""Human output for the discovery verbs: a compact aligned table, or a key/value
block for `info`. Everything here is cosmetic; the contract is the JSON.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

ELLIPSIS = "…"


def width() -> int:
    w = os.environ.get("NIXIN_WIDTH")
    if w and w.isdigit():
        return int(w)
    return shutil.get_terminal_size((100, 24)).columns


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    if n <= 0:
        return ""
    return text if len(text) <= n else text[: max(n - 1, 0)] + ELLIPSIS


def table(headers: list[str], rows: list[list[str]], total: int | None = None) -> str:
    """Columns sized to content; the last column absorbs what is left of the width."""
    total = total or width()
    cols = len(headers)
    rows = [[str(c) if c is not None else "" for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i in range(cols - 1):
            widths[i] = max(widths[i], len(" ".join(r[i].split())))
    # Keep the fixed columns from eating the line on their own.
    budget = total - 2 * (cols - 1)
    cap = max(12, budget // max(cols, 1))
    widths = [min(w, cap if i < cols - 1 else w) for i, w in enumerate(widths)]
    last = max(8, budget - sum(widths[:-1]))
    widths[-1] = last
    lines = []
    for r in [headers, *rows]:
        cells = [_clip(c, widths[i]).ljust(widths[i]) for i, c in enumerate(r)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _row(rec: dict[str, Any]) -> tuple[str, str, str]:
    kind = rec.get("kind")
    if kind == "pkg":
        return rec.get("name", ""), rec.get("version") or "", rec.get("description") or ""
    if kind in ("opt", "hm"):
        return rec.get("name", ""), rec.get("type") or "", rec.get("description") or ""
    if kind == "program":
        pkg = rec.get("package", "")
        ver = rec.get("version") or ""
        return rec.get("name", ""), f"{pkg} {ver}".strip(), rec.get("description") or ""
    if kind == "fn":
        return rec.get("name", ""), rec.get("signature") or "", rec.get("description") or ""
    return rec.get("name", ""), "", rec.get("description") or ""


_HEADERS = {
    "pkg": ["PACKAGE", "VERSION", "DESCRIPTION"],
    "opt": ["OPTION", "TYPE", "DESCRIPTION"],
    "hm": ["HM OPTION", "TYPE", "DESCRIPTION"],
    "program": ["PROGRAM", "PACKAGE", "DESCRIPTION"],
    "fn": ["FUNCTION", "SIGNATURE", "DESCRIPTION"],
}


def _footer(doc: dict[str, Any]) -> str:
    bits = [f"{doc.get('count', len(doc.get('results', [])))} result(s)"]
    if doc.get("source") == "all":
        for name, s in doc.get("sources", {}).items():
            tag = f"{name}"
            if s.get("channel"):
                tag += f" {s['channel']}"
            if s.get("rev"):
                tag += f"@{s['rev'][:8]}"
            if s.get("error"):
                tag += f" FAILED: {s['error'].get('message')}"
            elif s.get("cached"):
                tag += " (cached)"
            bits.append(tag)
    else:
        tag = str(doc.get("source", ""))
        if doc.get("channel"):
            tag += f" {doc['channel']}"
        if doc.get("rev"):
            tag += f"@{doc['rev'][:8]}"
        if doc.get("cached"):
            tag += " (cached)"
        bits.append(tag)
    lines = ["# " + " · ".join(bits)]
    lines += [f"# note: {n}" for n in doc.get("notes", [])]
    return "\n".join(lines)


def render_search(doc: dict[str, Any]) -> str:
    results = doc.get("results", [])
    kinds = {r.get("kind") for r in results}
    if not results:
        return _footer(doc)
    if len(kinds) == 1:
        kind = next(iter(kinds))
        headers = _HEADERS.get(kind, ["NAME", "", "DESCRIPTION"])
        rows = [list(_row(r)) for r in results]
    else:
        headers = ["KIND", "NAME", "VERSION/TYPE", "DESCRIPTION"]
        rows = [[r.get("kind", ""), *_row(r)] for r in results]
    return table(headers, rows) + "\n" + _footer(doc)


_INFO_ORDER = [
    "pname",
    "version",
    "description",
    "type",
    "default",
    "example",
    "homepage",
    "license",
    "unfree",
    "broken",
    "main_program",
    "programs",
    "outputs",
    "platforms",
    "position",
    "declarations",
    "declaration_urls",
    "signature",
    "aliases",
    "alternatives",
    "matched_by",
    "source",
    "channel",
    "rev",
]
_SKIP = {"kind", "name", "description", "example", "default"}


def _value(v: Any) -> str:
    if isinstance(v, list):
        if len(v) > 12:
            return ", ".join(str(x) for x in v[:12]) + f", … ({len(v)} total)"
        return ", ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def _block(rec: dict[str, Any]) -> str:
    lines = [f"{rec.get('kind', '')}: {rec.get('name', '')}"]
    for key in ("description", "default", "example"):
        v = rec.get(key)
        if v:
            text = str(v)
            if "\n" in text:
                lines.append(f"  {key}:")
                lines += [f"    {ln}" for ln in text.splitlines()]
            else:
                lines.append(f"  {key}: {text}")
    keys = [k for k in _INFO_ORDER if k not in _SKIP and rec.get(k) not in (None, [], "")]
    keys += [
        k for k in rec if k not in _INFO_ORDER and k not in _SKIP and rec[k] not in (None, [], "")
    ]
    w = max((len(k) for k in keys), default=0)
    for k in keys:
        lines.append(f"  {k.ljust(w)}  {_value(rec[k])}")
    return "\n".join(lines)


def render_info(doc: dict[str, Any]) -> str:
    blocks = [_block(r) for r in doc.get("results", [])]
    return "\n\n".join(blocks) + "\n" + _footer(doc)


def render_versions(doc: dict[str, Any]) -> str:
    head = [f"{doc.get('package', '')}"]
    if doc.get("summary"):
        head.append(str(doc["summary"]))
    total = doc.get("total")
    shown = len(doc.get("results", []))
    head.append(f"{shown} of {total} versions" if total is not None else f"{shown} versions")
    rows = [
        [
            r.get("version") or "",
            r.get("date") or "",
            (r.get("rev") or "")[:12],
            r.get("attr") or "",
            ", ".join(r.get("platforms") or []),
        ]
        for r in doc.get("results", [])
    ]
    out = " · ".join(head) + "\n"
    out += table(["VERSION", "DATE", "NIXPKGS REV", "ATTR", "PLATFORMS"], rows) + "\n"
    return out + _footer(doc)


def render(verb: str, doc: dict[str, Any]) -> str:
    if verb == "info":
        return render_info(doc)
    if verb == "versions":
        return render_versions(doc)
    return render_search(doc)
