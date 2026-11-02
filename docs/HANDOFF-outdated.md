# HANDOFF: `nixin outdated` (branch `outdated-verb`)

Written 2026-11-02 on mbp13, headless, as `master`. The branch was cut from `origin/main` @
a49755a. Nothing is pushed. This session only read `~/nixos` (no writes, no switch, no sudo).
`~/nixos` was at 81f19b7 on `main` with an uncommitted `installer/history.jsonl` edit that
was there before this session started and is still there, untouched.

The question was "is there a way to show what packages need updating?". `nixin outdated`
answers it two ways: what the next `flake.lock` bump would change for this host
(default), and brew-outdated style against a search.nixos.org channel (`--published`).

## State

Done. Nothing is half-written.

Gates at HEAD:
- `nix build`: 509 passed, 24 skipped (live) in the sandbox, then the GUI self-test. The
  output ships `nixin/outdated/outdated.nix`, and the built `bin/nixin outdated --host 19BK
  --config ~/nixos` gives the same 13 of 325 as the tree.
- `nix develop -c pytest`: 509 passed, 24 skipped (live). `tests/test_outdated.py` is 56 of
  them (+2 live), 0.4 s. The integration tests' contract checks (every verb has a section,
  every raised code is in the table) pass with the new verb.
- `NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest tests/test_outdated.py -k live`:
  2 passed (both modes, both hosts, `flake.lock` byte-identical afterwards).
- `ruff check`, `ruff format --check`, `nix fmt -- --check`: clean.

Files (all new except the last five):
- `src/nixin/outdated/__init__.py`: `register()`, the handler, the two evals and their
  caches, both modes, the follow-up commands.
- `src/nixin/outdated/target.py`: the nixpkgs input (resolved through `flake.lock`; the
  node is `nixpkgs_2` here), its `original` as a flakeref, `--nixpkgs`/`--channel` → target,
  `nix flake metadata` on it, and the network/bad-ref classification of nix's stderr.
- `src/nixin/outdated/compare.py`: nix's `compareVersions` (checked against nix 2.34.9 on
  20 cases), inventories, the lock diff, the `--published` attribute pick, the kernel
  series.
- `src/nixin/outdated/render.py`: human text.
- `src/nixin/outdated/outdated.nix`: the eval helper (package lists + meta.position,
  kernel version, `system.nixos.release`).
- `tests/test_outdated.py`, `tests/fixtures/outdated/` (recorded on mbp13 today, see the
  test docstring).
- Edited: `src/nixin/cli.py` (the import, one `VERBS` entry, `outdated.register(sub)`),
  `tests/test_cli.py` (`outdated.VERBS` counts as a configured verb), `pyproject.toml` (the
  `nixin.outdated` package data), `README.md`, `docs/CONTRACT.md`, `docs/HANDOFF-truth.md`
  (a note), `HANDOFF.md` (the index line).

## What it does

Lock mode: `outdated.nix` is applied to `nixosConfigurations.<host>` twice, as locked and
with `--override-input nixpkgs <target> --no-write-lock-file`. The target is the lock's
`original` (`github:nixos/nixpkgs/nixos-unstable`) as `nix flake metadata` resolves it now,
or `--nixpkgs FLAKEREF|REV`, or `--channel NAME`. The two runs are compared per scope
(system, each HM user) by pname. `system.build.toplevel` is never evaluated and nothing is
built. The target-side eval is cached in the truth cache by (flake fingerprint, host,
target commit); `--refresh` skips the cache and re-asks GitHub for the branch head.

`--published`: the lock-side eval only, then one `terms` query per 50 pnames against the
channel's index (the discovery layer's client and cache).

`docs/CONTRACT.md` has the fields, errors and costs.

## Live run on mbp13 against `~/nixos` (read only)

Lock 3ec6363 (2026-10-23) → nixos-unstable 0bb2eed (2026-11-01), 8.9 days.

| run | result |
|---|---|
| `outdated --host mbp13` | 57 of 337 would change. Plasma 6.8.1 → 6.8.2 (46 packages), firefox 158.0.1 → 159.0, hyprland 0.56.2 → 0.57.0, claude-code, tailscale, syncthing, fwupd, wireplumber, strace, xdg-desktop-portal-hyprland 1.4.1 → 1.5.0. fuse 2.9.9 removed (3.18.2 stays). Kernel 7.2.12 → 7.2.14 |
| `outdated --host 19BK` | 13 of 325: system 4 (firefox, strace, tailscale, wireplumber) plus fuse 2.9.9 removed; brucking 9 (harper 2.13.0 → 3.0.0, yt-dlp, ungoogled-chromium, claude-code, …). Kernel 7.2.12 → 7.2.14 |
| `--published` (index 1c89083) | mbp13 56 of 283, 19BK 13 of 275. Kernel → 7.2.13 (its series), latest 7.3. mbp13: 1 pname not on search.nixos.org; 19BK: 5 |
| `--channel 26.05 --host mbp13` | 163 changed (mostly `(older)`: Plasma 6.8 → 6.6, KDE gear 26.08 → 26.04), 10 removed, 1 added. The `next:` block pins 9bb5222 with `nix flake lock … --override-input`, with a note on changing `inputs.nixpkgs.url` to follow 26.05 for good |
| `--channel 26.05 --host 19BK` | `eval-failed`: "19BK does not evaluate with nixpkgs 9bb5222 (github:nixos/nixpkgs/nixos-26.05), so this update would break it: undefined variable 'tuxedo' at /home/master/nixos/configuration.nix:420:5" |
| `--nixpkgs 3ec6363` | `up_to_date`, no target eval, 0.7 s |
| GitHub unreachable (`https_proxy` to a dead port, `--channel 25.11`) | `network` after nix's retries, 5 s |
| `--channel bogus`, `--home nope`, `--host nope`, `--nixpkgs x --channel y` | `bad-channel`, `unknown-user`, `unknown-host`, `usage` |

nix confirms it does not write the lock ("warning: not writing modified lock file"). The live
test checks `flake.lock` is byte-identical afterwards.

Costs (quiet: load 0.4–0.7; nixin's cache empty, nix's fetcher cache warm):

| | mbp13 | 19BK |
|---|---|---|
| lock mode, cold nixin | 4.3 s (evals 1.9 + 2.0 s), 410 MiB | 6.0 s (2.7 + 3.0 s), 540 MiB |
| lock mode, warm | 0.36 s, 43 MiB | 0.38 s |
| `--refresh` (re-asks GitHub; same commit) | 4.4 s (resolve 0.33 s) | — |
| a target commit nix has not fetched | — | 65 s, of which resolve (GitHub + tarball download) 58 s; load 2.4 |
| `--published`, eval cached, search cold | 1.2 s (search 0.9 s) | 1.1 s (0.7 s; once 2.9 s) |
| `--published`, warm | 0.34 s | 0.33 s |

The target's first resolve also took 65 s on its own (`nix flake metadata
github:nixos/nixpkgs/nixos-unstable`, the tarball). After that a new nixos-unstable commit
costs the same once. Note: a fresh `XDG_CACHE_HOME` also empties nix's own fetcher cache
(`$XDG_CACHE_HOME/nix`), so to measure nixin cold with nix warm, symlink `nix` into it.

## Judgement calls (open to change)

1. **Default target = the lock's `original`, resolved now.** That is what `nix flake
   update nixpkgs` would write, and it may be a newer commit than the search.nixos.org
   index (0bb2eed vs 1c89083 today). `target.rev`/`date` say which commit was compared.
2. **Only nixpkgs moves.** Inputs that `follows` it move along (home-manager here). A bare
   `nix flake update` would also bump home-manager, nixos-hardware and the rest, so the
   suggested command names the input: `nix flake update nixpkgs --flake DIR`.
3. **By pname, with version sets.** Attribute paths are not recoverable from derivations.
   Versions on both sides cancel, the leftovers pair up in version order, and the rest is
   added/removed. Pname-less packages are keyed by name and never "change".
4. **`count` = changed only.** `added[]`/`removed[]` are separate lists and `total` is the
   lock side's distinct pnames, so the summary reads "N of M packages would change".
5. **`--published` picks one attribute per pname** (same file by meta.position, then same
   major version, then the attr named like the pname, then top-level, then shortest).
   Without it, python3 compared against 3.16.0a1, git against emacsPackages.git 20140128,
   ark against an unrelated 0.1.252 and qtbase against qt5. The kernel is compared within
   its series. Lock mode stays the exact answer; the document says so in `notes`.
6. **Pinning a non-default target** prints `nix flake lock DIR --override-input nixpkgs
   github:…/<rev>`. Checked on a throwaway flake: it writes the lock and keeps `original`,
   so the next `nix flake update` goes back to flake.nix's ref (the note says how to
   follow the new ref for good).
7. **Errors.** A target that does not evaluate is `eval-failed` with "this update would
   break it" and nix's error text. No new error codes: `network`, `bad-channel`
   (HTTP 404/422 from GitHub), `nix-failed`, `not-found` (no nixpkgs input) gained
   `outdated` in their "from" column.
8. **Own cache entries** (`outdated-lock`, `outdated-target`), not truth's `packages`:
   one eval gives packages and kernel together. See the note in `HANDOFF-truth.md`.
9. **`--home USER`** limits the tables to that user's `home.packages` (no system). The
   kernel is still reported.
10. **`--channel stable`** asks search.nixos.org for the newest release (one call), the
    same meaning discovery gives it.

## Gaps / next

1. The TUI and GUI have no page for it yet. The document is UI-ready: per-scope rows,
   `commands[]`, `notes[]`. A Status-page block "N packages would change" plus a
   table would fit `ui/view.py`.
2. The two evals run one after the other (~2 s each). Running them in parallel saves ~2 s
   cold.
3. Only `nixpkgs` is overridden. An `--inputs all` mode (override each input with its
   `original`) would answer "what would a bare `nix flake update` change", at one
   resolve per input.
4. Only installed packages and the kernel are compared. Dependencies that are not in
   `systemPackages`/`home.packages` do not show; `nixin plan` after the bump shows the
   whole closure diff.
5. `outdated-target` keeps one entry per host (the truth cache prunes older keys), so
   alternating between two targets re-evaluates.
6. A nixpkgs input that is not `github:` (tarball, git) is pinned with its fully locked
   URL; that path was not run live (this config's input is github).

## How to test

```
nix develop -c pytest tests/test_outdated.py          # offline: fixtures + fake nix + fake ES
nix develop -c pytest                                 # everything
NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest tests/test_outdated.py -k live
nix develop -c ruff check src tests && nix develop -c ruff format --check src tests
nix fmt -- --check
nix build                                             # pytest in the sandbox

# from the worktree (the dev shell resets PYTHONPATH, so set it inside):
nix develop -c sh -c 'PYTHONPATH=$PWD/src:$PYTHONPATH python3 -m nixin.cli outdated --config ~/nixos --host 19BK'
nix develop -c sh -c 'PYTHONPATH=$PWD/src:$PYTHONPATH python3 -m nixin.cli --json outdated --published --config ~/nixos'
nix develop -c sh -c 'PYTHONPATH=$PWD/src:$PYTHONPATH python3 -m nixin.cli outdated --channel 26.05 --config ~/nixos'
```

Scratch from this session (not in the repo): `/tmp/outdated-probe/` (probe scripts, the
recorded raw evals, `record.py` that trimmed the fixtures, `build.log`) and the throwaway
cache dirs `/tmp/outdated-xdg.*`, `/tmp/outdated-xdg2.*`, `/tmp/outdated-lockt.*`.
