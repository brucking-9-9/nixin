"""truth — what THIS host actually has, from evaluating the config flake itself.

Verbs: `status` and `where`. `register(subparsers)` is the only thing cli.py calls: it
adds the truth arguments to the two stub parsers and installs the handlers via
`set_defaults(run=...)`. A handler takes the argparse namespace and returns the --json
document (without verb/host, which cli adds); in human mode it also puts the rendered
text under "message", which cli prints.

STATUS: written against measured shapes but NOT yet run end to end (see HANDOFF.md).
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..configrepo import ConfigRepo, NixinError, local_hostname, running_system
from . import options as opts
from . import packages as pkgs
from . import scan
from .cache import TruthCache

VERBS = ("status", "where")


def register(sub: argparse._SubParsersAction) -> None:
    for verb, handler in (("status", status), ("where", where)):
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


def _run(ns: argparse.Namespace, fn) -> dict[str, Any]:
    try:
        return fn(ns)
    except NixinError as e:
        return e.doc()


# --- status -------------------------------------------------------------------------


def status(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _status)


def _status(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    repo = ConfigRepo.locate(ns.config)
    host = ns.host
    hosts = repo.hosts()
    if host not in hosts:
        raise NixinError(
            "unknown-host", f"{host!r} is not in nixosConfigurations ({', '.join(hosts)})"
        )
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
        store = opts.ExclusionStore(repo)
        option_docs["nixos"] = opts.summarize(opts.index(repo, host, cache, store=store))
        option_docs["home"] = {
            u: opts.summarize(opts.index(repo, host, cache, hm=u, store=store))
            for u in pk["hm_users"]
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
    doc = {
        "ok": True,
        "config": {
            "path": str(repo.path),
            **src.doc(),
            "hosts": hosts,
            "host_is_local": host == local_hostname(),
        },
        "lock": lock.doc() if lock else None,
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
        "cache": cache.doc(),
        "timings": {**timings, "total": round(time.monotonic() - t0, 2)},
    }
    if not ns.json:
        doc["message"] = render_status(doc, host)
    return doc


def render_status(d: dict[str, Any], host: str) -> str:
    c, lock, run = d["config"], d["lock"] or {}, d["running"]
    hosts = ", ".join(h + ("*" if h == run["hostname"] else "") for h in c["hosts"])
    head = (c["head"] or "?")[:7] + (" dirty" if c["dirty"] else " clean")
    lines = [
        f"config   {c['path']} @ {head}   hosts: {hosts}",
        f"lock     {lock.get('input', 'nixpkgs')} {lock.get('rev', '?')[:7]} {lock.get('ref') or ''} {lock.get('date') or ''}".rstrip(),
        f"running  {run.get('version') or '?'}"
        + ("  (matches lock)" if run["matches_lock"] else "  (lock differs)"),
        f"host     {host} evaluates  ({d['eval']['seconds']}s)",
        f"packages system {d['packages']['system']}"
        + "".join(f" · home {u} {n}" for u, n in d["packages"]["home"].items()),
        f"programs system {len(d['programs']['system'])}"
        + "".join(f" · home {u} {len(p)}" for u, p in d["programs"]["home"].items()),
    ]
    o = d["options"]
    if o:
        n = o["nixos"]
        lines.append(
            f"options  nixos {n['defined']} defined of {n['total']}"
            + "".join(
                f" · home {u} {h['defined']} defined of {h['total']}" for u, h in o["home"].items()
            )
            + (f"  ({n['excluded']} skipped)" if n["excluded"] else "")
        )
    cache = d["cache"]
    lines.append(
        f"cache    {cache['dir']}  hits {len(cache['hits'])} misses {len(cache['misses'])}"
    )
    return "\n".join(lines)


# --- where --------------------------------------------------------------------------


def where(ns: argparse.Namespace) -> dict[str, Any]:
    return _run(ns, _where)


def _where(ns: argparse.Namespace) -> dict[str, Any]:
    repo = ConfigRepo.locate(ns.config)
    if not ns.args:
        raise NixinError("usage", "where NAME  (a package attribute or an option path)")
    name = ns.args[0]
    host = ns.host
    hosts = repo.hosts()
    if host not in hosts:
        raise NixinError(
            "unknown-host", f"{host!r} is not in nixosConfigurations ({', '.join(hosts)})"
        )
    cache = TruthCache(repo, host, enabled=not ns.refresh)
    pk = pkgs.collect(repo, host, cache)
    kind = ns.kind or ("option" if "." in name else "package")
    doc = (
        _where_option(repo, host, cache, pk, name)
        if kind == "option"
        else _where_package(repo, host, cache, pk, name)
    )
    doc.update({"ok": True, "name": name, "kind": kind, "cache": cache.doc()})
    if not ns.json:
        doc["message"] = render_where(doc)
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


def _where_option(repo: ConfigRepo, host: str, cache: TruthCache, pk: dict, name: str) -> dict:
    path = name.split(".")
    scopes_out = []
    for scope, user in _scopes(pk):
        q = opts.prefix_query(repo, host, cache, path, hm=user)
        entries, excluded = q["entries"], q["excluded"]
        if not entries and not excluded:
            continue
        exact = opts.lookup(entries, name)
        if exact is not None:
            mode, items = "exact", [_entry_doc(repo, exact, [])]
        else:
            parent, rest = opts.longest_parent(entries, path)
            if parent is not None:
                mode, items = "sub", [_entry_doc(repo, parent, rest)]
            else:
                mode = "prefix"
                items = [_entry_doc(repo, e, []) for e in entries if e.get("isDefined")]
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
    return {"scopes": scopes_out}


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


def _where_package(repo: ConfigRepo, host: str, cache: TruthCache, pk: dict, name: str) -> dict:
    installed = pkgs.find(pk, name)
    enabled = pkgs.programs(pk, name)
    declared: list[dict] = []
    for scope, user in _scopes(pk):
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
        "mentions": [m.doc() for m in scan.grep_package(repo.path, name)],
    }


def render_where(d: dict[str, Any]) -> str:
    lines: list[str] = []
    if d["kind"] == "package":
        inst = d["installed"]
        lines.append(
            f"{d['name']}: "
            + (
                "installed — "
                + ", ".join(
                    f"{p['scope']}{' ' + p['user'] if p['user'] else ''}: {p['name']}" for p in inst
                )
                if inst
                else "not installed on this host"
            )
        )
        for v in d["programs"]:
            for df in v["definitions"]:
                where_ = (
                    f"{df.get('file')}:{df.get('line')}"
                    if df.get("line")
                    else (df.get("file") or df["source"])
                )
                lines.append(
                    f"  {v['option']} = {df.get('value')!s:<6} {where_}  [{v['scope']} {v['user'] or ''}]".rstrip()
                )
        for loc in d["declared"]:
            where_ = loc["file"] if loc["source"] == "config" else f"{loc['source']}: {loc['file']}"
            ln = f":{loc['lines'][0]['line']}" if loc.get("lines") else ""
            lines.append(
                f"  {loc['option']}  {where_}{ln}  [{loc['scope']} {loc['user'] or ''}]".rstrip()
            )
        if d["mentions"]:
            lines.append("mentions:")
            lines += [f"  {m['file']}:{m['line']}  {m['text']}" for m in d["mentions"][:8]]
        return "\n".join(lines)
    for s in d["scopes"]:
        tag = f"[{s['scope']}{' ' + s['user'] if s['user'] else ''}]"
        if s["mode"] == "prefix":
            lines.append(
                f"{d['name']} {tag}: {s['under_prefix']} options, {len(s['options'])} defined"
            )
        for o in s["options"]:
            val = f" = {o['value']!s}"[:200] if "value" in o else ""
            lines.append(f"{o['name']} {tag}  {o['type']}{val}")
            for dec in o["declarations"]:
                pos = f":{dec['line']}" if dec.get("line") else ""
                lines.append(f"  declared  {dec['source']}: {dec['file']}{pos}")
            for df in o["definitions"]:
                pos = f":{df['line']}" if df.get("line") else ""
                src = df["file"] if df["source"] == "config" else f"{df['source']}: {df['file']}"
                lines.append(f"  set in    {src}{pos}  = {df.get('value')!s}"[:200])
                for c in df.get("candidates", [])[:3]:
                    lines.append(f"            maybe {c['file']}:{c['line']}  {c['text']}")
            if o.get("description"):
                lines.append(f"  {o['description'][:160]}")
        for name, msg in s["excluded"].items():
            lines.append(f"  {name}: cannot evaluate: {msg.splitlines()[-1][:120]}")
    return "\n".join(lines)
