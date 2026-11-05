"""truth — what THIS host actually has, from evaluating the config flake itself.

Verbs: `status`, `where` and `list` (`list --diff A B` compares two hosts: hostdiff.py). `register(subparsers)` is the only thing cli.py
calls: it adds the truth arguments to the stub parsers and installs the handlers via
`set_defaults(run=...)`. A handler takes the argparse namespace and returns the --json
document (without verb/host, which cli adds); in human mode it also puts the rendered
text under "message", which cli prints.

Run end to end against the workspace config on mbp13 (HANDOFF.md has the numbers).
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..configrepo import ConfigRepo, NixinError, local_hostname, running_system, unknown_host
from ..lockstate import lock_change
from . import hostdiff, listing, render, scan
from . import options as opts
from . import packages as pkgs
from .cache import TruthCache

VERBS = ("status", "where", "list")
HOSTS_KEY = "_flake"  # the hosts cache file is per flake, not per host


def register(sub: argparse._SubParsersAction) -> None:
    for verb, handler in (("status", status), ("where", where), ("list", list_)):
        p = sub.choices.get(verb)
        if p is None:
            p = sub.add_parser(verb)
            p.add_argument("args", nargs="*")
        p.add_argument("--config", help="config flake dir (default: $NIXIN_CONFIG or /etc/nixos)")
        p.add_argument("--refresh", action="store_true", help="ignore cached eval results")
        p.set_defaults(run=handler)
    sub.choices["where"].add_argument(
        "--kind", choices=["package", "option"], help="what NAME is (default: guess from a dot)"
    )
    st = sub.choices["status"]
    st.add_argument(
        "--full",
        action="store_true",
        help="also build the option index (NixOS + each HM user; ~30 s per tree when cold)",
    )
    st.add_argument(
        "--quick", action="store_true", help="the default: option counts only if already cached"
    )
    ls = sub.choices["list"]
    ls.add_argument("--home", metavar="USER", help="only this Home Manager user's packages")
    ls.add_argument(
        "--scope", choices=listing.SCOPES, help="system, home (every HM user) or all (default)"
    )
    ls.add_argument("--filter", metavar="TEXT", help="name or pname contains TEXT (any case)")
    ls.add_argument(
        "--programs", action="store_true", help="also the enabled programs.<x> (as in status)"
    )
    ls.add_argument(
        "--diff",
        nargs="+",
        metavar="HOST",
        help="HOST_B, or HOST_A HOST_B: packages one host has and the other does not, and "
        "versions that differ (HOST_A defaults to --host)",
    )


def _run(ns: argparse.Namespace, fn) -> dict[str, Any]:
    try:
        return fn(ns)
    except NixinError as e:
        return e.doc()


def _hosts_of(repo: ConfigRepo, ns: argparse.Namespace) -> list[str]:
    return hosts(repo, refresh=getattr(ns, "refresh", False))


def hosts(repo: ConfigRepo, *, refresh: bool = False) -> list[str]:
    """nixosConfigurations' names: one eval, cached by the flake fingerprint (any tracked
    edit or lock change misses), so a warm `status`/`list`/`where` runs no eval at all."""
    cache = TruthCache(repo, HOSTS_KEY, enabled=not refresh)
    hit = cache.get("hosts", peek=True)
    if isinstance(hit, list) and all(isinstance(h, str) for h in hit):
        return hit
    names = repo.hosts()
    cache.put("hosts", names)
    return names


# --- status -------------------------------------------------------------------------


def status(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _status)


def _status(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    repo = ConfigRepo.locate(ns.config)
    if ns.args:
        raise NixinError("usage", f"status takes no arguments (got {' '.join(ns.args)!r})")
    host = ns.host
    hosts = _hosts_of(repo, ns)
    if host not in hosts:
        raise unknown_host(host, hosts)
    cache = TruthCache(repo, host, enabled=not ns.refresh)
    src, lock, run = repo.source(), repo.lock(), running_system()
    timings: dict[str, float] = {}

    # does the host evaluate? (system.build.toplevel runs the assertions)
    top = cache.get("toplevel")
    if top is None:
        t = time.monotonic()
        drv = repo.eval_raw(f"{repo.host_attr(host)}.config.system.build.toplevel.drvPath")
        top = {"drvPath": drv, "seconds": round(time.monotonic() - t, 2)}
        cache.put("toplevel", top)
    timings["toplevel"] = top["seconds"]

    pk = pkgs.collect(repo, host, cache)
    timings["packages"] = pk["seconds"]
    option_docs: dict[str, Any] = {}
    if ns.full:
        store = opts.ExclusionStore(repo, host)
        option_docs["nixos"] = opts.summary(repo, host, cache, store=store)
        option_docs["home"] = {
            u: opts.summary(repo, host, cache, hm=u, store=store) for u in pk["hm_users"]
        }
    else:  # whatever an earlier --full left in the cache, without evaluating anything
        nixos = opts.cached_summary(cache)
        if nixos is not None:
            home = {u: opts.cached_summary(cache, hm=u) for u in pk["hm_users"]}
            option_docs = {
                "nixos": nixos,
                "home": {u: h for u, h in home.items() if h is not None},
            }

    running_rev = run.get("nixpkgs_rev")
    from .. import optindex

    doc = {
        "ok": True,
        "config": {
            "path": str(repo.path),
            **src.doc(),
            "hosts": hosts,
            "host_is_local": host == local_hostname(),
        },
        "lock": lock.doc() if lock else None,
        "lock_change": lock_change(repo, lock.name if lock else "nixpkgs", host),
        "running": {
            **run,
            "hostname": local_hostname(),
            "matches_lock": bool(
                lock and lock.rev and running_rev and lock.rev.startswith(running_rev)
            ),
        },
        "eval": {"ok": True, "toplevel": top["drvPath"], "seconds": top["seconds"]},
        "hm_users": pk["hm_users"],
        "packages": pkgs.counts(pk),
        "programs": {
            "system": pk["system"]["programs"],
            "home": {u: h["programs"] for u, h in pk["home"].items()},
        },
        "options": option_docs,
        "indexes": optindex.status(host),
        "cache": cache.doc(),
        "timings": {**timings, "total": round(time.monotonic() - t0, 2)},
    }
    if not ns.json:
        doc["message"] = render.status(doc, host)
    from .. import apply  # the pending block (late import: apply imports truth.render)

    apply.annotate_status(doc, repo, host)
    return doc


# --- list ---------------------------------------------------------------------------


def list_(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _list)


def _list(ns: argparse.Namespace) -> dict[str, Any]:
    repo = ConfigRepo.locate(ns.config)
    if ns.args:
        raise NixinError(
            "usage", f"list takes no arguments (got {' '.join(ns.args)!r}; --filter TEXT?)"
        )
    host = ns.host
    hosts = _hosts_of(repo, ns)
    if ns.diff is not None:  # two hosts of this config, compared
        doc = hostdiff.collect(repo, ns, hosts)
        if not ns.json:
            doc["message"] = hostdiff.render(doc, expand=ns.home is not None)
        return doc
    if host not in hosts:
        raise unknown_host(host, hosts)
    cache = TruthCache(repo, host, enabled=not ns.refresh)
    doc = listing.collect(repo, host, cache, ns)
    doc["cache"] = cache.doc()
    if not ns.json:
        doc["message"] = render.listing(doc, host)
    return doc


# --- where --------------------------------------------------------------------------


def where(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _where)


def _where(ns: argparse.Namespace) -> dict[str, Any]:
    repo = ConfigRepo.locate(ns.config)
    if len(ns.args) != 1:
        raise NixinError("usage", "where NAME  (one package attribute or option path)")
    name = ns.args[0]
    host = ns.host
    hosts = _hosts_of(repo, ns)
    if host not in hosts:
        raise unknown_host(host, hosts)
    cache = TruthCache(repo, host, enabled=not ns.refresh)
    pk = pkgs.collect(repo, host, cache)
    kind = ns.kind or ("option" if "." in name else "package")
    doc = (
        _where_option(repo, host, cache, pk, name)
        if kind == "option"
        else _where_package(repo, host, cache, pk, name, hosts)
    )
    doc.update({"ok": True, "name": name, "kind": kind, "cache": cache.doc()})
    if not ns.json:
        doc["message"] = render.where(doc)
    return doc


def _scopes(pk: dict) -> list[tuple[str, str | None]]:
    return [("nixos", None)] + [("home", u) for u in pk["hm_users"]]


def _location(repo: ConfigRepo, raw: str, pos: dict | None = None) -> dict[str, Any]:
    loc = repo.locate_path(raw)
    if pos:
        loc.line, loc.column = pos.get("line"), pos.get("column")
    return loc.doc()


def _definition(repo: ConfigRepo, raw: str, path: list[str], value: Any = None) -> dict[str, Any]:
    """A definition file mapped to the repo, with the line found by the text scanner."""
    d = _location(repo, raw)
    d["value"] = value
    if d["source"] == "config" and d["file"]:
        try:
            hit = scan.best_option_line((repo.path / d["file"]).read_text(errors="replace"), path)
        except OSError:
            hit = None
        if hit:
            d.update({"line": hit.line, "line_heuristic": True, "text": hit.text.strip()})
    elif d["source"] == "inline":
        d["candidates"] = [m.doc() for m in scan.grep_option(repo.path, path)]
    return d


def _entry_doc(repo: ConfigRepo, e: dict, rest: list[str]) -> dict[str, Any]:
    path = e["name"].split(".") + rest
    positions = {p["file"]: p for p in e.get("declarationPositions", [])}
    out: dict[str, Any] = {
        "name": ".".join(path),
        "option": e["name"],
        "type": e.get("type"),
        "description": e.get("description"),
        "isDefined": e.get("isDefined"),
        "highestPrio": e.get("highestPrio"),
        "declarations": [_location(repo, f, positions.get(f)) for f in e.get("declarations", [])],
        "definitions": [],
    }
    out.update(_docs(e, rest))
    if "value" in e:
        found, v = opts.dig(e["value"], rest)
        out["value"] = v if found else None
        out["value_found"] = found
    if e.get("valueError"):
        out["value_error"] = True
    for d in e.get("definitionValues") or [
        {"file": f, "value": None} for f in e.get("definitions") or []
    ]:
        if rest:
            found, v = opts.dig(d["value"], rest)
            if not found:
                continue  # this file defines other sub-attributes
        else:
            v = d["value"]
        out["definitions"].append(_definition(repo, d["file"], path, v))
    return out


def _docs(e: dict, rest: list[str]) -> dict[str, Any]:
    """`type_info`, `default`, `example` of the name asked (the option itself, or the path
    `rest` below it, followed through its type by options.nix's `sub`); in sub mode also
    `sub_description` when that path is a declared sub-option."""
    d = e if not rest else (e.get("sub") or {})
    out: dict[str, Any] = {
        "type_info": d.get("typeInfo"),
        "default": opts.literal_text(d.get("default")),
        "example": opts.literal_text(d.get("example")),
    }
    if rest and d.get("description"):
        out["sub_description"] = d["description"].strip()
    return out


def _resolve(entries: list[dict], path: list[str]) -> tuple[str, dict | None, list[str]]:
    """(mode, entry, rest): the path is an option (`exact`), a path inside one (`sub`, the
    option and the rest of the path), or has only options below it (`prefix`, None)."""
    exact = opts.lookup(entries, ".".join(path))
    if exact is not None:
        return "exact", exact, []
    parent, rest = opts.longest_parent(entries, path)
    if parent is not None:
        return "sub", parent, rest
    return "prefix", None, []


def option_info(
    repo: ConfigRepo,
    host: str,
    path: list[str] | str,
    *,
    hm: str | None = None,
    cache: TruthCache | None = None,
) -> dict[str, Any] | None:
    """An option path (parsed, or dotted) in one scope (the NixOS tree, or HM user `hm`): `where`'s option entry (type,
    `type_info`, default, example, value, declarations, definitions) plus `mode` (`exact`,
    `sub`) and `excluded`; for a path that only has options below it, `mode` `prefix` with
    `options[]` (their names). None when the scope has nothing at or under NAME. One prefix
    eval, cached like `where`'s (the writer's type check and `explain` call it)."""
    path = path.split(".") if isinstance(path, str) else list(path)
    name = ".".join(path)
    cache = cache or TruthCache(repo, host)
    q = opts.prefix_query(repo, host, cache, path, hm=hm)
    entries, excluded = q["entries"], q["excluded"]
    mode, entry, rest = _resolve(entries, path)
    if entry is not None:
        doc = {"mode": mode, **_entry_doc(repo, entry, rest)}
    elif entries:
        doc = {"mode": mode, "name": name, "options": [e["name"] for e in entries]}
    elif excluded:
        doc = {"mode": "excluded", "name": name}
    else:
        return None
    doc.update({"scope": "nixos" if hm is None else "home", "user": hm, "excluded": excluded})
    return doc


def hm_users(repo: ConfigRepo, host: str, cache: TruthCache) -> list[str]:
    """The host's Home Manager users: from the cached package eval when there is one, else
    one small eval (cached on its own)."""
    hit = cache.get(pkgs.CACHE_NAME, pkgs.helper_text(), peek=True)
    if hit is not None:
        return list(hit["hm_users"])
    users = cache.get("hm-users", "1", peek=True)
    if users is None:
        users = sorted(repo.hm_users(host))
        cache.put("hm-users", users, "1")
    return list(users)


def option_infos(
    repo: ConfigRepo, host: str, path: list[str], *, home: str | None, cache: TruthCache
) -> list[dict[str, Any]]:
    """option_info() for every scope that has the path: NixOS, then each HM user (only
    `home` when given; `unknown-user` when the host has no such user)."""
    users = hm_users(repo, host, cache)
    if home is not None and home not in users:
        raise NixinError(
            "unknown-user",
            f"{home!r} is not a Home Manager user of {host} (users: {', '.join(users) or 'none'})",
        )
    scopes = [home] if home is not None else [None, *users]
    found = [option_info(repo, host, path, hm=u, cache=cache) for u in scopes]
    return [f for f in found if f is not None]


def package_info(repo: ConfigRepo, host: str, name: str, *, cache: TruthCache) -> dict[str, Any]:
    """`where PACKAGE`'s answer: installed copies, enabled programs.NAME.enable, the files
    that list it, mentions."""
    known = hosts(repo, refresh=not cache.enabled)
    if host not in known:
        raise unknown_host(host, known)
    return _where_package(repo, host, cache, pkgs.collect(repo, host, cache), name, known)


def _where_option(repo: ConfigRepo, host: str, cache: TruthCache, pk: dict, name: str) -> dict:
    from .. import optindex

    path = name.split(".")
    scopes_out = []
    skipped = optindex.prune_scopes(host, _scopes(pk), path)  # no eval where an index says no
    for scope, user in _scopes(pk):
        if (scope, user) in skipped:
            continue
        q = opts.prefix_query(repo, host, cache, path, hm=user)
        entries, excluded = q["entries"], q["excluded"]
        if not entries and not excluded:
            continue
        mode, entry, rest = _resolve(entries, path)
        if entry is not None:
            items = [_entry_doc(repo, entry, rest)]
        else:
            items = [_entry_doc(repo, e, []) for e in entries if opts.is_set(e)]
        scopes_out.append(
            {
                "scope": scope,
                "user": user,
                "mode": mode,
                "options": items,
                "under_prefix": len(entries),
                "excluded": excluded,
            }
        )
    if not scopes_out:
        raise NixinError(
            "not-found", f"{name!r} is not an option of {host} (NixOS or Home Manager)"
        )
    doc: dict[str, Any] = {
        "scopes": scopes_out,
        "skipped_scopes": [
            {"scope": scope, "user": user, "reason": why} for (scope, user), why in skipped.items()
        ],
    }
    optindex.annotate_where(doc, host)  # docs from the index; doc["index"]
    return doc


def _list_definers(
    repo: ConfigRepo, entry: dict | None, names: set[str], option: str
) -> list[dict]:
    """Files whose definition of a list-of-packages option contains one of `names`."""
    out = []
    for d in (entry or {}).get("definitionValues") or []:
        items = d["value"] if isinstance(d["value"], list) else []
        hit = [s for s in items if isinstance(s, str) and s in names]
        if hit:
            loc = _location(repo, d["file"])
            loc.update({"option": option, "items": hit})
            out.append(loc)
    return out


def _where_package(
    repo: ConfigRepo, host: str, cache: TruthCache, pk: dict, name: str, hosts: list[str]
) -> dict:
    installed = pkgs.find(pk, name)
    enabled = pkgs.programs(pk, name)
    declared: list[dict] = []
    for tree, user in _scopes(pk):
        scope = "system" if tree == "nixos" else tree  # package docs say system/home
        names = {
            f"<derivation {p['name']}>"
            for p in installed
            if p["scope"] == scope and p["user"] == user
        }
        if not names and not any(e["scope"] == scope and e["user"] == user for e in enabled):
            continue
        option = "environment.systemPackages" if user is None else "home.packages"
        q = opts.prefix_query(repo, host, cache, option.split("."), hm=user)
        for loc in _list_definers(repo, opts.lookup(q["entries"], option), names, option):
            loc.update({"scope": scope, "user": user})
            if loc["source"] == "config" and loc["file"]:
                lines = [
                    m
                    for m in scan.grep_package(repo.path, name, limit=200)
                    if m.file == loc["file"]
                ]
                loc["lines"] = [m.doc() for m in lines[:5]]
            declared.append(loc)
    via = []
    for e in enabled:
        q = opts.prefix_query(repo, host, cache, e["option"].split("."), hm=e["user"])
        ent = opts.lookup(q["entries"], e["option"])
        if ent is not None:
            d = _entry_doc(repo, ent, [])
            d.update({"scope": e["scope"], "user": e["user"]})
            via.append(d)
    return {
        "installed": installed,
        "programs": via,
        "declared": declared,
        "mentions": [
            m.doc()
            for m in scan.grep_package(repo.path, name, limit=200)
            if not _other_host_file(m.file, host, hosts)
        ][:20],
    }


def _other_host_file(rel: str, host: str, hosts: list[str]) -> bool:
    """A file under another host's directory (hosts/mbp13/… when asking about 19BK)."""
    return any(part in hosts and part != host for part in rel.split("/")[:-1])
