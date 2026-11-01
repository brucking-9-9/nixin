# HANDOFF: discovery layer (branch `discovery`)

Last written 2026-11-01 on mbp13 (headless, as `master`), continuing the 19BK
session's work. Nothing is pushed; 19BK fetches this branch over ssh.

## State

Functionally complete, built and verified. `nix build` passes in the sandbox,
and every README command has been re-run live from a second machine. A review
pass found and fixed the problems listed below. Nothing in this layer is half-done.
What remains is product decisions (see "Rough edges") and the merge with the
truth branch.

## Verified on mbp13 (2026-11-01)

Quality gates at HEAD:
- `nix build`: pytest in the sandbox, 182 passed (16 live tests skipped there).
- `nix develop -c pytest`: plain `pytest` works now (`pythonpath = ["src"]`).
- `ruff check` and `ruff format --check` (src, tests, scripts): clean.
- `nix fmt -- --check flake.nix package.nix`: clean (no Nix file touched on this branch).
- `NIXIN_LIVE=1 NIXIN_LIVE_NIXPKGS=github:nixos/nixpkgs/3ec6…  pytest tests/test_live.py`: 16 passed (28 s).

Live by hand (run from source, cold cache):

| command | seen on mbp13 |
|---|---|
| `search firefox` | firefox 159.0 … from `nixos-52-unstable-1c89083e…`, 0.7 s; second run `cached: true` |
| `search --kind opt services.openssh` | openssh options with type and plain-text description |
| `search --kind program rg` | rg → ripgrep 15.2.0 (`exact`), then substring rows |
| `info --kind pkg kitty` / `--channel stable` | full record; stable → `26.05`, rev `fa137313…` |
| `search --channel 99.99 x` | `bad-channel`, "known: unstable, stable, 25.11, 26.05" |
| `search --kind hm programs.kitty`, `info programs.kitty.enable` | HM rows, `updated` from extranix |
| `search --kind hm git --channel 20.03` | `bad-channel` (extranix 404) |
| `versions firefox --limit 5` / `versions no-such-pkg-xyz` | 5 of 210 / `not-found` |
| `search --kind fn concatStringsSep` | builtins + lib aliases with signature |
| `search kitty --nixpkgs github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504` | `local-nix`, rev 3ec6…; **cold 46 s** (note in `notes`), warm other query 3.9 s, cached 0.2 s |
| `info --kind pkg kitty --nixpkgs <same>` / `vscode` | 0.5 s; `unfree: false` / `unfree: true, license: [unfree]` |
| `info kitty --nixpkgs <bare rev>` | ok, `local-nix`; ES and HM in `sources` with `bad-channel` |
| `search --nixpkgs <rev> -- -grep` | regex starting with `-` reaches nix as a query |

That rev had to be fetched first on this host (`nix flake metadata` took 1:43). It is
the `nixpkgs` lock of `~/nixos`. The discovery layer does not evaluate `~/nixos`.
At the time, `~/nixos` (HEAD `ebd8190`) had 4 uncommitted changes in
`hosts/mbp13/{companion,default,kiosk}.nix` and `hosts/mbp13/hypr/default.nix`
(another session's kiosk work) and no stashes. It was only read, never touched.

`--json` sweep: 32 commands (every verb, kind and error path above, plus `--refresh`,
`--no-cache`, `--limit 0`, `'***'`, empty query, bad regex, missing rev). Each one
printed exactly one JSON document on stdout, nothing on stderr, and exited 0/1
matching `ok`. With the network cut (dead `HTTPS_PROXY`), every source mapped to
`network` in 1–3 s, also with empty stderr.

## Fixed in this session (one commit each)

- `ccb7e48` **ES `rev` could be wrong.** Searches went to the channel *alias*, which
  Hydra repoints to a new rev several times a day, while `rev` came from the alias list
  (cached 6 h). Search and count now go to the concrete index. Superseded indices stay
  up for about a week (seen live), and a 404 still maps to `bad-channel` + `--refresh`.
- `07e5806` **`_merge` lost answers.** With `--kind all`, if ES answered pkg but failed opt
  while HM was down, the whole document became an error. If the failing kind came first,
  ES kept a null channel/rev. Now: a source counts as answered once any of its kinds
  answered, `error` holds the first failure, and the document fails only when nothing
  answered. The multi-source top-level `channel` is now the concrete one (`stable` →
  `26.05`), as README promises.
- `3583bba` **local:** nix gets `stdin=DEVNULL`, so it can never stop to ask (e.g. about a
  flake's `nixConfig`) until the 900 s timeout. `--` goes before the query. Non-JSON
  output maps to `nix-failed` instead of a traceback. The 30-day eval cache key includes
  a digest of `APPLY`.
- `1f4cfe0` **cache:** per-process temp file for each write (concurrent UI calls), a future
  `stored` timestamp is a miss, a relative/empty `XDG_CACHE_HOME` is ignored (XDG spec).
- `5fd5276` ES `size` capped at 10 000 (`--limit 20000` was an opaque `http-400`).
- `b0ae86a` Any other exception becomes error code **`internal`** (type + file:line)
  instead of a traceback with empty stdout. In a multi-source call it is that source's
  error. New code, documented in README.
- `4f1adc0` `info` not-found names only the kinds that were asked; the others are listed
  as "not asked: Home Manager option (network)".
- `8dfcc5b`, `27e5580` README contract matches output: `alternatives` only on a pname
  match; versions doc carries `programs`; per-source extras (`updated`, `system`, `total`).
- `668ad4d` `pythonpath = ["src"]` (approved shared-file edit); `75870f3` live smoke tests.

## Layout

```
src/nixin/http.py                 one requests Session, UA nixin/<version>, 15 s timeout (HM/noogle 60 s),
                                  2 retries on 502/503/504, NetworkError(code=network|http-<status>)
src/nixin/cache.py                $XDG_CACHE_HOME/nixin/<source>/<sha1>.json, TTL table, normal/refresh/off,
                                  best-effort atomic writes (sandbox-safe), Cache.fetch -> (payload, cached)
src/nixin/discovery/__init__.py   register(); DiscoveryError; Part; _fan_out (per-kind failures);
                                  _merge (envelope + sources); _guard (error doc incl. `internal`; human render)
src/nixin/discovery/nixos_search.py  ES: aliases -> Channel(name, alias, index, rev), resolve_channel,
                                  channel_from_ref, search pkg|opt|program, info_package, info_option
src/nixin/discovery/home_manager.py  extranix options-<release>.json; release_for, load, score, search, info
src/nixin/discovery/nixhub.py     versions() from search.devbox.sh/v1/pkg
src/nixin/discovery/noogle.py     kind fn: load (trimmed), search, info
src/nixin/discovery/local.py      --nixpkgs: resolve (flake metadata), search (nix search --json), info (nix eval --apply)
src/nixin/discovery/format.py     human tables / info block / versions table; NIXIN_WIDTH
src/nixin/cli.py                  layer edit only: import discovery; discovery.register(sub); skip verbs in
                                  sub.choices; dispatch via ns.run
scripts/record-fixtures.py        NIXIN_LIVE=1 ... [es|hm|nixhub|noogle|local] re-records trimmed fixtures
tests/conftest.py                 FakeSession, transport / es_transport / cache_dir fixtures, has(), live marker
tests/test_live.py                live smoke (NIXIN_LIVE=1; nix cases also NIXIN_LIVE_NIXPKGS)
tests/fixtures/{es,hm,nixhub,noogle,local}/*.json
```

Output contract: README.md → "Discovery output". Keep README and this file true if
field names change.

## Rough edges (open; product decisions, not bugs)

1. `--kind all` includes `program` rows, which mostly duplicate `pkg` rows for a name
   query. Drop `program` from `ALL_SEARCH_KINDS`, or de-duplicate rows whose package is
   already a pkg row. Decide with the TUI author.
2. `info NAME` without `--kind` says `source: "all"` even when one record came back
   (other sources answered with 0). The record's own `source` is authoritative.
3. `channel_from_ref` is heuristic (`?rev=` / bare hex → None, `nixpkgs-25.11-darwin` →
   25.11). `beta` is not a channel; `stable` = newest release in the alias list.
4. `local-nix` search rows carry only pname/version/description. A follow-up could
   `nix eval --apply` the top N hits (~0.5 s each).
5. No `--system`; `local.current_system()` derives it from `platform.machine()`.
6. First `nix search` on a rev: 46 s on mbp13, 55–80 s on 19BK. nixin notes it but
   prints nothing while waiting, and the hard timeout is 900 s. A `--timeout` flag is open.
7. HM descriptions stay Markdown as published; ES option descriptions are plain text.
8. extranix is a third-party mirror of HM's options JSON. Fallback if it disappears:
   `nix build github:nix-community/home-manager#docs-json`, or parse HM's `print.html`.
9. `local.resolve` caches `nix flake metadata` for 1 h per flakeref string. For a moving
   ref (branch name, local checkout) a query within that hour is keyed and labelled with
   the previous rev. Harmless for the intended use (a locked rev).
10. `network` messages include urllib3's long `HTTPSConnectionPool(...)` repr. The code
    is what UIs should branch on.
11. `cli.main` itself has no catch-all. Discovery guards its own verbs (`internal`); the
    truth layer needs the same, or the integrator moves the guard into `cli.main`.
12. Idea, not done: ES keeps superseded indices for about a week, and today one of them is
    `nixos-52-unstable-3ec63636…`, exactly `~/nixos`'s lock. Fast exact-rev package *and
    option* answers without local eval would be possible when the locked rev is recent.
    Rev matching is cheap: `_cat/indices`. Out of scope for this pass (new source behaviour).

## Exact next steps

1. 19BK: fetch this branch, `nix build`, review the commits above, open the PR.
2. Merge with the truth branch. Both touch `cli.py` (keep the `if verb in sub.choices`
   guard and the `ns.run` dispatch once; one import + one `register` per layer) and
   `tests/test_cli.py` (the STUB_VERBS filter: combine both layers' verb sets).
3. Decide rough edges 1–2 with the TUI author. If a field name changes, update README
   and re-record fixtures (`NIXIN_LIVE=1 nix develop -c python3 scripts/record-fixtures.py <section>`;
   `es` fixtures now record against the concrete index, like the code).

## Open questions (unchanged)

- Should `versions` take `--channel` and mark which version each channel ships now
  (ES has the channel's version, NixHub the history)? Cheap on top of the two calls.
- Should the truth layer reuse `http.py`/`cache.py` (e.g. to cache `nix eval` of the
  config)? TTLs are in `cache.py:TTL`.
- NixHub names are its own flat names; `python3Packages.requests`-style attrs are untested.

## How to test

```
nix develop -c sh -c 'ruff check src tests scripts && ruff format --check src tests scripts'
nix develop -c pytest -q                       # 182 unit tests, fixtures only, ~1 s
nix build                                      # same tests in the sandbox
nix fmt -- --check flake.nix package.nix
NIXIN_LIVE=1 nix develop -c pytest -q tests/test_live.py        # 13 live cases, real services
NIXIN_LIVE=1 NIXIN_LIVE_NIXPKGS=github:nixos/nixpkgs/<locked rev> \
  nix develop -c pytest -q tests/test_live.py                    # + 3 nix cases (rev must be fetchable)
nix run . -- --json search firefox             # by hand
NIXIN_LIVE=1 nix develop -c python3 scripts/record-fixtures.py es   # re-record one source
```
