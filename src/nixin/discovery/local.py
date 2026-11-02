"""Exact-rev fallback: ask the host's own `nix` about one nixpkgs.

search.nixos.org indexes channel-latest, so it can name a version your
flake.lock does not have yet. `--nixpkgs FLAKEREF` (typically the locked rev,
`github:nixos/nixpkgs/<rev>`, already in the store) answers from that exact
tree instead:

    nix search --json FLAKEREF#legacyPackages.<system> QUERY     (search --kind pkg)
    nix eval --json FLAKEREF#legacyPackages.<system>.ATTR --apply ...   (info --kind pkg)

`nix search` returns only pname/version/description. The first search on a
rev builds nix's eval cache and takes about a minute; later queries on the
same rev take a few seconds, and nixin caches answers for 30 days keyed by
the resolved rev. `nix eval` of one package takes well under a second.

Records say `source: "local-nix"` and `channel: <the flakeref as given>`, with
`rev` from `nix flake metadata`. Options are not evaluated here: NixOS option
values need the config flake, which is the truth layer's job.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from typing import Any

from ..cache import TTL, Cache
from . import DiscoveryError, Part

SOURCE = "local-nix"
NIX_FLAGS = ["--extra-experimental-features", "nix-command flakes"]
SLOW_SECONDS = 15.0
_STORE_SOURCE = "-source/"

# One nix eval for everything `info` shows; tolerant of packages whose meta
# lacks fields or whose license is a single set or a string.
APPLY = """p:
let
  lic = p.meta.license or [ ];
  lics = if builtins.isList lic then lic else [ lic ];
  spdx = l: if builtins.isString l then l else (l.spdxId or l.shortName or "unknown");
in
{
  pname = p.pname or null;
  version = p.version or null;
  outputs = p.outputs or [ ];
  description = p.meta.description or null;
  homepage = p.meta.homepage or null;
  license = map spdx lics;
  unfree = p.meta.unfree or null;
  broken = p.meta.broken or false;
  platforms = p.meta.platforms or [ ];
  mainProgram = p.meta.mainProgram or null;
  position = p.meta.position or null;
}"""
# Part of the eval cache key: a changed APPLY must not be answered from 30-day-old entries.
_APPLY_DIGEST = hashlib.sha1(APPLY.encode()).hexdigest()[:8]


def current_system() -> str:
    machine = platform.machine()
    return {
        "x86_64": "x86_64-linux",
        "AMD64": "x86_64-linux",
        "aarch64": "aarch64-linux",
        "arm64": "aarch64-linux",
    }.get(machine, f"{machine}-linux")


def _nix(args: list[str], timeout: float) -> str:
    # stdin is closed so nix can never stop to ask (e.g. about a flake's nixConfig)
    try:
        proc = subprocess.run(
            ["nix", *NIX_FLAGS, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DiscoveryError("nix-missing", "nix is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DiscoveryError(
            "nix-timeout", f"nix {args[0]} took longer than {timeout:.0f}s"
        ) from exc
    if proc.returncode != 0:
        tail = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()][-3:]
        raise DiscoveryError(
            "nix-failed", f"nix {args[0]} failed: " + " ".join(ln.strip() for ln in tail)
        )
    return proc.stdout


def _json(out: str, what: str) -> Any:
    try:
        return json.loads(out)
    except ValueError as exc:
        raise DiscoveryError("nix-failed", f"nix {what} did not print JSON") from exc


def _is_missing_attr(message: str) -> bool:
    low = message.lower()
    return "does not provide attribute" in low or "attribute" in low and "missing" in low


def resolve(cache: Cache, flakeref: str) -> dict[str, Any]:
    """`nix flake metadata` for the ref: rev, store path, lastModified (branch refs move: 1 h TTL)."""

    def fetch() -> dict[str, Any]:
        data = _json(_nix(["flake", "metadata", "--json", flakeref], timeout=120), "flake metadata")
        if not isinstance(data, dict):
            raise DiscoveryError("nix-failed", "nix flake metadata did not print an object")
        return {
            "rev": data.get("revision"),
            "path": data.get("path"),
            "url": data.get("url"),
            "last_modified": data.get("lastModified"),
        }

    meta, _cached = cache.fetch("local", f"metadata:{flakeref}", TTL["es-search"], fetch)
    return meta


def _strip_attr(attr: str, system: str) -> str:
    prefix = f"legacyPackages.{system}."
    return attr.removeprefix(prefix)


def _strip_store(position: str | None) -> str | None:
    if not position:
        return None
    if position.startswith("/nix/store/") and _STORE_SOURCE in position:
        return position.split(_STORE_SOURCE, 1)[1]
    return position


def search_record(
    attr: str, info: dict[str, Any], flakeref: str, rev: str | None
) -> dict[str, Any]:
    return {
        "kind": "pkg",
        "source": SOURCE,
        "channel": flakeref,
        "rev": rev,
        "name": attr,
        "pname": info.get("pname") or None,
        "version": info.get("version") or None,
        "description": info.get("description") or None,
        "homepage": None,
        "license": [],
        "unfree": None,
        "programs": [],
        "main_program": None,
        "platforms": [],
        "outputs": [],
        "position": None,
    }


def _rank(query: str):
    q = query.lower()

    def key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, int, str]:
        attr = item[0]
        low = attr.lower()
        last = low.rsplit(".", 1)[-1]
        tier = 0 if last == q else 1 if last.startswith(q) else 2 if q in last else 3
        return (tier, attr.count("."), len(attr), attr)

    return key


def search(cache: Cache, flakeref: str, query: str, limit: int) -> Part:
    system = current_system()
    meta = resolve(cache, flakeref)
    rev = meta.get("rev")
    key = f"search:{rev or meta.get('path')}:{system}:{query}"

    def fetch() -> dict[str, Any]:
        started = time.monotonic()
        out = _nix(
            ["search", "--json", f"{flakeref}#legacyPackages.{system}", "--", query], timeout=900
        )
        hits = _json(out, "search") if out.strip() else {}
        if not isinstance(hits, dict):
            raise DiscoveryError("nix-failed", "nix search did not print an object")
        return {"hits": hits, "took": round(time.monotonic() - started, 1)}

    payload, cached = cache.fetch("local", key, TTL["local"], fetch)
    hits = {
        _strip_attr(a, system): v for a, v in payload.get("hits", {}).items() if isinstance(v, dict)
    }
    ranked = sorted(hits.items(), key=_rank(query))
    results = [search_record(a, v, flakeref, rev) for a, v in ranked[:limit]]
    notes = []
    took = payload.get("took")
    if not cached and isinstance(took, int | float) and took > SLOW_SECONDS:
        notes.append(
            f"nix search on this rev took {took:.0f}s (first run builds nix's eval cache); "
            "later queries on the same rev take a few seconds"
        )
    part = Part(SOURCE, flakeref, rev, cached, results, notes)
    part.extra["system"] = system
    part.extra["total"] = len(hits)
    return part


def info_record(attr: str, data: dict[str, Any], flakeref: str, rev: str | None) -> dict[str, Any]:
    main = data.get("mainProgram") or None
    return {
        "kind": "pkg",
        "source": SOURCE,
        "channel": flakeref,
        "rev": rev,
        "name": attr,
        "pname": data.get("pname") or None,
        "version": data.get("version") or None,
        "description": data.get("description") or None,
        "homepage": data.get("homepage") if isinstance(data.get("homepage"), str) else None,
        "license": [str(x) for x in data.get("license") or []],
        "unfree": data.get("unfree"),
        "broken": bool(data.get("broken", False)),
        "programs": [main] if main else [],
        "main_program": main,
        "platforms": [str(p) for p in data.get("platforms") or []],
        "outputs": [str(o) for o in data.get("outputs") or []],
        "position": _strip_store(data.get("position")),
        "matched_by": "attr",
    }


def info(cache: Cache, flakeref: str, attr: str) -> Part:
    system = current_system()
    meta = resolve(cache, flakeref)
    rev = meta.get("rev")
    key = f"eval:{rev or meta.get('path')}:{system}:{attr}:{_APPLY_DIGEST}"

    def fetch() -> dict[str, Any] | None:
        try:
            out = _nix(
                ["eval", "--json", f"{flakeref}#legacyPackages.{system}.{attr}", "--apply", APPLY],
                timeout=300,
            )
        except DiscoveryError as exc:
            if exc.code == "nix-failed" and _is_missing_attr(exc.message):
                return None
            raise
        data = _json(out, "eval")
        return data if isinstance(data, dict) else None

    data, cached = cache.fetch("local", key, TTL["local"], fetch)
    results = [info_record(attr, data, flakeref, rev)] if data else []
    part = Part(SOURCE, flakeref, rev, cached, results)
    part.extra["system"] = system
    return part
