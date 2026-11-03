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
  (cowsay) → rollback to 120 in 2.9 s, trees in sync. Its section of HANDOFF-apply.
  Branch `hosts-remote-diff`: `nixin apply --host H` for another host (commit, push
  to the hub, then the two pulls and the switch on H over one `ssh -t` session, with
  checks that H switches to exactly the pushed commit; `~/.config/nixin/hosts.toml`;
  `--dry-run` contacts nothing; `needs-tty` under `--json`/UIs). Offline-tested with a
  fake ssh that runs the real script; live only as dry runs (19BK is not reachable from
  mbp13). Same branch: `nixin list --diff A B` (truth). Its section of HANDOFF-apply,
  "`list --diff`" in HANDOFF-truth.
  Branch `gc-generations-speed`: `nixin generations` (history's rows + closure size,
  boot entry, what `gc --keep N` drops) and `nixin gc [--keep N] [--older-than DAYS]
  [--dry-run]` (never the running/booted/default generation; preview with an estimate,
  then `nix-env --delete-generations`, the boot default's `switch-to-configuration
  boot`, `nix-collect-garbage` through `sudo -n`; `gc-failed`); `outdated`'s two evals
  side by side (4.1 → 2.3 s); warm `status` 0.32 → 0.20 s and `list` 0.35 → 0.23 s
  (lazy imports: no requests/Textual/PySide6 at CLI start; the hosts list cached).
  Real `gc --keep 3` on mbp13: 31 generations deleted (87–117), 3.5 GB freed (`df`;
  estimate 4.8 GiB), /boot unchanged (already 2 entries), 77.5 s. Its section of
  HANDOFF-apply.
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
- **ui-packages** — merged (on `main` with `core-history-safety`): the **Packages** page in
  both front ends and the `update` verb behind its one button. Details, live findings
  (mbp13's config does not evaluate at nixos-unstable 0bb2eed: gtk2) and gaps: the
  "Packages page" sections of [HANDOFF-tui](docs/HANDOFF-tui.md) and
  [HANDOFF-gui](docs/HANDOFF-gui.md). Its leftovers (`~/nixos-ui-test`, `/tmp/uipk`) are gone.
- [typed](docs/HANDOFF-typed.md) — branch `typed-set-explain`: `set` checks the value
  against the option's type before writing (`type-mismatch`, `--force`; `set OPTION`
  describes it), `nixin explain OPTION|PKG` (the config's side + upstream's docs, a `view`
  for help panes), and the three judgement calls decided (eval failure after a write is
  `ok: false` with `undo[]`; exclusions per host; the `--host` default). Run on mbp13
  against `~/nixos` read-only and a clone.
- **ui-options** — branch `ui-options` (cut from `origin/main` 318e6cc, which has
  ui-packages): the **Options** page in both front ends. Details: the "Options page"
  sections of [HANDOFF-tui](docs/HANDOFF-tui.md) and [HANDOFF-gui](docs/HANDOFF-gui.md).
  - Done: a browser over `search --kind opt|hm` (NixOS or Home Manager options; name,
    type, description), the selected option in full (type, default, example, the whole
    description, the value on the host and the file:line that sets it from `where`, the
    Home Manager user's tree for `hm`, the declaring module on the locked input and
    upstream's link), Set (the dialog prefilled with the current value when it can be
    written back exactly, the type, example and default as a hint; a template's `<name>`
    filled in first; a list element set through its list) and Unset, each through the
    writer's `--dry-run` diff, then `where` again; the write shows on Pending. TUI `o` /
    `ctrl+5` (`ctrl+]` on legacy terminals), page keys `i o p s`; GUI rail entry,
    `Ctrl+5`. Shared view-model `ui/view.py` ("options"). No backend change.
  - Tests: TUI at 50×25 and 120×40, GUI offscreen, on documents recorded on mbp13
    (`tests/fixtures/tui/options_*.json`); qmllint clean. Gates at b40df71: `nix build`
    617 passed, 25 skipped (live), then `nixin gui: self-test ok`; `ruff check`, `ruff
    format --check`, `nix fmt -- --check` clean. Screenshots: `docs/tui/*-13…18-*`,
    `docs/gui/14…17-*` (fixtures), `live-*` (below).
  - Live on mbp13: both UIs read-only against `~/nixos` (mbp13's openssh options, the
    real `set --dry-run` diff, cancelled; 19BK's `programs.git.lfs.enable` for brucking);
    `~/nixos` unchanged (HEAD a198757, status/diff and file hashes compared). One real set
    → Pending → unset round trip through each UI on a throwaway clone `~/nixos-ui-test`
    (set 1.1 s, where 2.9 s, unset 1.2 s). Nothing switched, no sudo.
  - Not used yet: `explain` (merged alongside in `integrate-night`, not wired in). The seam is
    `view.option_sections(record, where, …)` and the one `where` call per page; the
    mapping is in HANDOFF-tui "Options page", judgement call 5.
  - Half-done: nothing. Next: wire `explain` after that branch merges (locked
    default/example, HM links at the locked rev); paging past 50 results; say in the Set
    dialog when it starts from the default because the value cannot be written back.
  - Left behind (this run's settings deny `rm -rf`): `rm -rf ~/nixos-ui-test
    /tmp/nixin-ui-options` (the clone, with `hosts/mbp13/default.nix` edited by the
    unset; recordings and scratch scripts).
  - How to test: `nix develop -c pytest tests/test_tui.py tests/test_gui.py -k option`;
    `nix build`; by hand `nixin-tui --config ~/nixos` → `o` and `nixin-gui --config
    ~/nixos` → Ctrl+5 (read-only; Set/Unset only on a clone).
