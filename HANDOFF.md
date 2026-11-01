# HANDOFF — discovery layer (branch `discovery`)

Written 2026-11-01 on 19BK when the machine had to go quiet. Continue on
another host from this branch; nothing is pushed.

## Done and verified live (from 19BK, 2026-11-01)

All through `nix develop -c python3 -m nixin.cli ...` with `PYTHONPATH=src:$PYTHONPATH`:

| command | seen |
|---|---|
| `search firefox` | firefox 159.0, firefox-unwrapped, firefox-esr … from index `nixos-52-unstable-1c89083e…`; second run `cached: true` |
| `search --kind opt services.openssh` | 6 options with type + plain-text description; `declaration_urls` built from the index rev |
| `search --kind program rg` | `rg` → ripgrep 15.2.0 (`exact: true`), then rgx/rgxg substring rows |
| `info --kind pkg kitty` | full record (outputs, programs, license, position, rev) |
| `info --kind pkg firefox --channel stable` | resolved to channel `26.05`, version 159.0 |
| `search --channel 99.99 x` | `{"ok": false, "error": {"code": "bad-channel", ...known: unstable, stable, 25.11, 26.05}}` |
| `search --kind hm programs.kitty` | 5 HM options, `sources["home-manager-options"].updated` = extranix `last_update` |
| `info programs.kitty.enable` | hm record with default/example/declarations |
| `versions firefox --limit 5` | 157.0.1 @ 8edc0c72 2026-10-08 … 5 of 210 |
| `versions no-such-pkg-xyz` | `not-found` |
| `search --kind fn concatStringsSep` | builtins.concatStringsSep + aliases, signature, first paragraph |
| `search kitty --nixpkgs github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504` | `source: local-nix`, `channel: <flakeref>`, `rev: 3ec6…`; cold run 81 s → note in `notes`; warm `nix search` is ~6 s |
| `info --kind pkg kitty --nixpkgs <same>` | `nix eval --apply` record incl. `unfree: false`, `broken: false` (0.4 s) |
| `info kitty` (no --kind) | pkg from ES, 0 opt, 0 hm, `source: "all"` |
| `search --kind all kitty --limit 3` | pkg+opt+hm+program rows, footer names both sources |

Quality gates at the last commit:
- `ruff check` and `ruff format --check` clean (src, tests, scripts).
- 169 unit tests pass (`pytest`, fixtures only, no network, ~1 s).
- `nix fmt -- --check`: no Nix file was touched on this branch, so unchanged from main.
- `nix build` (pytest in the sandbox) was run successfully on the skeleton only; it was
  NOT re-run after the discovery tests landed (machine had to stay quiet). Run it first
  on the next host. The tests were written for the sandbox: `HOME`-less cache writes are
  best-effort, `subprocess.run` is faked in `tests/test_local.py`, every HTTP call goes
  through the fake session in `tests/conftest.py`.

## Layout

```
src/nixin/http.py                 one requests Session, UA nixin/<version>, 15 s timeout, 2 retries on 502/503/504,
                                  NetworkError(code=network|http-<status>); tests swap it with set_session()
src/nixin/cache.py                $XDG_CACHE_HOME/nixin/<source>/<sha1>.json, TTL per source (TTL dict), modes
                                  normal/refresh/off (--refresh/--no-cache), Cache.fetch(source,key,ttl,fn)->(payload,cached)
src/nixin/discovery/__init__.py   register(subparsers) adds search/info/versions with their flags; DiscoveryError(code,msg);
                                  Part dataclass; _merge builds the envelope + `sources` map; _guard -> error doc / human `message`
src/nixin/discovery/nixos_search.py  ES: list_aliases/channels/resolve_channel (newest generation with documents),
                                  channel_from_ref (branch/flakeref -> channel), search(pkg|opt|program), info_package
                                  (attr, then pname + alternatives), info_option, strip_html
src/nixin/discovery/home_manager.py  extranix options-<release>.json; release_for, load, score, search, info
src/nixin/discovery/nixhub.py     versions(cache,name,limit) from search.devbox.sh/v1/pkg
src/nixin/discovery/noogle.py     kind fn: load (trimmed), search, info
src/nixin/discovery/local.py      --nixpkgs: resolve (flake metadata), search (nix search --json), info (nix eval --apply APPLY)
src/nixin/discovery/format.py     human tables / info block / versions table; NIXIN_WIDTH overrides width
src/nixin/cli.py                  edit kept to: import discovery; discovery.register(sub); skip verbs already in
                                  sub.choices; dispatch via getattr(ns, "run")
scripts/record-fixtures.py        NIXIN_LIVE=1 ... [es|hm|nixhub|noogle|local]  (re)records trimmed fixtures
tests/fixtures/{es,hm,nixhub,noogle,local}/*.json
tests/conftest.py                 FakeSession (rules: method, URL regex incl. ?params, body predicate -> fixture/status),
                                  `transport`, `es_transport`, `cache_dir` fixtures, `has()` helper
```

Output contract: README.md → "Discovery output". Keep README and this file true if you change field names.

## Half-done / rough edges

1. `pytest` alone inside `nix develop` fails with `ModuleNotFoundError: nixin` (the shell does not
   put `src/` on the path). Works with `PYTHONPATH=src:$PYTHONPATH pytest`. Fix candidates:
   `pythonpath = ["src"]` under `[tool.pytest.ini_options]` in pyproject.toml (shared file, so
   left for the integrator), or `python3 -m pip install -e .` style dev shell. README still says
   plain `pytest`.
2. `--kind all` includes `program` rows, which mostly duplicate `pkg` rows for a name query.
   Consider dropping `program` from `ALL_SEARCH_KINDS` or de-duplicating rows whose package
   already appears as a pkg row. The TUI may prefer to call kinds separately anyway.
3. `info NAME` without `--kind` reports `source: "all"` even when a single record came back;
   the record's own `source` is the authoritative one. Fine for JSON, slightly odd in the footer.
4. `channel_from_ref` is heuristic: `github:nixos/nixpkgs?rev=…` and bare 40-hex → None,
   `nixpkgs-25.11-darwin` → 25.11, anything else unknown → None. `beta` is not a channel here
   (search.nixos.org has no separate index); `stable` = newest release in the alias list.
5. `local.search` ranks by (exact last segment, depth, length); `nix search` gives only
   pname/version/description, so local pkg rows have empty license/homepage/programs. A follow-up
   could `nix eval --apply` the top N hits (0.4 s each).
6. `local` has no `--system` flag; `current_system()` derives it from `platform.machine()`.
7. First `nix search` on a rev is 55–80 s (nix builds its eval cache under `$XDG_CACHE_HOME/nix`);
   nixin notes it but prints nothing while waiting. A spinner in human mode would help the TUI less
   than a `--timeout`; both are open.
8. HM descriptions are left as published Markdown (`{file}` roles etc.); ES option descriptions
   are stripped to plain text. The TUI may want one rendering for both.
9. The extranix index is a third-party mirror of Home Manager's own options JSON. If it goes away,
   the fallback is `nix build github:nix-community/home-manager#docs-json` (gives the same JSON) or
   parsing `https://nix-community.github.io/home-manager/print.html` (needs an HTML parser;
   stdlib `html.parser` would do — see how mcp-nixos does it with BeautifulSoup).
10. `tests/test_cli.py` filters the stub-verb test to verbs not in `nixin.discovery.VERBS`; the
    truth layer will need the same filter for its verbs (same line, likely a merge conflict to
    resolve by combining both sets).

## Exact next steps

1. On the next host: `nix build` (sandbox pytest) and `nix fmt -- --check`; then
   `nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH pytest -q'` for the fast loop.
2. Re-run the live smoke list above (`nix run . -- --json search firefox` etc.). Everything uses
   public endpoints; nothing needs credentials beyond the frontend's public ES user baked into
   `nixos_search.ES_AUTH`.
3. Merge with the truth branch: both touch `cli.py` (import + one `register` call each; the
   `if verb in sub.choices` guard and the `ns.run` dispatch are layer-agnostic and should be kept
   once) and `tests/test_cli.py` (STUB_VERBS filter).
4. Decide items 1–3 above with the TUI author; then record new fixtures if any field name changes
   (`NIXIN_LIVE=1 nix develop -c python3 scripts/record-fixtures.py <section>`; `local` runs the
   host's `nix`, ~1 min cold).

## Open questions

- Should `versions` also accept `--channel` and mark which release each channel currently ships
  (ES gives the channel's version; NixHub gives the history) — i.e. a merged "where is 159.0"
  view? Cheap to add on top of the two existing calls.
- Should the truth layer reuse `http.py`/`cache.py` for anything (e.g. caching `nix eval` of the
  config), or stay purely local? The cache TTL table is in `cache.py:TTL`.
- NixHub package names are NixHub's own flat names; for `python3Packages.requests` style attrs we
  have not checked what it expects.

## How to run the tests

```
nix develop -c sh -c 'ruff check src tests scripts && ruff format --check src tests scripts'
nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH pytest -q'        # 169 tests, fixtures only
nix build                                                           # same tests in the sandbox
NIXIN_LIVE=1 nix develop -c python3 scripts/record-fixtures.py es   # re-record one source's fixtures
```

Live tests are gated by `NIXIN_LIVE=1` (`conftest.live` marker); none exist yet beyond the
recorder — the smoke list above was done by hand.
