# HANDOFF — truth layer (branch `truth`)

Written on 19BK on 2026-11-01, stopped early because the machine had to go quiet; work
continues on another host. Everything below was verified on 19BK unless marked otherwise.

## Done and verified

- `src/nixin/configrepo.py` — locating the config (`--config` > `$NIXIN_CONFIG` >
  `/etc/nixos`), the single `run_nix()` seam (tests inject a fake through
  `ConfigRepo(path, nix=...)`), `trim_nix_error()` (last `error:` block, traces/warnings
  stripped — turns the mbp13 failure into the clean message ending in
  "Scripted initrd has been removed."), `nix flake metadata --json` (HEAD, dirty, source
  store path, nix fingerprint), `nix flake archive --dry-run --json` (input name → store
  path, used to classify declaration paths as `config` / `nixpkgs` / `home-manager` / …),
  `flake.lock` parsing via `nodes.root.inputs.nixpkgs` (resolves `follows` lists; here
  the node is `nixpkgs_2`), hosts, HM users (`c.home-manager.users or {}` so hosts without
  HM give `[]`), `/run/current-system` version, `cache_key()`.
  **Live-verified** against /etc/nixos: hosts `["19BK","mbp13"]`, lock
  `3ec6363 nixos-unstable 2026-10-23`, source `/nix/store/4naz5g77…-source`, path mapping.
- `src/nixin/truth/options.nix` — the option-index helper, passed as TEXT through
  `nix eval --apply` (pure eval forbids `import /abs/path`). Walks the option tree under
  a prefix; per option: name, type description, short description, declarations +
  `declarationPositions` (file:line:col), isDefined, definition files, and with
  `values != "none"` the capped serialised value plus `definitionValues` (what EACH file
  contributes — the writer layer's gold). HM trees are rebuilt from the
  `attrsOf (submoduleWith …)` type (`getSubModules` + the user's definitions +
  `_module.args.name`, same specialArgs) — **live-verified**: `programs.kitty.enable`
  for user brucking comes out `true`, defined in `home/modules/terminal/kitty.nix`,
  declared in home-manager `modules/programs/kitty.nix:106`.
  **Live-verified prefix evals**: `services.openssh` (32 options, 3.8 s, 283 MiB) and HM
  `programs.kitty` (22 options, 3.4 s, 250 MiB).
- `src/nixin/truth/packages.nix` — system + per-HM-user packages `{name,pname,version}`
  and the `programs.<x>.enable == true` set (gated on `options.programs.<x>.enable.visible`
  so renamed aliases are never read: reading them prints `trace:` lines). The probe
  version of this expression was live-verified (4.5 s, 451 MiB on 19BK); the shipped file
  is the same logic reorganised and parses (`nix-instantiate --parse`), but was NOT run.
- `src/nixin/truth/scan.py` — brace-tracking scanner that maps an option path to a line
  in a config file (`line_heuristic: true`), and a ranked token grep for package names.
  **Live-verified** on /etc/nixos text: `services.openssh.enable` → `ssh.nix:18`,
  `programs.kitty.enable` → `home/modules/terminal/kitty.nix:5`.
- `src/nixin/truth/cache.py`, `options.py`, `packages.py`, `__init__.py` (verbs
  `status`/`where`, `register()`), `cli.py` hook (import + `truth.register(sub)` +
  a generic `ns.run` dispatch — the discovery branch should use the same
  `set_defaults(run=handler)` pattern so the merge is trivial), `pyproject.toml`
  package-data for `*.nix`.
- Tests: `tests/test_truth.py` (30 pass, 2 live skipped) with a `FakeNix` answering from
  `tests/fixtures/` (trimmed recordings of real evals). `tests/test_cli.py` adapted: the
  truth verbs are tested with `--config /nonexistent` → `config-not-found`.
- `ruff check` and `ruff format --check` clean (run from the store binaries, see below);
  `nixfmt --check` clean on both .nix helpers.

## Half-done / NOT verified

- `nixin status` and `nixin where` were never run end to end against a real host (only
  through the fake). The code paths are straightforward glue over verified pieces, but
  expect small shape bugs. Run first: `nix develop -c nixin --json status --quick`,
  then `--host mbp13` (must give `eval-failed` with the Scripted-initrd text),
  `nixin where kitty`, `nixin where services.openssh.enable`,
  `nixin where programs.kitty.enable`.
- `nix build` was NOT run (forbidden at wrap-up). Check that
  `result/lib/python3.14/site-packages/nixin/truth/options.nix` exists after it.
- The full option index (`status` without `--quick`) works in principle (the exclusion
  loop was exercised live with the draft helper, see numbers) but is far too slow cold —
  see "Next steps".
- Human (non-`--json`) rendering of `status`/`where` is unreviewed.

## Measured on 19BK (nix 2.34.9, nixpkgs 3ec6363, 8 threads, 15 GiB)

| eval | wall | peak RSS |
|---|---|---|
| `nixosConfigurations --apply attrNames` | 0.07 s (nix eval cache) | 41 MiB |
| `nix flake metadata` / `archive --dry-run` | 0.08 s each | 41 MiB |
| HM users of 19BK | 1.8 s | 194 MiB |
| packages.nix (system + HM packages + programs) | 4.5 s | 451 MiB |
| mbp13 `system.build.toplevel.drvPath` (fails: Scripted initrd) | 5.1 s | 475 MiB |
| 19BK `system.build.toplevel.drvPath` | 20.5 s cold, 34.7 s on a busy box; NOT cached by nix's eval cache | 1356 MiB |
| 19BK `config.assertions` failed-list only | 7.7 s | 601 MiB |
| prefix index `services.openssh`, values=all | 3.8 s | 283 MiB |
| HM prefix index `programs.kitty`, values=all (re-evaluates the HM submodule) | 3.4 s | 250 MiB |
| full NixOS index, values=none, final successful pass | 30.5 s | 1639 MiB → 17 015 options, 10.6 MB JSON |
| full HM index (brucking), values=none, final pass | 18.1 s | 1055 MiB → 4 093 options, 2.4 MB JSON |
| full NixOS index, values=defined | 30–38 s per attempt, more failures | 1650 MiB |

The full index needs the exclusion loop: `definitionsWithLocations`/`isDefined` force every
definition's `mkIf` condition and some nixpkgs/HM modules raise *type* errors there when
their service is off (`null.version`), which `builtins.tryEval` cannot catch. Each entry is
wrapped in `addErrorContext "nixin-option <name>"`; `options.evaluate()` reads the marker
from the failed eval's stderr, excludes the option, retries. On this lock the NixOS tree
needed 8 exclusions (≈22–27 s each): `hardware.nvidia.gsp.enable`, `hardware.nvidia.open`,
`hardware.nvidia.powerManagement.kernelSuspendNotifier`, `services.dawarich.redis.host`,
`services.greenlight.redis.host`, `services.immich.redis.host`, `services.kener.redis.host`,
`services.nagios.cgiConfigFile`; with values=defined also
`services.grafana-image-renderer.settings`, `services.immich-kiosk.settings`. HM needed 4:
`programs.google-chrome-beta.package`, `programs.google-chrome-dev.package`,
`programs.radicle.uri.web-rad.browser`, `programs.vscode.haskell.hie.executablePath`.
Cold total for a first `status` would be ≈ 4 min; unacceptable.

## Next steps (in order)

1. Persist the exclusion list per (nixpkgs rev, home-manager rev) — the failures are
   properties of the modules, not of the config — e.g. `$XDG_CACHE_HOME/nixin/truth/
   exclude-<nixpkgs-rev>.json`, seeded with the lists above, loaded by `options.index()`
   and extended whenever the loop learns a new one. That turns a cold full index into
   one 30 s pass (+ 18 s HM).
2. Decide what `status` does by default: `--quick` (no index, ≈ 25 s cold: toplevel 20 s +
   packages 4.5 s, then ≈ 0.3 s from cache) is probably the right default and the index
   an explicit `--index`/background step. Consider the `config.assertions` check
   (7.7 s / 601 MiB) as the "does it evaluate" test with a documented caveat, keeping
   toplevel for `plan`.
3. Run the live checks listed under "Half-done", fix shape bugs, then `nix build`.
4. `where <package>` only links a package to `programs.<name>.enable`; the general link
   (a module file in an input that adds the package → which user-defined options that file
   declares) is `options.options_declared_in()` and needs the full index — wire it in
   once (1) makes the index cheap.
5. Live tests: `NIXIN_LIVE=1 nix develop -c pytest -k live` (two small ones exist; add
   one for `status --quick` and the mbp13 error once verified).

## Open questions

- Should an `eval-failed` status still carry `config`/`lock`/`running` (known without the
  eval)? Today the error doc is strictly `{ok, verb, host, error}` per the contract.
- `definitionsWithLocations` has no line numbers; lines come from the text scanner. Is
  `line_heuristic` good enough for the writer, or should the writer re-scan itself?
- Inline modules (attrsets inside the `nixosSystem` call) are attributed by nixpkgs to
  `<nixpkgs>/flake.nix` (`setDefaultModuleLocation` in `nixos/lib/eval-config.nix`);
  reported as `source: "inline"` with repo-wide `candidates`. Fine for `where`; the
  writer must not edit based on it.
- Whether `status` should run the HM index per user eagerly (18 s each) or lazily.

## How to run the tests

```
nix develop -c pytest                 # unit tests, fixtures only (no nix needed)
NIXIN_LIVE=1 nix develop -c pytest    # + the live ones (run nix against $NIXIN_CONFIG)
nix develop -c ruff check src tests && nix develop -c ruff format --check src tests
nix fmt -- --check                    # or: nixfmt --check src/nixin/truth/*.nix
nix build                             # runs pytest in the sandbox (nix unavailable there)
```

Without the dev shell (how the last run here was done, because `nix develop` would have
rebuilt the shell env): `PYTHONPATH=src /nix/store/…-python3.14-pytest-9.1.1/bin/pytest -q`
and `/nix/store/…-ruff-0.17.2/bin/ruff check src tests`.

Scratch material from this session (not in the repo): `/tmp/nixin-scratch/` holds the
bench logs (`bench.log`, `bench2.log`), the draft helper and the full recorded outputs
(`out-ssh.json`, `out-kitty.json`) the fixtures were trimmed from.
