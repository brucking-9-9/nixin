"""AppStream layer: what a package looks like as an application.

Borrowed from nix-software-center and GNOME Software: a package's name, summary,
description, categories, icon, screenshots, license and desktop entry, from the AppStream
metainfo and desktop files its outputs ship.

    parse      metainfo XML, desktop entries and icons, read from an output tree (pure)
    store      which output to read: an installed one, the locked nixpkgs' in the store,
               or (`--appstream fetch`) one substituted when that is cheap
    installed  the applications the machine's profiles list (desktop entries, no nix)
    hint       the fields search.nixos.org's package documents carry (desktop entries,
               categories, icons, screenshots), the fallback when the store has nothing
    curated    the Browse categories and a shipped list of well-known apps per category
    images     screenshots and icons for the GUI, fetched lazily, size-capped, cached

Entry points: `for_package` (the `appstream` object of an `info` package row), `mark`
(`search --appstream`) and the `browse` verb (registered here).
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from ..cache import Cache
from . import curated, hint, installed, parse, store

MODES = ("local", "fetch", "hint", "off")
DEFAULT_MODE = "local"
VERBS = {"browse": "applications by category: installed, well-known, and search.nixos.org's"}
BROWSE_LIMIT = 30


# --- one package (`info`) --------------------------------------------------------------------


def _hints(rec: dict[str, Any]) -> tuple[str, ...]:
    attr = str(rec.get("name") or "")
    out = (str(rec.get("pname") or ""), str(rec.get("main_program") or ""), attr.split(".")[-1])
    return tuple(dict.fromkeys(h for h in out if h))


def merge(found: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """A store record over a hint: the store wins where it has a value; the hint adds the
    icon URL, search.nixos.org's screenshot copies, and what the files lack."""
    out = hint.empty()
    for k in ("id", "name", "summary", "description", "license", "launchable", "homepage"):
        out[k] = found.get(k) or base.get(k)
    out["categories"] = found.get("categories") or base.get("categories") or []
    out["desktop_ids"] = found.get("desktop_ids") or []
    out["files"] = found.get("files") or []
    out["gui"] = found.get("gui") if found.get("gui") is not None else base.get("gui")
    icon, hicon = found.get("icon"), base.get("icon")
    if icon or hicon:
        out["icon"] = {
            "name": (icon or {}).get("name") or (hicon or {}).get("name"),
            "path": (icon or {}).get("path"),
            "url": (hicon or {}).get("url"),
        }
    copies = {s["url"]: s for s in base.get("screenshots") or []}
    shots = []
    for s in found.get("screenshots") or []:
        h = copies.get(s["url"], {})
        shots.append({**s, "thumbnail": h.get("thumbnail"), "large": h.get("large")})
    out["screenshots"] = shots or list(base.get("screenshots") or [])
    return out


def for_package(
    cache: Cache,
    rec: dict[str, Any],
    *,
    mode: str = DEFAULT_MODE,
    flakeref: str | None = None,
    base: dict[str, Any] | None = None,
    profiles: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """The `appstream` object for one package row of `info`. `base` is search.nixos.org's
    hint for it (None for `--nixpkgs` rows); `profiles` the installed store paths
    (installed.store_paths(), for tests)."""
    attr = str(rec.get("name") or "")
    base = base if base is not None else hint.from_es({"package_attr_name": attr, **_es_like(rec)})
    if mode == "hint":
        return base
    hints = _hints(rec)
    pname = str(rec.get("pname") or attr.split(".")[-1])
    notes: list[str] = []
    found: tuple[str, str, dict[str, Any], tuple[str, ...]] | None = (
        None  # source, path, record, owners
    )
    for path, owners in installed.find(pname, profiles, rec.get("version")):
        r = store.read(cache, path, hints)
        if r is not None:
            found = ("installed", path, r, owners)
            break
    read_nothing = False
    if found is None and flakeref:
        try:
            outs = store.out_paths(cache, flakeref, attr)
        except store.StoreError as exc:
            notes.append(f"the out paths: {exc.code}: {exc}")
            outs = {}
        paths = store.ordered_outputs(outs)
        present = [p for p in paths if os.path.isdir(p)]
        got = store.read_first(cache, present, hints)
        if got is None and outs and not present and mode == "fetch":
            fetched, what = store.fetch_cheaply(flakeref, attr)
            notes.append(what)
            present = [p for p in paths if os.path.isdir(p)] if fetched else []
            got = store.read_first(cache, present, hints)
        if got is not None:
            found = ("store", got[0], got[1], ())
        elif outs and not present and mode != "fetch":  # fetch said why it did not
            notes.append("not in the local store (--appstream fetch substitutes it when cheap)")
        elif present:
            read_nothing = paths[0] in present  # `out` was there and has nothing
            notes.append("its outputs in the store ship no desktop entry or AppStream file")
    elif found is None:
        notes.append("no locked nixpkgs to look in (no flake.lock; pass --config or --nixpkgs)")
    if found is None:
        out = dict(base)
        if read_nothing and out.get("gui") is None:
            out["gui"] = False
    else:
        source, path, record, owners = found
        out = merge(record, base)
        out.update(source=source, store_path=path)
        if owners:
            out["installed_for"] = list(owners)
    out["notes"] = [*notes, *(n for n in base.get("notes") or [] if found is None)]
    return out


def _es_like(rec: dict[str, Any]) -> dict[str, Any]:
    """A discovery row's fields under search.nixos.org's names (for hint.from_es on rows
    that did not come from there: `--nixpkgs`)."""
    return {
        "package_pname": rec.get("pname"),
        "package_programs": rec.get("programs") or [],
        "package_mainProgram": rec.get("main_program"),
    }


def mark(rows: list[dict[str, Any]]) -> None:
    """`search --appstream`: every package row gets an `appstream` object; rows that came
    with search.nixos.org's hint keep it, the others (`--nixpkgs`) get the shipped list's."""
    for r in rows:
        if r.get("kind") == "pkg" and "appstream" not in r:
            r["appstream"] = hint.from_es({"package_attr_name": r.get("name"), **_es_like(r)})


# --- the `browse` verb -------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> set[str]:
    from . import browse

    sp = sub.add_parser("browse", help=VERBS["browse"])
    sp.add_argument("category", nargs="?", help="a category (Game, Graphics, …); none: the list")
    sp.add_argument("--channel", metavar="C", help="unstable (default), stable, NN.MM")
    sp.add_argument("--limit", type=int, default=BROWSE_LIMIT, metavar="N")
    sp.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    sp.add_argument("--refresh", action="store_true", help="ignore cached answers, fetch and store")
    sp.set_defaults(run=browse.run)
    return set(VERBS)


__all__ = ["MODES", "curated", "for_package", "hint", "installed", "mark", "parse", "register"]
