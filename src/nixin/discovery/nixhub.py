"""NixHub (nixhub.io, by Jetify) version history: which nixpkgs commit shipped which version.

`GET https://search.devbox.sh/v1/pkg?name=<pkg>` answers a list of releases,
newest first:

    [{"name": "firefox", "version": "157.0.1", "commit_hash": "<40-hex>", "last_updated": <epoch>,
      "summary": "...", "homepage": "...", "license": "MPL-2.0", "platforms": [...],
      "systems": {"x86_64-linux": {"attr_paths": ["firefox"], "programs": ["firefox"],
                                   "commit_hash": "<per-system hex>", ...}, ...}}, ...]

The release-level `commit_hash` is the one reported as `rev`: it is the commit
NixHub associates with the version; the per-system hashes can differ (they
are where that system's build was last seen). `attr_paths`/`programs` are read
from x86_64-linux when present, else the first system. Package names are
NixHub's own (flat attribute names); 400/404 mean "unknown package".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ..cache import TTL, Cache
from ..http import NetworkError, get_json
from . import DiscoveryError, Part

SOURCE = "nixhub.io"
API = "https://search.devbox.sh"

_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_PREFERRED_SYSTEMS = ("x86_64-linux", "aarch64-linux", "aarch64-darwin", "x86_64-darwin")


def _date(value: Any) -> str | None:
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%d")
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _system(release: dict[str, Any]) -> dict[str, Any]:
    systems = release.get("systems")
    if not isinstance(systems, dict) or not systems:
        return {}
    for name in _PREFERRED_SYSTEMS:
        if isinstance(systems.get(name), dict):
            return systems[name]
    first = next(iter(systems.values()))
    return first if isinstance(first, dict) else {}


def _strs(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def record(release: dict[str, Any]) -> dict[str, Any]:
    commit = str(release.get("commit_hash") or "")
    system = _system(release)
    attrs = _strs(system.get("attr_paths"))
    return {
        "kind": "version",
        "source": SOURCE,
        "channel": None,
        "rev": commit.lower() if _HEX40.match(commit) else None,
        "name": release.get("name") or None,
        "version": str(release.get("version") or ""),
        "date": _date(release.get("last_updated")),
        "attr": attrs[0] if attrs else None,
        "platforms": sorted(_strs(release.get("platforms"))),
        "programs": _strs(system.get("programs")),
    }


def versions(cache: Cache, name: str, limit: int) -> Part:
    url = f"{API}/v1/pkg"
    key = f"{url}?name={name}"

    def fetch() -> list[dict[str, Any]]:
        try:
            data = get_json(url, params={"name": name})
        except NetworkError as exc:
            if exc.status in (400, 404):
                raise DiscoveryError("not-found", f"{SOURCE} knows no package {name!r}") from exc
            raise
        if not isinstance(data, list):
            raise NetworkError(f"{url} returned no release list")
        return [r for r in data if isinstance(r, dict)]

    releases, cached = cache.fetch("nixhub", key, TTL["nixhub"], fetch)
    if not releases:
        raise DiscoveryError("not-found", f"{SOURCE} knows no package {name!r}")
    latest = releases[0]
    extra = {
        "total": len(releases),
        "latest": str(latest.get("version") or ""),
        "summary": latest.get("summary") or None,
        "homepage": latest.get("homepage") or None,
        "license": latest.get("license") or None,
        "programs": _strs(_system(latest).get("programs")),
    }
    return Part(SOURCE, None, None, cached, [record(r) for r in releases[:limit]], extra=extra)
