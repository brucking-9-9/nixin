# HANDOFF

Per-layer handoff notes live in `docs/HANDOFF-<layer>.md` (state, rough edges, next steps,
how to test). A layer branch keeps its own `HANDOFF.md` at the root while it is in flight;
on merge it moves here.

## Now: branch `integrate-night` (2026-11-02/03, mbp13)

[HANDOFF-integrate-night](docs/HANDOFF-integrate-night.md): the merge record, every
conflict decision, the live smoke table.

- **Done**: the night's seven lane branches merged onto `origin/main` @ 318e6cc, in this
  order: `typed-set-explain`, `ui-options`, `hosts-remote-diff`, `gc-generations-speed`,
  `modules-optindex`, `try-verb`, `appstream-categories`. `nix build .#nixin` passed after
  each merge; at the end 930 passed, 34 skipped (live), then `nixin gui: self-test ok`;
  `ruff check`, `ruff format --check`, `nix fmt -- --check` clean. Two helpers folded into
  one (the cached host list), the two sources of option docs in `where` reconciled,
  colliding screenshot numbers and `--only` flags unified, a flaky outdated test fixed,
  `explain`'s eager thread-pool import made lazy (startup test extended), the fixture
  screenshots redrawn on the merged tree. Live smoke on mbp13 against `~/nixos`, read-only
  (dry runs only, sudo stubbed out): the 22 verbs that have `--json` (34 runs): one
  document each, nothing on stderr, `~/nixos` unchanged; for `tui`/`gui` the GUI
  self-test and both screenshot scripts (fixtures and `--live`), clean.
- **Half-done**: nothing. Not run: anything that writes or switches (by design), the
  option index paths live (gen 126 has no nixin module), `apply --host 19BK` for real.
- **Next**: wire `explain` into the Options page; a TUI Browse screen; count
  `users.users.<name>.packages` as installed (truth); `default_host` on the cached host
  list; the AppStream layer imported on use (~8 ms of CLI start); bump the hub's nixin
  lock once this is on main (modules); `outdated --check` (ui-packages).
- **How to test**: `nix build .#nixin --no-link -L`; `nix develop -c pytest`; the commands
  at the end of HANDOFF-integrate-night. By hand, read-only: `nixin-tui --config ~/nixos`
  (`o` Options, `t` Try in Details) and `nixin-gui --config ~/nixos` (Ctrl+5 Options,
  Ctrl+B Browse).
- **Left behind**: `/tmp/integrate-night` (build logs, smoke outputs, screenshot runs, a
  clone of `~/nixos`): `rm -rf /tmp/integrate-night`.

## Layers

- [discovery](docs/HANDOFF-discovery.md) — merged; verified live on 19BK and mbp13.
- [truth](docs/HANDOFF-truth.md) — merged; verified end to end on mbp13. `list` (branch
  `list-verb`, merged): the installed packages with a best-effort `declared[]`; `list --diff
  A B` (branch `hosts-remote-diff`, merged on integrate-night): two hosts compared. Their
  sections of HANDOFF-truth.
- [writer](docs/HANDOFF-writer.md) — merged; acceptance list verified on mbp13 against a
  clone of the config. Fixes: branch `fix-set-validation` — `set` values are checked by
  syntax only, and the option eval check survives package sets.
- [typed](docs/HANDOFF-typed.md) — branch `typed-set-explain`, merged on integrate-night:
  `set` checks the value against the option's type before writing (`type-mismatch`,
  `--force`; `set OPTION` describes it), `nixin explain OPTION|PKG` (the config's side +
  upstream's docs, a `view` for help panes), and three judgement calls decided (eval
  failure after a write is `ok: false` with `undo[]`; exclusions per host; the `--host`
  default: this machine, else the config's only host).
- [integrate](docs/HANDOFF-integrate.md) — the merged CLI run end to end on mbp13; envelope
  fixes, `docs/CONTRACT.md`, integration tests, open judgement calls.
- [apply](docs/HANDOFF-apply.md) — `plan`, `apply`, `sync`, `status.pending` (branch
  `apply`; mbp13 switched for real, gens 110–112). `history`, `rollback [GEN]` and the boot
  block in `plan`/`apply` (branch `core-history-safety`; round trip gen 121 → 120 in
  2.9 s). On integrate-night: `apply --host H` for another host over one `ssh -t` session,
  `~/.config/nixin/hosts.toml`, `needs-tty` (branch `hosts-remote-diff`; offline-tested with
  a fake ssh, live only as dry runs: 19BK is not reachable from mbp13); `generations` and
  `gc [--keep N] [--older-than DAYS] [--dry-run]`, `outdated`'s evals side by side, lazy
  imports and the cached host list (branch `gc-generations-speed`; a real `gc --keep 3` on
  mbp13 deleted gens 87–117 and freed 3.5 GB). One section per branch in HANDOFF-apply.
- [outdated](docs/HANDOFF-outdated.md) — branch `outdated-verb`, merged: what a nixpkgs
  lock update would change for a host, and `--published` against a channel; `update`
  takes it (branch `ui-packages`).
- [modules](docs/HANDOFF-modules.md) — branch `modules-optindex`, merged on
  integrate-night: `nixosModules.default` / `homeModules.default` (`programs.nixin.{enable,
  package, optionIndex, outdated, pending}`) and build-time option indexes
  (`lib/options-index.nix`), read by `search --kind opt|hm`, `info`, `where` and
  `status.indexes`; `nixin-state` for the modules' timers. mbp13 ran the module on gen 125
  and is back on main (gen 126). The hub's nixin lock is bumped after this reaches main.
- [try](docs/HANDOFF-try.md) — branch `try-verb`, merged on integrate-night: `nixin try PKG
  [-- ARGS] [--shell] [--program NAME] [--nixpkgs REF] [--impure] [--build-only]` runs a
  package from the host's locked nixpkgs without installing it; **Try** next to Add in
  both front ends (a GUI app launched detached, a CLI program's command to copy or opened
  in a terminal).
- [tui](docs/HANDOFF-tui.md) — `nixin tui` / `nixin-tui` (Textual) over the same documents;
  the cowsay round trip ran from a 50×25 terminal on mbp13. Pages since: **Packages**
  (branch `ui-packages`, merged) and **Options** (branch `ui-options`, merged on
  integrate-night); their sections of HANDOFF-tui.
- [gui](docs/HANDOFF-gui.md) — `nixin gui` / `nixin-gui` (PySide6 + QML) on the shared
  `nixin.ui` data path and view-model; offscreen tests and screenshots. Pages since:
  **Packages** (ui-packages), **Options** (ui-options) and **Browse** with the details
  pane's AppStream block (branch `appstream-categories`: `info`'s `appstream`, `search
  --appstream`, the `browse` verb), the last two merged on integrate-night; their sections
  of HANDOFF-gui.
  - ui-options left behind `~/nixos-ui-test` and `/tmp/nixin-ui-options` (`rm -rf` both);
    appstream-categories left `/tmp/appstream-run` (`rm -rf /tmp/appstream-run`).
