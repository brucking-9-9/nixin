# HANDOFF: integrate-night (the night's seven lane branches on one tree)

Written on mbp13, headless, as `master`, 2026-11-02/03. Branch `integrate-night` in the
worktree `~/nixin-wt/integrate2`, cut from `origin/main` @ 318e6cc (ui-packages +
core-history-safety). Nothing pushed, nothing switched, no sudo run (the dry runs below had
a stub `sudo` first on PATH and `/run/wrappers/bin` off it), `~/nixos` only read.

## State

Done; nothing half-done. The seven branches are merged in the order asked, each with
`git merge --no-ff --no-edit`, and `nix build .#nixin` (pytest in the sandbox + the GUI
self-test) passed after every merge. The CLI has every verb once: search, info, versions,
browse, status, where, list, explain, add, rm, set, unset, plan, apply, sync, history,
rollback, generations, gc, outdated, update, try, tui, gui (and `nixin-state`, a second
program). The live smoke on mbp13 against `~/nixos` (the 22 verbs with a `--json` form,
34 runs; `tui`/`gui` through the GUI self-test and the screenshot scripts) answered one
JSON document each, nothing on stderr, and left `~/nixos` as it was (HEAD, status, the diff's
and the index's hashes compared before and after).

| # | branch (tip) | merge | conflicts | `nix build` after it |
|---|---|---|---|---|
| 1 | `typed-set-explain` (d2d5191; cut from 824425c, 9 behind main) | 8ed8695 | CONTRACT | 704 passed, 25 skipped, self-test ok |
| 2 | `ui-options` (f90bfa2) | 7ed635b | HANDOFF | 722 passed, 25 skipped, ok |
| 3 | `hosts-remote-diff` (2bd4d58) | 9144c5e, then 3502acb | README, CONTRACT, `truth/__init__.py` | 759 passed, 25 skipped, ok (built with 3502acb in the tree) |
| 4 | `gc-generations-speed` (1873025) | e13460f, then 0170c92 | HANDOFF, README, CONTRACT, HANDOFF-apply, `apply/__init__.py`, `truth/__init__.py`, test_integration | 785 passed, 25 skipped, ok (at 0170c92) |
| 5 | `modules-optindex` (59634d7) | 6a3b431 | HANDOFF, README, CONTRACT, `truth/render.py` | 816 passed, 30 skipped, ok |
| 6 | `try-verb` (63ffbe9) | 470d141 | HANDOFF, README, CONTRACT, `cli.py`, `ui/view.py`, both screenshot scripts, test_cli, test_integration | 872 passed, 34 skipped, ok |
| 7 | `appstream-categories` (084d4df) | f672613 | HANDOFF, README, CONTRACT, HANDOFF-gui, `cli.py`, `discovery/format.py`, `gui/bridge.py`, `Main.qml`, `NavRail.qml`, `ui/backend.py`, `ui/view.py`, test_cli/gui/tui, gui-screenshots | 930 passed, 34 skipped, ok |
| — | follow-ups | 9c82d48, 35f34f3 | — | 930 passed, 34 skipped, ok (at 9c82d48; 35f34f3 is screenshots and one doc line) |

Gates on the final tree: `nix develop -c ruff check` clean, `ruff format --check` clean
(134 files), `nix fmt -- --check` exit 0, `nix develop -c pytest` the same as the build.

## Conflict decisions

The merge commits from 4 on carry their decisions in the message; 1–3 have git's
`# Conflicts:` list only (their resolutions were docs and one import line).

- **Docs** (CONTRACT, README, HANDOFF, docs/HANDOFF-*): both sides kept, in merge order
  under their layer; verb tables list each verb once (CONTRACT's table and README's block:
  `browse` under discovery's row, `explain` after truth, `generations gc` after `history
  rollback`, `try` after `outdated update`). Error-code rows were merged per code where two
  branches extended the same row (`not-found`, `config-not-found`, `eval-failed`: explain's
  and try's notes both; `host-not-local`: gc's verbs plus "`apply` goes there over ssh
  instead"; `bad-channel`: try's). Cost rows and cache rows: both, the newer wording where
  one superseded the other (list's warm figure after the start-up work, exclusions keyed by
  host). HANDOFF-apply: the `hosts-remote-diff` and `gc-generations-speed` sections both
  sit above `core-history-safety`. HANDOFF-gui: the Options and AppStream sections both.
  The status line, `--host` paragraph (typed's default rule plus the option index's use of
  it) and `--config` list name every verb that takes them.
- **`cli.py`**: imports `apply, appstream, discovery, explain, gui, outdated, truth, tryout,
  tui, writer` and `default_host`; `register()` order discovery, appstream, tryout (before
  the stubs: their own positionals), then truth, writer, explain, apply, outdated, tui,
  gui; `VERBS` has all 24. `tests/test_cli.py` leaves `tui gui explain try browse` to their
  own tests.
- **One host-list reader** (merge 4): `gc-generations-speed` added `truth.hosts()` (cached
  by flake fingerprint); `typed-set-explain`'s `explain` and `truth.package_info` called
  `repo.hosts()` (an eval each time). Both use `truth.hosts()` now. `configrepo.default_host`
  keeps `repo.hosts()` behind its text scan (configrepo cannot import truth; it only runs
  when the scan misses). The generation reader was already one (`generations.py` builds on
  `history.generation_rows`).
- **Option docs in `where`** (merge 5): typed puts `default`/`example` (from the eval,
  exact for the locked nixpkgs, also for a path inside an option) on each option entry;
  modules adds `docs` from this machine's option index. Both stay in the document. The
  human output shows the eval's and falls back to `docs`' only for the option itself (the
  index describes the real option, not a sub path); `about` is `sub_description`, else the
  index's whole description, else the eval's first paragraph. CONTRACT says so.
- **`list --diff`** (3502acb): an unknown host is `unknown_host()`'s error, as the other
  truth verbs' after typed.
- **GUI pages** (merge 7): StackLayout order search, pending, status, packages, options,
  browse; rail Search, Browse, Pending, Status, Packages, Options; keys `Ctrl+5` Options,
  `Ctrl+B` Browse. `bridge.py` has `OptionsVM` and `BrowseVM`, `TryVM` and the image
  fetcher; `go()` takes both pages, a channel change reruns Options and refreshes Browse.
  `ui/backend.py` CONFIG_VERBS has `try` and `info`. `discovery/format.py` keeps the option
  index's field renderers and adds the appstream block.
- **Screenshots**: three lanes numbered from the same place. Try's moved after Options (TUI
  `*-19…21-try-*`, GUI `18…20-try-*`), Browse's after Try (GUI `21-browse`,
  `22-browse-details`, `live-17…19-browse*`); files renamed, HANDOFF-try and HANDOFF-gui
  updated. The two `--only` flags of `gui-screenshots.py` (ui-options `all|options`,
  appstream `main|packages|browse`) are one: `--only main|packages|options|try|browse`,
  none = all in that order, `--try` = `--only try`. `tui-screenshots.py` keeps `--only
  options` and `--try`. 35f34f3 redrew the fixture shots on the integrated tree (each lane
  had drawn its own without the others' pages); the `live-*` shots are as the lanes left
  them.

## Fixed on the way (follow-up commits)

- 0170c92 `tests: outdated's lock and target evals in either order`: gc-generations-speed
  runs the two evals side by side; `test_lock_mode_document` unpacked them by position and
  failed 3 runs in 5 on the merged tree (it was latent on that branch too).
- 9c82d48 `explain`: `concurrent.futures` imported where it runs. typed imported it at the
  top of `explain`, so every verb paid for `logging` and `traceback` again (~10 ms, what
  gc-generations-speed had removed from outdated). `tests/test_startup.py` now fails on
  `concurrent` or `logging` at CLI start.

## Live smoke on mbp13 (2026-11-02, ~23:50, the built binary of 9c82d48)

`~/nixos`: main @ a198757, `installer/history.jsonl` modified (as every handoff since
`apply` notes), no stashes; running generation 126 (= the workspace HEAD's build), booted
118; no other agent or rebuild running. Before and after: HEAD, `git status`, the diff's
and the index's sha256, `history.jsonl` and `flake.lock` hashed and compared: unchanged.
Every command was `nixin --json …` with `--config ~/nixos` where the verb takes one,
`NIXIN_CONFIG` unset, PATH without `/run/wrappers/bin` and with a `sudo` that exits 1.
All 34: exit code as the contract says, exactly one JSON document, empty stderr. Outputs:
`/tmp/integrate-night/smoke/`.

| command | result | s |
|---|---|---|
| `search cowsay` | ok, 10 rows, search.nixos.org unstable | 0.6 |
| `search --kind opt openssh.enable` | ok, 2 | 0.7 |
| `search --appstream firefox` | ok, 20 (firefox `gui: true` from the shipped list) | 0.5 |
| `info cowsay` | ok | 1.1 |
| `info kitty` | ok, `appstream.source: installed`, `gui: true` | 0.2 |
| `info --kind opt services.openssh.enable` | ok | 0.5 |
| `versions cowsay` | ok, 7 (nixhub.io) | 0.5 |
| `browse` / `browse Game` | ok, 11 categories / 40 apps | 0.4 / 0.6 |
| `status` | ok; pending: no commits, dirty `installer/history.jsonl`, running matches; `indexes`: neither present (gen 126 has no nixin module) | 0.2 |
| `where kitty` | ok, system 0.49.2, `hosts/mbp13/default.nix` | 0.2 |
| `where services.openssh.enable` | ok, scope nixos, no skipped scopes (no index) | 1.2 |
| `list` | ok, 340 packages | 0.3 |
| `list --diff 19BK` | ok, mbp13 vs 19BK: 223 only mbp13, 211 only 19BK, 114 same, 0 differ | 0.2 |
| `explain services.openssh.enable` | ok, NixOS option, boolean, default false, both sides answered | 0.3 |
| `explain kitty` | ok, package; installed, channel, listed in, programs, homepage, license, source | 0.4 |
| `outdated` | ok, 57 changed, target 0bb2eed (8.9 days newer), kernel 7.2.12 → 7.2.14; evals side by side, 2.5 s total | 2.7 |
| `outdated --published` | ok, 57, search.nixos.org unstable index 52 | 1.0 |
| `history` | ok, 9 generations (118–126), current/default 126, booted 118 | 0.2 |
| `generations` | ok, 9 rows with closure sizes (~23.3 GB each), boot entries 125–126; `--keep 3` preview drops 119–123, keeps 118 (booted) | 0.6 |
| `gc --dry-run` | ok, no policy so no generation; 4173 dead paths, 1.72 GiB estimate; `privilege.ready: false` (the stub) | 14.6 |
| `try cowsay` | ok, cached in the store (107 MiB closure), `nix run github:nixos/nixpkgs/3ec6363…#cowsay`, programs cowsay, cowthink | 0.8 |
| `plan` | ok, up to date, cached (the gen 126 toplevel), `reboot_needed: false` | 0.7 |
| `apply --dry-run` | `nothing-to-apply` (rc 1): a198757 is in /etc/nixos and runs as gen 126 | 0.2 |
| `--host 19BK apply --dry-run` | ok, 5 commands (push, the ssh script, H's three steps); contacts nothing | 0.2 |
| `rollback --dry-run` | ok, 126 → 125, no reboot; `privilege.ready: false` (the stub) | 1.0 |
| `sync` (on a clone, see below) | ok, fetch; workspace = hub = /etc/nixos = a198757 | 1.3 |
| `update --dry-run` | ok, nixpkgs from 3ec6363, the command; nothing run | 0.2 |
| `set services.openssh.enable false --dry-run` | ok, `hosts/mbp13/default.nix`, type check `fits` | 0.3 |
| `set services.openssh.enable '"yes"' --dry-run` | `type-mismatch` (rc 1): a string, not boolean; default false | 0.3 |
| `set services.openssh.enable` | ok, describes the option | 0.3 |
| `add cowsay --dry-run` | ok, `hosts/mbp13/default.nix` modified, `installer/mbp13.nix` + `state.json` created (preview only) | 1.2 |
| `rm kitty --dry-run` / `unset services.openssh.enable --dry-run` | ok, `hosts/mbp13/default.nix` | 0.2 / 0.2 |

`sync` was run on a throwaway clone (`/tmp/integrate-night/nixos-clone`, origin set to the
hub URL): plain `sync` fetches, which writes `~/nixos/.git`'s remote refs.

Front ends: `nixin-gui --config ~/nixos --self-test` → `self-test ok (offscreen,
1200x780, first frame drawn)`. `scripts/tui-screenshots.py` and `gui-screenshots.py`, fixtures
(42 SVG, 22 PNG, 42 s / 20 s) and `--live --config ~/nixos --hm-host 19BK` (20 SVG, 14 PNG,
40 s / 23 s; the Set and add previews are the real `--dry-run`, cancelled; Browse fetched
screenshots for an installed and a not-installed game) into `/tmp/integrate-night/shots/`:
no errors.
The live Browse details show the AppStream block and Try side by side. There is no TUI
self-test; the TUI's coverage is the test suite and the screenshot runs.

Seen live, not new: Prism Launcher reads `○ not installed` in the details (the truth layer
does not count `users.users.<name>.packages`; HANDOFF-gui, AppStream section). The option
index paths (`where`'s `docs`, `skipped_scopes`, `search` from the index) were not
exercised live: generation 126 has no nixin module, so no index exists on mbp13 now
(unit-tested; HANDOFF-modules has the live run on gen 125).

## Gaps / next

1. Wire `explain` into the Options page (the seam: `view.option_sections` and the one
   `where` call per page; HANDOFF-tui "Options page", judgement call 5).
2. `configrepo.default_host` could use the cached `truth.hosts()` (a lazy import) when the
   text scan misses.
3. CLI start still imports the AppStream layer (`xml.etree`, its parsers: ~8 ms of ~100 ms);
   `browse`/`info` could import it on use as discovery does with `requests`.
4. The lanes' own next steps stand: a TUI Browse screen, `users.users.<name>.packages` as
   installed, the real `apply --host 19BK` (only dry runs so far), bumping the hub's nixin
   lock once this is on main (modules), `outdated --check` (ui-packages).
5. The `live-*` screenshots are each lane's own (not redrawn): their rails lack the later
   pages.

## Left behind

This run cannot `rm -rf`. `/tmp/integrate-night/` holds the build logs, the smoke outputs,
the screenshot runs, the sudo stub, the clone `nixos-clone/` and the out-link `result`
(a GC root for nixin-0.1.0 until removed): `rm -rf /tmp/integrate-night`. The live GUI run
added a few images to `~/.cache/nixin/images/`.

## How to test

```
git -C ~/nixin-wt/integrate2 log --oneline --first-parent 318e6cc..integrate-night
nix build .#nixin --no-link -L                       # 930 passed, 34 skipped, then the self-test
nix develop -c ruff check && nix develop -c ruff format --check && nix fmt -- --check
nix develop -c pytest tests/test_startup.py tests/test_outdated.py   # the two follow-ups
# the smoke, read-only (sudo stubbed; scratch, gone with /tmp/integrate-night): smoke.py NIXIN OUTDIR [NAME…]
python3 -I /tmp/integrate-night/bin/smoke.py "$(nix build .#nixin --no-link --print-out-paths)/bin/nixin" /tmp/smoke
nix develop -c python3 scripts/gui-screenshots.py /tmp/shots   # --only main|packages|options|try|browse
```
