# HANDOFF

Per-layer handoff notes live in `docs/HANDOFF-<layer>.md` (state, rough edges, next steps,
how to test). A layer branch keeps its own `HANDOFF.md` at the root while it is in flight;
on merge it moves here.

- [discovery](docs/HANDOFF-discovery.md) — merged; verified live on 19BK and mbp13.
- [truth](docs/HANDOFF-truth.md) — merged; verified end to end on mbp13.
  `list` verb: branch `list-verb`. `nixin list [--home USER] [--scope system|home|all]
  [--filter TEXT] [--programs]` shows the installed packages (system + every HM user)
  from `status`'s cached package eval, with a best-effort `declared[]` (file:line) from
  the writer's scanner. Verified live on mbp13 against `~/nixos` (mbp13, 19BK/brucking);
  state, numbers, next steps and how to test: the "`list`" section of HANDOFF-truth.md.
- [writer](docs/HANDOFF-writer.md) — merged; acceptance list verified on mbp13 against a clone of the config.
  Fixes: branch `fix-set-validation` — `set` values are checked by syntax only (`pkgs.…`,
  `lib.mkForce …`, `config.…` were `invalid-nix`), and the option eval check survives package sets.
- [integrate](docs/HANDOFF-integrate.md) — the merged CLI run end to end on mbp13; envelope fixes,
  `docs/CONTRACT.md`, integration tests, open judgement calls.
- [apply](docs/HANDOFF-apply.md) — branch `apply`: `plan`, `apply`, `sync`, `status.pending`;
  mbp13 switched for real from its workspace (gens 110–112), `/boot` freed to 81M.
  Branch `core-history-safety`: `nixin history` (generations joined with
  `history.jsonl`), `nixin rollback [GEN]` (system back + one revert commit pulled into
  `/etc/nixos`), and the boot block in `plan`/`apply` (`reboot_needed`, `boot_space` from
  systemd-boot's window; `boot-space` refuses). Round trip run for real on mbp13: gen 121
  (cowsay) → rollback to 120 in 2.9 s, trees in sync. First section of HANDOFF-apply.
- [tui](docs/HANDOFF-tui.md) — branch `tui`: `nixin tui` / `nixin-tui` (Textual) over the same
  documents, `view.py` as the view-model for the GUI; the cowsay round trip ran from a
  50×25 terminal on mbp13 (gens 113–114).
- [gui](docs/HANDOFF-gui.md) — branch `gui`: `nixin gui` / `nixin-gui` (PySide6 + QML) on the
  shared `nixin.ui` data path and view-model (moved out of `tui/`); offscreen tests and
  screenshots, live read-only on mbp13 and its Wayland session; the real apply round trip
  was not run (another session had `~/nixos` mid-change).
- [outdated](docs/HANDOFF-outdated.md) — branch `outdated-verb`: `nixin outdated`, what a
  nixpkgs lock update would change for a host (two package-list evals, by pname, kernel),
  and `--published` against a search.nixos.org channel; run live on mbp13 for both hosts.
- **ui-packages** — branch `ui-packages`: the **Packages** page in both front ends and the
  `update` verb behind its one button. Details: the "Packages page" sections of
  [HANDOFF-tui](docs/HANDOFF-tui.md) and [HANDOFF-gui](docs/HANDOFF-gui.md).
  - Done: `nixin update [--input nixpkgs] [--to REF|REV] [--dry-run]` (workspace only,
    `{input, from, to, changed, command}`, never builds; CONTRACT "`update`");
    view-model rows for `list`/`outdated`/`update` (`ui/view.py`); TUI page (`i`,
    `ctrl+4`): installed (filter, for whom, Enter → Details for the row's user with
    Remove) and updates (lock / `--published`, kernel line, summary, `u` → confirm →
    `update` → Pending), page keys folded into one footer group below 80 cells; GUI page
    (`Ctrl+4`): the same two views, the details pane per page, the update dialog →
    Pending. Tests at 50×25 and 120×40 (TUI) and offscreen (GUI) on recorded documents;
    screenshots in `docs/tui/`, `docs/gui/`. Live on mbp13: both UIs read-only against
    `~/nixos`; the update round trip on a throwaway clone in both UIs. Nothing switched,
    no sudo, `~/nixos` untouched. Left behind: the clone `~/nixos-ui-test` and
    `/tmp/uipk` (the run could not `rm -rf`): `rm -rf ~/nixos-ui-test /tmp/uipk`.
  - Gates at 9c4f1cf: `nix build` 577 passed, 25 skipped (live), then `nixin gui:
    self-test ok`; `nix develop -c pytest` the same; `ruff check`, `ruff format
    --check`, `nix fmt -- --check` clean; qmllint clean (a test).
  - Half-done: nothing. Not run: Plan/Apply after an update (a new nixpkgs means a long
    build, and apply's dry run asks sudo).
  - Found live: at nixos-unstable 0bb2eed mbp13's config no longer evaluates (`gtk+-2.24.33`
    refused as broken); `outdated` cannot see that (package lists only). Next: an
    `outdated --check` that evaluates the target's toplevel; find mbp13's gtk2 user
    before moving its lock; a "revert the lock" action on Pending.
  - How to test: `nix develop -c pytest tests/test_update.py tests/test_tui.py
    tests/test_gui.py`; `nix build`; by hand `nixin-tui --config ~/nixos` → `i`, `t`, `m`
    and `nixin-gui --config ~/nixos` → Ctrl+4 (read-only; use Update only on a clone).
- [try](docs/HANDOFF-try.md) — branch `try-verb`: `nixin try PKG [-- ARGS] [--shell]
  [--program NAME] [--nixpkgs REF] [--impure] [--build-only]`, which runs a package from
  the host's locked nixpkgs without installing it. A probe eval, `nix build --no-link`
  (progress on stderr), then an exec of `nix run`/`nix shell`. `--json` only builds and
  returns `{out, programs, size, cached, fetched, command, gui, …}`. Errors: `not-found`
  (with suggestions), `unfree` (the `NIXPKGS_ALLOW_UNFREE=1 … --impure` commands),
  `ambiguous-program`, `no-program`, `build-failed`. **Try** sits next to Add in both front
  ends: the build streams into the log, then a GUI app is launched detached (`setsid`), and
  a CLI program gets the command to copy (TUI) or opens in the terminal (GUI: `$TERMINAL
  -e`, else kitty, …). Live on mbp13: cowsay, figlet on a pty, `--shell`, gnome-calculator
  built (detached `--version` only: no window on the kiosk screen), lolcat built from
  source. Nothing switched, no sudo, `~/nixos` only read.
