"""This machine's system profile: what runs, which generation that is, the apply history.

No nix and no root: the profile links under /nix/var/nix/profiles are world-readable
symlinks, and a generation's toplevel names its kernel, initrd and NixOS version. The
paths are module constants so tests can point them at a temp dir.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

CURRENT = "/run/current-system"
BOOTED = "/run/booted-system"
PROFILE = "/nix/var/nix/profiles/system"
HISTORY = "installer/history.jsonl"

_GEN = re.compile(r"^system-(\d+)-link$")
_KERNEL_DIR = re.compile(r"^[0-9a-z]{32}-linux-(\d.*)$")


def realpath(p: str) -> str | None:
    return os.path.realpath(p) if os.path.lexists(p) else None


def generations() -> list[tuple[int, str]]:
    """(number, store path) of every system generation, oldest first."""
    base = Path(PROFILE).parent
    out = []
    try:
        names = os.listdir(base)
    except OSError:
        return []
    for name in names:
        m = _GEN.match(name)
        if m:
            target = realpath(str(base / name))
            if target:
                out.append((int(m.group(1)), target))
    return sorted(out)


def link(n: int) -> Path:
    return Path(PROFILE).parent / f"system-{n}-link"


def link_time(n: int) -> str | None:
    """When generation n was made: its link's own mtime (as nixos-rebuild list-generations)."""
    try:
        t = os.lstat(link(n)).st_mtime
    except OSError:
        return None
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_generation(
    gens: list[tuple[int, str]], path: str | None, since: str | None = None
) -> int | None:
    """Which generation `path` is. Two generations can share a path (a rollback, a revert
    that rebuilds an older tree). Without `since`: the profile's own when it points
    there, else the newest that does. With `since` (a /run link, e.g. /run/booted-system):
    the newest made before that link was."""
    if not path:
        return None
    hits = [n for n, p in gens if p == path]
    if since is None:
        prof = profile_generation()
        return prof if prof in hits else (hits[-1] if hits else None)
    try:
        at = os.lstat(since).st_mtime
    except OSError:
        at = None
    if at is not None:
        before = [n for n in hits if _mtime(link(n)) <= at]
        if before:
            return before[-1]
    return hits[-1] if hits else None


def _mtime(p: Path) -> float:
    try:
        return os.lstat(p).st_mtime
    except OSError:
        return float("inf")


def kernel_version(out: str | None) -> str | None:
    """The kernel release a toplevel boots (`uname -r`): the one directory under
    kernel-modules/lib/modules, else the version in the kernel's store path name."""
    if not out:
        return None
    try:
        names = sorted(os.listdir(Path(out) / "kernel-modules" / "lib" / "modules"))
    except OSError:
        names = []
    if names:
        return names[0]
    k = realpath(str(Path(out) / "kernel"))
    m = _KERNEL_DIR.match(Path(k).parent.name) if k else None
    return m.group(1) if m else None


def nixos_version(out: str | None) -> str | None:
    if not out:
        return None
    try:
        return (Path(out) / "nixos-version").read_text().strip() or None
    except OSError:
        return None


def generation_of(store_path: str | None) -> int | None:
    """The newest generation whose link points at store_path."""
    if not store_path:
        return None
    hits = [n for n, p in generations() if p == store_path]
    return hits[-1] if hits else None


def profile_generation() -> int | None:
    try:
        m = _GEN.match(os.readlink(PROFILE))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def running() -> dict[str, Any]:
    path = realpath(CURRENT)
    return {
        "store_path": path,
        "generation": pick_generation(generations(), path),
        "profile_generation": profile_generation(),
        "profile": realpath(PROFILE),
    }


def append_history(workspace: Path, line: dict[str, Any]) -> str:
    """One JSON line in <workspace>/installer/history.jsonl; returns the repo-relative path."""
    p = workspace / HISTORY
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")
    return HISTORY


def read_history(workspace: Path) -> list[dict[str, Any]]:
    """Every well-formed line of <workspace>/installer/history.jsonl, in file order."""
    try:
        text = (workspace / HISTORY).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict) and isinstance(line.get("out"), str):
            out.append(line)
    return out


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
