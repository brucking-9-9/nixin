"""Noogle: nix lib / builtins function search (kind `fn`).

`GET https://noogle.dev/api/v1/data` is one ~4 MB JSON document with every
documented function (about 2,100 of them):

    {"data": [{"meta": {"title": "builtins.concatStringsSep", "path": ["builtins", "concatStringsSep"],
                        "aliases": [["lib", "concatStringsSep"], ...], "signature": "...", "is_primop": true,
                        "content_meta": {"position": {"file": "/nix/store/...-source/lib/strings.nix",
                                                      "line": 225}}},
               "content": {"content": "<markdown>", "source": {"position": {...}}}}, ...],
     "builtinTypes": {...}, ...}

Cheap enough (0.2 s, daily TTL) that only the fields used are kept in the cache.
`fn` is opt-in: it is not part of `--kind all`, which is about installable things.
"""

from __future__ import annotations

import re
from typing import Any

from ..cache import TTL, Cache
from ..http import NetworkError, get_json
from . import Part

SOURCE = "noogle.dev"
API = "https://noogle.dev/api/v1/data"

_STORE_PREFIX = re.compile(r"^/nix/store/[a-z0-9]{32}-[^/]+/")


def _path(doc: dict[str, Any]) -> str:
    meta = doc.get("meta") or {}
    path = meta.get("path")
    if isinstance(path, list) and path:
        return ".".join(str(p) for p in path)
    return str(meta.get("title") or "")


def _aliases(doc: dict[str, Any]) -> list[str]:
    out = []
    for a in (doc.get("meta") or {}).get("aliases") or []:
        out.append(".".join(str(p) for p in a) if isinstance(a, list) else str(a))
    return out


def _signature(doc: dict[str, Any]) -> str | None:
    meta = doc.get("meta") or {}
    content = doc.get("content") or {}
    for cand in (
        meta.get("signature"),
        content.get("signature") if isinstance(content, dict) else None,
    ):
        if isinstance(cand, str) and cand.strip():
            return " ".join(cand.split())
    return None


def _description(doc: dict[str, Any]) -> str | None:
    content = doc.get("content")
    text = content.get("content") if isinstance(content, dict) else None
    if not isinstance(text, str):
        return None
    para: list[str] = []
    for line in text.strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            if para:
                break
            continue
        para.append(line.strip())
    return " ".join(para) or None


def _position(doc: dict[str, Any]) -> str | None:
    meta = doc.get("meta") or {}
    content = doc.get("content") or {}
    for pos in (
        (meta.get("content_meta") or {}).get("position"),
        (content.get("source") or {}).get("position") if isinstance(content, dict) else None,
        meta.get("attr_position"),
        meta.get("lambda_position"),
    ):
        if isinstance(pos, dict) and pos.get("file"):
            file = _STORE_PREFIX.sub("", str(pos["file"]))
            return f"{file}:{pos['line']}" if pos.get("line") else file
    return None


def _trim(doc: dict[str, Any]) -> dict[str, Any]:
    meta = doc.get("meta") or {}
    return {
        "name": _path(doc),
        "aliases": _aliases(doc),
        "signature": _signature(doc),
        "description": _description(doc),
        "position": _position(doc),
        "primop": bool(meta.get("is_primop")),
    }


def load(cache: Cache) -> tuple[list[dict[str, Any]], bool]:
    def fetch() -> list[dict[str, Any]]:
        data = get_json(API, timeout=60)
        docs = data.get("data") if isinstance(data, dict) else None
        if not isinstance(docs, list) or not docs:
            raise NetworkError(f"{API} holds no function data")
        return [_trim(d) for d in docs if isinstance(d, dict) and _path(d)]

    return cache.fetch("noogle", API, TTL["noogle"], fetch)


def record(fn: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "fn", "source": SOURCE, "channel": None, "rev": None, **fn}


def score(fn: dict[str, Any], query: str) -> int:
    words = query.casefold().split()
    if not words:
        return 0
    name = fn["name"].casefold()
    aliases = [a.casefold() for a in fn["aliases"]]
    desc = (fn.get("description") or "").casefold()
    total = 0
    for w in words:
        if name == w or w in aliases:
            s = 100
        elif name.endswith("." + w) or any(a.endswith("." + w) for a in aliases):
            s = 70
        elif w in name:
            s = 50
        elif any(w in a for a in aliases):
            s = 40
        elif w in desc:
            s = 10
        else:
            return 0
        total += s
    return total // len(words)


def search(cache: Cache, query: str, limit: int) -> Part:
    fns, cached = load(cache)
    scored = sorted(((-score(f, query), f["name"], f) for f in fns), key=lambda t: (t[0], t[1]))
    results = [record(f) for s, _n, f in scored if s < 0][:limit]
    return Part(SOURCE, None, None, cached, results)


def info(cache: Cache, name: str) -> Part:
    fns, cached = load(cache)
    want = name.casefold()
    hits = [
        f
        for f in fns
        if f["name"].casefold() == want or want in (a.casefold() for a in f["aliases"])
    ]
    hits.sort(
        key=lambda f: f["name"].casefold() != want
    )  # the function itself before an alias holder
    return Part(SOURCE, None, None, cached, [record(h) for h in hits[:1]])
