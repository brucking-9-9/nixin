"""This machine's system profile: what runs, which generation that is, the apply history.

No nix and no root: the profile links under /nix/var/nix/profiles are world-readable
symlinks. The paths are module constants so tests can point them at a temp dir.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

CURRENT = "/run/current-system"
PROFILE = "/nix/var/nix/profiles/system"
HISTORY = "installer/history.jsonl"

_GEN = re.compile(r"^system-(\d+)-link$")


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
        "generation": generation_of(path),
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


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
