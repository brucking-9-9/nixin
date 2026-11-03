"""`nixin browse [CATEGORY]`: applications by freedesktop main category.

Three sources, merged by attribute (the first one wins, the rows keep where they came from
in `listed_by`):

    installed         desktop entries in this machine's profiles (installed.scan, no
                      network); their attribute is search.nixos.org's for that pname
    curated           the shipped well-known list (curated.WELL_KNOWN), looked up on the
                      channel (one request with the next)
    search.nixos.org  the channel's packages whose desktop entries are in the category
                      (`package_categories_set`, index generation 52 and later), most
                      popular first (repology), at most --limit, one row per pname

Without CATEGORY: the categories, with how many applications each has installed, in the
shipped list and on search.nixos.org (one aggregation request).

Without the network the installed applications and the shipped names still answer (the
source's error is in `sources`); the shipped names then have no version or summary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..cache import TTL, Cache
from ..discovery import DiscoveryError, _error
from ..discovery import nixos_search as es
from ..http import NetworkError, post_json
from . import curated, hint, installed, parse

SOURCE = es.SOURCE


def _post(cache: Cache, ch: es.Channel, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The whole answer (hits and aggregations), cached as searches are."""
    url = f"{es.ES_URL}/{ch.index}/_search"

    def fetch() -> dict[str, Any]:
        data = post_json(url, body, auth=es.ES_AUTH)
        return data if isinstance(data, dict) else {}

    return cache.fetch("es", url + "\n" + json.dumps(body, sort_keys=True), TTL["es-search"], fetch)


def _hits(data: dict[str, Any]) -> list[dict[str, Any]]:
    hits = (data.get("hits") or {}).get("hits") or []
    return [
        h["_source"] for h in hits if isinstance(h, dict) and isinstance(h.get("_source"), dict)
    ]


def _fields() -> list[str]:
    return [*es.PKG_SOURCE_FIELDS, *hint.ES_FIELDS]


def _installed_app(app: installed.App) -> dict[str, Any]:
    """An installed desktop entry as an `appstream` object (icon resolved in its store path)."""
    out = hint.empty("installed")
    path = parse.resolve_icon(Path(app.store_path), app.icon)
    out.update(
        store_path=app.store_path,
        gui=True,
        id=app.desktop_id.removesuffix(".desktop"),
        name=app.name,
        summary=app.summary,
        categories=list(app.categories),
        icon={"name": app.icon, "path": path, "url": None} if app.icon else None,
        launchable=app.desktop_id,
        desktop_ids=[app.desktop_id],
    )
    out["installed_for"] = list(app.owners)
    return out


def _row(src: dict[str, Any], ch: es.Channel) -> dict[str, Any]:
    rec = es.package_record(src, ch)
    rec["appstream"] = hint.from_es(src)
    return rec


def _bare(attr: str, apps: dict[str, Any] | None = None) -> dict[str, Any]:
    """A row without search.nixos.org: an installed app's, or a shipped name's."""
    a = apps or hint.from_es({"package_attr_name": attr})
    return {
        "kind": "pkg",
        "source": "installed" if apps else "curated",
        "channel": None,
        "rev": None,
        "name": attr,
        "pname": attr,
        "version": None,
        "description": a.get("summary"),
        "homepage": None,
        "license": [],
        "unfree": None,
        "programs": [],
        "main_program": None,
        "platforms": [],
        "outputs": [],
        "position": None,
        "appstream": a,
    }


def _overview(ns: argparse.Namespace, cache: Cache) -> dict[str, Any]:
    apps = installed.scan()
    sources: dict[str, Any] = {
        SOURCE: {
            "kinds": ["app"],
            "channel": None,
            "rev": None,
            "cached": False,
            "count": 0,
            "error": None,
        },
        "installed": {"count": len(apps), "profiles": [str(p) for _o, p in installed.profiles()]},
    }
    published: dict[str, int | None] = {c.key: None for c in curated.CATEGORIES}
    notes: list[str] = []
    try:
        ch = es.resolve_channel(cache, ns.channel or "unstable")
        body = {
            "size": 0,
            "query": {"term": {"type": "package"}},
            "aggs": {
                "cats": {
                    "filters": {
                        "filters": {
                            c.key: {"terms": {"package_categories_set": list(c.matches)}}
                            for c in curated.CATEGORIES
                        }
                    }
                }
            },
        }
        data, cached = _post(cache, ch, body)
        buckets = ((data.get("aggregations") or {}).get("cats") or {}).get("buckets") or {}
        for key in published:
            count = (buckets.get(key) or {}).get("doc_count")
            published[key] = int(count) if isinstance(count, int) else None
        sources[SOURCE].update(
            channel=ch.name,
            rev=ch.rev,
            cached=cached,
            count=sum(v or 0 for v in published.values()),
        )
        if not any(published.values()):
            notes.append(f"{SOURCE} {ch.name} has no desktop data in this index generation")
    except (DiscoveryError, NetworkError) as exc:
        sources[SOURCE]["error"] = {"code": exc.code, "message": str(exc)}
    cats = []
    for c in curated.CATEGORIES:
        mine = [a for a in apps if set(a.categories) & set(c.matches)]
        cats.append(
            {
                "key": c.key,
                "label": c.label,
                "matches": list(c.matches),
                "installed": len({a.pname for a in mine}),
                "curated": len(curated.WELL_KNOWN.get(c.key, ())),
                "published": published[c.key],
            }
        )
    doc: dict[str, Any] = {
        "ok": True,
        "category": None,
        "categories": cats,
        "installed": len({a.pname for a in apps}),
        "source": SOURCE,
        "channel": sources[SOURCE]["channel"],
        "rev": sources[SOURCE]["rev"],
        "cached": sources[SOURCE]["cached"],
        "sources": sources,
    }
    if notes:
        doc["notes"] = notes
    return doc


def _category(ns: argparse.Namespace, cache: Cache, cat: curated.Category) -> dict[str, Any]:
    apps = [a for a in installed.scan() if set(a.categories) & set(cat.matches)]
    by_pname: dict[str, list[installed.App]] = {}
    for a in apps:
        by_pname.setdefault(a.pname, []).append(a)
    shipped = list(curated.WELL_KNOWN.get(cat.key, ()))
    sources: dict[str, Any] = {
        SOURCE: {
            "kinds": ["app"],
            "channel": None,
            "rev": None,
            "cached": True,
            "count": 0,
            "error": None,
        },
        "installed": {"count": len(by_pname)},
        "curated": {"count": len(shipped)},
    }
    notes: list[str] = []
    named: list[
        dict[str, Any]
    ] = []  # search.nixos.org's documents for the shipped and installed names
    popular: list[dict[str, Any]] = []
    ch: es.Channel | None = None
    try:
        ch = es.resolve_channel(cache, ns.channel or "unstable")
        names = {
            "size": len(shipped) + 5 * len(by_pname),
            "_source": _fields(),
            "query": {
                "bool": {
                    "filter": [{"term": {"type": "package"}}],
                    "should": [
                        {"terms": {"package_attr_name": shipped}},
                        {"terms": {"package_pname": sorted(_pnames(by_pname))}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        }
        data, cached1 = _post(cache, ch, names)
        named = _hits(data)
        query = {
            "size": ns.limit * 2,  # variants share a pname and fold into one row
            "_source": _fields(),
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"type": "package"}},
                        {"terms": {"package_categories_set": list(cat.matches)}},
                    ],
                    "should": [
                        {
                            "rank_feature": {
                                "field": "package_repology_repos",
                                "boost": 5.0,
                                "saturation": {"pivot": 20.0},
                            }
                        }
                    ],
                }
            },
        }
        data, cached2 = _post(cache, ch, query)
        popular = _hits(data)
        sources[SOURCE].update(channel=ch.name, rev=ch.rev, cached=cached1 and cached2)
        if not popular and not any(hint.has_fields(s) for s in named):
            notes.append(f"{SOURCE} {ch.name} has no desktop data in this index generation")
    except (DiscoveryError, NetworkError) as exc:
        sources[SOURCE].update(cached=False, error={"code": exc.code, "message": str(exc)})
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}

    def add(row: dict[str, Any], by: str, owners: list[str] | None = None) -> None:
        """One row per application: a row whose attribute, pname, AppStream id or app
        name is already listed only adds `by` to that row's `listed_by`."""
        keys = _keys(row)
        old = next((seen[k] for k in keys if k in seen), None)
        if old is not None:
            if by not in old["listed_by"]:
                old["listed_by"].append(by)
            return
        row["installed"] = owners is not None
        row["installed_for"] = owners or []
        row["curated"] = row["name"] in shipped
        row["listed_by"] = [by]
        rows.append(row)
        seen.update((k, row) for k in keys)

    for pname in sorted(by_pname, key=lambda p: by_pname[p][0].name.lower()):
        mine = by_pname[pname]
        owners = list(dict.fromkeys(o for a in mine for o in a.owners))
        base = pname.removesuffix("-unwrapped")  # the wrapper is what people install
        docs = sorted(
            (s for s in named if s.get("package_pname") in (pname, base)),
            key=lambda s: (
                s.get("package_pname") != base,
                s.get("package_attr_name") != base,
                len(s.get("package_attr_name") or ""),
            ),
        )
        app = _installed_app(mine[0])
        if docs and ch is not None:
            row = _row(docs[0], ch)
            row["appstream"] = merge_installed(app, row["appstream"])
        else:
            row = _bare(base, app)
            row["version"] = mine[0].version or None
        add(row, "installed", owners)
    by_attr = {s.get("package_attr_name"): s for s in named}
    for attr in shipped:
        src = by_attr.get(attr)
        if src is not None and ch is not None:
            add(_row(src, ch), "curated")
        elif ch is None:  # no network: the shipped name alone
            add(_bare(attr), "curated")
    for src in popular:
        if len([r for r in rows if SOURCE in r["listed_by"]]) >= ns.limit:
            break
        if ch is not None:
            add(_row(src, ch), SOURCE)
    sources[SOURCE]["count"] = sum(1 for r in rows if SOURCE in r["listed_by"])
    doc: dict[str, Any] = {
        "ok": True,
        "category": {"key": cat.key, "label": cat.label, "matches": list(cat.matches)},
        "results": rows,
        "count": len(rows),
        "limit": ns.limit,
        "source": SOURCE,
        "channel": sources[SOURCE]["channel"],
        "rev": sources[SOURCE]["rev"],
        "cached": sources[SOURCE]["cached"],
        "sources": sources,
    }
    if notes:
        doc["notes"] = notes
    return doc


def _pnames(by_pname: dict[str, Any]) -> set[str]:
    """The installed pnames and, for `x-unwrapped`, `x`: the wrapper's attribute."""
    return set(by_pname) | {p.removesuffix("-unwrapped") for p in by_pname}


def _keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    a = row.get("appstream") or {}
    keys = [("attr", row["name"]), ("pname", row.get("pname") or row["name"])]
    if a.get("id"):
        keys.append(("id", str(a["id"]).lower()))
    if a.get("name") and a.get("source") not in (None, "curated"):
        keys.append(("app", str(a["name"]).lower()))
    return keys


def merge_installed(app: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    from . import merge

    out = merge(app, base)
    out.update(source="installed", store_path=app["store_path"], installed_for=app["installed_for"])
    return out


def _browse(ns: argparse.Namespace) -> dict[str, Any]:
    if ns.limit < 1:
        raise DiscoveryError("usage", "--limit must be at least 1")
    cache = Cache.from_flags(no_cache=ns.no_cache, refresh=ns.refresh)
    if not ns.category:
        return _overview(ns, cache)
    cat = curated.category(ns.category)
    if cat is None:
        known = ", ".join(c.key for c in curated.CATEGORIES)
        raise DiscoveryError("usage", f"unknown category {ns.category!r}; known: {known}")
    return _category(ns, cache, cat)


def run(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        doc = _browse(ns)
    except Exception as exc:  # noqa: BLE001 -- every failure must become the error document
        return {"ok": False, "error": _error(exc)}
    if getattr(ns, "json", False):
        return doc
    return {"ok": True, "message": render(doc)}


# --- human output ------------------------------------------------------------------------------


def _footer(doc: dict[str, Any]) -> str:
    s = (doc.get("sources") or {}).get(SOURCE) or {}
    bits = [
        f"{doc.get('count', 0)} app(s)"
        if doc.get("category")
        else f"{doc.get('installed', 0)} installed app(s)"
    ]
    tag = SOURCE + (f" {s['channel']}" if s.get("channel") else "")
    tag += f"@{s['rev'][:8]}" if s.get("rev") else ""
    tag += (
        f" FAILED: {s['error']['message']}"
        if s.get("error")
        else " (cached)"
        if s.get("cached")
        else ""
    )
    lines = ["# " + " · ".join([*bits, tag])]
    lines += [f"# note: {n}" for n in doc.get("notes") or []]
    return "\n".join(lines)


def render(doc: dict[str, Any]) -> str:
    from ..discovery.format import table

    if not doc.get("category"):
        rows = [
            [
                c["label"],
                c["key"],
                str(c["installed"]),
                str(c["curated"]),
                "" if c["published"] is None else str(c["published"]),
            ]
            for c in doc.get("categories") or []
        ]
        head = ["CATEGORY", "KEY", "INSTALLED", "WELL-KNOWN", "SEARCH.NIXOS.ORG"]
        hint_line = "# nixin browse KEY lists a category (● installed, ★ well-known)"
        return table(head, rows) + "\n" + _footer(doc) + "\n" + hint_line
    rows = []
    for r in doc.get("results") or []:
        a = r.get("appstream") or {}
        mark = ("●" if r.get("installed") else " ") + ("★" if r.get("curated") else " ")
        rows.append(
            [
                mark,
                a.get("name") or r["name"],
                r["name"],
                r.get("version") or "",
                a.get("summary") or r.get("description") or "",
            ]
        )
    cat = doc["category"]
    out = f"{cat['label']} ({cat['key']}) · ● installed  ★ well-known\n"
    return out + table(["", "APP", "PACKAGE", "VERSION", "SUMMARY"], rows) + "\n" + _footer(doc)
