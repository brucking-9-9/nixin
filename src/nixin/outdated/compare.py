"""The pure part of `outdated`: version order, package inventories, the two diffs.

No nix, no network: the handlers in __init__ feed it the package lists outdated.nix
returns (and search.nixos.org's hits for --published), so the tests drive it with
recorded fixtures alone.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from typing import Any

# --- version order ------------------------------------------------------------------

_COMPONENT = re.compile(r"[0-9]+|[^0-9.-]+")


def _components(v: str) -> list[str]:
    return _COMPONENT.findall(v)


def _component_lt(c1: str, c2: str) -> bool:
    """nix's componentsLT (libexpr/names.cc): numbers numerically, `pre` before anything,
    a missing component before a number, `2.3a` before `2.3.1`, else as strings."""
    n1, n2 = c1.isdigit(), c2.isdigit()
    if n1 and n2:
        return int(c1) < int(c2)
    if c1 == "" and n2:
        return True
    if c1 == "pre" and c2 != "pre":
        return True
    if c2 == "pre":
        return False
    if n2:
        return True
    if n1:
        return False
    return c1 < c2


def compare_versions(v1: str, v2: str) -> int:
    """builtins.compareVersions: -1, 0 or 1."""
    a, b = _components(v1), _components(v2)
    for i in range(max(len(a), len(b))):
        c1 = a[i] if i < len(a) else ""
        c2 = b[i] if i < len(b) else ""
        if _component_lt(c1, c2):
            return -1
        if _component_lt(c2, c1):
            return 1
    return 0


version_key = functools.cmp_to_key(compare_versions)


def direction(old: str, new: str) -> str:
    """`up`, `down`, or `same` when the text differs but nix orders them equal (`1-2`, `1.2`)."""
    c = compare_versions(new, old)
    return "up" if c > 0 else "down" if c < 0 else "same"


# --- inventories --------------------------------------------------------------------

Scope = tuple[str, str | None]  # ("system", None) | ("home", user)


def scope_label(scope: Scope) -> str:
    return "system" if scope[1] is None else f"home {scope[1]}"


def scopes(doc: dict[str, Any], home: str | None = None) -> list[Scope]:
    """The scopes an outdated.nix result has: system, then each HM user (only `home`)."""
    if home is not None:
        return [("home", home)]
    return [("system", None)] + [("home", u) for u in sorted(doc.get("home") or {})]


def packages(doc: dict[str, Any], scope: Scope) -> list[dict[str, Any]]:
    if scope[1] is None:
        return doc.get("system") or []
    return (doc.get("home") or {}).get(scope[1]) or []


def inventory(pkgs: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """pname (or the derivation name when there is none) -> the versions installed.

    A package listed twice (the same derivation from two modules) counts once. Without a
    version the name stands in, so a pname-less script never "changes".
    """
    inv: dict[str, set[str]] = {}
    for p in pkgs:
        key = p.get("pname") or p.get("name")
        if not key:
            continue
        inv.setdefault(key, set()).add(p.get("version") or p.get("name") or "")
    return inv


# --- lock mode: the same host on two nixpkgs ---------------------------------------


def diff(lock: dict[str, Any], target: dict[str, Any], home: str | None = None) -> dict[str, Any]:
    """`changed`, `added`, `removed` and `total` (distinct pnames on the lock side).

    Per pname, a version on both sides is unchanged; what is left pairs up in version
    order as changed (`from` -> `to`), and the rest is removed (only on the lock side)
    or added (only on the target side). So `fuse` 2.9.9 + 3.18.2 -> 3.18.2 is one
    removal, and a pname nixpkgs renamed shows as one removal and one addition.
    """
    changed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    total = 0
    per_scope: list[dict[str, Any]] = []
    for scope in scopes(lock, home):
        a = inventory(packages(lock, scope))
        b = inventory(packages(target, scope))
        total += len(a)
        n = {"changed": 0, "added": 0, "removed": 0}
        where = {"scope": scope[0], "user": scope[1]}
        for pname in sorted(a.keys() | b.keys()):
            va, vb = a.get(pname, set()), b.get(pname, set())
            old = sorted(va - vb, key=version_key)
            new = sorted(vb - va, key=version_key)
            for f, t in zip(old, new, strict=False):
                changed.append(
                    {"pname": pname, "from": f, "to": t, **where, "direction": direction(f, t)}
                )
                n["changed"] += 1
            for v in old[len(new) :]:
                removed.append({"pname": pname, "version": v, **where})
                n["removed"] += 1
            for v in new[len(old) :]:
                added.append({"pname": pname, "version": v, **where})
                n["added"] += 1
        per_scope.append({**where, "total": len(a), **n})
    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "total": total,
        "scopes": per_scope,
    }


# --- --published: installed versions against a channel index ----------------------


def pnames(doc: dict[str, Any], home: str | None = None) -> list[str]:
    """The distinct pnames installed in the scopes asked for (pname-less entries skipped)."""
    out: set[str] = set()
    for scope in scopes(doc, home):
        out |= {p["pname"] for p in packages(doc, scope) if p.get("pname")}
    return sorted(out)


_STORE_FILE = re.compile(r"^/nix/store/[^/]+/(.+?)(?::\d+)?$")
_REL_FILE = re.compile(r"^(.+?)(?::\d+)?$")


def position_file(position: str | None) -> str | None:
    """`/nix/store/…-source/pkgs/kde/gear/ark/default.nix:7` (meta.position) or
    `pkgs/kde/gear/ark/default.nix:7` (search.nixos.org) -> the nixpkgs-relative file."""
    if not position:
        return None
    m = _STORE_FILE.match(position) or _REL_FILE.match(position)
    return m.group(1) if m else None


def _major(version: str | None) -> str | None:
    comps = _components(version or "")
    return comps[0] if comps else None


def pick(
    pname: str,
    hits: list[dict[str, Any]],
    installed: str | None = None,
    files: set[str] | None = None,
) -> dict[str, Any] | None:
    """The channel's package for an installed pname, from the hits that share it.

    A pname is shared by many attributes (python3: python3, python314, python316 at
    3.16.0a1; git: git and emacsPackages.git at 20140128; ark: kdePackages.ark and an
    unrelated top-level ark at 0.1.252; qtbase: qt5.qtbase and qt6.qtbase), so the
    newest version is the wrong answer. In order: the attribute defined in the same
    nixpkgs file as the installed one (`files`, from meta.position), the same major
    version as `installed`, the attribute named like the pname, a top-level attribute,
    the shortest name (the order search.nixos.org ranks by).
    """
    mine = [h for h in hits if h.get("package_pname") == pname and h.get("package_pversion")]
    if not mine:
        return None
    major = _major(installed)

    def rank(h: dict[str, Any]) -> tuple[bool, bool, bool, bool, int, str]:
        attr = h.get("package_attr_name") or ""
        return (
            bool(files) and position_file(h.get("package_position")) not in files,
            major is not None and _major(h.get("package_pversion")) != major,
            attr != pname,
            "." in attr,
            len(attr),
            attr,
        )

    return min(mine, key=rank)


def published(
    lock: dict[str, Any],
    hits: list[dict[str, Any]],
    channel: str,
    rev: str | None,
    home: str | None = None,
) -> dict[str, Any]:
    """Rows where the installed version and the channel's differ; `unpublished` pnames."""
    by_pname: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        by_pname.setdefault(h.get("package_pname") or "", []).append(h)
    changed: list[dict[str, Any]] = []
    unpublished: set[str] = set()
    total = 0
    per_scope: list[dict[str, Any]] = []
    for scope in scopes(lock, home):
        mine = [p for p in packages(lock, scope) if p.get("pname")]
        inv = inventory(mine)
        files: dict[str, set[str]] = {}
        for p in mine:
            f = position_file(p.get("position"))
            if f:
                files.setdefault(p["pname"], set()).add(f)
        total += len(inv)
        n = 0
        for pname in sorted(inv):
            installed = sorted(inv[pname], key=version_key)
            old = installed[-1]
            cands = by_pname.get(pname, [])
            hit = pick(pname, cands, old, files.get(pname))
            if hit is None:
                unpublished.add(pname)
                continue
            new = hit["package_pversion"]
            if new in installed:
                continue
            changed.append(
                {
                    "pname": pname,
                    "installed": ", ".join(installed),
                    "published": new,
                    "attr": hit.get("package_attr_name"),
                    "alternatives": len({c.get("package_attr_name") for c in cands}) - 1,
                    "channel": channel,
                    "rev": rev,
                    "scope": scope[0],
                    "user": scope[1],
                    "direction": direction(old, new),
                }
            )
            n += 1
        per_scope.append({"scope": scope[0], "user": scope[1], "total": len(inv), "changed": n})
    return {
        "changed": changed,
        "unpublished": sorted(unpublished),
        "total": total,
        "scopes": per_scope,
    }


_SERIES = re.compile(r"^(\d+)\.(\d+)")


def kernel_published(version: str | None, hits: list[dict[str, Any]]) -> dict[str, Any]:
    """The installed kernel against the channel: the same series' newest patch release
    (linuxKernel.kernels.linux_7_2) and linux_latest. Its pname, `linux`, is shared by
    every kernel, so pick() would answer the default LTS."""
    out: dict[str, Any] = {"from": version, "to": None, "latest": None, "changed": False}
    if not version:
        return out
    attrs = {h.get("package_attr_name"): h.get("package_pversion") for h in hits}
    out["latest"] = attrs.get("linux_latest")
    m = _SERIES.match(version)
    if m:
        out["to"] = attrs.get(f"linuxKernel.kernels.linux_{m.group(1)}_{m.group(2)}")
    out["changed"] = out["to"] is not None and out["to"] != version
    return out
