"""A small Nix tokenizer and structural scanner, precise enough to edit hand-written modules.

Not a full parser. It lexes every token (strings with `${}` interpolation, '' strings,
paths, URIs, comments) with character offsets, matches brackets (and `let`/`in`), and walks
attribute-set bindings the way modules are written: through function headers, `let … in`,
`with x;`, `lib.mkIf c`, `lib.mkMerge [ … ]`, `//`, `++` and the priority wrappers, giving
every binding its full option path. Edits are made on token offsets, never on line guesses;
anything it does not understand is an opaque value it leaves alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field


class NixSyntaxError(ValueError):
    """The scanner could not follow the text (invalid Nix, or a form it does not know)."""

    def __init__(self, message: str, offset: int | None = None, text: str = "") -> None:
        if offset is not None and text:
            line = text.count("\n", 0, offset) + 1
            message = f"{message} (line {line})"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Tok:
    kind: str  # "id" "kw" "str" "path" "uri" "num" "comment", else the operator text itself
    text: str
    start: int
    end: int


KEYWORDS = frozenset({"if", "then", "else", "assert", "with", "let", "in", "rec", "inherit", "or"})
OPENERS = frozenset({"{", "${", "[", "(", "let"})
CLOSERS = {"}": ("{", "${"), "]": ("[",), ")": ("(",), "in": ("let",)}
_PC = r"[A-Za-z0-9._\-+]"
_PATH = re.compile(rf"{_PC}*(?:/{_PC}+)+/?")
_PATH_SEG = re.compile(rf"{_PC}*(?:/{_PC}+)*/")  # a path continuing with ${…}
_HPATH_SEG = re.compile(r"~/")
_HPATH = re.compile(rf"~(?:/{_PC}+)+/?")
_SPATH = re.compile(rf"<{_PC}+(?:/{_PC}+)*>")
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+\-.]*:[A-Za-z0-9%/?:@&=+$,\-_.!~*']+")
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*")
_FLOAT = re.compile(r"(?:[1-9][0-9]*\.[0-9]*|0?\.[0-9]+)(?:[Ee][+-]?[0-9]+)?")
_INT = re.compile(r"[0-9]+")
_PATH_TAIL = re.compile(rf"(?:{_PC}|/)*")
_OPS = ("...", "${", "==", "!=", "<=", ">=", "&&", "||", "->", "//", "++", "|>", "<|")
_SINGLE = frozenset("{}[]();:,.@=<>+-*/!?")
_WS = " \t\r\n"


def _skip_dq(src: str, i: int) -> int:
    """End offset of the "…" string starting at src[i]."""
    n = len(src)
    i += 1
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif src.startswith("$$", i):
            i += 2
        elif src.startswith("${", i):
            i = _skip_interp(src, i + 2)
        else:
            i += 1
    raise NixSyntaxError("unterminated string", i, src)


def _skip_ind(src: str, i: int) -> int:
    """End offset of the ''…'' string starting at src[i]."""
    n = len(src)
    i += 2
    while i < n:
        if src.startswith("''", i):
            if src.startswith("'''", i) or src.startswith("''$", i):
                i += 3
            elif src.startswith("''\\", i):
                i += 4
            else:
                return i + 2
        elif src.startswith("$$", i):
            i += 2
        elif src.startswith("${", i):
            i = _skip_interp(src, i + 2)
        else:
            i += 1
    raise NixSyntaxError("unterminated '' string", i, src)


def _skip_interp(src: str, i: int) -> int:
    """Offset just past the `}` closing an interpolation whose `${` ends at i."""
    depth = 0
    while True:
        tok, i = _next(src, i)
        if tok is None:
            raise NixSyntaxError("unterminated ${", i, src)
        if tok.kind in ("{", "${"):
            depth += 1
        elif tok.kind == "}":
            if depth == 0:
                return i
            depth -= 1


def _next(src: str, i: int) -> tuple[Tok | None, int]:
    n = len(src)
    while i < n and src[i] in _WS:
        i += 1
    if i >= n:
        return None, i
    c = src[i]
    if c == "#":
        j = src.find("\n", i)
        j = n if j == -1 else j
        return Tok("comment", src[i:j], i, j), j
    if src.startswith("/*", i):
        j = src.find("*/", i + 2)
        if j == -1:
            raise NixSyntaxError("unterminated comment", i, src)
        return Tok("comment", src[i : j + 2], i, j + 2), j + 2
    if c == '"':
        j = _skip_dq(src, i)
        return Tok("str", src[i:j], i, j), j
    if src.startswith("''", i):
        j = _skip_ind(src, i)
        return Tok("str", src[i:j], i, j), j
    # longest match among the word-like rules; ties go to the earlier rule (as in flex)
    best: tuple[int, str] = (0, "")
    for kind, rx in (
        ("id", _ID),
        ("num", _INT),
        ("num", _FLOAT),
        ("path", _PATH),
        ("path", _HPATH),
        ("path", _SPATH),
        ("uri", _URI),
    ):
        m = rx.match(src, i)
        if m and m.end() - i > best[0]:
            best = (m.end() - i, kind)
    seg = _PATH_SEG.match(src, i) or _HPATH_SEG.match(src, i)
    if seg and src.startswith("${", seg.end()):
        j = seg.end()
        while src.startswith("${", j):
            j = _skip_interp(src, j + 2)
            j = _PATH_TAIL.match(src, j).end()
        return Tok("path", src[i:j], i, j), j
    if best[0]:
        j = i + best[0]
        text = src[i:j]
        kind = "kw" if best[1] == "id" and text in KEYWORDS else best[1]
        return Tok(kind, text, i, j), j
    for op in _OPS:
        if src.startswith(op, i):
            return Tok(op, op, i, i + len(op)), i + len(op)
    if c in _SINGLE:
        return Tok(c, c, i, i + 1), i + 1
    raise NixSyntaxError(f"unexpected character {c!r}", i, src)


def lex(src: str) -> tuple[list[Tok], list[Tok]]:
    """(code tokens, comment tokens), both in source order."""
    toks: list[Tok] = []
    comments: list[Tok] = []
    i = 0
    while True:
        tok, i = _next(src, i)
        if tok is None:
            return toks, comments
        (comments if tok.kind == "comment" else toks).append(tok)


def unquote(text: str) -> str | None:
    """The value of a plain "…" string token; None when it interpolates (dynamic)."""
    if not text.startswith('"'):
        return None
    body = text[1:-1]
    if re.search(r"(?<!\\)(?<!\$)\$\{", body):
        return None
    esc = {"n": "\n", "t": "\t", "r": "\r"}
    return re.sub(r"\\(.)", lambda m: esc.get(m.group(1), m.group(1)), body)


# --- structure ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Shape:
    """What a value expression is, after unwrapping: an attrset, a list, or opaque.

    lo/hi are token indexes of the bracket pair (inclusive lo, exclusive hi, so the closing
    bracket is hi - 1); for "other" the whole remaining expression.
    """

    kind: str  # "attrs" | "list" | "other"
    lo: int
    hi: int
    conditional: bool = False  # under mkIf / optionals / optionalAttrs
    wrappers: tuple[str, ...] = ()  # mkForce, mkDefault, mkOverride, mkBefore, mkAfter


@dataclass(frozen=True, slots=True)
class Binding:
    path: tuple[str | None, ...]  # None for a ${…} component
    start: int  # token index of the first attrpath token
    eq: int  # token index of "="
    val_lo: int
    val_hi: int  # token index of the closing ";"


@dataclass(frozen=True, slots=True)
class Def:
    """A binding with its full path from the walk's root (a module's config)."""

    path: tuple[str | None, ...]
    binding: Binding
    depth: int  # 0 = written directly in the root attrset
    conditional: bool


@dataclass(frozen=True, slots=True)
class Elem:
    lo: int  # token range of one list element
    hi: int


_PRIORITY = {"mkDefault": 0, "mkForce": 0, "mkBefore": 0, "mkAfter": 0, "mkOverride": 1}
_PRIORITY["mkOrder"] = 1
_CONDITIONAL = {"mkIf": 1, "optionals": 1, "optionalAttrs": 1}
_SPECIAL_TOP = frozenset({"imports", "disabledModules", "_file", "key"})


@dataclass
class NixFile:
    text: str
    toks: list[Tok] = field(init=False)
    comments: list[Tok] = field(init=False)
    match: dict[int, int] = field(init=False)

    def __post_init__(self) -> None:
        self.toks, self.comments = lex(self.text)
        self.match = self._match_brackets()

    def _match_brackets(self) -> dict[int, int]:
        match: dict[int, int] = {}
        stack: list[int] = []
        for i, t in enumerate(self.toks):
            k = t.kind if t.kind != "kw" else t.text
            if k in OPENERS:
                stack.append(i)
            elif k in CLOSERS:
                if not stack:
                    raise NixSyntaxError(f"unmatched {t.text!r}", t.start, self.text)
                o = stack.pop()
                ok = self.toks[o].kind if self.toks[o].kind != "kw" else self.toks[o].text
                if ok not in CLOSERS[k]:
                    raise NixSyntaxError(f"{t.text!r} closes {ok!r}", t.start, self.text)
                match[o], match[i] = i, o
        if stack:
            o = self.toks[stack[-1]]
            raise NixSyntaxError(f"unclosed {o.text!r}", o.start, self.text)
        return match

    # --- token helpers ----------------------------------------------------------------

    def kind(self, i: int) -> str:
        if i >= len(self.toks):
            return ""
        t = self.toks[i]
        return t.text if t.kind == "kw" else t.kind

    def span(self, lo: int, hi: int) -> tuple[int, int]:
        """Character span of token range [lo, hi)."""
        return self.toks[lo].start, self.toks[hi - 1].end

    def source(self, lo: int, hi: int) -> str:
        s, e = self.span(lo, hi)
        return self.text[s:e]

    def compact(self, lo: int, hi: int) -> str:
        """Token texts joined without whitespace (`pkgs . kitty` == `pkgs.kitty`)."""
        return "".join(t.text for t in self.toks[lo:hi])

    def line_of(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1

    def _jump(self, i: int) -> int:
        """Index after the bracket group (or let…in head) starting at i."""
        return self.match[i] + 1

    def expr_end(self, lo: int, hi: int) -> int:
        """Index of the `;` (or hi) ending the expression at lo; `with x;`/`assert c;` and
        brackets are stepped over."""
        pending = 0
        i = lo
        while i < hi:
            k = self.kind(i)
            if k in OPENERS:
                i = self._jump(i)
                continue
            if k in ("with", "assert"):
                pending += 1
            elif k == ";":
                if not pending:
                    return i
                pending -= 1
            i += 1
        return hi

    def skip_select(self, i: int, hi: int) -> int:
        """End of one select-expression (a list element / function argument) at i."""
        if i >= hi:
            return i
        k = self.kind(i)
        if k in ("!", "-"):
            return self.skip_select(i + 1, hi)
        if k == "rec" and self.kind(i + 1) == "{":
            j = self._jump(i + 1)
        elif k in ("{", "[", "(", "${"):
            j = self._jump(i)
        elif k in ("id", "str", "path", "uri", "num"):
            j = i + 1
        else:
            return i
        while j < hi and self.kind(j) == "." and j + 1 < hi:
            nk = self.kind(j + 1)
            if nk == "${":
                j = self._jump(j + 1)
            elif nk in ("id", "str") or self.toks[j + 1].kind == "kw":
                j += 2
            else:
                break
        if j < hi and self.kind(j) == "or":
            j = self.skip_select(j + 1, hi)
        return j

    def _qualified(self, i: int, hi: int) -> tuple[list[str], int]:
        """`lib.mkIf` -> (["lib", "mkIf"], index after it)."""
        names: list[str] = []
        if self.kind(i) != "id":
            return names, i
        names.append(self.toks[i].text)
        j = i + 1
        while j + 1 < hi and self.kind(j) == "." and self.kind(j + 1) == "id":
            names.append(self.toks[j + 1].text)
            j += 2
        return names, j

    def split_top(self, lo: int, hi: int, op: str) -> list[tuple[int, int]]:
        parts: list[tuple[int, int]] = []
        i = start = lo
        while i < hi:
            k = self.kind(i)
            if k in OPENERS:
                i = self._jump(i)
                continue
            if k == op:
                parts.append((start, i))
                start = i + 1
            i += 1
        parts.append((start, hi))
        return parts

    # --- shapes -----------------------------------------------------------------------

    def shapes(
        self, lo: int, hi: int, *, cond: bool = False, wrap: tuple[str, ...] = ()
    ) -> list[Shape]:
        """What the value expression [lo, hi) is: attrsets/lists after unwrapping."""
        if lo >= hi:
            return []
        k = self.kind(lo)
        if k == "(" and self.match[lo] == hi - 1:
            return self.shapes(lo + 1, hi - 1, cond=cond, wrap=wrap)
        if k in ("with", "assert"):
            semi = self.expr_end(lo + 1, hi)
            return self.shapes(semi + 1, hi, cond=cond, wrap=wrap)
        if k == "let":
            return self.shapes(self._jump(lo), hi, cond=cond, wrap=wrap)
        for op in ("++", "//"):
            parts = self.split_top(lo, hi, op)
            if len(parts) > 1:
                return [s for a, b in parts for s in self.shapes(a, b, cond=cond, wrap=wrap)]
        if k == "{" and self.match[lo] == hi - 1:
            return [Shape("attrs", lo, hi, cond, wrap)]
        if k == "rec" and self.kind(lo + 1) == "{" and self.match[lo + 1] == hi - 1:
            return [Shape("attrs", lo + 1, hi, cond, wrap)]
        if k == "[" and self.match[lo] == hi - 1:
            return [Shape("list", lo, hi, cond, wrap)]
        names, j = self._qualified(lo, hi)
        if names and (len(names) == 1 or names[0] == "lib"):
            fn = names[-1]
            if fn in _CONDITIONAL or fn in _PRIORITY:
                skip = _CONDITIONAL.get(fn, _PRIORITY.get(fn, 0))
                for _ in range(skip):
                    j = self.skip_select(j, hi)
                if fn in _CONDITIONAL:
                    return self.shapes(j, hi, cond=True, wrap=wrap)
                return self.shapes(j, hi, cond=cond, wrap=(*wrap, fn))
            if fn == "mkMerge" and self.kind(j) == "[" and self.match[j] == hi - 1:
                return [
                    s
                    for e in self.list_elements(j + 1, hi - 1)
                    for s in self.shapes(e.lo, e.hi, cond=cond, wrap=wrap)
                ]
        return [Shape("other", lo, hi, cond, wrap)]

    def module_shapes(self, lo: int = 0, hi: int | None = None) -> list[Shape]:
        """A module expression: skip `{ … }:`, `args@{ … }:`, `x:` headers, then shapes."""
        hi = len(self.toks) if hi is None else hi
        i = lo
        while i < hi:
            k = self.kind(i)
            if k == "{" and self.kind(self.match[i] + 1) in (":", "@"):
                j = self.match[i] + 1
                if self.kind(j) == "@":
                    j += 2
                if self.kind(j) == ":":
                    i = j + 1
                    continue
            if k == "id" and self.kind(i + 1) == ":":
                i += 2
                continue
            if k == "id" and self.kind(i + 1) == "@" and self.kind(i + 2) == "{":
                j = self.match[i + 2] + 1
                if self.kind(j) == ":":
                    i = j + 1
                    continue
            break
        return self.shapes(i, hi)

    # --- bindings and lists -------------------------------------------------------------

    def bindings(self, lo: int, hi: int) -> list[Binding]:
        """The `path = value;` bindings of an attrset body [lo, hi); inherits are skipped."""
        out: list[Binding] = []
        i = lo
        while i < hi:
            if self.kind(i) == "inherit":
                i = self.expr_end(i + 1, hi) + 1
                continue
            comps: list[str | None] = []
            j = i
            while j < hi:
                t = self.toks[j]
                if t.kind in ("id", "kw"):
                    comps.append(t.text)
                    j += 1
                elif t.kind == "str":
                    comps.append(unquote(t.text))
                    j += 1
                elif t.kind == "${":
                    comps.append(None)
                    j = self._jump(j)
                else:
                    raise NixSyntaxError(f"unexpected {t.text!r} in attrset", t.start, self.text)
                if self.kind(j) == ".":
                    j += 1
                    continue
                break
            if self.kind(j) != "=":
                t = self.toks[min(j, len(self.toks) - 1)]
                raise NixSyntaxError(f"expected '=' before {t.text!r}", t.start, self.text)
            e = self.expr_end(j + 1, hi)
            if self.kind(e) != ";" or e >= hi:
                raise NixSyntaxError("binding without ';'", self.toks[i].start, self.text)
            out.append(Binding(tuple(comps), i, j, j + 1, e))
            i = e + 1
        return out

    def list_elements(self, lo: int, hi: int) -> list[Elem]:
        out: list[Elem] = []
        i = lo
        while i < hi:
            j = self.skip_select(i, hi)
            if j == i:
                t = self.toks[i]
                raise NixSyntaxError(f"unexpected {t.text!r} in list", t.start, self.text)
            out.append(Elem(i, j))
            i = j
        return out

    def walk(
        self, shapes: list[Shape], prefix: tuple = (), depth: int = 0, cond: bool = False
    ) -> Iterator[Def]:
        """Every binding under the attrset shapes, depth first, with full paths."""
        for sh in shapes:
            if sh.kind != "attrs":
                continue
            for b in self.bindings(sh.lo + 1, sh.hi - 1):
                full = prefix + b.path
                c = cond or sh.conditional
                yield Def(full, b, depth, c)
                yield from self.walk(self.shapes(b.val_lo, b.val_hi), full, depth + 1, c)

    def module_defs(self, lo: int = 0, hi: int | None = None) -> tuple[list[Def], list[Def]]:
        """(config definitions, special top-level ones like `imports`) of a module.

        A module with a top-level `config` or `options` attribute keeps its definitions
        under `config` (the module system's rule); those are returned with `config` stripped.
        """
        defs = list(self.walk(self.module_shapes(lo, hi)))
        top = {d.path[0] for d in defs if d.depth == 0 and d.path}
        special = [d for d in defs if d.depth == 0 and d.path and d.path[0] in _SPECIAL_TOP]
        if "config" in top or "options" in top:
            cfg = [
                Def(d.path[1:], d.binding, d.depth, d.conditional)
                for d in defs
                if len(d.path) > 1 and d.path[0] == "config"
            ]
            return cfg, special
        return [d for d in defs if d.path and d.path[0] not in _SPECIAL_TOP], special

    def list_items(self, d: Def) -> list[tuple[Shape, Elem]]:
        """The elements of the list literals a definition's value is made of."""
        out = []
        for sh in self.shapes(d.binding.val_lo, d.binding.val_hi):
            if sh.kind == "list":
                out.extend((sh, e) for e in self.list_elements(sh.lo + 1, sh.hi - 1))
        return out

    def elem_path(self, e: Elem) -> str | None:
        """The path literal an `imports` element names (`./x.nix`, `(import ./x.nix)`)."""
        lo, hi = e.lo, e.hi
        if self.kind(lo) == "(" and self.match[lo] == hi - 1:
            lo, hi = lo + 1, hi - 1
            if self.kind(lo) == "id" and self.toks[lo].text == "import":
                lo += 1
        if hi - lo == 1 and self.kind(lo) == "path":
            return self.toks[lo].text
        return None


def parse_attrpath(s: str) -> list[str]:
    """`services.openssh.settings.X11Forwarding` / `environment.etc."a.conf".text` -> parts."""
    try:
        toks, comments = lex(s)
    except NixSyntaxError as e:
        raise ValueError(str(e)) from e
    if comments or not toks:
        raise ValueError(f"{s!r} is not an attribute path")
    out: list[str] = []
    expect_name = True
    for t in toks:
        if expect_name:
            if t.kind in ("id", "kw"):
                out.append(t.text)
            elif t.kind == "str" and unquote(t.text) is not None:
                out.append(unquote(t.text))
            else:
                raise ValueError(f"{s!r} is not an attribute path (at {t.text!r})")
        elif t.kind != ".":
            raise ValueError(f"{s!r} is not an attribute path (at {t.text!r})")
        expect_name = not expect_name
    if expect_name:
        raise ValueError(f"{s!r} ends with '.'")
    return out
