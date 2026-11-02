"""Text edits on character offsets, unified diffs, and the `imports` list surgery."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from .nixtext import NixFile, NixSyntaxError, Shape, lex


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


def apply(text: str, edits: list[Edit]) -> str:
    out: list[str] = []
    pos = 0
    for e in sorted(edits, key=lambda e: (e.start, e.end)):
        if e.start < pos:
            raise ValueError(f"overlapping edits at offset {e.start}")
        out += [text[pos : e.start], e.text]
        pos = e.end
    out.append(text[pos:])
    return "".join(out)


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return ls, len(text) if le == -1 else le


def removal(text: str, start: int, end: int) -> Edit:
    """Remove [start, end): the whole line(s) when nothing but blanks or a trailing comment
    shares them (comments on the lines around are kept), else just the span and the blanks
    on one side of it."""
    ls, le = _line_bounds(text, start, end)
    before, after = text[ls:start], text[end:le]
    if not before.strip() and (not after.strip() or after.lstrip().startswith("#")):
        return Edit(ls, min(le + 1, len(text)), "")
    e = end
    while e < le and text[e] in " \t":
        e += 1
    if e > end:
        return Edit(start, e, "")
    s = start
    while s > ls and text[s - 1] in " \t":
        s -= 1
    return Edit(s, end, "")


def indent_of(text: str, offset: int) -> str:
    ls = text.rfind("\n", 0, offset) + 1
    line = text[ls:offset]
    return line[: len(line) - len(line.lstrip(" \t"))]


def insert_into_list(nf: NixFile, sh: Shape, entry: str) -> tuple[Edit, str | None]:
    """Add `entry` (an element plus its trailing comment) as the last item of the list.

    A multi-line list whose `]` starts its own line gets a new line before the `]`. Any
    other list (one line, or `]` after the last item) is rewritten one item per line; the
    original text is returned so that removing the entry again can restore it exactly.
    """
    text = nf.text
    open_t, close_t = nf.toks[sh.lo], nf.toks[sh.hi - 1]
    elems = nf.list_elements(sh.lo + 1, sh.hi - 1)
    ls = text.rfind("\n", 0, close_t.start) + 1
    if ls > open_t.start and not text[ls : close_t.start].strip():
        if elems:
            indent = indent_of(text, nf.toks[elems[-1].lo].start)
        else:
            indent = indent_of(text, close_t.start) + "  "
        return Edit(ls, ls, f"{indent}{entry}\n"), None
    items: list[tuple[int, str]] = [(nf.toks[e.lo].start, nf.source(e.lo, e.hi)) for e in elems]
    items += [(c.start, c.text) for c in nf.comments if open_t.end <= c.start < close_t.start]
    base = indent_of(text, open_t.start)
    inner = base + "  "
    body = "".join(f"{inner}{t}\n" for _, t in sorted(items))
    new = f"[\n{body}{inner}{entry}\n{base}]"
    return Edit(open_t.start, close_t.end, new), text[open_t.start : close_t.end]


def remove_from_list(nf: NixFile, sh: Shape, index: int, original: str | None) -> tuple[Edit, bool]:
    """Remove element `index` of the list; restore `original` (the text the list had before
    nixin expanded it) when the remaining code is exactly that. Returns (edit, restored)."""
    elems = nf.list_elements(sh.lo + 1, sh.hi - 1)
    e = elems[index]
    if original is not None:
        rest = [nf.compact(x.lo, x.hi) for i, x in enumerate(elems) if i != index]
        try:
            toks, _ = lex(original)
            orig_nf = NixFile(original)
            orig = [orig_nf.compact(x.lo, x.hi) for x in orig_nf.list_elements(1, len(toks) - 1)]
        except NixSyntaxError:
            orig = None
        if orig == rest:
            s, end = nf.span(sh.lo, sh.hi)
            return Edit(s, end, original), True
    s, end = nf.span(e.lo, e.hi)
    return removal(nf.text, s, end), False


def unified(rel: str, old: str | None, new: str | None) -> str:
    a = old.splitlines(keepends=True) if old is not None else []
    b = new.splitlines(keepends=True) if new is not None else []
    out: list[str] = []
    for line in difflib.unified_diff(
        a,
        b,
        f"a/{rel}" if old is not None else "/dev/null",
        f"b/{rel}" if new is not None else "/dev/null",
    ):
        out.append(line)
        if not line.endswith("\n"):
            out.append("\n\\ No newline at end of file\n")
    return "".join(out)
