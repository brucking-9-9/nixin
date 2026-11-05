# DESIGN: holds, pins and "reasons to switch" (branch `pins`)

Written 2026-11-05 on 19BK in `~/Projects/nixin-wt-pins`; `~/nixos` was only read, probes ran on throwaway
clones and `nix eval` (no lock written, no build, no sudo). **Status**: steps 1-3 of §7 are implemented on
this branch (`docs/HANDOFF-pins.md`); §4 (security) is **deferred** by the user (no `reasons[]`, no
`--security` in any document yet); §5 (TUI tab, GUI page, hold toggles) is implemented after the plan-progress merge.

## Probes (facts the design rests on)

1. `builtins.fetchTarball { url = "https://github.com/NixOS/nixpkgs/archive/<rev>.tar.gz"; sha256 = <the lock node's narHash>; }`
   resolves **offline in 0.03 s** to the very store path the flake input uses (`x85g4cm…-source`). So a pinned
   nixpkgs needs no flake.nix edit and no lock node: it is a fixed-output fetch, allowed in pure eval.
2. 19BK, same rev: `extendModules` with `nixpkgs.overlays = [ (final: prev: { linux_7_2 = pinned.linux_7_2; }) ]`
   gives `boot.kernelPackages.kernel.drvPath` **identical** to the plain config (`dgw0v7mz…-linux-t2-7.2.14.drv`),
   i.e. the T2 `override` + `lib.mkForce` in configuration.nix compose with the overlay untouched, and a pin
   equal to the lock is a no-op (basis of "stale"). Eval 4.7 s. **Cross-rev**: with `--override-input nixpkgs
   …/1d060086` (the rev the incident moved to; plain kernel there = `wy334spj…-linux-t2-7.2.15.drv`, a 2-3 h
   build) and the overlay holding `linux_7_2` from 0bb2eed, the kernel is again `dgw0v7mz…-linux-t2-7.2.14.drv`:
   zero kernel rebuild across the bump (fixed-output patch fetches hash by output; `override` runs in the
   pinned scope). glibc 2.44 on both sides today, so no skew. Eval 5.4 s.
3. In the locked nixpkgs `linux_7_2` and `linuxPackages_7_2` are **both aliases** (`aliases.nix:1607/1645`
   → `linuxKernel.kernels.linux_7_2` / `linuxKernel.packages.linux_7_2`); overlaying one does not move the
   other. 19BK reads `pkgs.linux_7_2`, mbp13 `pkgs.linuxPackages_7_2`, so the kernel hold sets both.
4. Lock incident: from `30f7d68^` (nixin 43eef77, nixpkgs 0bb2eed) `nix flake update nixin` on nix 2.34.9
   moved **only** nixin. The real 30f7d68 moved nixpkgs too, so another command did it (cause unknown; not
   claimed). The guard below is written against the lock diff, whatever the cause.
5. vulnix 1.12.5 (nix-community) is in nixpkgs and in cache.nixos.org (not installed here): NVD **2.0 bulk
   feeds**, ZODB cache in `$XDG_CACHE_HOME/vulnix`, reads work offline once the cache exists, an offline
   *update* retries 5× (10/20/40 s), `--json` items `{name,pname,version,derivation,affected_by,whitelisted,
   known_exploited,cvssv3_basescore,description}`, exit 2 = findings, `-C PATH` the runtime closure; per main.py
   `-f FILE.json` scans a packages.json without a build (not run here; shape confirmed in step 4). tracker.security.nixos.org timed out twice (20 s) and has
   no documented API: not usable from nixin today (link-out only). kernel.org `releases.json` answers in
   <1 s: today 7.2.15 (2026-11-01) vs 19BK's 7.2.14.

## 1. Hold semantics (decision: a second nixpkgs by narHash, applied as an overlay from nixin's generated module)

- A pin = `{name, attr(s), rev, nar_hash, date, version, mode, why, since}` under `nixos.<host>.pins` in
  `installer/state.json` (additive key, version stays 1), rendered into `installer/<host>.nix`. Hand-written
  files are never touched; flake.nix and flake.lock are never touched by a pin (rejected: a `nixpkgs-pin-<name>`
  input — edits flake.nix, adds a lock node, and `nix flake update` without an input name would move it).
- `rev` defaults to the nixpkgs rev locked **now** (so `nixin pin kernel` then `nixin update` is the flow:
  pin first, update second; `nixin update --hold kernel,firefox` does both in that order).
- Modes: `attr` (one top-level attribute: `firefox = pinned.firefox`); `kernel` (the kernel attr the config
  names: `linux_X_Y = pinned.linux_X_Y; linuxPackages_X_Y = final.linuxPackagesFor final.linux_X_Y;`, kernel held,
  out-of-tree modules from the new nixpkgs built against it); `kernel-set` (`--whole-set`: `linuxPackages_X_Y =
  pinned.linuxPackages_X_Y`, modules too, for when a new module expression drops the held kernel: mbp13's
  nvidia 470 / broadcom-sta case). The kernel attr comes from the text of the `boot.kernelPackages` declaration
  (truth `where` → writer's `nixtext` scan for `pkgs.linux(Packages)?_\w+`), confirmed by one eval
  (`pkgs.<attr>.version == config.boot.kernelPackages.kernel.version`).
- Refused (`pin-unsupported`, with the reason and the alternative `nixin update --to <oldrev>`): the kernel from
  another input (`inputs.…`, nixos-hardware's pins) or an attr the scan cannot find; nested attrs
  (`python3Packages.x`, `kdePackages.x`: an overlay there does not compose); HM scope when
  `home-manager.useGlobalPkgs` is true (19BK): the host overlay already covers `home.packages`, so `--home` pins are
  pins on the host (HM-only `nixpkgs.overlays` in `installer/<user>-home.nix` is phase 4, mbp13 has no HM).
- Rejected (b): a module-level `boot.kernelPackages = lib.mkForce (pkgs.linuxPackagesFor pinned.linux_7_2)` collides
  with the user's own `mkForce` ("conflicting definition values") and would drop the T2 `override`;
  `nixpkgs.config.packageOverrides` is an overlay by another name. The overlay is the only place that composes.
- First pin on a host: `installer/` has only `history.jsonl` today, so `pin` creates `state.json` + `installer/19BK.nix`
  and appends `./installer/19BK.nix` to configuration.nix's `imports` exactly as `add` does (writer transaction,
  `git add -N`, parse + one eval) — the one touch of a hand-written file, same as today's `add`.
- No `pkgs` from the module args inside the overlay (pkgs depends on `nixpkgs.overlays` → infinite recursion):
  `system` from `prev.stdenv.hostPlatform`, config from `config.nixpkgs.config`, `overlays = [ ]` (user overlays are
  not carried into the pinned set; an info note says so). Both probes passed `config.nixpkgs.config` in from
  outside the fixpoint; reading it inside the module is expected cycle-free but untested — step 2 checks it first,
  `prev.config` is the fallback.

Generated `installer/19BK.nix` for `nixin pin kernel --why "T2 kernel build takes 2-3 h"` (before nixfmt):

```nix
# generated by nixin — edit state.json or use the CLI
{ config, lib, pkgs, ... }:
let
  # nixpkgs 0bb2eed (2026-11-01): the lock before `nixin update` on 2026-11-05; same tree as the flake input had
  nixpkgs-0bb2eed = import (builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/0bb2eedc20e4739f29e8045034f2828ad7054f8f.tar.gz";
    sha256 = "sha256-fiPJ88izTVucjeo2EQ3jk4aWmLg03I1D4vxnX2QjEgE=";
  });
in
{
  nixpkgs.overlays = [
    (final: prev:
      let pinned = nixpkgs-0bb2eed { inherit (prev.stdenv.hostPlatform) system; config = config.nixpkgs.config; overlays = [ ]; };
      in {
        # pin "kernel" (2026-11-05): linux_7_2 held at 7.2.14 — T2 kernel build takes 2-3 h. `nixin unpin kernel`
        linux_7_2 = pinned.linux_7_2;
        linuxPackages_7_2 = final.linuxPackagesFor final.linux_7_2;
      })
  ];
}
```
A plain pin adds to the same overlay (one `let` binding per rev, one overlay per rev):
```nix
        # pin "firefox" (2026-11-05): firefox held at 159.0 — 160 breaks the kiosk profile. `nixin unpin firefox`
        firefox = pinned.firefox;
```
`render()` stays a pure function of state.json; `environment.systemPackages`/options render as today below the overlay.

- **`update --input` guard** (`outdated/update.py`, the one existing file touched besides `writer/state.py`): copy
  flake.lock, run nix, parse both; allowed = the requested root node + nodes reachable only through it in the
  *after* lock; any other node whose `locked` changed → restore the copy, fail `lock-moved-others` listing
  `node a→b` (`--allow-others` keeps it and reports `moved[]`). `nixin update --hold NAME…` = pin (at the lock
  rev) → update → the pins' checks, in one document.

## 2. Compatibility checks (`nixin pin` runs them before writing; `plan` repeats them; all evals, no build, ~3-6 s)

Levels: `info` (printed), `warn` (confirm step in the UIs), `block` (`pin` refuses; `--force` writes anyway).
`warnings[]` stays a list of strings (apply's rule); a structured `checks[] {id, level, text, data}` sits beside it.

| id | when | level | text (exact) |
|---|---|---|---|
| `kernel-modules-dropped` | with the hold applied (`extendModules`), `boot.extraModulePackages`, `hardware.nvidia.package`, every `specialisation.*` of these: a drvPath eval throws / `meta.broken` | block | `kernel held at 7.2.14 but <pname> from the new nixpkgs does not build for it: <nix error>. Hold the module set too: nixin pin kernel --whole-set` |
| `kernel-set-mismatch` | `boot.kernelPackages.kernel.version` ≠ the kernel of a module in `extraModulePackages` (text scan finds `pkgs.linuxPackages_*` outside `config.boot.kernelPackages`) | warn | `<file:line> takes <pname> from pkgs.linuxPackages_X_Y, not from config.boot.kernelPackages; with the kernel held they no longer match` |
| `kernel-toolchain` | mode `kernel` (modules from the new set) | info | `out-of-tree modules are built with the new nixpkgs' toolchain against the held kernel; vermagic is by version, so they load. A compiler-version complaint means: nixin pin kernel --whole-set` |
| `gl-skew` | pinned.glibc.version ≠ pkgs.glibc.version **and** the attr's `buildInputs`/`propagatedBuildInputs` name mesa, libglvnd, wayland, qtbase, gtk3/4 (1 level, a heuristic) | warn | `<attr> held at <v> brings its own glibc <a>; the system's Mesa/Wayland drivers are built against glibc <b>. The program is self-contained but can fail to find a GL/EGL driver at run time (seen with dorion 2026-10-11). Prefer nixin update --to <rev> for everything, or expect to wrap it with the pinned Mesa` |
| `glibc-skew` | glibc differs, no GL | info | `<attr> held at <v> runs on its own glibc <a> (system: <b>); fine for a leaf package` |
| `rebuilds-dependents` | `nix-store -q --referrers-closure` of the attr's current outPath ∩ the running system closure has N>1 members | warn (N≥5) / info | `<N> installed packages depend on <attr> and will rebuild against the held version (<first 5>): an overlay pin is not a leaf pin` |
| `lang-set` | attr is a python/node/haskell/perl package or `pythonPackages`-style set member | block | `<attr> lives in a package set; an overlay on it does not reach the set. Not pinnable; pin the whole nixpkgs instead (nixin update --to <rev>)` |
| `pin-stale` | pin.rev == lock rev, or `pkgs.<attr>.version` as locked == held version | info | `pin "<name>" holds nothing: the lock is at <rev> / <attr> is <v> either way. nixin unpin <name>` |
| `pin-blocks-security` | `--security`: a vulnix finding on the held pname/version that the pending lock changes | warn | `pin "<name>" keeps <pname> <v>, affected by <CVE…> (fixed? the pending lock has <v2>, version match only)` |
| `eval-cost` | always, once per rev | info | `a second nixpkgs tree adds about 1-2 s and 300 MiB to every evaluation; after a garbage collection nix re-downloads it (~60 s)` |

## 3. `nixin pins` (CLI; TUI tab; GUI page)

Per pin: `name`, `mode`, `attrs[]`, `held` (`rev`, `short_rev`, `date`, `version`), `now` (the lock's rev and the
attr's version there, from the `outdated-lock` cache or one eval), `stale` (bool, §2), `why`, `since`,
`drop` = `nixin unpin NAME`, `checks[]` (re-run, cheap ones only: stale, rebuilds-dependents), `cost`
(closure size of the held output when it exists locally: `nix path-info -S`; else `"not built"` — the delta is
`plan`'s diff-closures job, not promised here). Human: an aligned table, stale rows marked `(stale)`,
one summary line (`2 pins, 1 stale`). `unpin NAME` removes the entry, re-renders, `git add -N`, parse + one eval
(the writer's rule); `unpin --all`. `outdated` gains a `holds[]` block and its table marks held rows `(held)`
instead of listing them as changes; `plan`/`apply` carry `pins[]` and the stale note.

## 4. Security / reasons to switch (`nixin outdated --security`; offline-first, no accounts) — DEFERRED

*Deferred 2026-11-05 by the user: not implemented, nothing of it appears in the documents. Kept as designed.*

- Source: vulnix found on PATH (`vulnix-missing` names `nixin add vulnix`; not a runtime dep of nixin, so the
  security phase does not grow every closure; `nix run` never implicit) — open for the manager. `now`: `vulnix -C /run/current-system --json` (exact: the running closure). `after`: `vulnix -f
  pending.json --json` where pending.json is a packages.json nixin writes from `outdated`'s target-side eval
  (pname/version per installed package, no build) — version-only, says so. `fixed[]` = in `now` and not in `after`
  by (pname, CVE); `remaining[]`; `blocked_by_pins[]` (§2). The kernel (`linux*` pnames) is whitelisted by default
  (NVD's kernel CVEs are per point release and hundreds; `--kernel-cves` shows them) and reported as "point release
  behind" from kernel.org `releases.json` instead (`7.2.14 → 7.2.15 (2026-11-01)`), with `--published`'s
  nixpkgs-side `to`.
- Offline: nixin checks `nvd.nist.gov` reachability first (one HEAD, 5 s); unreachable → vulnix runs with the
  cache if `$XDG_CACHE_HOME/vulnix/Data.fs` exists (`data_age` reported), else the block is `unavailable`
  with `reason`, never an error for the rest of `outdated`. The cache lives for `nixin-state outdated` (timer)
  to refresh; the UIs read `summary.json`.
- Caveat text, always printed with findings: `vulnix matches NVD by package name and version; it does not see
  nixpkgs' backported patches (except CVE ids in patch names). Treat as leads, not verdicts.`
- Ranked `reasons[]` for the Status/Pending header (the user: CVEs first): 1 `known_exploited` CVE in the
  running closure; 2 CVSS ≥ 7.0 in it that the pending lock changes (`fixed`); 3 other CVEs fixed by the update;
  4 kernel point release behind (kernel.org); 5 `pending_reboot` (safety.py: booted ≠ running);
  6 running system older than 30 days (generation link mtime); 7 a pin blocking a fix. One line each, highest
  first, e.g. `security  2 CVEs (CVSS 9.8, 7.5) in the running system are gone with the pending lock; kernel 7.2.15 is out`.

## 5. UX — step 5, implemented after the plan-progress merge

- CLI: `nixin pin NAME [--attr A] [--rev R] [--whole-set] [--why TEXT] [--force]`, `nixin unpin NAME|--all`,
  `nixin pins [--sizes] [--check]`, `nixin update [--hold N,…] [--allow-others]`, `nixin outdated [--security]`.
- TUI Packages → Updates tab: a per-row `h` toggle (hold) on changed packages and the kernel line; "Update
  nixpkgs lock" confirm dialog lists holds + their `warn`/`block` checks (block disables the button until
  unticked or `--force`); a `Pins` tab (`n`) with the §3 table, `u` unpin. Security notice: one line in the Status
  header and the Pending header (`reasons[0]`), `r` opens the full `reasons[]` list. Offline: the line reads
  `security: no data (offline, cache 9 d old)`.
- GUI: `UpdateRow` gets a hold switch; `UpdateDialog` a "holds" section with the checks (warn = amber, block =
  red + disabled Apply); `PinsPage.qml` (NavRail entry) = the table + Unpin; `NoticeBar` shows `reasons[0]`.
- `nixin-state summary.json` gains `pins {count, stale}` and `security {kev, high, fixed, kernel_behind, at}` for the bar.

## 6. Tests

Fixtures: `tests/fixtures/pins/` with state.json before/after, the rendered modules (byte-exact, real nixfmt in
the sandbox), a flake.lock pair for the guard (only-nixin / nixin+nixpkgs moved), recorded `outdated` docs with
holds, a recorded vulnix JSON (two findings, one KEV, one whitelisted) + kernel.org `releases.json`. Fakes: nix
runner answering the extendModules/attr evals (version, drvPath, buildInputs, glibc), `nix-store --referrers-
closure`, a fake `vulnix` executable (prints the fixture, exits 2), unreachable NVD. Writer tests: render with
0/1/2 revs, kernel/kernel-set/attr, unpin leaves the file as before, state version stays 1 and an old nixin
loads it. Live (`NIXIN_LIVE=1`, a clone): `pin kernel` on 19BK → drvPath unchanged (probe 2), `pins` stale,
`unpin`; mbp13 `pin kernel` → `kernel-modules-dropped` or clean; `update --input nixin` guard on the clone.

## 7. Effort and order (each step ships on its own, tests included)

1. `update` lock guard + `lock-moved-others` (0.5 d). 2. `src/nixin/pins/`: state, render, `pin/unpin/pins`, kernel
attr detection, checks table (2 d). 3. `outdated`/`plan` show holds and stale (0.5 d). 4. `src/nixin/security/`:
vulnix wrapper, packages.json from the target eval, kernel.org, `reasons[]`, `nixin-state` fields (1.5 d). 5. UI
wiring last and additive (`tui/pins.py`, `PinsPage.qml`, hold toggles), after the plan-progress merge, since
`tui/packages.py` and `gui/bridge.py` are the other lane's files (1.5 d). New modules only, plus `writer/state.py`
(render) and `outdated/update.py` (guard, `--hold`). ≈ 6 days.
