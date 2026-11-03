"""`list --diff A B`: what one host installs and the other does not, and versions that differ.

Both hosts come from one config flake (one `--config`), so they share its lock and the
versions normally match; a host on another nixpkgs input, an overlay or a pinned package
shows up as `differ`. Packages are compared per scope, by pname (else the derivation
name, for packages without one): the system with the system, each Home Manager user with
the user of the same name on the other host (a user only one host has: every package of
it is "only" there). The lists are the package-list eval `list`, `status` and `where`
already cache per host (packages.py), so a warm diff runs no eval.
"""

from __future__ import annotations

import time
from typing import Any

from ..configrepo import ConfigRepo, NixinError, unknown_host
from . import packages as pkgs
from .cache import TruthCache
from .render import NAME_CAP, NAME_MIN, VERSION_CAP, cap, term_width

STATES = ("only_a", "only_b", "differ")
ABSENT = "-"
NO_VERSION = "yes"  # present, but the derivation has no version (scripts, nixos-version)


def pair(ns: Any) -> tuple[str, str]:
    """(a, b) from `--diff B` (a = --host) or `--diff A B`."""
    given = list(ns.diff)
    if len(given) == 1:
        given = [ns.host, given[0]]
    if len(given) != 2:
        raise NixinError("usage", "list --diff HOST_B (against --host) or --diff HOST_A HOST_B")
    a, b = given
    if a == b:
        raise NixinError("usage", f"list --diff: both hosts are {a!r}; name two different ones")
    return a, b


def index(listed: list[dict] | None, needle: str) -> dict[str, dict[str, set]]:
    """key (pname, else name) -> {"versions": {...}, "names": {...}} of the matching entries."""
    out: dict[str, dict[str, set]] = {}
    for p in listed or []:
        name, pname = p.get("name") or "", p.get("pname") or ""
        if needle and needle not in name.lower() and needle not in pname.lower():
            continue
        e = out.setdefault(pname or name, {"versions": set(), "names": set()})
        e["versions"].add(p.get("version"))
        e["names"].add(name)
    return out


def _versions(e: dict[str, set] | None) -> list[str | None]:
    return sorted(e["versions"], key=lambda v: (v is None, v or "")) if e else []


def compare(la: list[dict] | None, lb: list[dict] | None, needle: str):
    """(rows, same) for one scope: a row per key only one side has or whose versions differ."""
    ia, ib = index(la, needle), index(lb, needle)
    rows, same = [], 0
    for key in sorted(ia.keys() | ib.keys(), key=lambda k: (k.lower(), k)):
        ea, eb = ia.get(key), ib.get(key)
        if ea and eb and ea["versions"] == eb["versions"]:
            same += 1
            continue
        state = "only_a" if not eb else "only_b" if not ea else "differ"
        rows.append({"name": key, "state": state, "a": _versions(ea), "b": _versions(eb)})
    return rows, same


def _users(pa: dict, pb: dict, home: str | None, a: str, b: str) -> list[str]:
    users = sorted(set(pa["hm_users"]) | set(pb["hm_users"]))
    if home is None:
        return users
    if home not in users:
        raise NixinError(
            "unknown-user",
            f"{home!r} is a Home Manager user of neither {a} nor {b} "
            f"(users: {', '.join(users) or 'none'})",
        )
    return [home]


def collect(repo: ConfigRepo, ns: Any, hosts: list[str]) -> dict[str, Any]:
    """The `list --diff` document (without envelope or message)."""
    t0 = time.monotonic()
    a, b = pair(ns)
    for h in (a, b):
        if h not in hosts:
            raise unknown_host(h, hosts)
    if ns.home is not None and ns.scope == "system":
        raise NixinError("usage", "--home USER is a Home Manager user; not with --scope system")
    ca = TruthCache(repo, a, enabled=not ns.refresh)
    cb = TruthCache(repo, b, enabled=not ns.refresh)
    pa, pb = pkgs.collect(repo, a, ca), pkgs.collect(repo, b, cb)
    scope = ns.scope or ("home" if ns.home is not None else "all")
    users = _users(pa, pb, ns.home, a, b) if scope != "system" else []
    needle = (ns.filter or "").lower()
    rows: list[dict] = []
    scopes: list[dict] = []
    progs: list[dict] = []
    for user in ([None] if scope != "home" else []) + users:
        sa = pa["system"] if user is None else pa["home"].get(user)
        sb = pb["system"] if user is None else pb["home"].get(user)
        found, same = compare(sa and sa["packages"], sb and sb["packages"], needle)
        tag = {"scope": "system" if user is None else "home", "user": user}
        rows += [{**tag, **r} for r in found]
        counts = {s: sum(r["state"] == s for r in found) for s in STATES}
        on = [side for side, s in (("a", sa), ("b", sb)) if s is not None]
        scopes.append({**tag, "on": on, **counts, "same": same})
        if ns.programs:
            xa, xb = set((sa or {}).get("programs", [])), set((sb or {}).get("programs", []))
            for n in sorted(xa ^ xb, key=str.lower):
                if needle in n.lower():
                    state = "only_a" if n in xa else "only_b"
                    progs.append({**tag, "name": n, "state": state})
    doc: dict[str, Any] = {
        "ok": True,
        "a": a,
        "b": b,
        "scope": scope,
        "users": users,
        "filter": ns.filter,
        "packages": rows,
        "scopes": scopes,
        "counts": {k: sum(s[k] for s in scopes) for k in (*STATES, "same")},
        "cache": {"a": ca.doc(), "b": cb.doc()},
        "timings": {"a": pa["seconds"], "b": pb["seconds"], "total": 0.0},
        "warnings": [],
    }
    if ns.programs:
        doc["programs"] = progs
    doc["timings"]["total"] = round(time.monotonic() - t0, 2)
    return doc


# --- human output -----------------------------------------------------------------------


def _cell(versions: list[str | None]) -> str:
    if not versions:
        return ABSENT
    return ", ".join(v if v is not None else NO_VERSION for v in versions)


def render(d: dict[str, Any], expand: bool = False) -> str:
    """`scope  name  <a's version>  <b's version>  (user)`, grouped by scope, then one
    summary line per scope. A user only one host has is summed up, not listed (its rows
    are all "only"), unless --home names it (`expand`)."""
    a, b = d["a"], d["b"]
    total = term_width()
    collapsed = {s["user"] for s in d["scopes"] if len(s["on"]) == 1 and not expand}
    shown = [r for r in d["packages"] if r["user"] is None or r["user"] not in collapsed]
    lines = _table(total, a, b, shown) if shown else []
    if not shown:
        aside = any(r["user"] in collapsed for r in d["packages"])
        lines.append(
            f"no difference between {a} and {b}{_filter(d)}"
            + (" (users on one host only aside)" if aside else "")
        )
    if lines:
        lines.append("")
    for s in d["scopes"]:
        lines.append(_summary(s, a, b, collapsed))
    if "programs" in d:
        progs = [p for p in d["programs"] if p["user"] is None or p["user"] not in collapsed]
        lines += ["", f"programs.<name>.enable on one host only{_filter(d)}:"]
        lines += [
            f"  {p['scope']:<6}  {p['name']}"
            + (f" ({p['user']})" if p["user"] else "")
            + f"  {a if p['state'] == 'only_a' else b} only"
            for p in progs
        ] or ["  none" + (" (users on one host only aside)" if collapsed else "")]
    lines.append(_cache_line(d))
    lines += [f"note: {w}" for w in d["warnings"]]
    return "\n".join(lines)


def _filter(d: dict[str, Any]) -> str:
    return f" matching {d['filter']!r}" if d["filter"] else ""


def _summary(s: dict[str, Any], a: str, b: str, collapsed: set) -> str:
    label = "system" if s["user"] is None else f"home {s['user']}"
    if len(s["on"]) == 1:
        only, n = (a, s["only_a"]) if s["on"] == ["a"] else (b, s["only_b"])
        other = b if only == a else a
        more = f"; --home {s['user']} lists them" if s["user"] in collapsed else ""
        return f"{label}: only {only} has this user ({_n(n, 'package')}, none on {other}{more})"
    parts = [f"{s['only_a']} only on {a}", f"{s['only_b']} only on {b}"]
    parts.append("1 version differs" if s["differ"] == 1 else f"{s['differ']} versions differ")
    parts.append(f"{s['same']} the same")
    return f"{label}: " + ", ".join(parts)


def _n(n: int, what: str) -> str:
    return f"{n} {what}{'s' if n != 1 else ''}"


def _cache_line(d: dict[str, Any]) -> str:
    parts = []
    for side in ("a", "b"):
        hit = "packages" in d["cache"][side]["hits"]
        host = d[side]
        parts.append(f"{host} cached" if hit else f"{host} evaluated in {d['timings'][side]} s")
    return " · ".join(parts)


def _table(total: int, a: str, b: str, rows: list[dict]) -> list[str]:
    cells = [(r["scope"], r["name"], _cell(r["a"]), _cell(r["b"]), r["user"] or "") for r in rows]
    sw = max(len("scope"), *(len(c[0]) for c in cells))
    aw = min(max(len(a), *(len(c[2]) for c in cells)), max(VERSION_CAP, len(a)))
    bw = min(max(len(b), *(len(c[3]) for c in cells)), max(VERSION_CAP, len(b)))
    uw = max((len(c[4]) + 2 for c in cells if c[4]), default=0)
    fixed = sw + 2 + aw + 2 + bw + (uw + 2 if uw else 0)
    nw = min(max(len("name"), *(len(c[1]) for c in cells)), NAME_CAP)
    nw = max(min(nw, total - fixed - 2), min(nw, NAME_MIN))

    def line(scope: str, name: str, va: str, vb: str, user: str) -> str:
        parts = [scope.ljust(sw), cap(name, nw).ljust(nw), cap(va, aw).ljust(aw), cap(vb, bw)]
        if uw:
            parts[-1] = parts[-1].ljust(bw)
            parts.append(f"({user})" if user else "")
        return "  ".join(parts).rstrip()

    return [line("scope", "name", a, b, ""), *(line(*c) for c in cells)]
