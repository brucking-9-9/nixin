"""Home Manager options.

Data: the option JSON behind https://home-manager-options.extranix.com, one
file per Home Manager branch at `data/options-<release>.json`:

    {"last_update": "...", "options": [{"title": "programs.kitty.enable", "type": "boolean",
      "default": "false", "example": "true", "description": "<markdown>", "readOnly": false,
      "loc": ["programs", "kitty", "enable"],
      "declarations": [{"name": "<home-manager/modules/programs/kitty.nix>", "url": "https://..."}]}]}

That is the same `optionsJSON` Home Manager generates for its manual, so
default/example/declarations are structured rather than scraped. (The mdBook
manual's print.html has the same content but needs an HTML parser.) The
index is ~3 MB and cached for a day; matching happens locally.

Channels: unstable -> master, NN.MM -> release-NN.MM, stable -> the newest
release search.nixos.org knows (Home Manager releases track NixOS releases).
"""

from __future__ import annotations

import re
from typing import Any

from ..cache import TTL, Cache
from ..http import NetworkError, get_json
from . import DiscoveryError, Part
from . import nixos_search as es

SOURCE = "home-manager-options"
BASE = "https://home-manager-options.extranix.com/data"

_DECL_NAME = re.compile(r"^<home-manager/(.*)>$")


def release_for(cache: Cache, channel: str) -> tuple[str, str]:
    """`(channel name, release slug)` for a channel name, branch name or flakeref."""
    name = (
        channel if re.fullmatch(r"\d+\.\d+", channel) or channel in ("unstable", "stable") else None
    )
    if name is None:
        name = es.channel_from_ref(channel)
    if name is None:
        raise DiscoveryError(
            "bad-channel",
            f"{channel!r} is a commit, not a channel; Home Manager options are per channel",
        )
    if name == "stable":
        name = es.resolve_channel(cache, "stable").name
    if name == "unstable":
        return name, "master"
    return name, f"release-{name}"


def index_url(release: str) -> str:
    return f"{BASE}/options-{release}.json"


def load(cache: Cache, channel: str) -> tuple[dict[str, Any], bool, str]:
    """`(index, cached, channel name)`; index has `last_update` and `options`."""
    name, release = release_for(cache, channel)
    url = index_url(release)

    def fetch() -> dict[str, Any]:
        try:
            data = get_json(url, timeout=60)
        except NetworkError as exc:
            if exc.status == 404:
                raise DiscoveryError(
                    "bad-channel", f"no Home Manager option index for {name} ({url})"
                ) from exc
            raise
        options = data.get("options") if isinstance(data, dict) else None
        if not isinstance(options, list) or not options:
            raise NetworkError(f"{url} holds no options")
        return {"last_update": data.get("last_update"), "options": options}

    index, cached = cache.fetch("hm", url, TTL["hm-index"], fetch)
    return index, cached, name


def record(opt: dict[str, Any], channel: str) -> dict[str, Any]:
    decls = [d for d in opt.get("declarations") or [] if isinstance(d, dict)]
    names = []
    for d in decls:
        raw = str(d.get("name") or "")
        m = _DECL_NAME.match(raw)
        names.append(m.group(1) if m else raw)
    desc = opt.get("description")
    return {
        "kind": "hm",
        "source": SOURCE,
        "channel": channel,
        "rev": None,
        "name": opt.get("title") or ".".join(opt.get("loc") or []),
        "type": opt.get("type") or None,
        "default": opt.get("default") or None,
        "example": opt.get("example") or None,
        "description": str(desc).strip() if desc else None,
        "declarations": names,
        "declaration_urls": [str(d["url"]) for d in decls if d.get("url")],
        "read_only": bool(opt.get("readOnly", False)),
    }


def score(name: str, description: str | None, query: str) -> int:
    """How well an option name matches; higher is better, 0 is no match.

    Exact path 100, leading path prefix 80, whole dot-separated segment(s) 70
    minus their position (so programs.git.enable beats programs.foo.git.x for
    `git`), substring 60, description-only 20. Several words must all match
    somewhere; their scores are averaged.
    """
    words = query.casefold().split()
    if not words:
        return 0
    name_cf = name.casefold()
    desc_cf = (description or "").casefold()
    parts = name_cf.split(".")
    total = 0
    for w in words:
        if name_cf == w:
            s = 100
        elif name_cf.startswith(w + "."):
            s = 80
        else:
            s = 0
            qparts = w.split(".")
            for start in range(len(parts) - len(qparts) + 1):
                if parts[start : start + len(qparts)] == qparts:
                    s = 70 - min(start, 8)
                    break
            if not s:
                if w in name_cf:
                    s = 60
                elif w in desc_cf:
                    s = 20
        if not s:
            return 0
        total += s
    return total // len(words)


def search(cache: Cache, query: str, channel: str, limit: int) -> Part:
    index, cached, name = load(cache, channel)
    scored = []
    for opt in index["options"]:
        title = opt.get("title") or ""
        s = score(title, opt.get("description"), query)
        if s:
            scored.append((-s, title.casefold(), opt))
    scored.sort(key=lambda t: (t[0], t[1]))
    results = [record(opt, name) for _s, _t, opt in scored[:limit]]
    return Part(SOURCE, name, None, cached, results, extra={"updated": index.get("last_update")})


def info(cache: Cache, option: str, channel: str) -> Part:
    index, cached, name = load(cache, channel)
    hits = [opt for opt in index["options"] if opt.get("title") == option]
    return Part(
        SOURCE,
        name,
        None,
        cached,
        [record(h, name) for h in hits[:1]],
        extra={"updated": index.get("last_update")},
    )
