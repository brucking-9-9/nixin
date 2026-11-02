"""`list`: the installed packages of a host, one row each, with the lines that list them.

The rows come from the package-list eval `status` already caches (packages.py), so a warm
`list` runs no eval. `declared` is best effort and costs no eval either: the writer's
scanner (writer/nixtext.py, workspace.py) walks the host's modules (and each HM user's)
from flake.nix through `imports` path literals and collects the elements of every list
assigned to `environment.systemPackages` / `home.packages`. An element is tied to an
evaluated package by name: `pkgs.ripgrep` / `ripgrep` to pname `ripgrep`, a camelCase
let binding (`nightSchedule`) to `night-schedule`, `callPackage ./x/package.nix {}` to
`x`, `writeShellScriptBin "x" …` to `x`. A package that a module adds (programs.x.enable,
services, the NixOS defaults) or that the name rule misses has `declared: []`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ..configrepo import ConfigRepo, NixinError
from . import packages as pkgs

SCOPES = ("system", "home", "all")
_ATTRPATH = re.compile(r"[A-Za-z_][\w'-]*(?:\.[A-Za-z_][\w'-]*)*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME = re.compile(r"[\w.+-]+")


@dataclass
class Decl:
    """One element of a package list in a module of the config."""

    file: str
    line: int
    text: str
    names: frozenset[str]  # derivation names / pnames the element probably evaluates to
    conditional: bool = False

    def doc(self) -> dict[str, Any]:
        d: dict[str, Any] = {"file": self.file, "line": self.line, "text": self.text}
        if self.conditional:
            d["conditional"] = True
        return d


def wanted(pk: dict, host: str, scope: str | None, home: str | None) -> list[str | None]:
    """The scopes to list: None for the system, a user name for Home Manager."""
    users: list[str] = pk["hm_users"]
    if home is not None:
        if scope == "system":
            raise NixinError("usage", "--home USER is a Home Manager user; not with --scope system")
        if home not in users:
            raise NixinError(
                "unknown-user",
                f"{home!r} is not a Home Manager user of {host} "
                f"(users: {', '.join(users) or 'none'})",
            )
        users = [home]
    scope = scope or ("home" if home is not None else "all")
    return ([None] if scope != "home" else []) + (list(users) if scope != "system" else [])


def rows(pk: dict, scopes: list[str | None], text: str | None) -> tuple[list[dict], int]:
    """(rows, folded): one row per distinct package of a scope that matches `text`, sorted
    by name; `folded` counts the list entries dropped as repeats of a row (a package
    listed twice in environment.systemPackages is installed once)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    folded = 0
    needle = (text or "").lower()
    for user in scopes:
        listed = pk["system"]["packages"] if user is None else pk["home"][user]["packages"]
        for p in listed:
            if needle and not any(needle in (p.get(k) or "").lower() for k in ("name", "pname")):
                continue
            key = (user, p.get("name"), p.get("pname"), p.get("version"))
            if key in seen:
                folded += 1
                continue
            seen.add(key)
            out.append(
                {
                    "scope": "system" if user is None else "home",
                    "user": user,
                    "name": p.get("name"),
                    "pname": p.get("pname"),
                    "version": p.get("version"),
                    "declared": [],
                }
            )
    out.sort(key=sort_key)
    return out, folded


def sort_key(r: dict) -> tuple:
    shown = r.get("pname") or r.get("name") or ""
    return (shown.lower(), r.get("name") or "", r["scope"] != "system", r["user"] or "")


def programs(pk: dict, scopes: list[str | None], text: str | None) -> list[dict]:
    """The enabled programs.<x> of the listed scopes (`status.programs`, one row each)."""
    needle = (text or "").lower()
    out = []
    for user in scopes:
        names = pk["system"]["programs"] if user is None else pk["home"][user]["programs"]
        for n in names:
            if needle in n.lower():
                out.append(
                    {
                        "scope": "system" if user is None else "home",
                        "user": user,
                        "name": n,
                        "option": f"programs.{n}.enable",
                    }
                )
    out.sort(key=lambda r: (r["name"].lower(), r["scope"] != "system", r["user"] or ""))
    return out


# --- declared ------------------------------------------------------------------------------


def _kebab(s: str) -> str:
    return _CAMEL.sub("-", s).lower()


def element_names(nf: Any, lo: int, hi: int) -> frozenset[str]:
    """Names a list element probably evaluates to (see the module docstring)."""
    text = nf.compact(lo, hi)
    if _ATTRPATH.fullmatch(text):
        parts = text.split(".")
        if parts[0] == "pkgs" and len(parts) > 1:
            parts = parts[1:]
        last = parts[-1]
        return frozenset({last, _kebab(last), last.replace("_", "-"), "-".join(parts)})
    from ..writer.nixtext import unquote

    names: set[str] = set()
    builder = False
    toks = nf.toks[lo:hi]
    for i, t in enumerate(toks):
        if t.kind == "id" and t.text.startswith("write"):
            builder = True
        elif t.kind == "path" and i and toks[i - 1].text == "callPackage":
            p = PurePosixPath(t.text)
            names.add(p.parent.name if p.stem in ("package", "default") else p.stem)
        elif t.kind == "str" and builder:
            s = unquote(t.text)
            if s and _NAME.fullmatch(s):
                names.add(s)
            break
    return frozenset(n for n in names if n and n not in (".", ".."))


def declared(repo: ConfigRepo, host: str, scopes: list[str | None]):
    """{user-or-None: [Decl]} for the scopes, and the scanner's warnings. No eval."""
    # late imports: the writer imports truth.scan, and only `list` needs its scanner
    from .. import writer
    from ..writer.nixtext import NixSyntaxError
    from ..writer.workspace import Workspace

    ws = Workspace(repo.path, repo)
    out: dict[str | None, list[Decl]] = {}
    warnings: list[str] = []
    for user in scopes:
        try:
            sc = writer._scope(ws, host, user, use_eval=False)
        except NixinError as e:
            warnings.append(f"declared: {e.message}")
            continue
        found = out.setdefault(user, [])
        for m in sc.mods:
            try:
                defs, _ = ws.module_defs(m)
            except (OSError, NixSyntaxError):
                continue  # closure() already warned about it
            nf = ws.parsed(m.file)
            for d in defs:
                if d.path != sc.list_option:
                    continue
                for sh, e in nf.list_items(d):
                    start, _ = nf.span(e.lo, e.hi)
                    found.append(
                        Decl(
                            m.file,
                            nf.line_of(start),
                            nf.compact(e.lo, e.hi),
                            element_names(nf, e.lo, e.hi),
                            d.conditional or sh.conditional,
                        )
                    )
    return out, list(dict.fromkeys(ws.warnings + warnings))


def _keys(r: dict) -> set[str]:
    """What an element name may equal for this package: pname, name, name minus version."""
    name = r.get("name") or ""
    keys = {k for k in (r.get("pname"), name) if k}
    keys.update(
        name[:i] for i in range(1, len(name) - 1) if name[i] == "-" and name[i + 1].isdigit()
    )
    return keys


def attach(out: list[dict], decls: dict[str | None, list[Decl]]) -> None:
    """Fill each row's `declared` with the list elements whose names match it."""
    index: dict[tuple[str | None, str], list[Decl]] = {}
    for user, ds in decls.items():
        for d in ds:
            for n in d.names:
                index.setdefault((user, n), []).append(d)
    for r in out:
        hits = {id(d): d for k in _keys(r) for d in index.get((r["user"], k), [])}
        r["declared"] = [d.doc() for d in sorted(hits.values(), key=lambda d: (d.file, d.line))]


def collect(repo: ConfigRepo, host: str, cache, ns) -> dict[str, Any]:
    """The `list` document for a parsed command line (without envelope or message)."""
    t0 = time.monotonic()
    pk = pkgs.collect(repo, host, cache)
    scopes = wanted(pk, host, ns.scope, ns.home)
    out, folded = rows(pk, scopes, ns.filter)
    t = time.monotonic()
    decls, warnings = declared(repo, host, scopes)
    attach(out, decls)
    doc: dict[str, Any] = {
        "ok": True,
        "scope": ns.scope or ("home" if ns.home else "all"),
        "users": [u for u in scopes if u is not None],
        "filter": ns.filter,
        "packages": out,
        "count": len(out),
        "folded": folded,
        "warnings": warnings,
    }
    if ns.programs:
        doc["programs"] = programs(pk, scopes, ns.filter)
    doc["timings"] = {
        "packages": pk["seconds"],
        "declared": round(time.monotonic() - t, 2),
        "total": round(time.monotonic() - t0, 2),
    }
    return doc
