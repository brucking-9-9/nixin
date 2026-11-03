# HANDOFF: type-aware `set`, `explain`, the three judgement calls (branch `typed-set-explain`)

Written 2026-11-02 on mbp13 (headless, as `master`). The branch was cut from `origin/main`
@ 3dad300, the first run was cut off by a usage limit, and the second run rebased it onto
`origin/main` @ 824425c (the Packages page and `update`) before going on. Nothing is
pushed. Scope: writer, truth, discovery (read only: explain calls its source functions),
the new `explain` package, one import + one register call + the `VERBS` entry in `cli.py`,
CONTRACT/README/handoffs, and a test-only argument in `package.nix`/`flake.nix`.

## State

Everything in the brief is done, tested offline and run live. Nothing is half-done.

| commit | what |
|---|---|
| `d57db7e` | truth: the exclusion file is keyed by (nixpkgs rev, home-manager rev, **host**); the old per-(nixpkgs, hm) files are ignored, since the seed covers what they held |
| `f03ccf6` | `--host` default (cli.py + `configrepo.default_host`/`static_hosts`/`unknown_host`) |
| `29752a9` | truth: `options.nix` docs mode (structured type, default, example, `sub` = the type followed below an option), `where` shows them, `option_info()`; `options.nix` tested on the real module system |
| `a6e5df9` | writer: type check before `set`, `set OPTION` describes, `--force`, `--no-check`; `eval-failed` after a write is `ok: false` with `undo[]` |
| `2a53780` | `explain` (`src/nixin/explain/`) |
| `02d0cfe`, `3c342e1` | CONTRACT, README |
| `35b2982` | an option value that does not evaluate after a write: the message has nix's reason |

## The three judgement calls, as decided

1. **Writer: an eval failure after a write fails the verb.** When `checks.eval` has
   `code` `eval-failed`/`eval-timeout`, the document is `ok: false`, exit 1, `error.code`
   that code, and keeps every result field (`changes[]` lists what was written,
   `actions[]`, `checks`). `error.undo[]` holds the commands that take it back: the
   inverse nixin command where it restores exactly (`rm` what `add` added, `unset` what
   `set` newly set, `set OPTION OLD [--where FILE:LINE]` for a changed value, `add` what
   `rm` dropped from the generated module), else `git -C WORKSPACE checkout -- FILE` for a
   hand-written file an element/binding was removed from. The message says the same and
   that `--no-check` (new name of `--no-eval`, which still works) skips the eval. An eval
   that runs but shows `still_installed`/`missing` stays `ok: true` with a warning (the
   config evaluates; the change just did not have the full effect). For an option value
   that throws, a second eval without `tryEval` puts nix's own message in.
2. **Truth: exclusions per host.** `exclude-<nixpkgs12>-<hm12>-<host>.json`, the file
   records `host`; an option learned on one host is not skipped on another. Files
   without the host are ignored (not migrated: they mixed hosts, and `SEED_EXCLUSIONS`
   holds what they had). Test: `test_exclusions_are_kept_per_host_and_old_files_ignored`.
3. **`--host` default.** No `--host`: this machine's hostname if the config
   (`--config`, `$NIXIN_CONFIG`, `/etc/nixos`) has it (a text scan of `flake.nix`, no
   eval); else, if the config has exactly one host (one `nix eval` of the names), that
   host; else the hostname stays and the verb's own check fails with `unknown-host`, whose
   message lists the hosts and says "that is this machine's hostname, the default: name
   one with --host". Never picks one of several. Verbs without a config (discovery) get
   the hostname without anything being read. In `cli.document`, so the TUI/GUI (which
   always pass `--host`) and every layer get it; apply's and outdated's own
   `unknown-host` messages do not have the hint (their files; not touched).

## Type-aware `set`

- **Where the type comes from.** `truth/options.nix` has a `docs` mode, used by every
  prefix query (`where`, `explain`, `set`): `typeInfo` (`name`, `description`, enum
  `values`, `nested` `elemType`/`left`/`right`/`coercedType`/`finalType`/
  `freeformType`), `default`/`example` (`{text, kind}` for `literalExpression`/
  `literalMD`/`defaultText`, else `{value}`; absent when they throw), and `sub` for a
  path below an option: followed through submodule options (`getSubOptions`), nested
  option groups, a freeform type, `attrsOf` elements, `nullOr`/`unique`
  (`services.openssh.settings.PermitRootLogin` → `null or one of "yes", …`; `users.users.bob.shell`
  → its submodule option). `{opaque: NAME}` where it cannot go on (`either`, `listOf`),
  `{missing: k}` for a name a non-freeform submodule does not have. The full index does
  not use docs mode (it stays the size it was).
- **`truth.option_info(repo, host, path, hm=…)`**: `where`'s entry for one scope plus
  `mode` (`exact`, `sub`, `prefix` with `options[]`, `excluded`); None when the scope has
  nothing there. `where`'s entries gained `type_info`, `default`, `example`,
  `sub_description` (they describe NAME as asked; `type`/`description` stay the real
  option's). Human `where` shows the type of a sub path and `default`/`example`.
- **The check** (`writer/typecheck.py`) classifies the value's text with the writer's
  scanner (bool, int, float, string, null, path, list, attrset, function; `word` = a bare
  name that is no builtin, no usual module argument and not under `with`/`let`, which
  is what `set services.openssh.enable yes` is; anything else `unknown`), looks through
  `mkForce`/`mkDefault`/`mkOverride N`/`mkBefore`/`mkAfter`/`mkOrder N`/`mkIf C`/
  `mkMerge`, and judges only definite mismatches: bool, the int types with their range
  (from the description, `between A and B`, `unsignedInt`, `positiveInt`), float,
  number, string types (a word gets "quote it for Nix, '\"box\"'"; `nonEmptyStr`,
  `singleLineStr`), enum membership, `listOf`/`attrsOf` shape then each element,
  `nullOr`, `either`, `coercedTo`, `unique`, `attrs`. Not judged: package, path,
  submodule, function, anything, `strMatching`'s pattern, HM's own `hm.types.*`, any
  expression. A path with only options below it (`set services.openssh true`) must be an
  attrset.
- **Order in `set`:** syntax → option exists in the scope (`not-found`, "`nixin search
  --kind opt|hm` finds options; an option of the other tree goes with/without --home") →
  type (`type-mismatch` with `expected`, `type_info`, `default`, `example`, `option`,
  `value`). `--force` skips both evals; `--no-check` skips every eval. `checks.type` in
  the result: `{ok: true, option, expected, note: "fits" | "an expression the eval
  judges"}` or `{ok: null, note}` (not checked: `--force`, unknown type, failed query).
- **`set OPTION`** (no value): the scope's `option_info` plus `target` (where `set`
  would write: the hand-written definition in place, the generated module, or
  `candidates[]`), human block like `where`'s with a `writes` line.

## `explain`

`src/nixin/explain/{__init__,render}.py`. Truth side: `truth.option_infos()` (NixOS +
each HM user, or `--home`; HM users from the cached package eval, else one small eval,
cached as `hm-users`) / `truth.package_info()` (`where PACKAGE`'s answer). Upstream side:
discovery's `es.info_option`/`info_package`/`hm.info`, channel = `--channel`, else the one
the lock's nixpkgs ref implies, else unstable. Both in a 2-thread pool. Declarations in a
GitHub-locked input get a `url` at the locked rev and line; a package's `position` gets
one at the channel index's rev. `--offline` skips upstream. Rules: either side may be
missing (notes); nothing found → the config's error when upstream was not reached either,
else `not-found`; `--home` naming no user → `unknown-user`; an option name nobody knows
is retried as a package unless `--kind`/`--home`. `view` = `{title, subtitle, rows[{label,
text, url}], about, notes[]}` is what the human output renders (wrapped to the width;
paths and URLs are never split) and what a help pane can show.

## Live on mbp13 (2026-11-02)

`~/nixos` read only, HEAD `a198757`, one uncommitted file from another session
(`installer/history.jsonl`), no stash entries; the same before and after every run. The
writer runs against it used `--dry-run` or failed before writing; the real writes went to
a throwaway clone (`/tmp/nixin-typed/clone`), which ended clean. Outputs:
`/tmp/nixin-typed/live/*.out`. Dev-shell python on the worktree's `src`. Times are wall,
the box shared with other sessions.

| command | result |
|---|---|
| `explain services.openssh.enable` | type boolean, default false, value true, set in `hosts/mbp13/default.nix:249`, declared `sshd.nix:247` + GitHub link at 3ec6363, upstream description; 1–3 s |
| `explain kitty` | installed 0.49.2 [system], channel 0.49.2 on unstable, listed in `hosts/mbp13/default.nix:206`, binaries, homepage, license, source link |
| `--host 19BK explain kitty` | installed [home brucking], listed in home-manager's `modules/programs/kitty.nix`, `via programs.kitty.enable = true in home/modules/terminal/kitty.nix:5`; 7.4 s cold |
| `--host 19BK explain programs.git.enable` | "NixOS and Home Manager (brucking) option": value false [nixos], true [home brucking] set in `home/modules/git.nix:5`, both declarations linked; 3.2 s |
| `--host 19BK explain services.openssh.settings.PermitRootLogin` | the enum type through the freeform submodule, default `"prohibit-password"`, value `"no"` set in `ssh.nix:35` |
| `explain cowsay`, `explain nosuchthing-xyz`, `--offline`, `--config /nonexistent` | not installed + channel 3.8.4; `not-found`; config only + note; upstream only + note (and `--offline` without a config: `config-not-found`) |
| `set services.openssh.enable yes --dry-run` | `type-mismatch`: "yes is a bare word (Nix reads it as a variable), not boolean; default false"; 4.4 s cold, 1.2 s warm |
| `set … '"yes"'`, `networking.firewall.allowedTCPPorts '[ 22 "80" ]'`, `…PermitRootLogin '"maybe"'`, `services.opnessh.enable true` | "is a string, not boolean"; "element 2: \"80\" is a string, not 16 bit unsigned integer…; example [ 22 80 ]"; "not one of …"; `not-found` |
| `set services.openssh.enable` | type, default, declared, set in, about, `writes hosts/mbp13/default.nix:249 (in place)` |
| `--host 19BK set --home brucking programs.kitty.enable yes` / `…settings.font_size 12` / `'[ 12 ]'` / `services.openssh.enable true` / `--home nobody` | mismatch; fits (diff of `home/home.nix` + `installer/brucking-home.nix`); "is a list, not string or boolean or…"; `not-found` (an option of the other tree); `unknown-user` |
| clone: `set …X11Forwarding true` → `set services.openssh.enable '"yes"' --force` → its `undo[0]` → `unset …X11Forwarding` | eval ok; `eval-failed`, `ok: false`, change kept, message with nix's "is not of type `boolean'", undo `nixin --host mbp13 set services.openssh.enable true --where hosts/mbp13/default.nix:249 --config …`; that command restored the line; the clone's `git status` empty at the end |
| `explain … --offline --config /tmp/nixin-typed/solo` (one host `solo`) / `duo` (`a`, `b`) | host `solo` chosen (29 s: first hash of the `path:` nixpkgs input); `unknown-host` "'mbp13' is not in nixosConfigurations (a, b); that is this machine's hostname, the default: name one with --host" |
| `NIXIN_LIVE=1 pytest -k live tests/test_writer.py tests/test_truth.py` | 3 passed (the writer's add/rm round trip on a clone of `~/nixos`, 19BK) |

## Gates

- `nix develop -c pytest`: 535 passed / 25 skipped without the UI tests, the TUI/GUI tests
  146 passed (run separately); `ruff check`, `ruff format --check`, `nix fmt -- --check`
  clean. `nix build` at `3c342e1` (the code as it is; later commits are docs): 682
  passed / 25 skipped in the sandbox (the `options.nix` module-system test ran there),
  GUI self-test ok; the built `bin/nixin` answers `explain` and the `set … yes`
  type-mismatch against `~/nixos`.
- New tests: `test_writer.py` (73 type-check cases, mismatch/fits/force/no-check/
  describe/undo per verb, the nix reason), `test_truth.py` (where's docs fields,
  `option_info` modes, `literal_text`, `options.nix` on `lib.evalModules` +
  `lib.types` with storeless `nix-instantiate`), `test_integration.py` (the host default
  in four cases, the option flow's describe/mismatch/`checks.type`, explain for options,
  packages, HM, without a config or network, the human block at 60 columns, the sweep).
- `package.nix` takes an optional `nixpkgsLib` (the flake passes `"${nixpkgs}/lib"`, the
  dev shell exports it as `NIXIN_TEST_NIXPKGS_LIB`); without it that one test skips (the
  overlay passes none).

## Gaps and next steps

1. The TUI and GUI do not call `explain` yet (the help pane: render `view` as is;
   `rows[].url` are links). Their Set dialog shows `type-mismatch` as a generic error
   notice; it could show `expected`/`default`/`example` and offer "write anyway"
   (`--force`). They must also treat a writer `eval-failed` as "written but broken":
   `changes[]` is filled and the change is on disk (Pending shows it), `error.undo[]` is
   the way back. Not changed here (other sessions own the UIs).
2. `explain` on a group of options (`explain services.openssh`) is `not-found`; it could
   list the options under it (`option_info` already returns them, `mode: "prefix"`).
3. The type check does not judge `strMatching` patterns (POSIX ERE vs Python `re`),
   submodule shapes (the brief said skip), or HM's `hm.types.*`. A `set` value's
   `let`/`with` scope makes every bare name an expression.
4. `set` costs one prefix eval before the write (≈1 s on mbp13, up to 4 s cold) plus
   `nix flake metadata`; `set OPTION` then `set OPTION VALUE` on an unchanged tree hits
   the cache. The pre-write eval and the post-write eval could be one if nixin wrote to
   a temp copy first; not worth it at these numbers.
5. apply's and outdated's `unknown-host` messages lack the "pass --host" hint
   (`configrepo.unknown_host()` gives it; a one-line change in their files).
6. HM option descriptions are Markdown as published; `explain` shows them raw.

## How to test

```
nix develop -c pytest -q                                   # offline (sets NIXIN_TEST_NIXPKGS_LIB)
nix develop -c pytest -q tests/test_writer.py -k "type or set or undo"
nix develop -c pytest -q tests/test_integration.py -k "explain or host_default or option_flow"
nix build                                                  # the same in the sandbox
NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos nix develop -c pytest -q -k live tests/test_writer.py tests/test_truth.py

# by hand (dev shell python, the tree's src); ~/nixos only read, writes on a clone:
X() { nix develop -c sh -c 'PYTHONPATH=src:$PYTHONPATH exec python3 -m nixin.cli "$@"' nixin "$@"; }
X explain services.openssh.enable --config ~/nixos
X --host 19BK explain kitty --config ~/nixos
X set services.openssh.enable yes --dry-run --config ~/nixos     # type-mismatch, nothing written
X --host 19BK set --home brucking programs.kitty.enable --config ~/nixos
git clone -q ~/nixos /tmp/c && X --json set services.openssh.enable '"yes"' --force --config /tmp/c   # eval-failed + undo[]
```

Scratch (not in the repo, not deleted: `rm -rf` is denied to these runs):
`/tmp/nixin-typed/` (`q.sh` = options.nix docs query against `~/nixos`, `nx.sh` = run the
tree's CLI, `live/*.out`, `clone/` (clean), `solo/`, `duo/` (one- and two-host flakes on
the store's nixpkgs), `openssh-19BK.json`/`prl-19BK.json`/`kitty-19BK.json` (the recorded
answers the fixtures came from), `build.log`), and `/tmp/nixin-typed-commit.sh`.
