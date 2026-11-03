"""Discovery layer: what exists, in which versions.

Verbs (registered into the CLI by `register`):

    search QUERY [--kind pkg|opt|hm|program|fn|all] [--channel C] [--limit N] [--nixpkgs REF]
    info NAME [--kind pkg|opt|hm|fn|all] [--channel C] [--nixpkgs REF]
    versions PKG [--limit N]

Sources, one module each:

    nixos_search   search.nixos.org Elasticsearch: packages (pkg), NixOS options (opt),
                   programs (which package ships a binary)
    home_manager   Home Manager options (hm), from the option JSON behind
                   home-manager-options.extranix.com
    nixhub         version history (versions): which nixpkgs commit shipped which version
    local          exact-rev fallback: `nix search` / `nix eval` on `--nixpkgs FLAKEREF`
    noogle         nix lib functions (fn); opt-in, not part of --kind all

Every result record carries `kind`, `source`, `channel` and (where known) `rev`,
so a UI can tell "unstable says 145.0" from "your lock has 144.0". The exact
field names per kind are documented in README.md under "Discovery output".

Each verb returns one document for cli._emit:

    {"ok": true, "results": [...], "count": N, "source": ..., "channel": ..., "rev": ...,
     "cached": bool, "sources": {<source>: {...}}, ...verb-specific}
    {"ok": false, "error": {"code": "network"|"not-found"|"bad-channel"|"usage"|..., "message"}}

Human output is rendered by `format.render` and handed to cli._emit as `message`.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..cache import Cache
from ..http import NetworkError

VERBS: dict[str, str] = {
    "search": "search packages / options / programs; --kind filters",
    "info": "details for one package or option (exact name)",
    "versions": "version history of a package (which nixpkgs rev shipped what)",
}

KINDS = ("pkg", "opt", "hm", "program", "fn", "all")
INFO_KINDS = ("pkg", "opt", "hm", "fn", "all")
ALL_SEARCH_KINDS = ("pkg", "opt", "hm", "program")  # what --kind all fans out to
ALL_INFO_KINDS = ("pkg", "opt", "hm")
DEFAULT_LIMIT = 20
DEFAULT_CHANNEL = "unstable"


class DiscoveryError(Exception):
    """A verb failed in a way the user can act on; `code` is the --json error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self)


@dataclass
class Part:
    """One source's answer to a query; `run_*` merge Parts into the document."""

    source: str
    channel: str | None
    rev: str | None
    cached: bool
    results: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # verb-specific top-level fields


# --------------------------------------------------------------------------- parser


def _cache_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    sp.add_argument("--refresh", action="store_true", help="ignore cached answers, fetch and store")


def _index_flag(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--no-index",
        action="store_true",
        help="options from upstream even when this machine has an option index "
        "(nixin's modules; --channel and --nixpkgs imply it)",
    )


def _channel_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--channel",
        metavar="C",
        help="unstable (default), stable, NN.MM, or a nixpkgs branch name like nixos-25.11",
    )
    sp.add_argument(
        "--nixpkgs",
        metavar="FLAKEREF",
        help="exact-rev source for packages: nix search / nix eval on this nixpkgs "
        "(e.g. github:nixos/nixpkgs/<rev>); options still come from the channel the ref implies",
    )


def register(sub: argparse._SubParsersAction) -> set[str]:
    """Add the discovery verbs to the CLI's subparsers; returns the verb names taken."""
    sp = sub.add_parser("search", help=VERBS["search"])
    sp.add_argument("query")
    sp.add_argument("--kind", choices=KINDS, default="pkg")
    sp.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N")
    sp.add_argument(
        "--appstream",
        action="store_true",
        help="mark GUI applications (an `appstream` hint per package: search.nixos.org's "
        "desktop data, else nixin's list of well-known apps; no nix)",
    )
    _channel_flags(sp)
    _index_flag(sp)
    _cache_flags(sp)
    sp.set_defaults(run=run_search)

    sp = sub.add_parser("info", help=VERBS["info"])
    sp.add_argument("name")
    sp.add_argument("--kind", choices=INFO_KINDS, default="all")
    sp.add_argument(
        "--appstream",
        choices=("local", "fetch", "hint", "off"),
        default="local",
        help="a package's AppStream data: local (default; the installed or locked output "
        "in the store, else search.nixos.org's hint), fetch (also substitute it when that "
        "is cheap), hint (search.nixos.org only, no nix), off",
    )
    sp.add_argument(
        "--config",
        metavar="DIR",
        help="the config flake whose locked nixpkgs --appstream evaluates "
        "(default $NIXIN_CONFIG, else /etc/nixos)",
    )
    _channel_flags(sp)
    _index_flag(sp)
    _cache_flags(sp)
    sp.set_defaults(run=run_info)

    sp = sub.add_parser("versions", help=VERBS["versions"])
    sp.add_argument("package")
    sp.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N")
    _cache_flags(sp)
    sp.set_defaults(run=run_versions)
    return set(VERBS)


# --------------------------------------------------------------------------- plumbing


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, DiscoveryError | NetworkError):
        return {"code": exc.code, "message": exc.message}
    # Anything else is a bug or a source that changed shape. Report it as an error
    # document, because under --json a traceback would leave stdout empty.
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)
    where = f" at {os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else ""
    return {"code": "internal", "message": f"{type(exc).__name__}: {exc}{where}"}


def _guard(fn: Callable[[argparse.Namespace], dict[str, Any]]) -> Callable:
    """Turn exceptions into the error document and, without --json, render the table."""

    def run(ns: argparse.Namespace) -> dict[str, Any]:
        try:
            doc = fn(ns)
        except Exception as exc:  # noqa: BLE001 -- every failure must become the error document
            return {"ok": False, "error": _error(exc)}
        if getattr(ns, "json", False):
            return doc
        from . import format as fmt

        return {"ok": True, "message": fmt.render(ns.verb, doc)}

    return run


def _cache(ns: argparse.Namespace) -> Cache:
    return Cache.from_flags(no_cache=ns.no_cache, refresh=ns.refresh)


def _channel_name(ns: argparse.Namespace) -> str:
    """The channel to ask the channel-based sources for.

    Explicit --channel wins; otherwise a branch-named --nixpkgs ref implies one
    (nixos-25.11 -> 25.11); a bare rev implies nothing, so the caller must say.
    """
    if ns.channel:
        return ns.channel
    if ns.nixpkgs:
        from . import nixos_search as es

        implied = es.channel_from_ref(ns.nixpkgs)
        if implied is None:
            raise DiscoveryError(
                "bad-channel",
                f"{ns.nixpkgs!r} names a commit, not a channel; channel-based sources need "
                "--channel (packages from that exact rev work without it)",
            )
        return implied
    return DEFAULT_CHANNEL


def _merge(ns: argparse.Namespace, parts: list[tuple[str, Part | None, dict | None]]) -> dict:
    """Fold per-kind Parts into the envelope.

    `parts` holds `(kind, part, error)`; `error` is set when that kind's source
    failed and the request spanned several kinds (a single-kind failure raises
    instead). One source can serve several kinds (search.nixos.org: pkg, opt,
    program) and fail for only some of them: its `error` is then the first
    failure while the kinds it did answer still count. The document fails only
    when no source answered at all. The `sources` map always exists; the
    top-level `source`, `channel`, `rev` and `cached` are the single-source
    convenience, and read `"all"` / the concrete channel the channel-based
    sources searched / null when several sources answered.
    """
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    sources: dict[str, dict[str, Any]] = {}
    answered: dict[str, dict[str, Any]] = {}
    for kind, part, error in parts:
        name = part.source if part is not None else error["source"]  # type: ignore[index]
        entry = sources.setdefault(
            name,
            {"kinds": [], "channel": None, "rev": None, "cached": True, "count": 0, "error": None},
        )
        entry["kinds"].append(kind)
        if part is None:
            assert error is not None
            entry["error"] = entry["error"] or {"code": error["code"], "message": error["message"]}
            continue
        if name not in answered:
            entry.update(channel=part.channel, rev=part.rev)
            answered[name] = entry
        entry["cached"] = entry["cached"] and part.cached
        entry["count"] += len(part.results)
        for k, v in part.extra.items():  # dicts merge: one source can answer several kinds
            entry[k] = {**entry[k], **v} if isinstance(v, dict) and k in entry else v
        results.extend(part.results)
        notes.extend(n for n in part.notes if n not in notes)
    for name, entry in sources.items():
        if name not in answered:
            entry["cached"] = False
    if not answered:
        first = next(iter(sources.values()))["error"]
        raise DiscoveryError(first["code"], first["message"])
    doc: dict[str, Any] = {
        "ok": True,
        "results": results,
        "count": len(results),
        "sources": sources,
    }
    if notes:
        doc["notes"] = notes
    if len(answered) == 1:
        ((name, entry),) = answered.items()
        doc.update(source=name, channel=entry["channel"], rev=entry["rev"], cached=entry["cached"])
    else:
        # local-nix reports the flakeref as its channel; the others agree on one channel name
        flakeref = getattr(ns, "nixpkgs", None)
        named = {e["channel"] for e in answered.values() if e["channel"] not in (None, flakeref)}
        doc.update(
            source="all",
            channel=named.pop()
            if len(named) == 1
            else getattr(ns, "channel", None) or DEFAULT_CHANNEL,
            rev=None,
            cached=all(e["cached"] for e in answered.values()),
        )
    return doc


def _source_for(kind: str, ns: argparse.Namespace) -> str:
    """Which source name a kind reports under when it fails before answering."""
    if kind == "pkg" and getattr(ns, "nixpkgs", None):
        from . import local

        return local.SOURCE
    if kind == "hm":
        from . import home_manager

        return home_manager.SOURCE
    if kind == "fn":
        from . import noogle

        return noogle.SOURCE
    from . import nixos_search

    return nixos_search.SOURCE


def _fan_out(
    ns: argparse.Namespace, kinds: tuple[str, ...], one: Callable[[str], Part]
) -> list[tuple[str, Part | None, dict | None]]:
    parts: list[tuple[str, Part | None, dict | None]] = []
    for kind in kinds:
        try:
            parts.append((kind, one(kind), None))
        except Exception as exc:
            if len(kinds) == 1:
                raise
            parts.append((kind, None, {**_error(exc), "source": _source_for(kind, ns)}))
    return parts


def _index_part(ns: argparse.Namespace, kind: str, pick: Callable) -> Part | None:
    """This machine's option index answering for `opt` (the system's) or `hm` (this
    user's): `pick(index)` -> its entries. None when it does not apply: another kind,
    `--no-index`, `--channel` or `--nixpkgs` (a question about upstream), no index, or one
    built for another host than `--host`."""
    if kind not in ("opt", "hm") or getattr(ns, "no_index", False):
        return None
    if getattr(ns, "channel", None) or getattr(ns, "nixpkgs", None):
        return None
    from .. import optindex

    host = getattr(ns, "host", None) or optindex.hostname()
    if kind == "opt":
        idx = optindex.usable("nixos", host)
    else:
        idx = optindex.usable("home", host, optindex.current_user())
    if idx is None:
        return None
    results = [idx.record(o) for o in pick(idx)]
    return Part(
        optindex.SOURCE, None, idx.rev, True, results, extra={"indexes": {idx.scope: idx.doc()}}
    )


def index_info(ns: argparse.Namespace, kind: str, name: str) -> Part | None:
    """`info`'s exact lookup in this machine's option index, for callers outside
    discovery (explain's upstream side): a Part with one result, a Part with none (the
    index applies but lacks NAME), or None (no index for this kind and `ns.host`, or
    `--no-index`/`--channel`/`--nixpkgs` on `ns`)."""

    def exact(idx: Any) -> list[dict]:
        hit = idx.lookup(name)
        return [hit] if hit is not None else []

    return _index_part(ns, kind, exact)


def _prefer_index(ns: argparse.Namespace, kind: str, pick: Callable, upstream: Callable) -> Part:
    """The index's answer when it has one; else upstream's, with a note when the index
    was asked and had nothing."""
    part = _index_part(ns, kind, pick)
    if part is not None and part.results:
        return part
    up = upstream(kind)
    if part is not None:
        up.notes.append(f"nothing in this machine's option index; asked {up.source}")
    return up


# --------------------------------------------------------------------------- verbs


def _search(ns: argparse.Namespace) -> dict[str, Any]:
    query = ns.query.strip()
    if not query:
        raise DiscoveryError("usage", "empty query")
    if ns.limit < 1:
        raise DiscoveryError("usage", "--limit must be at least 1")
    cache = _cache(ns)
    kinds = ALL_SEARCH_KINDS if ns.kind == "all" else (ns.kind,)

    def upstream(kind: str) -> Part:
        if kind == "pkg" and ns.nixpkgs:
            from . import local

            return local.search(cache, ns.nixpkgs, query, ns.limit)
        if kind in ("pkg", "opt", "program"):
            from . import nixos_search as es

            channel = es.resolve_channel(cache, _channel_name(ns))
            return es.search(cache, query, kind, channel, ns.limit, ns.appstream)
        if kind == "hm":
            from . import home_manager as hm

            return hm.search(cache, query, _channel_name(ns), ns.limit)
        from . import noogle

        return noogle.search(cache, query, ns.limit)

    def one(kind: str) -> Part:
        return _prefer_index(ns, kind, lambda idx: idx.search(query, ns.limit), upstream)

    doc = _merge(ns, _fan_out(ns, kinds, one))
    doc.update(query=query, kind=ns.kind, limit=ns.limit)
    if ns.appstream:
        from .. import appstream

        appstream.mark(doc["results"])
    return doc


def _info(ns: argparse.Namespace) -> dict[str, Any]:
    name = ns.name.strip()
    if not name:
        raise DiscoveryError("usage", "empty name")
    cache = _cache(ns)
    kinds = ALL_INFO_KINDS if ns.kind == "all" else (ns.kind,)

    def upstream(kind: str) -> Part:
        if kind == "pkg" and ns.nixpkgs:
            from . import local

            return local.info(cache, ns.nixpkgs, name)
        if kind in ("pkg", "opt"):
            from . import nixos_search as es

            channel = es.resolve_channel(cache, _channel_name(ns))
            return (
                es.info_package(cache, name, channel, ns.appstream != "off")
                if kind == "pkg"
                else es.info_option(cache, name, channel)
            )
        if kind == "hm":
            from . import home_manager as hm

            return hm.info(cache, name, _channel_name(ns))
        from . import noogle

        return noogle.info(cache, name)

    def one(kind: str) -> Part:
        part = index_info(ns, kind, name)
        if part is not None and part.results:
            return part
        up = upstream(kind)
        if part is not None:
            up.notes.append(f"nothing in this machine's option index; asked {up.source}")
        return up

    parts = _fan_out(ns, kinds, one)
    doc = _merge(ns, parts)
    doc.update(name=name, kind=ns.kind)
    if not doc["results"]:
        what = {
            "pkg": "package",
            "opt": "NixOS option",
            "hm": "Home Manager option",
            "fn": "function",
        }
        # only claim "no such X" for the kinds that were actually asked
        wanted = " or ".join(what[k] for k, part, _err in parts if part is not None)
        where = f" on {doc['channel']}" if doc.get("channel") else ""
        message = f"no {wanted} named {name!r}{where}"
        failed = [f"{what[k]} ({err['code']})" for k, part, err in parts if part is None and err]
        if failed:
            message += "; not asked: " + ", ".join(failed)
        raise DiscoveryError("not-found", message)
    if ns.appstream != "off":
        _appstream(ns, cache, doc["results"])
    return doc


def _locked_nixpkgs(ns: argparse.Namespace) -> str | None:
    """The flakeref the AppStream lookup evaluates: --nixpkgs, else the config's lock."""
    if ns.nixpkgs:
        return ns.nixpkgs
    from ..configrepo import ConfigRepo, NixinError

    try:
        lock = ConfigRepo.locate(getattr(ns, "config", None)).lock("nixpkgs")
    except (NixinError, OSError, ValueError):
        return None
    return lock.flakeref if lock else None


def _appstream(ns: argparse.Namespace, cache: Cache, results: list[dict[str, Any]]) -> None:
    """Each package row's `appstream` object (appstream.for_package)."""
    from .. import appstream

    rows = [r for r in results if r.get("kind") == "pkg"]
    if not rows:
        return
    flakeref = _locked_nixpkgs(ns) if ns.appstream in ("local", "fetch") else None
    paths = appstream.installed.store_paths() if ns.appstream in ("local", "fetch") else {}
    for r in rows:
        r["appstream"] = appstream.for_package(
            cache, r, mode=ns.appstream, flakeref=flakeref, base=r.get("appstream"), profiles=paths
        )


def _versions(ns: argparse.Namespace) -> dict[str, Any]:
    package = ns.package.strip()
    if not package:
        raise DiscoveryError("usage", "empty package name")
    if ns.limit < 1:
        raise DiscoveryError("usage", "--limit must be at least 1")
    from . import nixhub

    part = nixhub.versions(_cache(ns), package, ns.limit)
    doc = _merge(ns, [("version", part, None)])
    doc.update(package=package, limit=ns.limit, **part.extra)
    return doc


run_search = _guard(_search)
run_info = _guard(_info)
run_versions = _guard(_versions)
