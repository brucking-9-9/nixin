"""explain — everything nixin knows about one option or package, in one document.

    explain NAME [--kind option|package] [--home USER] [--config DIR] [--channel C]
                 [--offline] [--refresh]

Joins two sides, asked at the same time:

* the config (truth): for an option, every scope that has it (NixOS, each Home Manager
  user, or only `--home USER`) with its type, default, example, value, where it is
  declared (nixpkgs/home-manager paths, linked on GitHub at the locked revision) and
  where the config sets it; for a package, what is installed, which files list it, and
  which enabled `programs.NAME.enable` brings it in.
* upstream (discovery): the option's documentation from search.nixos.org (NixOS) and the
  Home Manager option index, or the package's description, homepage, license and version
  on the channel the config's lock follows (`--channel` overrides; `--offline` skips it).

Either side may be missing (no config, no network); the document says which in
`sources` and `view.notes`. `view` is the display form (title, subtitle, rows of label /
text / url, the description as `about`): the human output is rendered from it, and the
TUI/GUI help pane can show it as is.

`register(subparsers)` is the only thing cli.py calls.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..configrepo import ConfigRepo, NixinError, unknown_host
from ..truth import hosts as truth_hosts
from ..truth import option_infos, package_info
from ..truth import render as truth_render
from ..truth.cache import TruthCache
from . import render

VERB = "explain"
HELP = "everything about one option or package: docs, type, value, where it is set"
KINDS = ("option", "package")
DEFAULT_CHANNEL = "unstable"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.choices.get(VERB)
    if p is None:
        p = sub.add_parser(VERB, help=HELP)
        p.add_argument("args", nargs="*")
    p.add_argument(
        "--kind", choices=KINDS, help="what NAME is (default: an option if it has a dot)"
    )
    p.add_argument("--home", metavar="USER", help="only this Home Manager user's option tree")
    p.add_argument("--config", help="config flake dir (default: $NIXIN_CONFIG or /etc/nixos)")
    p.add_argument("--channel", metavar="C", help="upstream docs from this channel")
    p.add_argument("--offline", action="store_true", help="no network: the config's side only")
    p.add_argument("--refresh", action="store_true", help="ignore cached answers (both sides)")
    p.set_defaults(run=run)


def run(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        doc = explain(ns)
    except NixinError as e:
        return e.doc()
    if not ns.json:
        doc["message"] = render.text(doc["view"])
    return doc


def explain(ns: argparse.Namespace) -> dict[str, Any]:
    if len(ns.args) != 1 or not ns.args[0].strip():
        raise NixinError("usage", "explain NAME  (an option path or a package attribute)")
    name = ns.args[0].strip()
    kind = ns.kind or ("option" if "." in name else "package")
    config = Config.open(ns)
    if kind == "option":
        doc = _option(ns, config, name)
        fallback = ns.kind is None and ns.home is None
        if doc is None and fallback:
            doc = _package(ns, config, name)  # python3Packages.requests has a dot too
        if doc is None:
            raise _nothing(name, "option or package" if fallback else "option", config, ns)
    else:
        doc = _package(ns, config, name)
        if doc is None:
            raise _nothing(name, "package", config, ns)
    return doc


# --- the config side -------------------------------------------------------------------


class Config:
    """The config flake as far as it can be read: `repo` None (and `error` set) without one."""

    def __init__(self, ns: argparse.Namespace) -> None:
        self.ns, self.host = ns, ns.host
        self.repo: ConfigRepo | None = None
        self.cache: TruthCache | None = None
        self.error: dict[str, str] | None = None

    @classmethod
    def open(cls, ns: argparse.Namespace) -> Config:
        c = cls(ns)
        try:
            c.repo = ConfigRepo.locate(ns.config)
            c.cache = TruthCache(c.repo, ns.host, enabled=not ns.refresh)
        except NixinError as e:
            c.error = {"code": e.code, "message": e.message}
        return c

    def lock(self, name: str):
        try:
            return self.repo.lock(name) if self.repo else None
        except (OSError, ValueError, KeyError):
            return None

    def channel(self) -> str:
        """--channel, else the one the config's nixpkgs lock follows, else unstable."""
        if self.ns.channel:
            return self.ns.channel
        from ..discovery.nixos_search import channel_from_ref

        lock = self.lock("nixpkgs")
        implied = channel_from_ref(lock.ref) if lock and lock.ref else None
        return implied or DEFAULT_CHANNEL

    def github(self, loc: dict[str, Any]) -> str | None:
        """A declaration in a GitHub-locked input, linked at the locked revision."""
        if loc.get("source") in (None, "config", "inline", "other") or not loc.get("file"):
            return None
        lock = self.lock(loc["source"])
        if lock is None or lock.type != "github" or not lock.rev:
            return None
        line = f"#L{loc['line']}" if loc.get("line") else ""
        return f"https://github.com/{lock.owner}/{lock.repo}/blob/{lock.rev}/{loc['file']}{line}"

    def ask(self, fn) -> tuple[Any, dict[str, str] | None]:
        """Run a truth query; (result, None) or (None, the error) — never raises."""
        if self.repo is None:
            return None, self.error
        try:
            hosts = truth_hosts(self.repo, refresh=self.ns.refresh)
            if self.host not in hosts:
                raise unknown_host(self.host, hosts)
            return fn(), None
        except NixinError as e:
            return None, {"code": e.code, "message": e.message}

    def doc(self, error: dict[str, str] | None) -> dict[str, Any]:
        return {
            "ok": error is None,
            "config": str(self.repo.path) if self.repo else None,
            "error": error,
            "cache": self.cache.doc() if self.cache else None,
        }


# --- the upstream side -----------------------------------------------------------------


def _upstream(ns: argparse.Namespace, name: str, kinds: tuple[str, ...], channel: str) -> dict:
    """discovery's exact-name lookup for each kind (`pkg`, `opt`, `hm`): {kind: Part
    results}, and per source {channel, rev, cached, error}."""
    out: dict[str, Any] = {"results": {}, "sources": {}}
    if ns.offline:
        return out
    from ..cache import Cache
    from ..discovery import home_manager as hm
    from ..discovery import nixos_search as es

    cache = Cache.from_flags(no_cache=False, refresh=ns.refresh)
    for kind in kinds:
        source = hm.SOURCE if kind == "hm" else es.SOURCE
        try:
            if kind == "hm":
                part = hm.info(cache, name, channel)
            else:
                ch = es.resolve_channel(cache, channel)
                part = (es.info_package if kind == "pkg" else es.info_option)(cache, name, ch)
        except Exception as exc:  # noqa: BLE001 -- a source that fails is a note, not a crash
            code = getattr(exc, "code", "internal")
            message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
            out["sources"].setdefault(source, {"error": {"code": code, "message": message}})
            continue
        out["results"][kind] = part.results
        out["sources"][source] = {
            "channel": part.channel,
            "rev": part.rev,
            "cached": part.cached,
            "error": None,
        }
    return out


def _both(truth_fn, upstream_fn) -> tuple[tuple[Any, Any], dict]:
    from concurrent.futures import ThreadPoolExecutor  # here: 10 ms at import (logging)

    with ThreadPoolExecutor(max_workers=2) as pool:
        t = pool.submit(truth_fn)
        u = pool.submit(upstream_fn)
        return t.result(), u.result()


# --- options ---------------------------------------------------------------------------


def _option(ns: argparse.Namespace, config: Config, name: str) -> dict[str, Any] | None:
    from ..writer.nixtext import parse_attrpath

    try:
        path = parse_attrpath(name)
    except ValueError as e:
        raise NixinError("usage", f"option {name!r}: {e}") from e
    kinds = ("hm",) if ns.home else ("opt", "hm")
    channel = config.channel()
    (scopes, t_err), up = _both(
        lambda: config.ask(
            lambda: option_infos(config.repo, config.host, path, home=ns.home, cache=config.cache)
        ),
        lambda: _upstream(ns, name, kinds, channel),
    )
    if t_err is not None and t_err["code"] == "unknown-user":
        raise NixinError(t_err["code"], t_err["message"])  # --home named no such user
    scopes = [s for s in scopes or [] if s["mode"] in ("exact", "sub")]
    upstream = None
    for kind in kinds:
        rows = up["results"].get(kind) or []
        if rows:
            upstream = {"kind": kind, **rows[0]}
            break
    if not scopes and upstream is None:
        _config_error_if_alone(t_err, up)
        return None
    first = scopes[0] if scopes else {}
    ti = first.get("type_info") or {}
    doc: dict[str, Any] = {
        "ok": True,
        "name": name,
        "kind": "option",
        "type": ti.get("description") or first.get("type") or (upstream or {}).get("type"),
        "type_info": first.get("type_info"),
        "default": _pick(first, upstream, "default"),
        "example": _pick(first, upstream, "example"),
        "description": _description(first, upstream),
        "read_only": bool((upstream or {}).get("read_only")),
        "scopes": [_scope_doc(config, s) for s in scopes],
        "declarations": [],
        "upstream": upstream,
        "sources": {"config": config.doc(t_err), "upstream": _sources_doc(ns, up, channel)},
    }
    if scopes:
        doc["declarations"] = doc["scopes"][0]["declarations"]
    elif upstream:
        doc["declarations"] = [
            {
                "source": "nixpkgs" if upstream["kind"] == "opt" else "home-manager",
                "file": f,
                "url": u,
            }
            for f, u in zip(upstream.get("declarations") or [], _urls(upstream), strict=False)
        ]
    doc["view"] = _option_view(doc, config, t_err, up)
    return doc


def _urls(upstream: dict) -> list[str | None]:
    urls = list(upstream.get("declaration_urls") or [])
    return urls + [None] * (len(upstream.get("declarations") or []) - len(urls))


def _pick(first: dict, upstream: dict | None, field: str) -> str | None:
    """The config's own answer (exact for its lock and host) before upstream's."""
    if first.get(field) is not None:
        return first[field]
    return (upstream or {}).get(field)


def _description(first: dict, upstream: dict | None) -> str | None:
    """Upstream's full text, else what the config's eval knows (its first paragraph)."""
    text = (upstream or {}).get("description") or first.get("sub_description")
    text = text or first.get("description")
    return text.strip() if text else None


def _scope_doc(config: Config, s: dict[str, Any]) -> dict[str, Any]:
    decls = [{**d, "url": config.github(d)} for d in s.get("declarations") or []]
    defs = [
        {k: v for k, v in d.items() if k in ("source", "file", "line", "value", "candidates")}
        for d in s.get("definitions") or []
    ]
    out = {
        "scope": s["scope"],
        "user": s["user"],
        "mode": s["mode"],
        "option": s.get("option"),
        "value": s.get("value"),
        "declarations": decls,
        "definitions": defs,
    }
    for k in ("value_found", "value_error"):
        if k in s:
            out[k] = s[k]
    return out


def _sources_doc(ns: argparse.Namespace, up: dict, channel: str) -> dict[str, Any]:
    if ns.offline:
        return {"ok": False, "skipped": "--offline", "channel": channel, "sources": {}}
    srcs = up.get("sources") or {}
    return {
        "ok": any(v.get("error") is None for v in srcs.values()),
        "channel": channel,
        "sources": srcs,
    }


def _tag(scope: str, user: str | None) -> str:
    return f"[{scope}{' ' + user if user else ''}]"


def _option_view(doc: dict, config: Config, t_err: dict | None, up: dict) -> dict[str, Any]:
    scopes = doc["scopes"]
    trees = {"nixos": "NixOS", "home": "Home Manager"}
    if scopes:
        names = [trees[s["scope"]] + (f" ({s['user']})" if s["user"] else "") for s in scopes]
        subtitle = " and ".join(dict.fromkeys(names)) + " option"
    else:
        subtitle = ("Home Manager" if doc["upstream"]["kind"] == "hm" else "NixOS") + " option"
        subtitle += " (not in this config)" if t_err is None else ""
    rows: list[dict[str, Any]] = []

    def row(label: str, text: Any, url: str | None = None) -> None:
        if text is not None and text != "":
            rows.append({"label": label, "text": str(text), "url": url})

    row("type", doc["type"])
    if doc["read_only"]:
        row("", "read-only")
    row("default", doc["default"])
    row("example", doc["example"])
    for s in scopes:
        value = (
            "<cannot be evaluated>"
            if s.get("value_error")
            else "<not set>"
            if s.get("value_found") is False
            else truth_render.nix_text(s["value"])
        )
        tag = _tag(s["scope"], s["user"])
        row("value", f"{value}  {tag}" if len(scopes) > 1 else value)
        mine = [d for d in s["definitions"] if d.get("source") in ("config", "inline")]
        for d in mine:
            row("set in", truth_render.place(d) + (f"  {tag}" if len(scopes) > 1 else ""))
        if not mine:
            row("set in", "nothing in the config (the default or a module)")
    many = len(scopes) > 1
    shown: set[tuple] = set()
    for s in scopes or [{"declarations": doc["declarations"], "scope": None, "user": None}]:
        for d in s["declarations"]:
            key = (d.get("source"), d.get("file"), d.get("line"))
            if key in shown:
                continue  # the same declaration in two scopes
            shown.add(key)
            where = truth_render.place(d) if "raw" in d else f"{d['source']} {d['file']}"
            tag = f"  {_tag(s['scope'], s['user'])}" if many else ""
            row("declared", where + tag, d.get("url"))
    notes = _notes(config, t_err, doc["sources"]["upstream"])
    return {
        "title": doc["name"],
        "subtitle": subtitle,
        "rows": rows,
        "about": doc["description"],
        "notes": notes,
    }


def _notes(config: Config, t_err: dict | None, upstream: dict) -> list[str]:
    notes = []
    if t_err is not None:
        first = (t_err["message"].strip().splitlines() or [t_err["code"]])[0]
        notes.append(f"config not read ({t_err['code']}): {first}")
    if upstream.get("skipped"):
        notes.append("upstream docs not asked (--offline)")
    for source, s in (upstream.get("sources") or {}).items():
        if s.get("error"):
            first = (s["error"]["message"].strip().splitlines() or [""])[0]
            notes.append(f"{source} not reached ({s['error']['code']}): {first}")
    return notes


# --- packages --------------------------------------------------------------------------


def _package(ns: argparse.Namespace, config: Config, name: str) -> dict[str, Any] | None:
    channel = config.channel()
    (truth, t_err), up = _both(
        lambda: config.ask(
            lambda: package_info(config.repo, config.host, name, cache=config.cache)
        ),
        lambda: _upstream(ns, name, ("pkg",), channel),
    )
    rows = up["results"].get("pkg") or []
    upstream = rows[0] if rows else None
    truth = truth or {}
    installed = truth.get("installed") or []
    if upstream is None and not installed and not truth.get("declared"):
        _config_error_if_alone(t_err, up)
        return None
    es = up["sources"].get("search.nixos.org") or {}
    doc: dict[str, Any] = {
        "ok": True,
        "name": name,
        "kind": "package",
        "description": (upstream or {}).get("description"),
        "homepage": (upstream or {}).get("homepage"),
        "license": (upstream or {}).get("license") or [],
        "programs": (upstream or {}).get("programs") or [],
        "installed": installed,
        "channel_version": (upstream or {}).get("version"),
        "declared": truth.get("declared") or [],
        "enabled_by": truth.get("programs") or [],
        "mentions": (truth.get("mentions") or [])[:5],
        "position": (upstream or {}).get("position"),
        "source_url": _position_url((upstream or {}).get("position"), es.get("rev")),
        "upstream": upstream,
        "sources": {"config": config.doc(t_err), "upstream": _sources_doc(ns, up, channel)},
    }
    doc["view"] = _package_view(doc, config, t_err, channel)
    return doc


def _position_url(position: str | None, rev: str | None) -> str | None:
    if not position or not rev:
        return None
    file, _, line = position.rpartition(":")
    if not line.isdigit():
        file, line = position, ""
    return f"https://github.com/NixOS/nixpkgs/blob/{rev}/{file}" + (f"#L{line}" if line else "")


def _package_view(doc: dict, config: Config, t_err: dict | None, channel: str) -> dict:
    rows: list[dict[str, Any]] = []

    def row(label: str, text: Any, url: str | None = None) -> None:
        if text is not None and text != "":
            rows.append({"label": label, "text": str(text), "url": url})

    if doc["installed"]:
        for p in doc["installed"]:
            row("installed", f"{p.get('version') or p['name']}  {_tag(p['scope'], p['user'])}")
    elif t_err is None:
        row("installed", f"no (not on {config.host})")
    if doc["channel_version"]:
        row("channel", f"{doc['channel_version']} on {doc['sources']['upstream']['channel']}")
    for d in doc["declared"]:
        tag = _tag(d["scope"], d["user"])
        row("listed in", f"{truth_render.place(d)} ({d['option']})  {tag}")
    for p in doc["enabled_by"]:
        for d in p.get("definitions") or []:
            if d.get("source") in ("config", "inline"):
                text = f"{p['option']} = {truth_render.nix_text(d.get('value'))}"
                row(
                    "via",
                    f"{text} in {truth_render.place(d)}  {_tag(p['scope'], p['user'])}",
                )
    if doc["installed"] and not doc["declared"] and not doc["enabled_by"]:
        row("listed in", "nowhere nixin can see (a module adds it)")
    row("programs", ", ".join(doc["programs"]))
    homepage = doc["homepage"]
    if isinstance(homepage, list):
        homepage = homepage[0] if homepage else None
    row("homepage", homepage, homepage)
    row("license", ", ".join(doc["license"]))
    row("source", doc["position"], doc["source_url"])
    return {
        "title": doc["name"],
        "subtitle": "package"
        + (" (not installed)" if not doc["installed"] and t_err is None else ""),
        "rows": rows,
        "about": doc["description"],
        "notes": _notes(config, t_err, doc["sources"]["upstream"]),
    }


# --- nothing found -----------------------------------------------------------------------


def _config_error_if_alone(t_err: dict | None, up: dict) -> None:
    """Nothing found: the caller's `not-found` when a side answered; when the config could
    not be read and no upstream source answered either, raise the config's error."""
    answered = any(v.get("error") is None for v in (up.get("sources") or {}).values())
    if t_err is not None and not answered:
        raise NixinError(t_err["code"], t_err["message"])


def _nothing(name: str, kind: str, config: Config, ns: argparse.Namespace) -> NixinError:
    where = f"{config.host}'s config" if config.repo else "the config (none read)"
    up = "upstream skipped (--offline)" if ns.offline else "nor upstream"
    msg = f"no {kind} named {name!r} in {where}, {up}"
    if config.error:
        msg += f"; config: {config.error['message']}"
    return NixinError("not-found", msg)
