"""On-disk JSON cache for discovery results.

Layout: `$XDG_CACHE_HOME/nixin/<source>/<sha1(key)>.json`, one entry per file:

    {"key": "<the key>", "stored": <epoch seconds>, "payload": <anything JSON>}

`source` is a short slug (`es`, `hm`, `nixhub`, ...), `key` is whatever
identifies the request (usually the URL plus the serialized body). Each caller
chooses its own TTL because the sources move at different speeds: a channel's
alias list changes every few hours, a nixpkgs rev never does.

Modes, from `--no-cache` / `--refresh`:
    normal   read fresh entries, write new ones
    refresh  never read, still write (forces a live fetch and updates the cache)
    off      neither read nor write

Writes are best-effort: a read-only or missing cache dir (the `nix build`
sandbox has `HOME=/homeless-shelter`) must never turn a good answer into an
error, so every OSError on the way to disk is swallowed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MODES = ("normal", "refresh", "off")


def default_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "nixin"


class Cache:
    def __init__(self, root: Path | None = None, mode: str = "normal"):
        if mode not in MODES:
            raise ValueError(f"cache mode must be one of {MODES}, not {mode!r}")
        self.root = Path(root) if root is not None else default_root()
        self.mode = mode

    @classmethod
    def from_flags(cls, *, no_cache: bool = False, refresh: bool = False) -> Cache:
        return cls(mode="off" if no_cache else "refresh" if refresh else "normal")

    def path(self, source: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:32]
        return self.root / source / f"{digest}.json"

    def get(self, source: str, key: str, ttl: float) -> Any | None:
        """The cached payload if it exists and is younger than `ttl` seconds, else None."""
        if self.mode != "normal":
            return None
        path = self.path(source, key)
        try:
            with path.open(encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("key") != key or "payload" not in entry:
            return None
        stored = entry.get("stored", 0)
        if not isinstance(stored, int | float) or time.time() - stored > ttl:
            return None
        return entry["payload"]

    def put(self, source: str, key: str, payload: Any) -> None:
        if self.mode == "off":
            return
        path = self.path(source, key)
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump({"key": key, "stored": time.time(), "payload": payload}, fh)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def fetch(
        self, source: str, key: str, ttl: float, fetch: Callable[[], Any]
    ) -> tuple[Any, bool]:
        """`(payload, cached)`: the cached payload when fresh, otherwise `fetch()` stored."""
        hit = self.get(source, key, ttl)
        if hit is not None:
            return hit, True
        payload = fetch()
        self.put(source, key, payload)
        return payload, False


# TTLs in seconds, per source. Named here so README and code agree.
HOUR = 3600.0
DAY = 24 * HOUR
TTL = {
    "es-aliases": 6 * HOUR,  # channel generations roll every few hours at most
    "es-search": 1 * HOUR,  # a channel index is rebuilt a few times a day
    "hm-index": 1 * DAY,  # extranix regenerates its option JSON daily
    "nixhub": 6 * HOUR,
    "noogle": 1 * DAY,
    "local": 30 * DAY,  # keyed by resolved rev, which never changes
}
