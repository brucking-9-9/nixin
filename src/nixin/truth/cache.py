"""On-disk cache for eval results, under $XDG_CACHE_HOME/nixin/truth/.

Files are `<host>-<key>-<name>.json`; the key comes from ConfigRepo.cache_key(), i.e. nix's
flake fingerprint (source tree as nix sees it + lock) plus a per-artifact salt, so any
tracked edit, lock change or helper change misses. Everything is best effort: a cache
that cannot be written (nix build sandbox, read-only HOME) never fails a verb.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..configrepo import ConfigRepo

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "nixin" / "truth"


class TruthCache:
    """`enabled=False` (--refresh) skips reads; fresh results are still written."""

    def __init__(self, repo: ConfigRepo, host: str, *, enabled: bool = True) -> None:
        self.repo = repo
        self.host = host
        self.enabled = enabled
        self.dir = cache_dir()
        self.hits: list[str] = []
        self.misses: list[str] = []

    def _key(self, salt: str) -> str:
        return self.repo.cache_key(self.host, salt)

    def path(self, name: str, salt: str = "") -> Path:
        host = _SAFE.sub("_", self.host)
        return self.dir / f"{host}-{self._key(salt)}-{name}.json"

    def get(self, name: str, salt: str = "", *, peek: bool = False) -> Any | None:
        """`peek`: an optional read that is not an eval avoided; a miss is not recorded."""
        if not self.enabled:
            if not peek:
                self.misses.append(name)
            return None
        p = self.path(name, salt)
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            if not peek:
                self.misses.append(name)
            return None
        self.hits.append(name)
        return doc

    def put(self, name: str, doc: Any, salt: str = "") -> bool:
        p = self.path(name, salt)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, separators=(",", ":")))
            os.replace(tmp, p)
        except OSError:
            return False
        self.prune(name, keep=p)
        return True

    def prune(self, name: str, keep: Path) -> None:
        """Drop this host's older copies of `name` (other keys): one generation is enough."""
        host = _SAFE.sub("_", self.host)
        try:
            for old in self.dir.glob(f"{host}-*-{name}.json"):
                if old != keep:
                    old.unlink(missing_ok=True)
        except OSError:
            pass

    def doc(self) -> dict[str, Any]:
        return {
            "dir": str(self.dir),
            "enabled": self.enabled,
            "hits": sorted(set(self.hits)),
            "misses": sorted(set(self.misses)),
        }
