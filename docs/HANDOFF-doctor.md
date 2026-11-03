# HANDOFF: `nixin doctor` + the `nixin-state --help` guard (on `review/nixin`)

Written 2026-11-03 on mbp13, headless, as `master`, on top of last night's four commits
(2d97c24). Nothing is pushed. This session only read `~/nixos` and `/etc/nixos`: no
writes, no switch, no sudo.

The job (MORNING.md's pick from the nixin, config-19bk and config-mbp13 reviews): one
read-only command that answers what the review needed a dozen commands for, and the
hand-installed secrets checklist that the 2026-10-23 reinstall lacked.

## State

Done. Nothing is half-written.

- `src/nixin/doctor.py`: the verb, registered in `cli.py` (VERBS, `doctor.register`).
  Eight check groups run side by side in threads; each outside command goes through
  `Ctx.run` (tests pass a fake). `{name, ok, detail, fix}` per check, `ok` null =
  unknown or informational. CONTRACT.md `### doctor` has the full table.
- `src/nixin/state.py`: `-h`/`--help` anywhere prints the usage on stdout, exit 0, runs
  nothing (it used to run `outdated`, notification included).
- `tests/test_doctor.py` (23), 4 + 1 new cases in `tests/test_state.py`, `test_cli`'s
  every-verb test knows doctor stops at `host-not-local` for `--host testhost`.

## Judgement calls

- **Findings are not errors.** `ok` stays true, exit 0; `healthy`/`failed[]` carry the
  verdict. CONTRACT says `ok: false` means the verb could not do what was asked, and a
  doctor that found a problem did its job.
- **`ok: null` for "your work in progress".** A dirty workspace, commits not applied
  yet, lock drift, failed units, more generations than `configurationLimit`: shown with
  a fix, never a failure, or `healthy` would be false every time you `nixin add`.
- **Network only with `--full`.** The brief asks for < 2 s; one unreachable host costs
  ConnectTimeout (5 s). Without `--full`, `tree.hub` and `host.*` rows say "not asked".
- **`--config` has dest `workspace`**, not `config`: `cli.document` would otherwise run
  `default_host` on it (a flake scan, possibly a `nix eval`). Doctor is always this
  machine: `--host` elsewhere is `host-not-local`.
- **Secrets are found per host, eval-free.** From `nixosConfigurations.<host>`'s block in
  `flake.nix`, the relative paths are followed through the tracked `.nix` files. On the
  hub that keeps 19BK's `/etc/beszel-agent.env` (root `beszel-agent.nix`) out of
  mbp13's list. Only literal paths count, plus `${config.home.homeDirectory}`,
  `${config.xdg.configHome}`, `$HOME`, `~/`. Things that are not an option's path,
  like couchdb's `[admins]` inside `local.ini`, the syncthing identity passed via
  `cert = "${…}/cert.pem"`, or `fprintd-list`, are not found; see Next.
- **systemd-boot is detected from the running system's builder**
  (`safety.boot_window`), because mbp13's `/boot` is root 0700 and `loader/entries` is
  unreadable as the user. `boot.space` compares free space with the largest kernel+initrd
  set of any generation, specialisations included. A full /boot gets no fix line: gc
  cannot free anything when the menu already holds only `configurationLimit` entries.

## Live on mbp13 (read-only, `~/nixos` @ d06d488, unchanged and clean afterwards)

| run | result |
|---|---|
| `nixin doctor --config ~/nixos` | 15 checks, 0.19 s wall (33 ms in the checks); `boot.space` BAD |
| `nixin --json doctor --config ~/nixos` | one document, stderr empty, exit 0, `healthy: false`, `failed: ["boot.space"]` |
| `nixin --json doctor --full --config ~/nixos` | 2.7 s (`git ls-remote` to GitHub over ssh); `failed: ["tree.hub", "boot.space"]` |

Real findings: **/boot has 80 MiB free; every generation's kernel+initrd set (default +
`gaming` + `nvidia-plasma`) is 107 MiB**, so the next kernel bump will not fit next to
the current one. **`~/nixos` main is 1 commit behind origin/main** (d1e2357, "19BK
nixin-state: not in home.packages"). The index is generation 128's (fresh); both timers
are enabled and their last runs succeeded; the only secret mbp13 names (beszel's
`~/.config/beszel/agent.env`) is present.

## Next

- A `programs.nixin.doctor` hook for checks the grep cannot see: a module option
  `programs.nixin.doctor.paths = [ { path; fix; } ]`, written into the option index or
  `/etc/nixin/doctor.json`, so couchdb's admin file, the syncthing identity and
  `/etc/nix/mbp13-builder` (built from a string in remote-build.nix) are listed by the
  module that needs them.
- The agents board / bar: run `nixin-state doctor` from the status timer into
  `~/.local/state/nixin/doctor.json` (one more document next to `summary.json`).
- The TUI/GUI: a Health page over the same document.
- `cache dir size` from the original pitch is not a check (it is not a health problem
  on its own); add it to `detail` of a cache row if the board wants it.

## How to test

`nix develop -c pytest tests/test_doctor.py tests/test_state.py`; by hand, read-only:
`nixin doctor`, `nixin --json doctor --full`.
