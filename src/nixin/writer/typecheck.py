"""Does a `set` value fit the option's type? A look at the value's text, before the write.

The type is truth's `type_info` (options.nix: `name`, `description`, enum `values`,
`nested` element/left/right/coerced/final/freeform types). The value is classified from
its tokens with the writer's scanner: a literal bool, int, float, string, null, path,
list, attrset or function. A name, a call, a select (`pkgs.x`, `config.y`), `if`, `let`,
string concatenation and the like are `unknown`. A bare name that is no builtin, no usual
module argument and not under `with`/`let` is a `word`: unbound where it lands, so most
likely a string without its quotes (`set services.openssh.enable yes`). The module system's wrappers
(`lib.mkForce`, `mkDefault`, `mkOverride N`, `mkBefore`, `mkAfter`, `mkOrder N`,
`mkIf COND`, `mkMerge [ … ]`) are looked through, as the module system does.

`check()` returns None when nothing is wrong as far as the text shows, else the reason.
Only these are judged: bool, the integer types (with their range), float, number, the
string types, `enum` (membership), `listOf`/`attrsOf` (the shape, then each element),
`nullOr`, `either`/`oneOf`, `coercedTo`, `unique`, `attrs`. Everything else (package,
path, submodule, function, anything, a type nixin does not know) is not checked, and
neither is an unknown value: the eval after the write still judges those.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

from .nixtext import _FLOAT, NixFile, NixSyntaxError, unquote

BOOL = frozenset({"bool", "boolByOr"})
INT = frozenset({"int", "intBetween", "unsignedInt", "positiveInt"})
INT_BITS = re.compile(r"(?:un)?signedInt\d+")
NUMBER = frozenset({"number", "numberBetween", "numberNonnegative", "numberPositive"})
STR = frozenset({"str", "string", "nonEmptyStr", "singleLineStr", "separatedString"})
STR_PREFIXES = ("strMatching", "passwdEntry")
ATTRS_OF = frozenset({"attrsOf", "lazyAttrsOf"})
BETWEEN = re.compile(r"between (-?\d+) and (-?\d+)")
# wrapper name -> how many arguments come before the value
WRAPPERS = {
    "mkForce": 0,
    "mkDefault": 0,
    "mkBefore": 0,
    "mkAfter": 0,
    "mkVMOverride": 0,
    "mkImageMediaOverride": 0,
    "mkOptionDefault": 0,
    "mkOverride": 1,
    "mkOrder": 1,
    "mkIf": 1,
}
# names a value may use bare: Nix's global builtins and the usual module arguments (a bare
# word that is none of these is unbound where it lands, unless under `with`/`let`)
BOUND = frozenset(
    {
        *("builtins", "import", "toString", "map", "throw", "abort", "baseNameOf", "dirOf"),
        *("derivation", "fetchTarball", "fetchGit", "fetchTree", "fetchurl", "isNull"),
        *("placeholder", "removeAttrs", "scopedImport"),
        *("config", "lib", "pkgs", "options", "modulesPath", "specialArgs", "inputs", "self"),
        *("osConfig", "nixosConfig", "darwinConfig", "name", "utils", "extendModules"),
    }
)
BOOL_OPS = ("->", "||", "&&", "==", "!=", "<", "<=", ">", ">=")
PHRASE = {
    "bool": "a boolean",
    "int": "an integer",
    "float": "a float",
    "string": "a string",
    "null": "null",
    "path": "a path",
    "list": "a list",
    "attrs": "an attribute set",
    "function": "a function",
    "word": "a bare word (Nix reads it as a variable)",
}


@dataclass
class Lit:
    """What a value expression is, as far as its text shows."""

    kind: str  # bool int float string null path list attrs function word unknown
    text: str = ""
    value: Any = None  # bool/int/float/str when static (None: an interpolated string, …)
    items: list[Lit] = field(default_factory=list)  # list elements nixin can see
    attrs: dict[str, Lit] = field(default_factory=dict)  # bindings by their first name

    @property
    def shown(self) -> str:
        t = " ".join(self.text.split())
        return t if len(t) <= 40 else t[:39] + "…"


def literal(expr: str) -> Lit:
    """Classify a Nix expression (`unknown` when the scanner cannot follow it)."""
    try:
        nf = NixFile(expr)
    except NixSyntaxError:
        return Lit("unknown", expr)
    if not nf.toks:
        return Lit("unknown", expr)
    return _classify(nf, 0, len(nf.toks))


def _classify(nf: NixFile, lo: int, hi: int, dyn: bool = False) -> Lit:
    """`dyn`: under `with`/`let`, where a bare name may be bound."""
    text = nf.source(lo, hi) if lo < hi else ""
    if lo >= hi:
        return Lit("unknown", text)
    k = nf.kind(lo)
    if k == "(" and nf.match[lo] == hi - 1:
        return _classify(nf, lo + 1, hi - 1, dyn)
    if k in ("with", "assert"):
        return _classify(nf, nf.expr_end(lo + 1, hi) + 1, hi, dyn or k == "with")
    if k == "let":
        return _classify(nf, nf.match[lo] + 1, hi, True)
    if k in ("if", "rec") and not (k == "rec" and nf.kind(lo + 1) == "{"):
        return Lit("unknown", text)
    # functions: `x: …`, `x@{ … }: …`, `{ … }: …`, `{ … }@x: …`
    if k == "id" and nf.kind(lo + 1) in (":", "@"):
        return Lit("function", text)
    if k == "{" and nf.kind(nf.match[lo] + 1) in (":", "@"):
        return Lit("function", text)
    # binary operators, loosest first
    for op in BOOL_OPS:
        if len(nf.split_top(lo, hi, op)) > 1:
            return Lit("bool", text)
    for op, kind in (("//", "attrs"), ("++", "list")):
        parts = nf.split_top(lo, hi, op)
        if len(parts) > 1:
            out = Lit(kind, text)
            for a, b in parts:
                part = _classify(nf, a, b, dyn)
                out.items += part.items
                out.attrs.update(part.attrs)
            return out
    if any(len(nf.split_top(lo, hi, op)) > 1 for op in ("+", "*", "/")):
        return Lit("unknown", text)
    if k == "!":
        return Lit("bool", text)
    if k == "-":
        if len(nf.split_top(lo + 1, hi, "-")) > 1:
            return Lit("unknown", text)
        inner = _classify(nf, lo + 1, hi, dyn)
        if inner.kind in ("int", "float") and inner.value is not None:
            return Lit(inner.kind, text, -inner.value)
        return Lit("unknown", text)
    if len(nf.split_top(lo, hi, "-")) > 1:
        return Lit("unknown", text)
    if k == "{" and nf.match[lo] == hi - 1:
        return _attrs(nf, lo, hi, text, dyn)
    if k == "rec" and nf.match[lo + 1] == hi - 1:
        return _attrs(nf, lo + 1, hi, text, True)
    if k == "[" and nf.match[lo] == hi - 1:
        items = [_classify(nf, e.lo, e.hi, dyn) for e in nf.list_elements(lo + 1, hi - 1)]
        return Lit("list", text, items=items)
    names, j = nf._qualified(lo, hi)
    if names and (len(names) == 1 or (len(names) == 2 and names[0] == "lib")):
        fn = names[-1]
        if fn in WRAPPERS:
            for _ in range(WRAPPERS[fn]):
                j = nf.skip_select(j, hi)
            return _classify(nf, j, hi, dyn)
        if fn == "mkMerge" and nf.kind(j) == "[" and nf.match[j] == hi - 1:
            parts = [_classify(nf, e.lo, e.hi, dyn) for e in nf.list_elements(j + 1, hi - 1)]
            kinds = {p.kind for p in parts}
            if len(kinds) != 1 or kinds & {"unknown"}:
                return Lit("unknown", text)
            out = Lit(kinds.pop(), text)
            for p in parts:
                out.items += p.items
                out.attrs.update(p.attrs)
            return out
    if hi - lo == 1:
        return _token(nf.toks[lo].kind, nf.toks[lo].text, dyn)
    return Lit("unknown", text)


def _token(kind: str, text: str, dyn: bool) -> Lit:
    if kind == "id":
        known = {"true": ("bool", True), "false": ("bool", False), "null": ("null", None)}
        if text not in known:
            if dyn or text in BOUND or text.startswith("__"):
                return Lit("unknown", text)  # a variable that may well exist
            return Lit("word", text)
        kind, value = known[text]
        return Lit(kind, text, value)
    if kind == "num":
        if _FLOAT.fullmatch(text) and not text.isdigit():
            return Lit("float", text, float(text))
        return Lit("int", text, int(text))
    if kind == "str":
        if text.startswith('"'):
            return Lit("string", text, unquote(text))
        body = text[2:-2]
        static = None if "${" in body else textwrap.dedent(body.removeprefix("\n"))
        return Lit("string", text, static)
    if kind == "uri":
        return Lit("string", text, text)
    if kind == "path":
        return Lit("path", text)
    return Lit("unknown", text)


def _attrs(nf: NixFile, lo: int, hi: int, text: str, dyn: bool) -> Lit:
    """An attrset literal; `a.b = v` counts as `a = { b = v; }` (merged with other `a.…`)."""
    out = Lit("attrs", text)
    try:
        bindings = nf.bindings(lo + 1, hi - 1)
    except NixSyntaxError:
        return out
    for b in bindings:
        if any(c is None for c in b.path):
            continue  # a ${…} name
        value = _classify(nf, b.val_lo, b.val_hi, dyn)
        head, *rest = b.path
        target = out
        for name in [head, *rest][:-1]:
            nxt = target.attrs.get(name)
            if nxt is None or nxt.kind != "attrs":
                nxt = target.attrs[name] = Lit("attrs", f"{{ {name} = … }}")
            target = nxt
        target.attrs[b.path[-1]] = value
    return out


# --- the check ---------------------------------------------------------------------------


def check(t: dict[str, Any] | None, v: Lit) -> str | None:
    """None, or why `v` cannot be a value of type `t`."""
    if not t or v.kind == "unknown":
        return None
    n = str(t.get("name") or "?")
    nested = t.get("nested") or {}
    desc = t.get("description") or n
    if n in ("nullOr", "unique"):
        if n == "nullOr" and v.kind == "null":
            return None
        return check(nested.get("elemType"), v)
    if n in ("either", "coercedTo"):
        a, b = ("left", "right") if n == "either" else ("coercedType", "finalType")
        why_a, why_b = check(nested.get(a), v), check(nested.get(b), v)
        if why_a is None or why_b is None:
            return None
        return _not(v, desc)
    if n in BOOL:
        return None if v.kind == "bool" else _not(v, desc)
    if n in INT or INT_BITS.fullmatch(n):
        if v.kind != "int":
            return _not(v, desc)
        return _range(n, desc, v)
    if n == "float":
        return None if v.kind == "float" else _not(v, desc)
    if n in NUMBER:
        return None if v.kind in ("int", "float") else _not(v, desc)
    if n in STR or n.startswith(STR_PREFIXES):
        if v.kind != "string":
            return _not_str(v, desc)
        if n == "nonEmptyStr" and isinstance(v.value, str) and not v.value.strip():
            return f"{v.shown} is empty"
        if n == "singleLineStr" and isinstance(v.value, str) and "\n" in v.value.strip("\n"):
            return f"{v.shown} has more than one line"
        return None
    if n == "enum":
        return _enum(t.get("values") or [], v, desc)
    if n == "listOf":
        if v.kind != "list":
            return _not(v, desc)
        elem = nested.get("elemType")
        for i, item in enumerate(v.items, 1):
            why = check(elem, item)
            if why:
                return f"element {i}: {why}"
        return None
    if n in ATTRS_OF:
        if v.kind != "attrs":
            return _not(v, desc)
        elem = nested.get("elemType")
        for name, item in v.attrs.items():
            why = check(elem, item)
            if why:
                return f"{name}: {why}"
        return None
    if n == "attrs":
        return None if v.kind == "attrs" else _not(v, desc)
    return None  # package, path, submodule, function, anything, …: not judged here


def _not(v: Lit, desc: str) -> str:
    return f"{v.shown} is {PHRASE.get(v.kind, v.kind)}, not {desc}"


def _not_str(v: Lit, desc: str) -> str:
    if v.kind == "word":  # `set networking.hostName box`: the string "box" was meant
        return f"{v.text} is a bare word, not {desc}: quote it for Nix, '\"{v.text}\"'"
    return _not(v, desc)


def _range(name: str, desc: str, v: Lit) -> str | None:
    x = v.value
    if not isinstance(x, int):
        return None
    lo: int | None = None
    hi: int | None = None
    m = BETWEEN.search(desc)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    elif name == "unsignedInt":
        lo = 0
    elif name == "positiveInt":
        lo = 1
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        span = f"{lo}…{hi}" if hi is not None else f"at least {lo}"
        return f"{x} is out of range ({span})"
    return None


def _enum(values: list[Any], v: Lit, desc: str) -> str | None:
    kinds = {_kind(x) for x in values}
    if v.kind not in kinds:
        return _not_str(v, desc) if "string" in kinds else _not(v, desc)
    if v.value is None and v.kind != "null":
        return None  # an interpolated string: can be anything
    if any(_kind(x) == v.kind and x == v.value for x in values):
        return None
    shown = ", ".join(_nix(x) for x in values[:12]) + (", …" if len(values) > 12 else "")
    return f"{v.shown} is not one of {shown}"


def _kind(x: Any) -> str:
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, int):
        return "int"
    if isinstance(x, float):
        return "float"
    if isinstance(x, str):
        return "string"
    return "other"


def _nix(x: Any) -> str:
    if isinstance(x, str):
        return '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true" if x else "false"
    return str(x)
