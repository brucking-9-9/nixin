#!/usr/bin/env python3
"""Record trimmed live responses into tests/fixtures/ for the unit tests.

    NIXIN_LIVE=1 nix develop -c python3 scripts/record-fixtures.py [es|hm|nixhub|noogle|local ...]

Each section fetches through nixin's own http/backends, cuts the payload down
to a few documents with the fields the code reads, and writes
`tests/fixtures/<source>/<name>.json`. Re-run a section when a source changes
shape; the tests assert on structure and on a handful of stable values (kitty,
firefox, ripgrep, services.openssh.enable, programs.kitty.enable).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nixin.cache import Cache
from nixin.discovery import nixos_search as es
from nixin.http import get_json, post_json

FIX = ROOT / "tests" / "fixtures"
cache = Cache(mode="off")


def write(rel: str, payload: object) -> None:
    path = FIX / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


def trim_source(src: dict) -> dict:
    out = {k: v for k, v in src.items() if k in es.PKG_SOURCE_FIELDS + es.OPT_SOURCE_FIELDS}
    if isinstance(out.get("package_platforms"), list):
        out["package_platforms"] = sorted(out["package_platforms"])[:3]
    return out


def trim_hits(resp: dict, n: int) -> dict:
    hits = resp.get("hits", {}).get("hits", [])[:n]
    return {
        "hits": {
            "total": resp.get("hits", {}).get("total"),
            "hits": [
                {"_index": h.get("_index"), "_source": trim_source(h["_source"])} for h in hits
            ],
        }
    }


def record_es() -> None:
    aliases = get_json(f"{es.ES_URL}/_cat/aliases", params={"format": "json"}, auth=es.ES_AUTH)
    keep = [
        {"alias": a["alias"], "index": a["index"]}
        for a in aliases
        if es._ALIAS_RE.match(a["alias"])
    ]
    # Highest generation per channel plus one older unstable generation, so the
    # "newest wins" and "empty newest falls back" paths both have data.
    by_ch: dict[str, list[dict]] = {}
    for a in keep:
        m = es._ALIAS_RE.match(a["alias"])
        by_ch.setdefault(m.group(2), []).append(a)
    chosen = []
    for ch, items in by_ch.items():
        items.sort(key=lambda a: int(es._ALIAS_RE.match(a["alias"]).group(1)), reverse=True)
        chosen += items[: 2 if ch == "unstable" else 1]
    write("es/aliases.json", chosen)

    ch = es.resolve_channel(cache, "unstable")
    write(
        "es/count.json",
        post_json(f"{es.ES_URL}/{ch.index}/_count", {"query": {"match_all": {}}}, auth=es.ES_AUTH),
    )

    def search_body(query: str, kind: str, limit: int) -> dict:
        words = es._words(query)
        if kind == "pkg":
            q = es._build_query(
                words,
                "package",
                es._PKG_FIELDS,
                "package_attr_name",
                es._PKG_PHRASE,
                es._PKG_POPULARITY,
            )
            return {
                "query": q,
                "size": limit,
                "_source": es.PKG_SOURCE_FIELDS,
                "rescore": es._PKG_RESCORE,
            }
        if kind == "program":
            q = es._build_query(
                words, "package", es._PKG_FIELDS, "package_programs", es._PKG_PHRASE, []
            )
            return {"query": q, "size": limit, "_source": es.PKG_SOURCE_FIELDS}
        q = es._build_query(words, "option", es._OPT_FIELDS, "option_name", es._OPT_PHRASE, [])
        return {"query": q, "size": limit, "_source": es.OPT_SOURCE_FIELDS}

    url = f"{es.ES_URL}/{ch.index}/_search"
    write(
        "es/search_pkg_firefox.json",
        trim_hits(post_json(url, search_body("firefox", "pkg", 5), auth=es.ES_AUTH), 4),
    )
    write(
        "es/search_opt_openssh.json",
        trim_hits(post_json(url, search_body("services.openssh", "opt", 5), auth=es.ES_AUTH), 3),
    )
    write(
        "es/search_program_rg.json",
        trim_hits(post_json(url, search_body("rg", "program", 5), auth=es.ES_AUTH), 3),
    )
    write(
        "es/info_pkg_kitty.json",
        trim_hits(
            post_json(
                url,
                es._term("package", "package_attr_name", "kitty", 1, es.PKG_SOURCE_FIELDS),
                auth=es.ES_AUTH,
            ),
            1,
        ),
    )
    write(
        "es/info_pkg_pname_gcc_wrapper.json",
        trim_hits(
            post_json(
                url,
                es._term("package", "package_pname", "gcc-wrapper", 5, es.PKG_SOURCE_FIELDS),
                auth=es.ES_AUTH,
            ),
            4,
        ),
    )
    write(
        "es/info_opt_openssh_enable.json",
        trim_hits(
            post_json(
                url,
                es._term(
                    "option", "option_name", "services.openssh.enable", 1, es.OPT_SOURCE_FIELDS
                ),
                auth=es.ES_AUTH,
            ),
            1,
        ),
    )
    write("es/empty.json", {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}})


def record_hm() -> None:
    from nixin.discovery import home_manager as hm

    data = get_json(hm.index_url("master"))
    wanted = {
        "programs.kitty.enable",
        "programs.kitty.settings",
        "programs.kitty.themeFile",
        "programs.git.enable",
        "programs.git.userName",
        "services.mako.enable",
    }
    options = [o for o in data["options"] if o.get("title") in wanted]
    write("hm/options-master.json", {"last_update": data.get("last_update"), "options": options})


def record_nixhub() -> None:
    from nixin.discovery import nixhub

    data = get_json(f"{nixhub.API}/v1/pkg", params={"name": "firefox"})
    releases = data[:3]
    for r in releases:
        systems = r.get("systems", {})
        r["systems"] = {k: v for k, v in systems.items() if k in ("x86_64-linux", "aarch64-darwin")}
    write("nixhub/pkg_firefox.json", releases)


def record_noogle() -> None:
    from nixin.discovery import noogle

    data = get_json(noogle.API, timeout=60)
    wanted = {
        "builtins.concatStringsSep",
        "lib.strings.concatStringsSep",
        "lib.attrsets.mapAttrs",
        "builtins.map",
    }
    docs = [
        d
        for d in data["data"]
        if noogle._path(d) in wanted or d.get("meta", {}).get("title") in wanted
    ]
    write("noogle/data.json", {"data": docs[:4], "builtinTypes": {}})


def record_local() -> None:
    ref = os.environ.get(
        "NIXIN_NIXPKGS", "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
    )
    from nixin.discovery import local

    meta = subprocess.run(
        ["nix", *local.NIX_FLAGS, "flake", "metadata", "--json", ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    write("local/flake_metadata.json", json.loads(meta))
    out = subprocess.run(
        [
            "nix",
            *local.NIX_FLAGS,
            "search",
            "--json",
            f"{ref}#legacyPackages.x86_64-linux",
            "kitty",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    keep = {
        k: v
        for k, v in data.items()
        if k.rsplit(".", 1)[-1] in ("kitty", "kitty-img", "kitty-themes")
    }
    write("local/search_kitty.json", keep)
    out = subprocess.run(
        [
            "nix",
            *local.NIX_FLAGS,
            "eval",
            "--json",
            f"{ref}#legacyPackages.x86_64-linux.kitty",
            "--apply",
            local.APPLY,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    data["platforms"] = sorted(data.get("platforms", []))[:3]
    write("local/eval_kitty.json", data)


SECTIONS = {
    "es": record_es,
    "hm": record_hm,
    "nixhub": record_nixhub,
    "noogle": record_noogle,
    "local": record_local,
}


def main(argv: list[str]) -> int:
    if os.environ.get("NIXIN_LIVE") != "1":
        print("refusing to talk to the network without NIXIN_LIVE=1", file=sys.stderr)
        return 2
    for name in argv or list(SECTIONS):
        print(f"== {name}")
        SECTIONS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
