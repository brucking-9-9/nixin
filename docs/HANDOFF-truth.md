# HANDOFF — truth layer (branch `truth`)

Started on 19BK, continued on mbp13 on 2026-11-01 (headless, as `master`). Everything below
"verified" was run on mbp13 against the workspace config `~/nixos` (read only) unless marked.

## `list` (branch `list-verb`, mbp13, 2026-11-02)

The gap: `status` gave counts only and `where NAME` one package at a time, so there was
no way to *see* what a host installs. `nixin list [--home USER] [--scope
system|home|all] [--filter TEXT] [--programs] [--json]` shows it. Contract:
`docs/CONTRACT.md`, "`list`".

- **Code.** `truth/listing.py` (rows, scope resolution, the filter, folding,
  `declared[]`, programs), `list_`/`_list` in `truth/__init__.py` (registered like
  `status`/`where`, same `unknown-host`/`--refresh`/`--config` handling),
  `render.listing` in `truth/render.py`. In `cli.py`, only the `VERBS` entry.
- **Data.** The rows are `packages.collect()`, the eval and cache entry `status` and
  `where` already use (`truth/<host>-<key>-packages.json`, fingerprint-keyed). Verified
  live: `status` run after `list` hit `packages` and evaluated only `toplevel`. A
  second `list` runs no eval. `--refresh` evaluates again. No new cache file.
- **Folding.** The evaluated lists repeat packages: mbp13 has 425 entries and 340
  distinct ones (the same `git`/`tmux`/KDE packages added by several modules). `list`
  shows one row per (scope, name, pname, version) and reports the dropped repeats as
  `folded`, so `status.packages` = `count + folded` for the listed scopes. The same
  package in system and home is two rows.
- **`declared[]` (best effort, documented as such).** No eval: the writer's scanner
  (`writer._scope(…, use_eval=False)`, `Workspace.module_defs`, `NixFile.list_items`)
  walks the host's and each user's module closure from `flake.nix`, generated
  `installer/*.nix` included (`package_decls` skips those, so the loop is in
  `listing.declared`). Each list element gets candidate names (`pkgs.x`/`x` → `x`,
  `a.b` → `b` and `a-b`, camelCase → kebab, `_` → `-`, `callPackage ./dir/package.nix`
  → `dir`, `write*Bin "name"` → `name`). Each package row is matched on pname, name, or
  name minus the version. The scan takes 0.04–0.08 s on `~/nixos`. Live hit rate:
  mbp13 73 of 340 rows, 19BK 90 of 328 (brucking 65 of 190). The rest come from modules
  (NixOS defaults, `programs.x.enable`, KDE), or from elements the name rule misses:
  let-bound helpers whose derivation name differs (`picker` → `kiosk-picker`),
  `mode "music" [ … ]` calls, renamed pnames (`dust` → `du-dust`, `pandoc` →
  `pandoc-cli`), aliases (`networkmanagerapplet`). Truth imports the writer lazily
  inside `listing.declared`, because the writer imports `truth.scan` at module level.
- **Human output.** `scope  name  version  (user)  file:line`, sorted by name, a footer
  (count per scope, folded repeats, cached or the eval's seconds). `NIXIN_WIDTH` is
  honoured with the same rule as discovery's tables (`render.term_width()`, a copy, so that
  truth does not import discovery and with it `requests`). On a narrow terminal the
  name column gives way (down to 16), then the location is cut from the left and dropped
  below 12 columns, then the footer goes onto separate lines.
- **`--programs`.** Shown, because it is already in the status data (`packages.nix`
  collects `programs.<x>.enable` per scope). Same scope and filter rules. HM programs
  come from values only (as in `status`), so `home-manager`, `man`, `ssh` appear.

Verified live, read only, against `~/nixos` at `81f19b7` (one uncommitted file,
`installer/history.jsonl`, from another session; no stash entries), dev shell python
on the worktree's `src`:

| run | wall | peak RSS | result |
|---|---|---|---|
| `--json --host mbp13 list`, nixin cache cold | 2.54 s | 339 MiB | 340 rows, 85 folded, 73 with `declared` |
| same, warm | 0.36 s | 42 MiB | `cache.hits = [packages]` |
| `--host mbp13 list` (human), warm | 0.36 s | 42 MiB | |
| `--json --host 19BK list --home brucking`, cold | 3.21 s | 452 MiB | 190 rows, 2 folded, 65 with `declared` |
| same, warm | 0.41 s | 42 MiB | |
| `--json --host 19BK list --refresh` | 3.07 s | 452 MiB | 328 rows (138 system + 190 brucking), 67 folded |
| `--json --host 19BK status` right after | 21.8 s | 1.36 GiB | `hits [packages]`, `misses [toplevel]` |

The box was quiet (load 1.0–1.7). nix's own eval cache was as other sessions left it.
Also checked by hand: `--filter KITTY`, `--programs`, `NIXIN_WIDTH` 120/80/50.
Scratch: `/tmp/nixin-list-bench/` (`bench.sh`, `*.out`, `*.time`).

Tests: `tests/test_truth.py` (`test_list_*`, the FakeNix fixtures: contract, scopes,
`--home`, filter, programs, cache hit and `--refresh`, every error, `declared` from a
small module tree with an `imports`ed conditional list, folding, element names and
matching, human output at 120/40 columns). `tests/test_integration.py`: `list` in the
envelope sweep and `test_error_codes`, plus `test_list_flow` on `writer-config` (the
hand-written lists, `btop` listed in two files, `cowsay` found in the generated
`installer/mbp13.nix` after `add`, 19BK's HM user `alice`, `unknown-user` on mbp13).

Not done / next:

- `declared[]` could take the evaluated attribution where it is already cached: the
  `environment.systemPackages` / `home.packages` prefix query `where PACKAGE` runs
  gives each config file's derivations exactly (`definitionValues`). With it, `list
  --exact` (one prefix eval per scope, a few seconds cold) would place every
  config-listed package, including the let-bound helpers the name rule misses. The
  line would still come from the scanner.
- A `via` field for packages with an empty `declared[]` that an enabled
  `programs.<pname>` explains (19BK brucking: `kitty`, `btop`, `bat`, `git`, …). The
  data is already in the document.
- The TUI and GUI do not show `list` yet (a Packages page: the rows, the filter, and
  Remove on a row with a `declared` entry).

## Done and verified

- **Build and checks.** `nix build` passes (34 tests in the sandbox, 2 live skipped). The
  output ships `nixin/truth/options.nix` and `packages.nix`, and the built `bin/nixin` answers
  `where` against `~/nixos`. `ruff check`, `ruff format --check` and `nix fmt -- --check .`
  are clean (via `nix develop -c …`). `pyproject.toml` now has `pythonpath = ["src"]`, the
  same line the discovery branch added, so `nix develop -c pytest` works and the merge is clean.
- **End to end on mbp13** (`--config ~/nixos`), JSON and human output:
  - `status` for mbp13 (the default host here) and for `--host 19BK`;
  - `--host mbp13` at the committed HEAD (a `/tmp` clone of `~/nixos`) gives the clean
    `eval-failed` "Failed assertions: … Scripted initrd has been removed." with exit 1 and no
    traceback;
  - `where kitty` (mbp13: system package listed in `hosts/mbp13/default.nix:198`; 19BK:
    `programs.kitty.enable` in `home/modules/terminal/kitty.nix:5`, which home-manager's
    `modules/programs/kitty.nix` turns into `home.packages`);
  - `where services.openssh.enable` (mbp13 `hosts/mbp13/default.nix:240`, 19BK `ssh.nix:18`);
  - `where programs.kitty.enable --host 19BK` (HM, user brucking);
  - prefix mode `where services.openssh`, sub mode
    `where services.openssh.settings.PermitRootLogin`;
  - `not-found`.
- **Exclusions persisted** (`options.ExclusionStore`): one file per (nixpkgs rev,
  home-manager rev), `$XDG_CACHE_HOME/nixin/truth/exclude-<nixpkgs12>-<hm12>.json`, holding
  `{"nixos": {name: first line of the error}, "home": {...}, "nixpkgs": rev,
  "home-manager": rev}`. It is seeded from `SEED_EXCLUSIONS` (the 8 NixOS + 4 HM options
  learned on 3ec6363 / c216bf5; mbp13 hit exactly the same 8 as 19BK, so they belong to the
  modules, not the config), extended when the retry loop learns one, and written on the first
  pass. `--refresh` still uses it; delete the file to re-learn. Prefix queries (`where`) do
  not use it.
- **`status` is quick by default.** `--quick` is still accepted and means the same thing.
  `--full` builds the index of NixOS and of each HM user. Without `--full`, `options` shows
  the summaries an earlier `--full` cached for the same tree (separate small cache files, so
  the 10 MB index is never read), else `{}`.
- **`defined` fixed.** `isDefined` is true for every option that has a default (the module
  system adds the default as a definition from the declaring file). `defined` now means "set
  by more than its own default" (`options.is_set`: definitions ≠ `[declarations[0]]`). New
  field `in_config` = defined in a file of the config tree. 19BK: nixos 239 set (86 by the
  config) of 17 015; home brucking 156 (132) of 4 093; mbp13 nixos 322 (140) of 16 989.
  Before, "14 548 defined". Prefix mode of `where` filters with the same rule
  (services.openssh: 4 of 32 instead of 18).
- **Bugs found live and fixed:**
  - `where <system package>` never looked at `environment.systemPackages`, because scope
    `nixos` and `system` were compared;
  - a module imported as a directory (`./hosts/mbp13`) is reported by nix as the directory;
    `locate_path` now maps it to `…/default.nix`, so the line scan works;
  - the scanner now also matches a line that extends the option path
    (`settings.PermitRootLogin = …` defines `settings`);
  - `mentions` drop files under another host's directory (a path component naming another
    host);
  - a cached summary carries `SUMMARY_VERSION` in its key, so a meaning change misses.
- **Human rendering** reviewed and rewritten (`truth/render.py`): one fact per line, aligned
  labels, values in Nix syntax capped to a line, "cached" instead of stale seconds, "running …
  on mbp13, not 19BK" when asking about another host, mentions capped to 4 strong ones.
  Tested in `test_human_rendering`.

## Half-done / not verified

- A cold `status --full` for 19BK was measured with toplevel/packages warm; the all-cold
  number exists for mbp13 only (and under heavy load, see below).
- `nix fmt -- --check` *without* a path fails on `main` too: the skeleton's formatter is
  bare `nixfmt`, `nix fmt` passes no paths, and nixfmt then reads empty stdin.
  `nix fmt -- --check .` works (prints a deprecation note). The fix belongs in `flake.nix`
  (shared with discovery), so it is not done here. `nixfmt-tree` has no `--check`, so the fix
  would be a small `writeShellApplication` that appends `git ls-files '*.nix'` when no file
  argument is given.
- The HM seed messages are placeholders ("seed list (failed on 19BK)"); the real ones get
  recorded if the list is ever re-learned.

## Measured on mbp13 (nix 2.34.9, 8 cores, 15 GiB, nixpkgs 3ec6363, HM c216bf5)

The box was shared with three other Claude sessions and their nix evals. The 1-minute load
was 3–9 for the first runs and 13–26 later, so wall times are upper bounds; peak RSS is not
affected. `~/nixos` stayed at HEAD `ebd8190` + 4 uncommitted files under `hosts/mbp13/`
(another session's work; no stash entries). With those edits mbp13 evaluates; at HEAD it
does not.

| run | wall | peak RSS | load before → after |
|---|---|---|---|
| `nix build` (sandbox pytest) | 5–11 s | 240 MiB | — |
| mbp13 `status`, OLD default (full index), cold: 9 index passes | 8 min 22 s | 3.1 GiB | 5.9 → 9.0 |
| … of which index (8 exclusions learned, ≈50 s per pass) | 458 s | | |
| 19BK `status` quick, cold (toplevel 61.5 s + packages 8.1 s) | 70 s | 1.34 GiB | 5.0 → 12.8 |
| 19BK / mbp13 `status`, warm | 0.5–1.8 s | 41–60 MiB | |
| 19BK `status --full`, seeded store, toplevel/packages warm: NixOS index 24.6 s + HM 27.7 s, **1 pass each** | 52.7 s | 1.61 GiB | 4.9 → 8.5 |
| mbp13 `status --full --refresh`, all cold, seeded: toplevel 65 s, packages 2.8 s, index 184 s **1 pass** | 4 min 13 s | 3.1 GiB | 8.5 → 20.8 |
| mbp13 toplevel alone | 263 s | 2.88 GiB | 21.6 |
| mbp13 index alone (seeded: 16 989 options, 0 retries) | 106 s | 3.10 GiB | 26 |
| `status --full`, warm (reads 13 MB of index JSON) | 2.7 s | 59 MiB | |
| mbp13 at HEAD → `eval-failed` | 4.3 s | 475 MiB | 13.3 |
| `where services.openssh.enable`, cold (one prefix eval) | 3.1 s (mbp13) / 6.2 s (19BK) | 196 / 222 MiB | ~13 |
| `where programs.kitty.enable --host 19BK`, cold / warm | 3.0 s / 0.7 s | 195 / 42 MiB | ~14 |
| `where kitty --host 19BK`, cold (2 prefix evals) | 10.5 s | 427 MiB | ~13 |

19BK numbers from the first session (quiet box): toplevel 20.5 s / 1.36 GiB, packages
4.5 s, full NixOS index 30.5 s / 1.6 GiB, HM index 18.1 s / 1.0 GiB.

## Next steps (in order)

1. Run independent evals in parallel. With `--full`, NixOS and HM indexes run at the same
   time (19BK: 24.6 + 27.7 s → ≈28 s, peak ≈2.6 GiB); `ExclusionStore.add` already takes a
   lock. In the quick path, toplevel and packages run at the same time (saves 3–8 s). Measure
   on a quiet box.
2. The cold cost of a quick `status` is the toplevel eval (19BK 20–60 s; mbp13 41–263 s,
   2.9 GiB). Consider `config.assertions` (7.7 s / 601 MiB on 19BK) as the "does it
   evaluate" check, with the caveat that it skips build-time failures, and keep toplevel for
   `plan`.
3. `where <package>` general link: a module file in an input that adds the package → the
   user-defined options that file declares (`options.options_declared_in()`, needs the
   index; now one `--full` away).
4. Live tests (`NIXIN_LIVE=1`): `status` quick against `$NIXIN_CONFIG`, and `eval-failed`
   against a clone pinned to a failing rev (ebd8190 of the workspace fails for mbp13).
5. mbp13's evals peak at about 3 GiB each (toplevel and index), twice 19BK's on the same box
   and nixpkgs, and run much slower (index pass 106–184 s vs 25 s for 19BK). Load explains
   the time only partly. Re-measure on a quiet box and find what in the mbp13 config costs
   that (it matters for parallelising, step 1: two 3 GiB evals at once on a 15 GiB box).

## Open questions

- A seeded exclusion hides an option the index could evaluate on a host that uses it
  (`hardware.nvidia.open` with nvidia enabled). `where` still answers for it. Should the
  index retry excluded options that the config defines, or key the store per host as well?
- Should an `eval-failed` status still carry `config`/`lock`/`running` (known without the
  eval)? Today the error doc is strictly `{ok, verb, host, error}` per the contract.
- `definitionsWithLocations` has no line numbers; lines come from the text scanner. Is
  `line_heuristic` good enough for the writer, or should the writer re-scan itself?
- Inline modules (attrsets inside the `nixosSystem` call) are attributed by nixpkgs to
  `<nixpkgs>/flake.nix`; reported as `source: "inline"` with repo-wide `candidates`. On
  this config the only such definition is nixpkgs' own `nixpkgs.flake.source`. Fine for
  `where`; the writer must not edit based on it.
- `in_config` counts files of the config tree only (inline definitions not counted).

## Note: `outdated` builds on truth (branch `outdated-verb`, 2026-11-02)

`nixin outdated` (`src/nixin/outdated/`, `docs/HANDOFF-outdated.md`) uses three truth
pieces and changes none of them: `TruthCache` (names `outdated-lock` and `outdated-target`;
the target's salt holds the target commit, so the key is (fingerprint, host, target rev)),
`configrepo.parse_lock` (the nixpkgs node, `nixpkgs_2` here), and `ConfigRepo.nix` as the
test seam. Its eval helper `outdated/outdated.nix` copies `pkgInfo` from
`truth/packages.nix` and adds `meta.position`, the kernel version and `system.nixos.release`.
If `packages.nix` changes how a package is read, change both. One eval gives the
packages and the kernel together (~2 s), so outdated does not read truth's `packages`
cache. Unifying them (packages.nix grows `position`/`kernel`, outdated reuses that entry
for the lock side) would save one ~2 s eval on a cold run.

## How to test

```
nix develop -c pytest                 # unit tests, fixtures only (no nix needed)
NIXIN_LIVE=1 nix develop -c pytest    # + the live ones (run nix against $NIXIN_CONFIG)
nix develop -c ruff check src tests && nix develop -c ruff format --check src tests
nix fmt -- --check .                  # the path matters, see "Half-done"
nix build                             # runs pytest in the sandbox (nix unavailable there)

# end to end, from the worktree (the dev shell's python; truth needs no dependencies):
PYTHONPATH=src python -m nixin.cli --json status --config ~/nixos
PYTHONPATH=src python -m nixin.cli --host 19BK status --full --config ~/nixos
PYTHONPATH=src python -m nixin.cli --host 19BK where programs.kitty.enable --config ~/nixos

# list (cli.py imports every layer now, so keep the dev shell's PYTHONPATH for requests):
nix develop -c pytest -q -k list tests/test_truth.py tests/test_integration.py
nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH python3 -m nixin.cli --host mbp13 list --config ~/nixos'
nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH python3 -m nixin.cli --host 19BK list --home brucking --config ~/nixos'
nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH NIXIN_WIDTH=50 python3 -m nixin.cli --json --host 19BK list --filter git --programs --config ~/nixos'
```

Scratch from the mbp13 session (not in the repo): `/tmp/nixin-mbp13/`, with `bench.sh` (the
`time -v` wrapper), `*.out` (JSON of each run), `*.time` (`time -v` + load), `split.py`, and
`nixos-head/` (the clone used for the `eval-failed` check).
