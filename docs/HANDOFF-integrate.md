# HANDOFF: integration pass (branch `integrate`)

Written 2026-11-01 on mbp13 (headless, as `master`). The branch was cut from `main` @
808a2f2 (discovery + truth + writer merged with only hook conflicts resolved). Nothing is
pushed. Backend only: no TUI/GUI, no apply logic, no sudo.

## State

The merged CLI was run as one tool on this host. The full install flow, the option flow
and the Home Manager flow work end to end, with `--json` and without. Ten small fixes
are in (below). The contract for UI authors is `docs/CONTRACT.md`, and README now
points there. `tests/test_integration.py` runs the flows offline through `main()` and
checks the envelope for every verb and error path. What is left is the judgement calls
and open questions at the end. Nothing is half-done.

Gates at HEAD:
- `nix build`: 297 passed, 19 skipped (live) in the sandbox.
- `nix develop -c pytest`: the same 297 / 19.
- `nix develop -c ruff check` and `ruff format --check` (src, tests, scripts): clean.
- `nix fmt -- --check` without arguments: clean. It now works (see the first fix).

## Fixed (one commit each)

| commit | what |
|---|---|
| `6c6afe5` | **flake formatter.** It was bare `nixfmt`, which reads stdin when `nix fmt` passes no paths ("unexpected end of input"). Now it is a `writeShellApplication` wrapper: with no path argument it formats `git ls-files ':(top)*.nix'`; with paths (or `-`) it passes everything through. `nix fmt` formats, `nix fmt -- --check` checks, and both also work from a subdirectory. I picked the wrapper over README instructions because `nix fmt -- --check` was the documented command in three handoffs. |
| `2df9084` | **`--json`/`--host` after the verb** worked for writer verbs only (the writer added them to its subparsers). `where X --host 19BK --json` was an argparse error on stderr with no JSON. cli now adds both to every subparser; the writer's copies are gone. |
| `4d26f41` | **usage errors under `--json`.** argparse errors printed usage text to stderr with nothing on stdout (bare `nixin --json` even printed help to stdout). The verbs' own `usage` errors exited 1, while the contract says 2. Now: under `--json` every usage error is one `{"ok": false, "error": {"code": "usage"}}` document (`verb` null when none was recognised), exit 2. Without `--json`, the argparse text goes to stderr as before. |
| `072d579` | **catch-all.** Only discovery guarded its verbs (`internal`). An unexpected exception in truth or the writer was a traceback with empty stdout. `cli.main` now turns any exception from a verb into `internal` (exception type + `file:line`). |
| `41419b7` | **nix stdin.** `configrepo.run_nix` (truth and writer) left nix's stdin attached, so a flake's `nixConfig` question could block a UI. Now `stdin=DEVNULL`, as in discovery's `local.py`. |
| `a12ea71` | **cache root.** truth honoured a relative `$XDG_CACHE_HOME`; discovery ignores it (XDG spec). The two layers could cache under different roots. truth now uses `cache.default_root()`. |
| `1d88d6b` | **truth cache writes** used one shared `<file>.tmp`. Two concurrent UI calls could tear it. Now a per-process temp file, like discovery's cache and the exclusion store. |
| `3c7da7c` | `status foo` and `where a b` silently ignored the extra arguments. Now `usage`. |
| `8ed72f9`, `c7eb812`, `00070a6`, `b28cc9b` | `docs/CONTRACT.md`; README cut from 275 to 99 lines (overview, CLI summary, develop, status). |
| `d3501a9`, `8d7b476` | `tests/test_integration.py` (49 tests). |

## End to end on mbp13 (merged binary)

Truth and discovery ran against `~/nixos` read-only. The writer ran against my clone
`~/nixin-test-config-2` (`git clone -q ~/nixos`, HEAD ebd8190). The clone was clean
after every add→rm and set→unset. Scratch outputs: `/tmp/nixin-integrate/` (`e2e/*.out|err`
for each command, `cold/` for the timing run, `run.py` the envelope-checking runner,
`cold.sh` the timing script).

`~/nixos` state (another session's work, not touched): HEAD ebd8190, with
`hosts/mbp13/{companion,default,kiosk}.nix` and `hosts/mbp13/hypr/default.nix` modified
and an untracked `hosts/mbp13/broadcom-sta/` that the tracked edits import. No stash
entries. Because of the untracked directory, **mbp13 does not evaluate from `~/nixos`
right now**: `status` gives a clean `eval-failed` ("Path 'hosts/mbp13/broadcom-sta' … is
not tracked by Git"), exit 1, empty stderr. So I ran truth on `~/nixos` for `--host 19BK`
and for mbp13 on the clone. There, `status` is `eval-failed` with "Scripted initrd has
been removed" (the known failure at HEAD), while `where`, `add`, `rm`, `set` and `unset`
all work. They never evaluate `system.build.toplevel`.

| flow | result |
|---|---|
| `search cowsay` → `info cowsay --nixpkgs github:nixos/nixpkgs/3ec6363…` | cowsay 3.8.4 from search.nixos.org; info from `local-nix` at the locked rev, with search.nixos.org and HM listed in `sources` as `bad-channel` (bare rev), `ok: true` |
| `status` | 19BK on `~/nixos`: evaluates, 203 system / 191 home packages; mbp13 as above |
| `where cowsay` → `add cowsay --dry-run` → `add cowsay` → `where cowsay` → `rm cowsay` | not installed (empty lists) → diff of 3 files, nothing written → `installer/mbp13.nix` + `installer/state.json` created, import added to `hosts/mbp13/default.nix`, eval: `cowsay-3.8.4` in systemPackages → `declared` in `installer/mbp13.nix:10` → everything gone, `git status` clean |
| `search --kind opt …X11Forwarding`, `info`, `where`, `set … true`, `where`, `set` again, `unset`, `unset` again | `where` mode `sub` under `services.openssh.settings`; set → eval `true`, definition at `installer/mbp13.nix:9`; second set `unchanged`; unset → eval back to `false`, tree clean; second unset `not-found` |
| HM: `--host 19BK add --home brucking cowsay`, `where`, `rm`; `set/unset --home brucking programs.bat.enable` | `installer/brucking-home.nix`, `home/home.nix` import list expanded and restored exactly on rm; `where` shows `scope: home, user: brucking`; option `exact` in the home scope. `add --home brucking` without `--host` → `unknown-user` "… it is one on 19BK (use --host)" |
| without `--json` | tables, info blocks, diffs + summary lines on stdout, empty stderr; failures one `nixin VERB: …` line on stderr |
| `--json` error sweep (34 commands) | all verb-level errors were one document with matching exit code and empty stderr. The argparse cases were not, and are fixed above. Re-run with the new binary: all pass |

Costs measured with an empty nixin cache are in CONTRACT.md "Costs". The headline numbers
(load 14–20 from other sessions, so upper bounds): `search --nixpkgs` on a rev nix had not
searched took 204 s and 4.6 GiB (46 s quiet in discovery's handoff); cold `status` 77 s /
1.4 GiB; `status --full` another 92 s / 1.7 GiB; writer add/rm 3–10 s; everything warm
under a second.

## Judgement calls (found, not decided)

These are the places where the layers disagree. CONTRACT.md "Shared vocabulary" documents
today's behaviour, so a UI can map it. Each one is a naming or semantics decision for
the UI author and the layer owners.

1. **Package attribute field.** discovery: `name` (pkg row), `package` (program row,
   where `name` is the binary), `attr` (version row). truth: no attribute at all,
   `installed[].name` is the *derivation* name (`cowsay-3.8.4`). writer: `packages[]`,
   `actions[].name`. Options: a common `attr` field wherever an attribute is known; rename
   truth's `name` to `drv_name`.
2. **Scope vocabulary.** truth's package answers (`where PACKAGE`'s `scope`,
   `status.packages.system`, `status.programs.system`) say `system`; truth's option
   answers and the writer say `nixos`. One word, probably `nixos`.
3. **`--kind` vocabulary.** discovery `pkg`/`opt`(/`hm`/…), truth `package`/`option`. Same
   field name `kind` in the result.
4. **nix error codes.** discovery `nix-failed`/`nix-timeout`; truth/writer
   `eval-failed`/`eval-timeout`, plus truth's `not-a-flake` (it was undocumented; it is in
   CONTRACT now). Keep two families (upstream nixpkgs vs your config), or merge?
5. **"Nothing there".** `where PACKAGE` that is not installed is `ok: true` with empty
   lists; `where OPTION` unknown is `not-found`; `rm`/`unset` of something not declared
   is `not-found` (exit 1), while `add` of something present is `ok` with
   `already-added`. Should `rm`/`unset` be idempotent (`ok`, action `unchanged`)?
6. **Shapes of the same field.** `declarations[]` are strings in discovery and objects in
   truth. Values are Nix text in discovery (`default`, `example`) and in the writer
   (`value`), JSON in truth (`value`) and in the writer's `checks.eval.value`.
7. **Cache flags and reporting.** discovery has `--refresh` (refetch and store) and
   `--no-cache`; truth has only `--refresh`, which skips reads but still writes; the
   writer has neither. Reporting: discovery `cached: bool`, truth `cache: {dir, enabled,
   hits, misses}`. A UI that wants "force fresh" must know per verb.
8. **Timeouts.** truth evals have no time limit (a cold toplevel took 68 s here and has
   taken 263 s); discovery's `nix search` 900 s; writer evals 900 s. A common
   `--timeout`, or none?
9. **What the writer's eval proves.** On the mbp13 clone, `status` fails (`eval-failed`,
   toplevel assertion) while `add`/`rm` report `checks.eval.ok: true`, because the writer
   only evaluates the list/option it changed. A UI must not show the writer's check as
   "the host builds". Is that `plan`'s job, or should the writer evaluate toplevel (about a
   minute cold)?
10. `host` is echoed in discovery documents although discovery ignores it. It is harmless;
    keep it for a uniform envelope?
11. `set --where` is documented as repeatable, but `set` acts on the first match only.
12. Discovery's human `info` footer repeats the same `bad-channel` sentence once per
    failed source (`--nixpkgs` with a bare rev). Cosmetic.

## Open questions (from the brief, not decided)

1. **Writer `ok: true` when the post-write eval fails.** Today the edit stands and is
   reported, `checks.eval.ok: false`, the failure is repeated in `warnings`, exit 0. The
   alternative is `ok: false` (exit 1) with the changes attached, or a distinct exit code.
   The cost of today's choice: a UI that only checks `ok` reports success for a broken
   config. CONTRACT.md tells UIs to read `checks.eval.ok`.
2. **truth's persisted exclusions hide an option on hosts where it would evaluate.** The
   store is keyed per (nixpkgs rev, home-manager rev), not per host. A seeded entry like
   `hardware.nvidia.open` is skipped in every host's `--full` index, including one with
   nvidia enabled, so `status` counts and any index-based lookup miss it there. `where`
   is unaffected (prefix queries ignore the store). Options: key per host as well; or
   retry the excluded options that the config defines.
3. **`--host` default when `--config` points elsewhere.** The default is always this
   machine's hostname. On mbp13, `--config` pointing at a config without an `mbp13`
   gives `unknown-host`. A UI on a laptop editing a clone for another machine has to pass
   `--host`. Alternatives: if the hostname is not in the config, default to the only
   host (or fail and list the hosts, as today); or the config could carry a default host.
   A related subtlety: `status.running` always describes *this* machine, even for
   `--host 19BK`, and `config.host_is_local` says whether they match.

## How to test

```
nix develop -c pytest -q                         # 297 offline tests (fixtures, fakes; no nix/network)
nix develop -c pytest -q tests/test_integration.py   # just the integration flows + envelope sweep
nix build                                        # the same in the sandbox
nix develop -c sh -c 'ruff check && ruff format --check'
nix fmt -- --check                               # no path needed any more
NIXIN_LIVE=1 nix develop -c pytest -q tests/test_live.py   # discovery live smoke

# by hand, from the tree (keep the dev shell's PYTHONPATH, it carries requests):
nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH python3 -m nixin.cli --json search cowsay'
# or the built binary:
B=$(nix build --no-link --print-out-paths .)/bin/nixin
$B --json --host 19BK status --config ~/nixos          # read only
git clone -q ~/nixos ~/nixin-test-config-2              # writer: a clone, never ~/nixos
$B --json add cowsay --config ~/nixin-test-config-2 && $B --json rm cowsay --config ~/nixin-test-config-2
git -C ~/nixin-test-config-2 status --short             # empty again
python3 -I /tmp/nixin-integrate/run.py NAME -- $B --json …   # (scratch) times + checks the envelope
```

The truth handoff's `PYTHONPATH=src python -m nixin.cli …` recipe replaces the dev shell's
`PYTHONPATH`, so `import requests` fails and discovery cannot load. Use
`src:$PYTHONPATH` as above.

`tests/test_integration.py` in short: `World` is one fake nix shared by truth
(`ConfigRepo.locate` wrapped) and the writer (`writer.NIX`). It answers every query from
the fixture tree on disk (`installer/state.json`), so a write shows up in the next
`where`. `envelope()` holds the invariants. `test_contract_lists_every_code` and
`test_no_error_code_escapes_the_contract` keep CONTRACT.md's error table, the test's
`ERROR_CODES` and the codes the source raises in step. A new error code fails the build
until it is documented.
