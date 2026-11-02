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
