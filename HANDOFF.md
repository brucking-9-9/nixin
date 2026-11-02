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
- [tui](docs/HANDOFF-tui.md) — branch `tui`: `nixin tui` / `nixin-tui` (Textual) over the same
  documents, `view.py` as the view-model for the GUI; the cowsay round trip ran from a
  50×25 terminal on mbp13 (gens 113–114).
- [gui](docs/HANDOFF-gui.md) — branch `gui`: `nixin gui` / `nixin-gui` (PySide6 + QML) on the
  shared `nixin.ui` data path and view-model (moved out of `tui/`); offscreen tests and
  screenshots, live read-only on mbp13 and its Wayland session; the real apply round trip
  was not run (another session had `~/nixos` mid-change).
