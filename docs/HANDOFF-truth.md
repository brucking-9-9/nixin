# HANDOFF — truth layer (branch `truth`)

Started on 19BK, continued on mbp13 on 2026-11-01 (headless, as `master`). Everything below
"verified" was run on mbp13 against the workspace config `~/nixos` (read only) unless marked.

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
```

Scratch from the mbp13 session (not in the repo): `/tmp/nixin-mbp13/`, with `bench.sh` (the
`time -v` wrapper), `*.out` (JSON of each run), `*.time` (`time -v` + load), `split.py`, and
`nixos-head/` (the clone used for the `eval-failed` check).
