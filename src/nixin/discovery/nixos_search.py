"""search.nixos.org backend: packages, NixOS options, programs.

The site is an Elasticsearch cluster behind `https://search.nixos.org/backend`,
readable with the frontend's public credentials. Hydra publishes one index per
channel generation and an alias per channel:

    latest-52-nixos-unstable  ->  nixos-52-unstable-<40-hex nixpkgs rev>
    latest-52-nixos-26.05     ->  nixos-52-26.05-<rev>

so the alias list (`_cat/aliases`) is the channel list, the highest generation
per channel is the freshest data, and the index name tells the exact nixpkgs
commit the documents were built from (reported as `rev`). Queries go to that
concrete index, not the alias: the alias moves to a new rev several times a
day while the alias list is cached, and the superseded indices stay up for
days, so asking the index keeps `rev` true to the documents returned.

Document fields used (type "package"): package_attr_name, package_pname,
package_pversion, package_description, package_homepage (list),
package_license_set (SPDX ids), package_programs, package_platforms,
package_outputs, package_mainProgram, package_position. Type "option":
option_name, option_type, option_default, option_example, option_description
(wrapped in <rendered-html>), option_source (declaring file in nixpkgs).

The query DSL mirrors the frontend (nixos-search `frontend/src/Search/Query.elm`):
a cross_fields multi_match plus wildcards on the primary field for substring and
`_`/`-` variants, boosted by exact term, prefix and (for packages) popularity,
rescored by attribute-name length so `firefox` beats `firefox-esr-unwrapped`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

from ..cache import TTL, Cache
from ..http import NetworkError, get_json, post_json
from . import DiscoveryError, Part

SOURCE = "search.nixos.org"
ES_URL = "https://search.nixos.org/backend"
ES_AUTH = ("aWVSALXpZv", "X8gPHnzL52wFEekuxsfQ9cSh")  # the public frontend's read-only user

_ALIAS_RE = re.compile(r"^latest-(\d+)-nixos-(unstable|\d+\.\d+)$")
_REV_RE = re.compile(r"-([0-9a-f]{40})$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_BRANCH = re.compile(r"^(?:nixos|nixpkgs|release)-(\d+\.\d+)(?:-small|-darwin|-aarch64)?$")
_UNSTABLE_BRANCHES = {
    "master",
    "nixos-unstable",
    "nixos-unstable-small",
    "nixpkgs-unstable",
    "staging",
    "staging-next",
    "unstable",
}

PKG_SOURCE_FIELDS = [
    "package_attr_name",
    "package_pname",
    "package_pversion",
    "package_description",
    "package_homepage",
    "package_license_set",
    "package_programs",
    "package_platforms",
    "package_outputs",
    "package_mainProgram",
    "package_position",
]
OPT_SOURCE_FIELDS = [
    "option_name",
    "option_type",
    "option_default",
    "option_example",
    "option_description",
    "option_source",
]

# (field, weight) the cross_fields match scores over; the `.*` sub-fields join at 0.6x.
_PKG_FIELDS = [
    ("package_attr_name", 9.0),
    ("package_programs", 9.0),
    ("package_pname", 6.0),
    ("package_description", 1.3),
    ("package_longDescription", 1.0),
]
_OPT_FIELDS = [("option_name", 6.0), ("option_description", 1.0)]
_PKG_PHRASE = ["package_description^3", "package_longDescription^1"]
_OPT_PHRASE = ["option_description^3"]
_PKG_POPULARITY = [("package_repology_repos", 20.0), ("package_dep_count", 1000.0)]
_PKG_RESCORE = {
    "window_size": 100,
    "query": {
        "rescore_query": {
            "function_score": {
                "script_score": {
                    "script": {"source": "1.0 / doc['package_attr_name'].value.length()"}
                }
            }
        },
        "rescore_query_weight": 20.0,
    },
}


@dataclass(frozen=True)
class Channel:
    name: str  # the concrete channel: "unstable" or "NN.MM" (never "stable")
    alias: str  # latest-<gen>-nixos-<name>
    index: str  # nixos-<gen>-<name>-<rev>
    rev: str | None  # nixpkgs commit the index was built from

    @property
    def generation(self) -> int:
        m = _ALIAS_RE.match(self.alias)
        return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------- channels


def channel_from_ref(ref: str) -> str | None:
    """Map a nixpkgs branch name or flakeref to a channel name, or None for a bare commit.

    nixos-unstable, nixpkgs-unstable, master      -> "unstable"
    nixos-25.11, release-25.11, nixpkgs-25.11-darwin -> "25.11"
    github:nixos/nixpkgs/nixos-25.11, github:nixos/nixpkgs?ref=nixos-25.11,
    nixpkgs/nixos-25.11, flake:nixpkgs/nixos-25.11  -> "25.11"
    github:nixos/nixpkgs, nixpkgs                    -> "unstable" (default branch)
    github:nixos/nixpkgs/<40-hex>, <40-hex>          -> None
    """
    ref = ref.strip()
    if not ref:
        return None
    branch: str | None = ref
    if "://" in ref or ref.startswith(("github:", "gitlab:", "sourcehut:", "flake:")):
        parts = urlsplit(ref)
        query = parse_qs(parts.query)
        if "rev" in query:
            return None
        if "ref" in query:
            branch = query["ref"][0]
        else:
            path = parts.path.lstrip("/")
            segments = [s for s in path.split("/") if s]
            if parts.scheme == "flake":
                branch = segments[1] if len(segments) > 1 else "unstable"
            else:
                branch = segments[2] if len(segments) > 2 else "unstable"
    elif ref.startswith(("/", ".", "path:", "git+")):
        return None  # a local checkout or arbitrary git url: nothing to map
    elif "/" in ref:
        head, _, tail = ref.partition("/")
        if head == "nixpkgs":
            branch = tail or "unstable"
        else:
            return None
    elif ref == "nixpkgs":
        branch = "unstable"
    assert branch is not None
    branch = branch.strip()
    if _HEX40.match(branch):
        return None
    if branch in _UNSTABLE_BRANCHES:
        return "unstable"
    if branch == "stable":
        return "stable"
    if re.fullmatch(r"\d+\.\d+", branch):
        return branch
    m = _RELEASE_BRANCH.match(branch)
    if m:
        return m.group(1)
    return None


def list_aliases(cache: Cache) -> tuple[list[dict[str, str]], bool]:
    """Live `latest-<gen>-nixos-<channel>` aliases with their index names."""

    def fetch() -> list[dict[str, str]]:
        data = get_json(f"{ES_URL}/_cat/aliases", params={"format": "json"}, auth=ES_AUTH)
        if not isinstance(data, list):
            raise NetworkError(f"{ES_URL}/_cat/aliases returned no alias list")
        return [
            {"alias": e["alias"], "index": e["index"]}
            for e in data
            if isinstance(e, dict) and _ALIAS_RE.match(str(e.get("alias", "")))
        ]

    return cache.fetch("es", "aliases", TTL["es-aliases"], fetch)


def channels(cache: Cache) -> tuple[dict[str, list[Channel]], bool]:
    """Channel name -> candidate Channels, newest generation first.

    Keys are the concrete names ("unstable", "26.05", "25.11"); `stable` and
    branch names are resolved by `resolve_channel`.
    """
    aliases, cached = list_aliases(cache)
    if not aliases:
        raise DiscoveryError("network", f"{SOURCE} lists no channel indices right now")
    by_name: dict[str, list[tuple[int, Channel]]] = {}
    for e in aliases:
        m = _ALIAS_RE.match(e["alias"])
        if not m:
            continue
        rev = _REV_RE.search(e["index"])
        ch = Channel(m.group(2), e["alias"], e["index"], rev.group(1) if rev else None)
        by_name.setdefault(ch.name, []).append((int(m.group(1)), ch))
    return {
        name: [ch for _gen, ch in sorted(cands, key=lambda c: c[0], reverse=True)]
        for name, cands in by_name.items()
    }, cached


def _release_key(name: str) -> tuple[int, ...]:
    return tuple(int(p) for p in name.split("."))


def resolve_channel(cache: Cache, name: str) -> Channel:
    """`unstable`, `stable`, `NN.MM` or a branch name -> the Channel to query.

    Picks the newest generation that actually holds documents: Hydra publishes
    the alias before the index is filled, so mid-rollover the newest can be
    empty for a while.
    """
    by_name, _ = channels(cache)
    key = channel_from_ref(name) if name not in by_name else name
    if key is None:
        raise DiscoveryError(
            "bad-channel", f"{name!r} is a commit, not a channel; {SOURCE} indexes channels only"
        )
    if key == "stable":
        releases = sorted((n for n in by_name if n != "unstable"), key=_release_key)
        if not releases:
            raise DiscoveryError("bad-channel", f"{SOURCE} lists no release channel right now")
        key = releases[-1]
    candidates = by_name.get(key)
    if not candidates:
        known = ", ".join(
            [
                "unstable",
                "stable",
                *sorted((n for n in by_name if n != "unstable"), key=_release_key),
            ]
        )
        raise DiscoveryError("bad-channel", f"unknown channel {name!r}; known: {known}")
    for ch in candidates:
        if _count(cache, ch) > 0:
            return ch
    return candidates[0]


def _sort_key(name: str) -> tuple[int, ...]:
    return (0,) if name == "unstable" else (1, *_release_key(name))


def _count(cache: Cache, ch: Channel) -> int:
    def fetch() -> int:
        try:
            data = post_json(
                f"{ES_URL}/{ch.index}/_count", {"query": {"match_all": {}}}, auth=ES_AUTH
            )
        except NetworkError as exc:
            if exc.status == 404:
                return 0
            raise
        count = data.get("count", 0) if isinstance(data, dict) else 0
        return int(count) if isinstance(count, int) else 0

    count, _ = cache.fetch("es", f"count:{ch.index}", TTL["es-search"], fetch)
    return count


# --------------------------------------------------------------------------- queries


def _words(query: str) -> list[str]:
    # `*`, `?` and `\` are wildcard metacharacters and never occur in attribute
    # names, option paths or binaries: dropping them is what the user meant.
    return query.lower().translate({ord(c): None for c in "*?\\"}).split()


def _variants(words: list[str]) -> list[str]:
    seen: set[str] = set()
    for w in words:
        seen |= {w, w.replace("_", "-"), w.replace("-", "_")}
    seen.discard("")
    return sorted(seen)


def _build_query(
    words: list[str],
    doc_type: str,
    fields: list[tuple[str, float]],
    main: str,
    phrase_fields: list[str],
    popularity: list[tuple[str, float]],
) -> dict[str, Any]:
    weighted: list[str] = []
    for name, weight in fields:
        weighted += [f"{name}^{weight}", f"{name}.*^{round(weight * 0.6, 4)}"]
    joined = "".join(words)
    should: list[dict[str, Any]] = [
        {"term": {main: {"value": joined, "boost": 100.0}}},
        {"prefix": {main: {"value": joined, "boost": 20.0, "case_insensitive": True}}},
    ]
    if len(words) > 1:
        should.append(
            {
                "constant_score": {
                    "filter": {
                        "multi_match": {
                            "type": "phrase",
                            "query": " ".join(words),
                            "fields": phrase_fields,
                        }
                    },
                    "boost": 80.0,
                }
            }
        )
    should += [
        {"rank_feature": {"field": f, "boost": 5.0, "saturation": {"pivot": p}}}
        for f, p in popularity
    ]
    return {
        "bool": {
            "filter": [{"term": {"type": doc_type}}],
            "must": [
                {
                    "dis_max": {
                        "tie_breaker": 0.7,
                        "queries": [
                            {
                                "multi_match": {
                                    "type": "cross_fields",
                                    "query": " ".join(words),
                                    "analyzer": "whitespace",
                                    "auto_generate_synonyms_phrase_query": False,
                                    "operator": "and",
                                    "fields": weighted,
                                }
                            },
                            *(
                                {"wildcard": {main: {"value": f"*{v}*", "case_insensitive": True}}}
                                for v in _variants(words)
                            ),
                        ],
                    }
                }
            ],
            "should": should,
        }
    }


def _es_search(cache: Cache, ch: Channel, body: dict[str, Any]) -> tuple[list[dict], bool]:
    """POST a search body; returns the hits' `_source` documents."""
    url = f"{ES_URL}/{ch.index}/_search"
    key = url + "\n" + json.dumps(body, sort_keys=True)

    def fetch() -> list[dict]:
        try:
            data = post_json(url, body, auth=ES_AUTH)
        except NetworkError as exc:
            if exc.status == 404:
                raise DiscoveryError(
                    "bad-channel",
                    f"index {ch.index} is gone from {SOURCE} (channel rolled over); retry with --refresh",
                ) from exc
            raise
        hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
        return [
            h["_source"] for h in hits if isinstance(h, dict) and isinstance(h.get("_source"), dict)
        ]

    return cache.fetch("es", key, TTL["es-search"], fetch)


# --------------------------------------------------------------------------- records


class _Text(HTMLParser):
    """HTML -> plain text; block ends become line breaks, all other whitespace collapses."""

    _BLOCK: ClassVar[frozenset[str]] = frozenset(
        {"p", "div", "li", "pre", "br", "ul", "ol", "h1", "h2", "h3", "h4", "blockquote"}
    )
    BREAK = "\x00"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "br":
            self.parts.append(self.BREAK)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self.parts.append(self.BREAK)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(text: str | None) -> str | None:
    """Plain text from the index's `<rendered-html>` descriptions (None stays None)."""
    if not text:
        return None
    p = _Text()
    p.feed(text)
    p.close()
    lines = [" ".join(chunk.split()) for chunk in "".join(p.parts).split(_Text.BREAK)]
    return "\n".join(line for line in lines if line) or None


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def _list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def package_record(src: dict[str, Any], ch: Channel) -> dict[str, Any]:
    return {
        "kind": "pkg",
        "source": SOURCE,
        "channel": ch.name,
        "rev": ch.rev,
        "name": src.get("package_attr_name") or "",
        "pname": src.get("package_pname") or None,
        "version": src.get("package_pversion") or None,
        "description": src.get("package_description") or None,
        "homepage": _first(src.get("package_homepage")),
        "license": _list(src.get("package_license_set")),
        "unfree": None,  # the index carries no free/unfree flag; `--nixpkgs` evaluation does
        "programs": _list(src.get("package_programs")),
        "main_program": src.get("package_mainProgram") or None,
        "platforms": _list(src.get("package_platforms")),
        "outputs": _list(src.get("package_outputs")),
        "position": src.get("package_position") or None,
    }


def option_record(src: dict[str, Any], ch: Channel) -> dict[str, Any]:
    declared = src.get("option_source") or None
    base = f"https://github.com/NixOS/nixpkgs/blob/{ch.rev or 'master'}/"
    return {
        "kind": "opt",
        "source": SOURCE,
        "channel": ch.name,
        "rev": ch.rev,
        "name": src.get("option_name") or "",
        "type": src.get("option_type") or None,
        "default": src.get("option_default") or None,
        "example": src.get("option_example") or None,
        "description": strip_html(src.get("option_description")),
        "declarations": [declared] if declared else [],
        "declaration_urls": [base + declared] if declared else [],
    }


def program_record(program: str, src: dict[str, Any], ch: Channel, exact: bool) -> dict[str, Any]:
    return {
        "kind": "program",
        "source": SOURCE,
        "channel": ch.name,
        "rev": ch.rev,
        "name": program,
        "package": src.get("package_attr_name") or "",
        "pname": src.get("package_pname") or None,
        "version": src.get("package_pversion") or None,
        "description": src.get("package_description") or None,
        "exact": exact,
    }


# --------------------------------------------------------------------------- API


def search(cache: Cache, query: str, kind: str, ch: Channel, limit: int) -> Part:
    """`kind` is pkg, opt or program."""
    words = _words(query)
    if not words:
        raise DiscoveryError("usage", f"nothing searchable in {query!r}")
    body: dict[str, Any]
    if kind == "pkg":
        q = _build_query(
            words, "package", _PKG_FIELDS, "package_attr_name", _PKG_PHRASE, _PKG_POPULARITY
        )
        body = {"query": q, "size": limit, "_source": PKG_SOURCE_FIELDS, "rescore": _PKG_RESCORE}
    elif kind == "program":
        q = _build_query(words, "package", _PKG_FIELDS, "package_programs", _PKG_PHRASE, [])
        body = {"query": q, "size": limit, "_source": PKG_SOURCE_FIELDS}
    elif kind == "opt":
        q = _build_query(words, "option", _OPT_FIELDS, "option_name", _OPT_PHRASE, [])
        body = {"query": q, "size": limit, "_source": OPT_SOURCE_FIELDS}
    else:
        raise DiscoveryError("usage", f"{SOURCE} has no kind {kind!r}")
    hits, cached = _es_search(cache, ch, body)
    if kind == "pkg":
        results = [package_record(h, ch) for h in hits]
    elif kind == "opt":
        results = [option_record(h, ch) for h in hits]
    else:
        results = _program_rows(hits, words, ch)[:limit]
    return Part(SOURCE, ch.name, ch.rev, cached, results)


def _program_rows(hits: list[dict], words: list[str], ch: Channel) -> list[dict[str, Any]]:
    needle = "".join(words)
    variants = _variants(words)
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for h in hits:
        for prog in _list(h.get("package_programs")):
            low = prog.lower()
            if low == needle or low in variants:
                exact.append(program_record(prog, h, ch, True))
            elif any(v in low for v in variants):
                partial.append(program_record(prog, h, ch, False))
    return exact + partial


def _term(doc_type: str, field: str, value: str, size: int, fields: list[str]) -> dict[str, Any]:
    return {
        "query": {
            "bool": {"filter": [{"term": {"type": doc_type}}], "must": [{"term": {field: value}}]}
        },
        "size": size,
        "_source": fields,
    }


def info_package(cache: Cache, name: str, ch: Channel) -> Part:
    """Exact attribute match first; then packages whose pname is `name`.

    Several attributes can share a pname (firefox, firefox-esr, ...): the
    canonical one (attr == pname) wins, the others are listed in `alternatives`.
    """
    hits, cached = _es_search(
        cache, ch, _term("package", "package_attr_name", name, 1, PKG_SOURCE_FIELDS)
    )
    if hits:
        rec = package_record(hits[0], ch)
        rec["matched_by"] = "attr"
        return Part(SOURCE, ch.name, ch.rev, cached, [rec])
    hits, cached2 = _es_search(
        cache, ch, _term("package", "package_pname", name, 5, PKG_SOURCE_FIELDS)
    )
    if not hits:
        return Part(SOURCE, ch.name, ch.rev, cached and cached2, [])
    hits = sorted(
        hits,
        key=lambda h: (
            h.get("package_attr_name") != h.get("package_pname"),
            h.get("package_attr_name", ""),
        ),
    )
    rec = package_record(hits[0], ch)
    rec["matched_by"] = "pname"
    rec["alternatives"] = [h["package_attr_name"] for h in hits[1:] if h.get("package_attr_name")]
    return Part(SOURCE, ch.name, ch.rev, cached and cached2, [rec])


def info_option(cache: Cache, name: str, ch: Channel) -> Part:
    hits, cached = _es_search(cache, ch, _term("option", "option_name", name, 1, OPT_SOURCE_FIELDS))
    return Part(SOURCE, ch.name, ch.rev, cached, [option_record(h, ch) for h in hits[:1]])
