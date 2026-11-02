# HANDOFF

Per-layer handoff notes live in `docs/HANDOFF-<layer>.md` (state, rough edges, next steps,
how to test). A layer branch keeps its own `HANDOFF.md` at the root while it is in flight;
on merge it moves here.

- [discovery](docs/HANDOFF-discovery.md) — merged; verified live on 19BK and mbp13.
- [truth](docs/HANDOFF-truth.md) — merged; verified end to end on mbp13.
- [writer](docs/HANDOFF-writer.md) — merged; acceptance list verified on mbp13 against a clone of the config.
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
