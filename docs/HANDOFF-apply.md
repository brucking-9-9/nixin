# HANDOFF: apply layer (branch `apply`)

Written 2026-11-01 (evening) on mbp13, headless, as `master`, with a sudo ticket. The branch
was cut from `main` @ f03f4e7 (discovery + truth + writer + integrate). Nothing is pushed.
This session switched mbp13 four times (gens 110–112 plus a no-op preflight switch). It was
the only session switching the machine during the run.

## State

`plan`, `apply` and `sync` work. They ran for real on mbp13 against the real workspace
(`~/nixos`) and the root-owned authoritative clone (`/etc/nixos`). Every item on the
brief's acceptance list passes; the numbers are below. `status` has a `pending` block.
Nothing is half-done.

Gates at HEAD:
- `nix build`: 332 passed, 22 skipped (live) in the sandbox. The 28 apply tests run there
  with real git (`git` is in `nativeCheckInputs` now).
- `nix develop -c pytest`: the same.
- `NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest tests/test_apply_live.py`:
  3 passed (plan, `apply --dry-run`, `sync` on a throwaway clone; none of them switches
  or writes the workspace).
- `ruff check`, `ruff format --check`, `nix fmt -- --check`: clean.

## Files

| file | what |
|---|---|
| `src/nixin/apply/__init__.py` | `register()`, the verbs, `Ctx` (workspace + authoritative tree), `_relation` (same/ahead/behind/diverged/unknown), `_target` (the out path from plan's cache, else built), `pending()` + `annotate_status()` for truth's `status` |
| `apply/proc.py` | `proc.RUN`, the one seam for nix, git, sudo, nixos-rebuild, systemctl and the helper. `stream` tees output to stderr in human mode. stdin is always closed |
| `apply/git.py` | `Git(path)`, always with `-c safe.directory=*` (the authoritative clone is root-owned): status (porcelain v1 `-z`, renames, intent-to-add), ancestry, log, commit (`add -u` + explicit paths, the repo's own identity and hooks) |
| `apply/privilege.py` | `Step` (pull, switch), `Backend`: `Sudo` (`sudo -n`; `display()` gives what a person types) and `NixosSwitch` (19BK's helper; waits for `$XDG_RUNTIME_DIR/nixos-switch/status`), and `backends()`, which picks the backend per step |
| `apply/build.py` | eval `toplevel.drvPath` → `nix build <drv>^out`, the `nix` adapter for `ConfigRepo`, `PlanCache` (by source hash, by commit), `diff()` |
| `apply/closure.py` | `nix store diff-closures` parser (ANSI, `∅`, `ε`, version sets, size-only lines, unsigned growth) |
| `apply/system.py` | `/run/current-system`, profile generations, `installer/history.jsonl` |
| `apply/commitmsg.py` | the generated commit message (state.json diff, else the files) |
| `apply/render.py` | human output |
| `cli.py` | `apply` import + `apply.register(sub)`; **plus** `--config` before the verb (see judgement call 6) |
| `truth/__init__.py` | one late import + `apply.annotate_status(doc, repo, host)` at the end of `_status` |
| `tests/test_apply.py` | `Machine`: a temp workspace + clone + bare hub with real git, and fakes for nix (content-hashed out paths), sudo, nixos-rebuild (builds the *authoritative* tree the same way and moves the profile links), systemctl, the helper. Tests: the add→plan→apply→rm→apply flow, cache reuse, nothing-to-apply without an eval, dry run, needs-sudo and retry, every refusal, switch/verify failure, `--no-switch`, the helper backend, sync incl. diverged, pending, the closure parser on recorded real output (`tests/fixtures/apply/diff-closures-107-109.txt`), commit messages, porcelain parsing |
| `tests/test_apply_live.py` | the three live checks |
| `tests/test_cli.py`, `tests/test_integration.py` | plan/apply/sync are no longer stubs; the integration world fakes apply's seam; `ApplyError` is in the contract scan; `--config` before the verb |
| `package.nix` | `git` in `nativeCheckInputs` |
| `docs/CONTRACT.md`, `README.md` | the three verbs, `status.pending`, 11 new error codes, measured costs, plan's cache |

## Live run on mbp13

### Preflight (addendum)

- `~/nixos`: `fetch origin`, `checkout main`, `pull --ff-only origin main` → **849cfc3**.
  `git diff --stat mbp13-3ec6363 main` was empty.
- `/etc/nixos`: `sudo -n git fetch /home/master/nixos main && checkout -B main FETCH_HEAD`
  → 849cfc3, branch `main`.
- `sudo -n nixos-rebuild switch --flake /etc/nixos#mbp13`: exit 0, 35 s. Same
  `/run/current-system` (`4ja0awg7…`), still **gen 109**, no new generation.

### Acceptance

All with the built binary (`nix build` of the branch), real workspace, real sudo:

| step | result | wall |
|---|---|---|
| `plan` (clean tree, cold) | `out` = `4ja0awg7…` = running gen 109, `up_to_date`, diff empty; eval 32.2 s, build 4.7 s | 37.6 s |
| `plan` again | `cached` (commit 849cfc3) | 0.8 s |
| `apply` (clean) | `nothing-to-apply` | 0.2 s |
| `status --config ~/nixos` | `pending`: no commits, `running_matches: true` (status's 35 s is truth's own cold eval) | 34.8 s |
| `add cowsay --config ~/nixos` | writer: `installer/mbp13.nix`, `state.json`, import in `hosts/mbp13/default.nix` | — |
| `plan` | `qsrps64f…`, **39 derivations** built (three `system-path`s for default/gaming/nvidia-plasma, dbus/polkit/accounts-daemon units, etc), diff: `+ cowsay 3.8.4 +51.2 KiB`, nothing else | **84.6 s** |
| `apply --dry-run -m "test: cowsay"` | the four commands (`git add -u`, `git commit`, `sudo -n git -C /etc/nixos pull --ff-only /home/master/nixos main`, `sudo -n nixos-rebuild switch --flake /etc/nixos#mbp13`), out cached, sudo ready; HEAD unchanged | 1.3 s |
| `apply -m "test: cowsay"` | commit **716ed68** (author `brucking-9-9 <bruck@tekle.com>` from the workspace config), `cached: true` (no rebuild), pulled, switched: **gen 110** `qsrps64f…`; build 0.9 s, pull 0.05 s, switch 29.4 s | **30.6 s** |
| checks | `readlink /run/current-system` changed 4ja0awg7 → qsrps64f; `zsh -l -c 'command -v cowsay'` → `/run/current-system/sw/bin/cowsay` (and it talks); `sudo -n git -C /etc/nixos log -1` = 716ed68 | — |
| `rm cowsay`, `apply` (human mode, no plan first) | commit **ac28f48** `nixin: mbp13: -cowsay` (generated), includes `installer/history.jsonl` (first line); no rebuild because that tree's content equals 849cfc3's, which the first plan had cached; **gen 111** = `4ja0awg7…` (same path as gen 109); root's pull and nixos-rebuild streamed to stderr; switch 30.2 s | **31.3 s** |
| checks | `/run/current-system/sw/bin/cowsay` gone, not on PATH in a login shell; `/etc/nixos` at ac28f48 | — |
| `apply`, `apply --json`, `apply --dry-run` | `nothing-to-apply` (generation 111), no eval | 0.2 s |
| `sync` (origin = GitHub, fetch only) | workspace 2 ahead, 0 behind `origin/main` | 1.3 s |
| `sync --remote test` (bare `~/nixin-test-remote`) | remote head null (no branch yet) | — |
| `sync --remote test --push` | pushed; 0/0; authoritative `same`, `behind_workspace: 0` | 0.4 s |
| hub-side commit (force-pushed from a scratch clone, based on 716ed68) | `sync`: 1 ahead, 1 behind; `--pull` → `diverged`; `--push` → `diverged` (rejected); workspace untouched | — |
| hub reset to ac28f48, `sync --pull` | `done: [fetch, pull]`, 0/0 | — |

Afterwards I removed the `test` remote from `~/nixos` again (`git remote` = `origin`
only). The bare repo `~/nixin-test-remote` stays; it is harmless, delete it when you like.
Nothing was pushed to GitHub.

### Freeing /boot (final acceptance)

| step | result | wall |
|---|---|---|
| `nixin --config ~/nixos set boot.loader.systemd-boot.configurationLimit 2` | edited **in place** at `hosts/mbp13/default.nix:76` (`= 5;` → `= 2;`), no generated duplicate (`installer/` got nothing; the line count of `configurationLimit` stays 1); eval check 2 | — |
| `plan` | `zknqa0x8…`; built `systemd-boot`, `install-systemd-boot.sh` and the toplevels; **closure diff empty** (same kernels/initrds) | 37.2 s |
| `apply -m "mbp13: configurationLimit 2 — …"` | commit **7da2b4c** (default.nix + history line 2), cached, **gen 112**; switch 29.5 s | **30.3 s** |

| | before | after |
|---|---|---|
| `df /boot` | 197M, 195M used, **2.6M free** (2,724,864 B), 99% | 197M, 117M used, **81M free** (84,118,016 B), 60% |
| `/boot/EFI/nixos` | 8 files: gen 107's 7.2.10 / 6.12.112 kernels + initrds (81,384,753 B = 77.6 MiB; 107 was the last kept generation on them) and the 7.3 / 6.12.114 set | 4 files: `linux-7.3` bzImage + initrd, `linux-6.12.114` bzImage + initrd (113,200,520 B) |
| `bootctl list` NixOS entries | 15 (gens 107–111 × 3) | **6** (gens 111, 112 × default/nvidia-plasma/gaming), plus the Apple set_os entry |
| `sudo -n bootctl list \| grep -c Generation` | 30 | **12**: each entry matches twice (its `title:` and `version:` lines), so 12 = 2 gens × 3 entries |

Freed 81,393,152 B: gen 107's four files (81,384,753 B) plus the 9 dropped `.conf`
entries (gens 107–110 out, gen 112 in). The bootloader step collected first and then
wrote, as expected.

**Note for later:** each generation has two kernels (~57 MB for 7.3, ~55 MB for 6.12.114,
initrd included), so with 197 MB `/boot` can only ever hold **one** distinct kernel set
plus the shared files. The next kernel or initrd change has room only because the kept
set drops the old one first, and only with `configurationLimit` low enough that no kept
generation still references it. The lasting lever is filtering the Intel microcode
bundle to this CPU (06-46-01): about 15 MB per initrd, 30 MB per generation. Not done.

### Final state

- Running: **generation 112**, `/nix/store/zknqa0x828qvan1f12iwwra35dpn4hap-nixos-system-mbp13-26.11.20261023.3ec6363`
  (kernel 7.3.0 booted; the generation was activated by `switch`, no reboot needed).
- `~/nixos` `main` = `/etc/nixos` `main` = **7da2b4c**, and the running system is its build
  (`status.pending.running_matches: true`, `plan`: `up to date: nothing to apply`).
  `/etc/nixos` is clean. 0 failed units, system and user.
- No cowsay: not in `/run/current-system/sw/bin`, not in `installer/` (`state.json` and
  `mbp13.nix` were deleted by `rm` and that deletion is committed).
- `installer/history.jsonl` is tracked; its lines for gens 110 and 111 are committed.
  The line for gen 112 is the one uncommitted change in the workspace. That is the design
  ("committed on the next apply, not this one"): the next apply that commits takes it
  along. A history-only change never makes its own commit and never counts as something
  to apply.
- Workspace `main` is 3 commits ahead of `origin/main` (716ed68 `test: cowsay`, ac28f48
  `nixin: mbp13: -cowsay`, 7da2b4c configurationLimit). Not pushed: that is your call.
  The cowsay pair cancel out except for history.jsonl; squash them before pushing if you
  prefer.

## Judgement calls (decided here; revisit if you disagree)

1. **git+file, not `path:`.** The brief said `nix build path:<workspace>#…`. I measured
   on `~/nixos`: `path:` copies `.git` and untracked files into the store (23 MB against
   244 KB) under its own fingerprint. git+file of the workspace gives the **same store
   path and fingerprint as `/etc/nixos`** at the same commit. It contains exactly what
   `apply` commits (`git add -u` = tracked + intent-to-add), so plan's out path is the
   one root's `nixos-rebuild` produces. With `path:`, a file the config imports but git
   does not track would build in `plan` and fail in `/etc/nixos`. With git+file, `plan`
   reports `eval-failed` and warns about untracked files.
2. **Cache key: the source store path, not the fingerprint.** nix's fingerprint for the
   same content changes between "dirty" and "committed". The source path is a content
   hash, so the entry `plan` writes for a dirty tree answers `apply`'s build right after
   its own commit. A second map, commit → out, answers `nothing-to-apply`, `status.pending`
   and a history-only tree without running nix. One more case: the first commit of
   `history.jsonl` changes the source hash. `apply` therefore looks up the pre-commit
   tree before committing; same out, since no module reads that file (an assumption,
   documented).
3. **`needs-sudo` is detected, not probed.** `sudo -n true` fails with NOPASSWD rules that
   only allow certain commands. apply runs `sudo -n git … pull` and treats "a password is
   required" as `needs-sudo`. `commands[]` holds what is left to run, and nothing past that
   point has happened. The commit and the build happen *before*, so the hand-run commands
   deploy exactly what nixin built, and a retry after `sudo -v` takes ~30 s. Only
   `--dry-run` probes, and only for its report.
4. **Extra refusals the brief did not list:** `host-not-local` (`apply --host 19BK` on
   mbp13 would switch mbp13 to 19BK's config), `authoritative-dirty` (a pull into a
   dirty tree would deploy edits that are in no commit; `verify-failed` would catch it
   only after the switch), `authoritative-missing`, `busy` (a per-user `flock`: two UIs
   clicking apply), `git-failed`, `verify-failed`, `switch-failed`. All are in
   CONTRACT's table, which the contract test keeps in step.
5. **Commits run the workspace's hooks and identity** (no `--no-verify`, no `-c user.*`).
   Untracked files are never committed, only reported.
6. **`--config` before the verb** (`cli.py`, beyond one import + one call). The brief's own
   commands (`nixin --config ~/nixos add cowsay`, `… set …`) were usage errors: `--config`
   was a per-verb flag. Same pattern as integrate's `--json`/`--host` fix: a global flag
   with its own dest fills `ns.config` only when the verb has one and it is unset.
   Discovery ignores it. Separate commit (4928a7e), tested.
7. **`nixos-switch` backend (untested on 19BK).** Read from
   `home/modules/bar/quickshell-bottom.nix`: the helper exits at once, runs `nixos-rebuild
   switch --flake /etc/nixos` (hostname, no `#host`) in a transient user unit, authenticates
   with sudo + Touch ID/askpass, and writes `status` (rc), `log`, `auth`. The backend is
   used only for a `switch` of this machine from `/etc/nixos` when the helper is on PATH.
   It refuses when the unit is already active (a second call would do nothing and nixin
   would wait on someone else's run), waits up to 4 h for `status`, and reports `auth` when
   the log is empty. **The pull still needs `sudo -n`, and 19BK has no NOPASSWD for git, so
   apply on 19BK will answer `needs-sudo` for the pull.** Options: a NOPASSWD rule for
   exactly `git -C /etc/nixos pull --ff-only /home/brucking/nixos *`, or let the helper
   pull too (`nixos-switch --pull WS BRANCH`), then add a pull step to the backend.
8. **`history.jsonl`** gets `mode` in addition to the brief's fields. It is appended after the
   verify step, so a failed switch leaves no line.

## Open items (not done)

1. **Plain `nixin status` (truth's default `--config /etc/nixos`) fails as `master` now.**
   nix's libgit2 refuses the root-owned repo ("repository path '/etc/nixos' is not owned by
   current user"). This is truth's path (apply never runs nix on `/etc/nixos`; root does).
   I tested the fix: only `~/.gitconfig` works (`[safe] directory = /etc/nixos`; libgit2
   ignores `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_COUNT`). For master on mbp13 that is
   `programs.git.extraConfig.safe.directory = "/etc/nixos";` in HM (or `git config --global
   --add safe.directory /etc/nixos`). Not changed: it is the user's global config.
   `--config ~/nixos` works meanwhile.
2. `hosts/mbp13/default.nix:75` still says "keep the last 5 generations' kernels". The
   writer edits values, not comments. A one-word follow-up for you.
3. `nixos-rebuild switch` re-evaluates the config as root, which is most of its ~30 s:
   activation itself took 1.6–1.7 s (`switch-to-configuration`'s own accounting in the
   log). Root has its own eval cache, and every commit is a new fingerprint. Switching the built out path
   directly (`nix-env -p /nix/var/nix/profiles/system --set OUT && OUT/bin/switch-to-configuration
   switch`) would save that, but it departs from "root only ever pulls and `nixos-rebuild
   switch`es" (and from the bootloader step's normal path). Left as designed.
4. A system running a specialisation never equals `out`, so `apply` re-switches to the
   default configuration (as plain `nixos-rebuild switch` would). Not special-cased.
5. plan's out path is not a GC root (`--no-link`, as the brief said). A GC between plan
   and apply just means a rebuild (the cache checks the path exists).
6. `sync` does not bring commits that only the authoritative tree has into the workspace
   (`authoritative-diverged`'s message gives the `git fetch /etc/nixos …` commands).
7. Integrate's judgement call 9 ("the writer's eval does not prove the host builds") now
   has an answer: `plan` is that proof. A UI should offer `plan` after writer verbs.

## How to test

```
nix develop -c pytest -q tests/test_apply.py     # 28 offline tests, real git on temp repos
nix develop -c pytest -q                          # everything (332 + 22 live skipped)
nix build                                         # the same in the sandbox
NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest -q tests/test_apply_live.py

B=$(nix build --no-link --print-out-paths .)/bin/nixin
$B --config ~/nixos plan                 # human: nix's build log on stderr, summary on stdout
$B --json --config ~/nixos apply --dry-run
$B --config ~/nixos add cowsay && $B --config ~/nixos plan && $B --config ~/nixos apply -m "test"
$B --config ~/nixos rm cowsay && $B --config ~/nixos apply     # back; generated message
$B --config ~/nixos apply                # nothing-to-apply
$B --config ~/nixos sync                 # fetch origin, report (add --remote NAME --push for a test remote)
$B --json --config ~/nixos status | jq .pending
```

Scratch outputs of this run: `/tmp/nixin-apply-live/` (every JSON document and human
output, `/boot` and `bootctl` before/after, `/boot/EFI/nixos` listings). Preflight notes:
`~/nixin-reports/apply-notes.md`.
