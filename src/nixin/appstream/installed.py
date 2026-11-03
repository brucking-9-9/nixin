"""The applications installed on this machine, from the profiles' desktop entries.

No nix, no eval: the system profile (`/run/current-system/sw`), every per-user profile
(`/etc/profiles/per-user/*`, where Home Manager's NixOS module installs), `~/.nix-profile`
and `~/.local/state/nix/profile` each have `share/applications/*.desktop`, symlinks into
the store paths that ship them. Reading those gives the installed GUI applications with
their categories, and the store path each one comes from, which `info` then reads in full.
A machine without these directories (the build sandbox, another distro) has none.

A store path's name is `<pname>-<version>`; the attribute that installed it is not
recoverable (as for `list`), so an application is matched to a package by pname.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import parse

STORE = "/nix/store/"
SYSTEM_PROFILE = "/run/current-system/sw"
PER_USER = "/etc/profiles/per-user"
_HASH = re.compile(r"^[0-9a-z]{32}-")


@dataclass(frozen=True)
class App:
    desktop_id: str
    name: str
    summary: str | None
    categories: tuple[str, ...]
    icon: str | None
    store_path: str
    pname: str
    version: str
    owners: tuple[str, ...] = field(default=())  # "system", or a user name


def parse_store_name(name: str) -> tuple[str, str]:
    """`konsole-26.08.2` → (`konsole`, `26.08.2`), nix's parseDrvName rule: the version
    starts at the first `-` followed by a digit."""
    m = re.search(r"-(?=\d)", name)
    return (name[: m.start()], name[m.end() :]) if m else (name, "")


def store_root(path: str) -> str | None:
    """`/nix/store/<hash>-name/share/…` → `/nix/store/<hash>-name`."""
    if not path.startswith(STORE):
        return None
    top = path[len(STORE) :].split("/", 1)[0]
    return STORE + top if _HASH.match(top) else None


def profiles(home: str | None = None) -> list[tuple[str, Path]]:
    """(owner, profile directory) for every profile that exists here."""
    home = home or os.path.expanduser("~")
    user = os.environ.get("USER") or Path(home).name
    out: list[tuple[str, Path]] = [("system", Path(SYSTEM_PROFILE))]
    try:
        users = sorted(Path(PER_USER).iterdir())
    except OSError:
        users = []
    out += [(p.name, p) for p in users]
    out += [(user, Path(home) / ".nix-profile"), (user, Path(home, ".local/state/nix/profile"))]
    return [(o, p) for o, p in out if p.is_dir()]


def scan(roots: list[tuple[str, Path]] | None = None) -> list[App]:
    """Every listed desktop entry in the profiles, one App per (store path, desktop id),
    with every profile that has it as an owner. Sorted by name."""
    found: dict[tuple[str, str], App] = {}
    for owner, profile in roots if roots is not None else profiles():
        apps = profile / "share" / "applications"
        try:
            files = sorted(apps.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            try:
                real = os.path.realpath(f)
                root = store_root(real)
                if root is None or os.path.getsize(real) > parse.MAX_FILE:
                    continue
                entry = parse.parse_desktop(
                    Path(real).read_text(encoding="utf-8", errors="replace")
                )
            except (OSError, ValueError):
                continue
            if not parse.shown(entry):
                continue
            key = (root, f.name)
            if key in found:
                old = found[key]
                if owner not in old.owners:
                    found[key] = replace(old, owners=(*old.owners, owner))
                continue
            pname, version = parse_store_name(root[len(STORE) + 33 :])
            found[key] = App(
                desktop_id=f.name,
                name=entry.get("name") or f.name.removesuffix(".desktop"),
                summary=entry.get("comment") or entry.get("generic_name"),
                categories=tuple(entry.get("categories") or ()),
                icon=entry.get("icon"),
                store_path=root,
                pname=pname,
                version=version,
                owners=(owner,),
            )
    return sorted(found.values(), key=lambda a: (a.name.lower(), a.desktop_id))


def store_paths(roots: list[tuple[str, Path]] | None = None) -> dict[str, tuple[str, ...]]:
    """Every store path a profile links an AppStream file or a desktop entry from, with
    its owners: also the components without a desktop entry (MangoHud's metainfo)."""
    out: dict[str, tuple[str, ...]] = {}
    for owner, profile in roots if roots is not None else profiles():
        for sub in ("share/applications", "share/metainfo", "share/appdata"):
            d = profile / sub
            try:
                files = list(d.iterdir())
            except OSError:
                continue
            for f in files:
                root = store_root(os.path.realpath(f))
                if root is not None and owner not in out.get(root, ()):
                    out[root] = (*out.get(root, ()), owner)
    return out


def find(
    pname: str, paths: dict[str, tuple[str, ...]] | None = None, version: str | None = None
) -> list[tuple[str, tuple[str, ...]]]:
    """The installed store paths whose name is `pname` (or `pname-unwrapped`, where a
    wrapper's desktop entry and metainfo usually live), with their owners; the one with
    `version` first, then the others newest first."""
    paths = store_paths() if paths is None else paths
    wanted = {pname, f"{pname}-unwrapped"}
    hits = []
    for root, owners in paths.items():
        name, ver = parse_store_name(root[len(STORE) + 33 :])
        if name in wanted:
            hits.append((root, owners, name == pname, ver))

    def key(h: tuple[str, tuple[str, ...], bool, str]) -> tuple:
        _root, _owners, exact, ver = h
        parts = tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.-]", ver))
        return (ver == version, exact, parts)

    return [(r, o) for r, o, _e, _v in sorted(hits, key=key, reverse=True)]
