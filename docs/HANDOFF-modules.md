# HANDOFF: modules (branch `modules-optindex`)

mbp13, headless, 2026-11-02/03. The idea comes from optnix (water-sucks/optnix): build an
option index per module system ("scope") at build time, so option search is instant and
covers the host's own module set; and ship `nixosModules`/`homeModules` so a config
enables nixin declaratively.

## State

Done, tested, run live on mbp13:

- **`lib/options-index.nix`** (flake output `lib.optionsIndex`): `entries`, `document`,
  `mkIndex`. It turns a module system's `options` into one JSON document through
  `lib.optionAttrSetToDocList`: name, loc, type, description, default and example (the
  literalExpression/literalMD text), declarations `{source, file, line}` relative to
  roots (config, nixin, nixpkgs, home-manager), readOnly/internal/visible. A field that
  throws becomes null and its name goes into `errors[]` (`tryEval`; on mbp13 2 of 28 042,
  both defaults). Submodules' `_module.*` options are dropped (6 340 of 34 366 entries on
  mbp13). `prune` leaves out sub-options (NixOS: `home-manager.users`). `stripPrefix`
  takes `home-manager.users.<name>` off HM-as-NixOS-module names. String context is
  discarded and the content goes through `builtins.toFile`, so the index never makes
  the build realise a package. One derivation per scope
  (`nixin-options-{nixos,home}.json`, `passthru.{doc,options,count}`).
- **`nixosModules.default`** (`modules/nixos.nix`) and **`homeModules.default`**
  (`modules/home.nix`; `homeManagerModules.default` is the same module):
  `programs.nixin.{enable, package, optionIndex.{enable, configRoot, prune, file},
  outdated.{enable, interval, notify, notifyTimeout, quietHours}, pending.{enable,
  interval}, authoritative, workspace}`, plus `user` (NixOS, `ConditionUser`). Shared
  pieces: `modules/options.nix` (declarations), `modules/common.nix` (roots, config-root
  detection, the timers' command lines, the pending path watch). The index goes to
  `/etc/nixin/options-nixos.json` (`environment.etc`) and
  `$XDG_CONFIG_HOME/nixin/options-home.json` (`xdg.configFile`). The default package is
  `python3Packages.callPackage <nixin>/package.nix {}` on the config's pkgs, so no
  overlay is needed.
- **`nixin-state`** (`src/nixin/state.py`, a fourth entry point). This is the config
  lane's hand-wired `home/modules/tools/nixin/state.py` from the hub branch
  `nixin-integration`, moved into nixin. It now runs the verbs in-process and has a
  `--no-notify` flag. The modules' `outdated` and `pending` timers run it; the unit
  names, schedules, nice levels, timeouts and path watch are the lane's.
- **`src/nixin/optindex.py`**: finds the two files (`NIXIN_INDEX_NIXOS`,
  `NIXIN_INDEX_HOME`, `NIXIN_NO_INDEX`), works out which generation each belongs to
  (system: the generation whose `etc` links the same store file, running one first;
  HM: `gcroots/current-home` + `profiles/home-manager-N-link`), `fresh`, `applies`
  (host, HM user). It reads the header without parsing the list (`peek`), and provides
  `lookup`, `covers`, `search` (the HM source's scoring, then shallower paths first;
  internal and hidden options left out) and records with GitHub links at the
  generation's revision. Parsed indexes stay cached per process.
- **Verbs**: `search --kind opt|hm` and `info` answer from the index when it applies to
  `--host` (`source: "index"`, `channel: null`, the generation), else from upstream;
  an index with no hit falls back to upstream with a note. `--no-index`, `--channel`
  and `--nixpkgs` go upstream. `where OPTION` adds `docs` (from the index) to each
  option, plus `index[]` and `skipped_scopes[]`: a scope whose *fresh* index lacks NAME
  is not evaluated when another scope's index has it. `status` gains `indexes`. The
  human output names the generation (search/info footer, info's locations, status's
  `indexes` line, where's default/example). `discovery.index_info()` is the seam for
  `explain` (below).
- **Docs**: README "Modules" (the two snippets, every option, the index, its measured
  build cost, the timers and the files the bar/companion read), CONTRACT "Option
  indexes", `search`/`info`/`status`/`where` fields, "`nixin-state`", costs.

Half-done: nothing in the code. Not done live: anything on 19BK (mbp13 cannot ssh there;
19BK was evaluated only), a real Home Manager activation with the index, and the
timers (not enabled on the hub branch, see "Hub").

## Files

New: `lib/options-index.nix`, `modules/{nixos,home,options,common}.nix`,
`src/nixin/optindex.py`, `src/nixin/state.py`, `tests/nix/{default,nixos-config,
home-config,record-fixtures}.nix`, `tests/fixtures/optindex/options-{nixos,home}.json`
(recorded by `scripts/record-index-fixtures.sh`), `tests/test_{optindex,state,
modules_live}.py`, this file.
Changed: `flake.nix` (nixosModules, homeModules, homeManagerModules, lib),
`pyproject.toml` (`nixin-state`), `discovery/__init__.py` (`--no-index`, index first,
`index_info`, dict extras merge in `_merge`), `discovery/format.py` (index footer,
info's locations/generation), `truth/__init__.py` (where: skip + annotate; status:
`indexes`), `truth/render.py` (indexes line; where's default/example/about, skipped
scopes), `tests/conftest.py` (autouse `no_local_index`: the machine's own indexes never
leak into tests), README, CONTRACT. `cli.py` is unchanged.

## Live run on mbp13

- Prototype on `~/nixos` (mbp13, read only): 34 366 options before the `_module` cut,
  18.8 MB, 30 s, 3 GiB for the whole eval. No option breaks it; 2 defaults throw.
- Module eval tests (`tests/nix`, `NIXIN_LIVE=1`): 22 s, 2.2 GiB, all facts as expected.
- **Hub**: worktree `~/nixos-modules-wt`, branch `nixin-modules` from origin/main
  e089d27, commit **1c1da1e** ("nixin: programs.nixin from its modules instead of the
  overlay and two package lines"). mbp13: `inputs.nixin.nixosModules.default` in its
  `nixosSystem` modules + `programs.nixin.enable = true` (hosts/mbp13/default.nix, the
  `nixin` systemPackages line gone). 19BK: `home-manager.sharedModules = [
  inputs.nixin.homeModules.default ]` + `programs.nixin.enable = true` in
  home/modules/packages.nix (the `nixin` home.packages line gone). Both overlay lines
  are dropped: nothing on hub main uses `pkgs.nixin` any more (the lane's
  `home/modules/tools/nixin/package.nix` does; see "Next"). The lock was overridden
  only in the worktree (`nix flake lock --override-input nixin
  path:$HOME/nixin-wt/modules`), never committed, and restored afterwards.
  **Bump the hub's lock after this branch merges** (`nix flake update nixin`).
- mbp13 toplevel built from the worktree: 83 s (then 192 s with nixin's tests
  re-running after a test fix). 19BK toplevel evaluated, not built: drvPath
  `/nix/store/0n7cm28mlz6xswq2d9lskwdwc3yljh0w-nixos-system-19BK-26.11.20261023.3ec6363.drv`
  (nixin at 71eddbf; later commits change Python and docs only). Brucking's index: 6 286
  options, `programs.kitty.enable` → `home-manager modules/programs/kitty.nix:106`.
- Index cost on each rebuild (eval of toplevel.drvPath, with and without
  `optionIndex.enable` through `extendModules`, twice each, load ≈ 1.4): mbp13 33–35 s →
  42–47 s, peak 3.0 → 3.6 GiB; 19BK 15–17 s either way (+0.5 s, +20 MiB). The NixOS tree
  is the expensive one (28 042 options and many submodule evals), HM's is cheap.
- **Switch** (sudo ticket; only this session ran, `~/nixos` and `/etc/nixos` at
  a198757): `sudo -n git -C /etc/nixos fetch /home/master/nixos nixin-modules`,
  `checkout -B nixin-modules FETCH_HEAD`, `sudo -n nixos-rebuild switch --flake
  /etc/nixos#mbp13 --override-input nixin path:/home/master/nixin-wt/modules` → 47.5 s,
  **generation 125** (`c50xqma9…`). `/etc/nixin/options-nixos.json` → `/etc/static/…` →
  `znpg25n5…-nixin-options-nixos.json` (28 042 options, 13 MB). With the installed
  nixin: `search --kind opt openssh` 0.49 s from the index (gen 125, fresh), `info
  --kind opt services.openssh.enable` 0.43 s with `sshd.nix#L247`, `search --kind opt
  programs.nixin` lists nixin's own 15 options at `modules/options.nix:<line>`, `where
  services.openssh.enable` with `docs` (default `false`, the declaration's link), `status`
  `indexes` (present, fresh, applies, generation 125) in 0.32 s. `--no-index --refresh`
  goes upstream (0.58 s). `nixin-state status` into a scratch state dir: 0 pending,
  0.3 s.
- **Restored** with the task's commands (`fetch /home/master/nixos main`, `checkout -B
  main FETCH_HEAD`, `nixos-rebuild switch --flake /etc/nixos#mbp13`): 4.5 s,
  **generation 126** = `5i7wg6fl…`, the same toplevel as 124. `/etc/nixos` on main at
  a198757, clean. The activation left `/etc/nixin` behind as an empty directory, which
  I removed (`sudo -n rmdir`).
- Found live: `nixin-state outdated` (and any user `nixin --config /etc/nixos`, with
  generation 124's nixin as well) fails on main: nix's libgit2 refuses the root-owned
  `/etc/nixos` ("not owned by current user"). The lane's branch fixes it in
  `hosts/mbp13/dots.nix` (`safe.directory = "/etc/nixos"` in `/etc/gitconfig`). Tested:
  a `[safe] directory = /etc/nixos` git config that libgit2 reads fixes it;
  `GIT_CONFIG_*` variables do not. The modules leave git config alone (a list/str
  conflict with the user's own `safe.directory` is likely); README says so.

## Judgement calls

- **The file is an object with a header, not a bare list.** `options[]` is the list; the
  header (host, user, nixpkgs/HM revisions, roots, count) is what makes `applies`,
  links and `status` possible without nix. toJSON's key order lets `peek` read the
  header from the two ends of the file.
- **`where` keeps its eval fields and adds `docs`.** The task says "prefer the index";
  `where`'s values must come from the eval, and the sibling branch `typed-set-explain`
  changes the same fields (type_info/default/example), so the index goes into a field
  of its own, which keeps the merge clean. Skipping scopes needs a *fresh* index of
  every skipped scope and a hit in another: a workspace that declares a new option in
  a skipped scope since the running generation would be missed (accepted; `--refresh`
  does not change that, `NIXIN_NO_INDEX=1` does).
- **`status --full` and the exclusion store are unchanged.** They serve values
  (`defined`, `in_config`); the index covers declarations and types and needs no
  exclusions.
- **A search with no index hit asks upstream** (one network call for a typo, the old
  behaviour), with a note. A search with hits never mixes in upstream.
- **`/etc/nixin/hosts.toml`: not written.** On `hosts-remote-diff`, hosts.toml is the
  per-user `~/.config/nixin/hosts.toml` that only `apply` reads; a system file nobody
  reads is not useful yet.
- **`pending` moved too**, not only `outdated`: the lane's `nixin-state` does both, and
  moving half would leave two copies of the script.
- **Config root**: `optionIndex.configRoot`, else the tree of the first file that sets
  `programs.nixin.enable`, skipping trees that are already roots. An inline module in a
  `nixosSystem` call is attributed to nixpkgs' `flake.nix`, and that made nixpkgs "the
  config" before the skip. Outside the store and `/etc/nixos`, the file's directory.
- **Declaration lines**: from `declarationPositions`, paired with `declarations`. A
  position inside nixpkgs' `lib/` (aliases from `mkRenamedOptionModule`) gives way to
  the declaring module, without a line. Templates (`<name>`, `*`) have files only.

## Next

1. **Hub `nixin-integration` onto the modules**, after this branch merges and the hub's
   nixin lock is bumped. Delete `home/modules/tools/nixin.nix`,
   `home/modules/tools/nixin/{package.nix,state.py}` and `hosts/mbp13/nixin.nix`, and
   their two import lines (`home/modules/default.nix`, `hosts/mbp13/default.nix`).
   Then set on mbp13 `programs.nixin = { enable = true; user = "master"; outdated = {
   enable = true; notifyTimeout = 30000; quietHours = "23-8"; }; pending.enable = true;
   };`, and for brucking `programs.nixin = { enable = true; outdated.enable = true;
   pending.enable = true; };`. The unit names, files and summary fields are the same,
   so `NixinIsland.qml`, `services/Nixin.qml`, `companion/local.py`/`Footer.qml` and
   `flake-report.sh` (`nixin-state report`, now from the nixin package on PATH) stay
   as they are. Keep its `safe.directory` in `dots.nix`. Without this migration the
   lane's `package.nix` needs `pkgs.nixin`, i.e. the overlay this hub commit drops.
2. **`explain`** (branch `typed-set-explain`): in `explain._upstream`, ask
   `discovery.index_info(ns, kind, name)` first for `opt`/`hm` and use its Part when it
   has a result (source `index`). It takes explain's namespace as it is
   (`ns.host`, `ns.channel`).
3. The TUI/GUI show `source index` like any other source; a "generation N" chip and the
   `in_config` mark ("your option") would make the index visible there. `view.py` row
   code is in `ui/`.
4. 19BK: switch it with the hub branch (from 19BK; mbp13 cannot ssh) and check
   `~/.config/nixin/options-home.json` after HM's activation: `fresh` from
   `gcroots/current-home`, `where programs.git.enable --host 19BK` skipping the NixOS
   scope (`skipped_scopes`, the eval saved), `status.indexes.home`.
5. A `where` not-found without eval when every scope has a fresh index and none covers
   NAME (needs "is the workspace the running generation's tree", which `status.pending`
   knows).

## How to test

```
nix develop -c pytest tests/test_optindex.py tests/test_state.py   # sandbox-safe, fixtures
NIXIN_LIVE=1 nix develop -c pytest tests/test_modules_live.py      # nix eval of tests/nix (~20 s, 2 GiB)
nix build                                                           # 629 passed, 30 skipped; self-test
nix develop -c ruff check src tests && nix develop -c ruff format --check src tests
nix fmt -- --check
scripts/record-index-fixtures.sh                                    # re-record the fixtures

# against a built system, without switching:
out=$(nix build --no-link --print-out-paths ~/nixos-modules-wt#nixosConfigurations.mbp13.config.system.build.toplevel)
NIXIN_INDEX_NIXOS=$out/etc/nixin/options-nixos.json nix run . -- --json search --kind opt openssh
# the hub worktree needs the lock override first (never commit it):
git -C ~/nixos-modules-wt worktree list; cd ~/nixos-modules-wt && nix flake lock --override-input nixin path:$HOME/nixin-wt/modules
```

Leftovers: `/tmp/nixin-modules/` (scratch: prototype outputs, build/switch logs, the
marginal-cost expressions, a scratch state dir and git config); generation 125 in the
system profile (the test switch; gc-able). The hub worktree `~/nixos-modules-wt` stays
for the pull request. The lane's `~/.local/state/nixin` was not touched.
