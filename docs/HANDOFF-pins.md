# HANDOFF: pins (branch `pins`, 2026-11-05, 19BK)

Design: `docs/DESIGN-pins.md` (probes, decisions, the deferred security section). Branch cut from `main` @
61fde16 in `~/Projects/nixin-wt-pins`; steps 1-3 merged to main as dffe51d, step 5 on the branch after it. `~/nixos` was never written: live checks
ran on `git clone` copies under /tmp.

## Done (steps 1-3 of the design)

1. **`update` lock guard** (`outdated/update.py`): after nix ran, the two locks are compared node by node; the
   requested input and the nodes only it reaches (its private sub-inputs, in the new lock) may move; anything
   else moving puts the saved `flake.lock` back and fails with `lock-moved-others` (`moved[]`);
   `--allow-others` keeps nix's result. `--hold NAME,…` = `nixin pin` each at the current lock, then the
   update, then the pins' checks against the new lock (`holds[]`, `checks[]`). Repro of the hub's 30f7d68:
   `nix flake update nixin` on nix 2.34.9 moved only nixin; what moved nixpkgs that day is not known.
2. **`src/nixin/pins/`**: `pin NAME [--attr A]… [--rev R] [--whole-set] [--why T]`, `unpin NAME…|--all`,
   `pins [--check]`. State: `nixos.<host>.pins` in `installer/state.json` (version stays 1; older nixins read
   it). Render (`writer/state.py`): a `let` binding per pinned revision (`builtins.fetchTarball` by the lock's
   narHash: offline, the same store path the flake input has) and one overlay per revision; packages/options
   render below as before. `pin kernel` reads the kernel attribute from the `boot.kernelPackages`
   declaration (`pins/kernel.py`: writer's `option_decls` + a `pkgs.linux…` scan) and overlays both aliases
   (`linux_X = pinned.linux_X`, `linuxPackages_X = final.linuxPackagesFor final.linux_X`; `--whole-set`:
   the set from the pin). Checks (`pins/checks.py`, ids stable for the UIs): `pin-stale`, `kernel-toolchain`,
   `eval-cost` (info), `glibc-skew` (info) / `gl-skew` (warn), `rebuilds-dependents` (`nix-store -q
   --referrers-closure` ∩ the running closure; warn ≥ 5), `kernel-set-mismatch` (warn), `kernel-modules-
   dropped`, `pin-not-holding` (block → `checks.eval.code: pin-blocked` + a warning; the write stays).
   Nested attributes / language sets / a kernel from another input / a kernel attribute that is not the host's
   kernel are `pin-unsupported` (nothing written). `--home` is `usage`: pins are per host.
3. **holds in the documents**: `outdated` (lock mode) → `holds[]` (+ `available`, `behind`; one extra cached
   eval `outdated-holds` on the target without the overlay) and `kernel.held`; human: `kernel 7.2.14 (held by
   pin kernel; 7.2.15 available)` + a `holds:` block. `plan`, `apply`, `apply --dry-run` → `pins[]`, stale
   pins as warnings (three additive lines in `apply/__init__.py`; the plan-progress lane restructured that
   file, expect a trivial merge at the `"boot": boot,` anchors).

Decisions that differ from the design text: no `--force` on `pin` (the only blocks before writing are
unsupported attributes, which `--force` cannot make work); `pins` without `--check` evaluates nothing
(stale = the pin's revision is the lock's); `pins --sizes` folded into `--check` (closure size only when the
output is in the store). Human renderings live in `pins/render.py` and `outdated/render.py`, not
`ui/view.py` (the other lane's file).

## Verified

- Probes (design §Probes): fetchTarball by narHash = the flake input's store path, offline, 0.03 s; the
  kernel overlay gives the identical `dgw0v7mz…-linux-t2-7.2.14.drv` at the same rev and with nixpkgs
  overridden to 1d06008 (plain: 7.2.15); `config.nixpkgs.config` read inside the overlay inside the module
  fixpoint evaluated 19BK's toplevel (28 s).
- Live, on a clone of `~/nixos` (`/tmp/nixin-pins-live.sh`, log in `/tmp/nixin-pins-live.log`): `pin kernel`
  on 19BK (eval ok, 3 files), kernel drvPath unchanged, `pins` → `1 pin, 1 stale`, `pins --check` → the three
  infos, `outdated` → `kernel 7.2.14 (held by pin kernel; 7.2.15 available)`, `unpin kernel` → `git status`
  clean, drvPath unchanged.
- Offline: `tests/test_pins.py` (34 + 1 live), `tests/test_update.py` (+3 guard tests), `tests/test_outdated.py`
  (+1 holds test); the generated module byte for byte through the real nixfmt
  (`tests/fixtures/pins/19BK.nix`, regenerate with `NIXIN_WRITE_FIXTURES=1`); the eval expressions parsed by
  the real `nix-instantiate` (the fakes never parse Nix; this caught two syntax errors).

## Step 5: the UIs (after the plan-progress merge, dffe51d)

- `ui/view.py`: `UpdateRow.held` + `hold_args`, held rows from `holds[]` in `update_rows`, `kernel_hold`,
  `hold` lines in `update_confirm_fields`, `PinRow`/`pin_rows`/`pin_columns`/`pin_cells`/`pin_detail`,
  `check_fields`/`check_counts`, `writer_summary` for pin/unpin. `ui/backend.py`: `CONFIG_VERBS` + pin, unpin, pins.
- TUI (`tui/packages.py`): the Updates tab holds a row (`h`) or the kernel (`k`, also a button on a
  desktop) through the writer's diff modal (`app.write`: `WRITE_VERBS` + pin/unpin), then evaluates
  `outdated` again; the update confirm lists the holds; after an `update` with holds, `pins --check` and
  a notification with the warn/block counts. A Pins tab (`n`): the table, the detail line, the checks
  (`c`: `pins --check`), `u`: unpin the row. `app.pins_doc` is kept like the outdated documents.
- GUI: `PinsVM` (area `pins`, page `pins`, Ctrl+6, NavRail entry; `PinsPage.qml`, `PinRow.qml`);
  `PackagesVM.hold(i)`/`holdKernel()` (`UpdateRow`'s hold / unhold button, the "hold kernel" button,
  `kernelHold` state); the update dialog shows the `hold` fields; `models.PIN`, `UPDATE` + `held`.
- `nixin-state status` also writes `summary.json`'s `pins {count, stale, at}` (one `pins` call, no eval).
- Tests: fixtures `pins_19BK`, `pins_19BK_check`, `pins_empty`, `pin_kernel{,_dry}`, `pin_firefox{,_dry}`,
  `unpin_kernel{,_dry}`, `outdated_19BK_holds` (hand-made in the CONTRACT shape); `tui_fake.HOLDS` (the
  second `outdated` answers with holds); view-model tests; one TUI flow at both sizes and one GUI flow
  (hold kernel → diff → write → held rows → hold a row → update dialog's holds → Pins → check → unpin);
  the GUI grabs test takes the Pins page too; the state test checks the `pins` part.

## Step 6: undo and pin at a previous revision (branch `pin-previous`, 2026-11-05)

The gap: after "Update nixpkgs lock" in the GUI the lock had moved (0bb2eed → 1d06008,
uncommitted) and nothing in the UIs could hold the kernel at the *old* revision or take the
move back; only `nixin pin kernel --rev 0bb2eed` did.

- `src/nixin/lockstate.py`: `LockRevs.read(repo, input, host)` = the working lock, the
  committed one (`git show HEAD:flake.lock`), the applied one (the authoritative tree's
  `flake.lock`, `$NIXIN_AUTHORITATIVE`/`/etc/nixos`), the running system's short rev
  (`nixos-version`; this machine only). `lock_change()` → `{from, to, committed, running}` or
  None; `revisions()` for the chooser; `known(rev)` for a `--rev` that needs no prefetch.
  No nix. Tests fake the running system (conftest autouse) and point the authoritative
  tree at nothing, so a developer's own /etc/nixos never leaks in.
- `update --undo [--dry-run]` (`outdated/update.py`): `git checkout HEAD -- flake.lock`
  (index + worktree), `restored[]` every node that went back, `nothing-to-undo` with the
  pin-at-the-applied-revision / `git revert` alternative when the move is a commit.
- `outdated`: `lock_change` on every document; while the move is uncommitted and no target
  is named, the comparison is the move itself (`_pending`): the committed node through
  `--override-input … ?narHash=` (offline: nix has the tree) against the working lock,
  `target.from = "pending"`, `lock_change.compared = true`, commands plan/apply/undo.
  `status` carries `lock_change` too.
- `pins`: `revisions[]`. `pin --rev`: a committed/applied revision takes narHash and date
  from that lock (no `nix flake prefetch`).
- `ui/view.py`: `UpdateRow.rev`/`hold_label`, `previous_rev`, `kernel_hold` with `--rev`,
  `lock_change_field`, `can_undo`, `undo_confirm_fields`, `pin_rev_choices`, `pin_at_args`;
  `update_args` is None for `target.from == "pending"` (the Update button disables itself).
- TUI: `z` (Updates, Pending) = undo through `UpdateModal` (title/button switch on
  `dry.undo`); `p` on the Pins tab = `PinModal` (name, a `RadioSet` of revisions + a free
  field); `check_action` keeps `p` = Pending elsewhere. Both buttons hide on a phone (keys).
- GUI: `PackagesVM.startUndo` through the update dialog (`updateConfirmLabel`), `canUndo`
  on Packages and Pending; `PinsVM.startPin/confirmPin/cancelPin` + `PinDialog.qml`;
  `UpdateRow` shows `holdLabel` ("hold at 158.0 (0bb2eed)"). The row buttons sit above the
  row's MouseArea (`z: 1`, main's fix); the tests click them for real.
- Fixtures: `outdated_19BK_moved`, `status_moved`, `update_undo{,_dry}`, `pins_19BK_revs`,
  `pin_kernel_rev{,_dry}`; `lock_change: null` and `revisions[]` added to the recorded ones.
- Live (clones of `~/nixos`, logs `/tmp/nixin-pinprev-live*.log`): update → `outdated`
  compares the move in 7 s offline → `update --undo` leaves the clone byte for byte at HEAD;
  update → `pin kernel --rev <HEAD's>` without prefetch → `pins` shows it not stale.

## Left (step 6)

- The pending comparison evaluates no `available` for the holds (`_holds(..., target=None)`):
  with the kernel pinned at the old revision and the lock moved, the kernel line reads
  `held by pin kernel` without the `7.2.15 available` tail and `behind` stays false. One
  more `--override-input` eval at the working lock would give it.
- While the move is uncommitted the branch head is not resolved, so `nixin-state outdated`
  (the timer) records the move's changes in that window and `state.render_lock` prints
  `locked X → X` (`target.rev == lock.rev` in pending mode); `summary.json` has no
  `lock_change` field yet. Transient (until apply or undo), visible on the bar.
- The drawer's Escape (`Keys.onEscapePressed` on `PaneDrawer`) works while the pane has
  the focus; after a selection the focus stays in the list, so only the back button closes it
  from there. A handler on the page Item would cover both.
- The GUI's hold / hold-kernel controls were to be restyled as bordered buttons; the request
  was cancelled: only their label text changed (`hold at 158.0 (3ec6363)`).

## Left

- §4 security (vulnix, kernel.org, `reasons[]`): deferred by the user.
- The hold of a row pins by pname (`pin PNAME`): right for top-level attributes named like their pname;
  a nested one answers `pin-unsupported` in the modal/notice (the CLI's `pin NAME --attr ATTR` covers it).
- HM-scope pins for hosts without `useGlobalPkgs` (none here); `pin --rev` for non-github inputs;
  TUI/GUI screenshots for the docs (`scripts/tui-screenshots.py`) not redrawn.

## How to test

```
nix develop -c pytest -q tests/test_pins.py tests/test_update.py tests/test_outdated.py tests/test_apply.py
NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest -q tests/test_pins.py -k live   # clones, never edits ~/nixos
nix build .#default --no-link -L
```
