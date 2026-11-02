# nixin CLI contract

The one reference for UI authors (TUI, GUI, agents) who drive `nixin --json`. It covers
the envelope, every verb's arguments and result fields, every error code, exit codes,
what each verb costs (network, nix, disk writes, time) and where the caches are.
README.md is the overview and points here. When code and this file disagree, it is a bug
in one of them. `tests/test_integration.py` checks the envelope rules and the error-code
list against this file.

Status: all four layers are implemented: discovery, truth, writer and apply (`plan`,
`apply`, `sync`).

## Invocation

```
nixin [--json] [--host H] VERB [ARGS...]
nixin --version | --help | VERB --help
```

| verb | layer | does |
|---|---|---|
| `search`, `info`, `versions` | discovery | what exists upstream, in which versions |
| `status`, `where` | truth | what this config gives a host (it evaluates the config flake) |
| `add`, `rm`, `set`, `unset` | writer | edit a user-owned workspace clone of the config |
| `plan`, `apply`, `sync` | apply | build + closure diff; commit, pull into the authoritative tree and switch (root); git sync with the hub |

- `--json` and `--host H` are global. They go before the verb, or after it, in any order
  with the verb's own flags (`nixin where kitty --host 19BK --json`).
- `--host` names `nixosConfigurations.<H>`. The default is this machine's hostname
  (`socket.gethostname()`), even when `--config` points at a config that has no such
  host (see open question 3 in `docs/HANDOFF-integrate.md`). Discovery ignores it but
  still echoes it in the envelope.
- `--config DIR` (truth, writer and apply verbs; before or after the verb, the verb's own
  wins; discovery ignores it) is the config flake: `--config`, else
  `$NIXIN_CONFIG`, else `/etc/nixos`. Truth only reads it. The writer and apply refuse
  `/etc/nixos` and any tree you cannot write (`not-a-workspace`). They work on a clone
  (`git clone /etc/nixos ~/nixos`, then `--config ~/nixos` or `NIXIN_CONFIG=~/nixos`).
- `--authoritative DIR` (apply verbs) is the root-owned clone the system is switched
  from: `--authoritative`, else `$NIXIN_AUTHORITATIVE`, else `/etc/nixos`. nixin only
  reads it; root `git pull --ff-only`s the workspace into it during `apply`.
- Nothing prompts, ever. nix and git run with stdin closed, sudo always as `sudo -n`. A
  step that needs root and cannot get it fails with `needs-sudo` and the commands to run
  (only `apply` needs root).
- `--help` and `--version` print text to stdout and exit 0, with or without `--json`.

## Envelope

With `--json`, every invocation prints exactly one JSON object followed by a newline on
stdout, and nothing else. stderr stays empty. That also holds for usage errors and for
unexpected exceptions. The output is indented and key-sorted; do not depend on that.

```json
{"ok": true,  "verb": "search", "host": "mbp13", ...verb fields}
{"ok": false, "verb": "add",    "host": "mbp13", "error": {"code": "unknown-package", "message": "..."}}
```

| key | always | meaning |
|---|---|---|
| `ok` | yes | `true`: the verb did what was asked (see the writer's `checks` for the one caveat). `false`: `error` says why |
| `verb` | yes | the verb, or `null` for a usage error where no verb was recognised (`nixin --json`, `nixin --json frobnicate`) |
| `host` | yes | the `--host` value (default: this machine's hostname) |
| `error` | when `ok` is false | `code` (a slug from [Error codes](#error-codes)), `message` (one human sentence, may contain newlines from nix), and for some writer codes extra fields: `hints[]`, `candidates[]`, `file`, `line` |

A UI should branch on `ok` and `error.code`, never on `message`.

Without `--json`, output is human text: results on stdout. A failed verb prints one
`nixin VERB: message` line to stderr and nothing to stdout. A usage error prints
argparse's usage and `nixin VERB: error: …` to stderr. The text format is not a contract.
`NIXIN_WIDTH` overrides the terminal width for discovery's tables.

## Exit codes

| code | when |
|---|---|
| 0 | `ok: true` |
| 1 | `ok: false`, any code except `usage` |
| 2 | `ok: false` with code `usage`: argparse rejected the command line (unknown verb or flag, missing argument, bad `--kind`, `--limit abc`), no verb was given, or the verb's own argument check failed (`search ""`, `where` without NAME, `set` without a value, `--where` not `FILE:LINE`) |

## Shared vocabulary, and where the layers still differ

The layers were written separately. These differences are real today. A UI has to map
them; the decision on unifying them is open (`docs/HANDOFF-integrate.md`).

| concept | discovery | truth | writer |
|---|---|---|---|
| a package's attribute path | `name` on `pkg` rows; `package` on `program` rows (`name` is the binary there); `attr` on `version` rows | not recoverable from derivations: `installed[].name` is the derivation name (`cowsay-3.8.4`), with `pname`/`version` | `packages[]` and `actions[].name` (`cowsay`, `python3Packages.requests`) |
| what NAME is | `--kind`/`kind`: `pkg`, `opt`, `hm`, `program`, `fn`, `version`; `all` | `--kind`/`kind`: `package`, `option` | implied by the verb |
| NixOS vs Home Manager | `kind` `opt` vs `hm` | option answers: `scope` = `nixos` \| `home`, plus `user`. Package answers: `scope` = `system` \| `home`, plus `user`; `status` uses `packages.system`, `programs.system` | `scope` = `nixos` \| `home`, plus `user`; select with `--home USER` |
| declarations | `declarations[]` = strings (`nixos/modules/…nix`), plus `declaration_urls[]` | `declarations[]` = objects `{source, file, line, column, raw}` | — |
| values | `default`, `example`: Nix source text (strings) | `value`: JSON, with placeholders `<derivation x>`, `<function>`, `<… N more>` | `value`: the Nix text you passed; `checks.eval.value`: JSON |
| cache state | `cached` (bool), per source in `sources` | `cache` = `{dir, enabled, hits[], misses[]}` | none, always evaluates |
| cache flags | `--refresh` (refetch and store), `--no-cache` (no reads, no writes) | `--refresh` (no reads; results are still written) | none |
| nix failed / timed out | `nix-failed`, `nix-timeout` | `eval-failed`, `not-a-flake` (truth evals have no time limit) | `eval-failed`, `eval-timeout` (900 s) |
| nothing there | `not-found` error | `where OPTION`: `not-found` error. `where PACKAGE`: `ok: true` with empty `installed`/`declared`/`programs` | `rm`/`unset`: `not-found` error with `hints[]` |
| `--config` unset | — | `$NIXIN_CONFIG`, else reads `/etc/nixos` | `$NIXIN_CONFIG`, else `/etc/nixos`, which is refused (`not-a-workspace`) |

The apply layer follows the writer for `--config` (a workspace; `not-a-workspace`
otherwise), says `nixos` nowhere (it builds the whole host), reports nix failures as
`eval-failed` / `eval-timeout` / `build-failed`, and keeps its own cache (plan's out
paths, no flag).

## Verbs

Each verb lists its arguments, its result fields (besides `ok`/`verb`/`host`), its error
codes, and its costs. "Network" means nixin's own HTTPS calls. Any verb that runs nix may
also make nix fetch flake inputs that are not in the store yet. Timings are in
[Costs](#costs-measured).

### `search`

```
nixin search QUERY [--kind pkg|opt|hm|program|fn|all] [--channel C] [--limit N]
                   [--nixpkgs FLAKEREF] [--refresh | --no-cache]
```

| argument | meaning |
|---|---|
| `QUERY` | free text; a leading `-` needs `--` before it (`search -- -grep`) |
| `--kind` | `pkg` (default), `opt` (NixOS options), `hm` (Home Manager options), `program` (which package ships a binary: `rg` → ripgrep), `fn` (lib/builtins, from noogle; not part of `all`), `all` = pkg + opt + hm + program |
| `--channel` | `unstable` (default), `stable` (newest release), `NN.MM`, a branch name (`nixos-25.11`) or a branch flakeref |
| `--limit` | rows per kind, default 20, at least 1 |
| `--nixpkgs` | packages come from `nix search --json` on that exact nixpkgs (the `local-nix` source), typically the host's locked rev. A branch-named ref also picks the channel for options; a bare rev does not (pass `--channel`, else options and HM fail with `bad-channel` while packages still answer) |

Result: `query`, `kind`, `limit`, `results[]`, `count`, `source`, `channel`, `rev`,
`cached`, `sources`, and `notes[]` when there is something to say (e.g. "nix search on
this rev took 110s (first run builds nix's eval cache)").

- `source`, `channel`, `rev` and `cached` describe the single source that answered. When
  several answered (`--kind all`), `source` is `"all"`, `channel` is the concrete channel
  the channel-based sources searched (`stable` → `26.05`) and `rev` is null.
- `sources` maps each source name (`search.nixos.org`, `home-manager-options`,
  `local-nix`, `noogle.dev`, `nixhub.io`) to `{kinds[], channel, rev, cached, count,
  error}`. `error` is null or that source's first `{code, message}`. `ok` stays true as
  long as one source answered, so a source can carry an `error` and a non-zero `count`.
  Extras: `updated` (home-manager-options: when the index was generated), `system` and
  `total` (local-nix: the system searched, the hit count before `--limit`).
- `channel` is null for sources without channels (noogle, nixhub). For `local-nix` it is
  the flakeref as given.

Every row carries `kind`, `source`, `channel`, `rev`, plus:

| `kind` | fields |
|---|---|
| `pkg` | `name` (attribute path), `pname`, `version`, `description`, `homepage`, `license[]` (SPDX ids), `unfree` (null from search.nixos.org, bool from `--nixpkgs` info), `programs[]`, `main_program`, `platforms[]`, `outputs[]`, `position` (`file:line` in nixpkgs). `local-nix` search rows fill only `name`, `pname`, `version` and `description` (the rest are null/empty) |
| `opt` | `name`, `type`, `default`, `example` (Nix text or null), `description` (plain text), `declarations[]`, `declaration_urls[]` |
| `hm` | `name`, `type`, `default`, `example`, `description` (Markdown as published), `declarations[]` (`modules/…nix`), `declaration_urls[]`, `read_only` |
| `program` | `name` (the binary), `package` (attribute), `pname`, `version`, `description`, `exact` (false for substring matches) |
| `fn` | `name`, `aliases[]`, `signature`, `description` (first paragraph), `position`, `primop` |

Errors: `usage`, `bad-channel`, `network`, `http-<status>`, `nix-missing`, `nix-failed`,
`nix-timeout`, `internal`. No hits is `ok: true` with `count: 0`, not an error.

Costs: network (search.nixos.org, the HM option JSON, noogle) except `--kind pkg
--nixpkgs`, which runs nix only (`nix flake metadata`, `nix search`). Writes cache files
only.

### `info`

```
nixin info NAME [--kind pkg|opt|hm|fn|all] [--channel C] [--nixpkgs FLAKEREF] [--refresh | --no-cache]
```

Exact-name lookup. `--kind` defaults to `all` = pkg + opt + hm (no `program`; `fn` only
when asked). `--channel` and `--nixpkgs` work as for `search`. With `--nixpkgs`, the
package comes from `nix eval` on that rev (about 0.5 s once the rev is in the store) and
gains `broken`, and `unfree` becomes a bool.

Result: `name`, `kind`, `results[]` (usually one; a name can be both a package and an
option), `count`, `source`, `channel`, `rev`, `cached`, `sources`. Package rows add
`matched_by` (`attr` or `pname`). With `pname`, rows also get `alternatives[]` (the other
attrs with that pname); an `attr` match leaves that field out.

Errors: `not-found` (none of the asked kinds has NAME; the message also lists the kinds
whose sources failed), and the `search` codes.

Costs: as `search`.

### `versions`

```
nixin versions PKG [--limit N] [--refresh | --no-cache]
```

Which nixpkgs commit shipped which version, from NixHub.

Result: `package`, `limit`, `results[]`, `count`, `total`, `latest`, and the latest
release's `summary`, `homepage`, `license`, `programs`; plus `source` (`nixhub.io`),
`channel` (null), `rev`, `cached`, `sources`. Rows (`kind: "version"`): `name`,
`version`, `date` (`YYYY-MM-DD`), `rev` (nixpkgs commit), `attr`, `platforms[]`,
`programs[]`.

Errors: `not-found`, `usage`, `network`, `http-<status>`, `internal`.

Costs: network (search.devbox.sh). Writes cache files only.

### `status`

```
nixin status [--config DIR] [--full] [--quick] [--refresh]
```

Evaluates the host from the config flake. The default (also spelled `--quick`)
evaluates `system.build.toplevel.drvPath` (runs the assertions) and the package lists.
`--full` also builds the option index of the NixOS tree and of each HM user, one heavy
eval each. Later quick runs show those counts from the cache for as long as the tree
stays unchanged. Some nixpkgs/HM options throw an error nix cannot catch when their
service is off. The index skips them (`excluded`). Which ones depends on the module set,
so the list is kept per (nixpkgs rev, home-manager rev) in the exclusion file (see
[Caches](#caches)) and a new one costs one extra eval once. `where` does not use that
list.

Result:

| field | meaning |
|---|---|
| `config` | `path`, `head` (git HEAD), `dirty`, `source` (the `/nix/store/…-source` nix evaluates), `source_hash`, `fingerprint`, `last_modified`, `hosts[]`, `host_is_local` (`--host` = this machine's hostname) |
| `lock` | the root flake's nixpkgs node: `input`, `node` (e.g. `nixpkgs_2`), `type`, `owner`, `repo`, `rev`, `ref`, `date`, `last_modified`, `flakeref`, `nar_hash`; null without `flake.lock` |
| `running` | `/run/current-system` of *this machine*: `system`, `version`, `nixpkgs_rev`, `hostname`, `matches_lock` |
| `eval` | `ok` (always true here; a host that does not evaluate is an `eval-failed` error instead), `toplevel` (drvPath), `seconds` |
| `hm_users[]` | Home Manager users of the host |
| `packages` | counts: `system`, `home.<user>` |
| `programs` | names with `programs.<x>.enable = true`: `system[]`, `home.<user>[]` |
| `options` | per scope (`nixos`, `home.<user>`): `total`, `defined` (set beyond the option's own default), `in_config` (defined in a file of the config tree), `excluded`, `definitions_error`, `seconds`. Without `--full`: the cached counts of an earlier `--full`, else `{}` |
| `cache` | `dir`, `enabled` (false with `--refresh`), `hits[]`, `misses[]` |
| `timings` | seconds per eval (`toplevel`, `packages`) and `total` |
| `pending` | from the apply layer, no nix: `workspace` (the `--config` path, null when that is the authoritative tree itself), `authoritative` = `{path, head}`, `commits[]` (`{commit, subject}`: workspace commits the authoritative tree lacks; null when unknown), `dirty[]`, `untracked[]`, `built` (the authoritative HEAD's out path if plan's cache has it, else null), `running_matches` (bool: `/run/current-system` is that build; null when unknown or `--host` is another machine). On a git problem: `{error: {code, message}}` instead; `status` itself still succeeds |

Errors: `usage` (any positional argument), `config-not-found`, `not-a-flake`,
`unknown-host`, `eval-failed` (the last
`error:` block of nix's stderr, traces stripped; e.g. failed assertions, or a file that
git does not track yet), `nix-missing`, `internal`.

Costs: runs nix (no time limit), reads the config, writes cache files. It is slow when
cold (about a minute; with `--full` add one to three minutes) and under a second when
warm.

### `where`

```
nixin where NAME [--kind package|option] [--config DIR] [--refresh]
```

A NAME with a dot (or `--kind option`) is an option. It is answered for every scope it
exists in. Without a dot it is a package. No `--home`: every HM user of the host is
covered.

Result, always: `name`, `kind` (`package` | `option`), `cache` (as in `status`).

For an option, `scopes[]`, each with `scope` (`nixos` | `home`), `user`, `mode`,
`options[]`, `under_prefix` (how many options exist under NAME), `excluded`. `mode` is
`exact` (NAME is an option), `sub` (a path inside an `attrsOf`/submodule option, e.g.
`services.openssh.settings.X11Forwarding` inside `services.openssh.settings`; the value
is dug out of the parent), or `prefix` (NAME is a non-leaf path: the options under it
that something sets beyond their default). Each option has `name` (as asked), `option`
(the real option), `type`, `description`, `isDefined`, `highestPrio`, `value` (JSON,
size-capped), `value_found` (sub mode), `value_error` (when the value does not
evaluate), `declarations[]` (`source` = `config` | `nixpkgs` | `home-manager` | another
input | `inline` | `other`; `file` relative to that source, a directory module mapped to
its `default.nix`; `line`/`column`; `raw` store path) and `definitions[]`. Each
definition has the same location fields, `value` (what that file contributes), and for
config files `line`, `line_heuristic: true` and `text` (from a text scan). `inline`
definitions (attrsets written inside the `nixosSystem` call) get `candidates[]` instead.

For a package: `installed[]` (`scope` `system`|`home`, `user`, `name` (derivation name),
`pname`, `version`), `programs[]` (the `programs.NAME.enable` options that are true,
shaped like option entries plus `scope`/`user`), `declared[]` (files whose
`environment.systemPackages`/`home.packages` definition lists it: location fields,
`option`, `items[]` (`<derivation cowsay-3.8.4>`), `scope`, `user`, `lines[]`
(`{file, line, score, text}` candidates)), `mentions[]` (ranked grep of tracked `*.nix`
files, lowest confidence; files under another host's directory are left out). A package
that is not installed is `ok: true` with empty lists.

Errors: `usage` (no NAME, or more than one), `not-found` (an option NAME that no scope has),
`config-not-found`, `not-a-flake`, `unknown-host`, `eval-failed`, `nix-missing`,
`internal`.

Costs: runs nix: one package-list eval per tree change, then one small eval per prefix
(an option) or per list option plus each matching `programs.x.enable` (a package). Reads
the config (and greps it), writes cache files. A few seconds cold, under a second warm.

### `add`

```
nixin add PKG... [--home USER] [--config DIR] [--dry-run] [--no-eval]
```

Adds packages for the host (or for HM user USER on the host) to the generated module
`installer/<host>.nix` (or `installer/<user>-home.nix`), rendered from
`installer/state.json` and run through nixfmt. On first use, the module gets imported
from the host's entry module (the first file in its flake.nix `modules`) or the user's
HM entry module, with a `# generated by nixin` comment. PKG is an attribute path;
a `pkgs.` prefix is dropped.

Which modules belong to the host is read from the text (flake.nix `modules`, then
`imports` path literals), so this also works for a host that does not evaluate. Modules
imported through an expression (`map`, `readDir`, flake-parts helpers) are invisible to
it.

Before writing, one small eval checks that every new PKG exists in the host's `pkgs`
(skipped with `--no-eval`). A PKG that a hand-written module already lists is reported
as `already-declared` and left alone.

### `rm`

```
nixin rm PKG... [--home USER] [--where FILE:LINE]... [--config DIR] [--dry-run] [--no-eval]
```

Removes packages wherever the host's (or user's) modules declare them. A generated entry
is dropped from state.json. A hand-written list element is removed in place, comments
kept. When the generated module becomes empty, its file and its import line go away
(so `add` then `rm` leaves the tree byte for byte as it was). A package declared in
several places is `ambiguous`; repeat with `--where FILE:LINE` (from `candidates[]`).

### `set`

```
nixin set OPTION NIX-EXPR... [--home USER] [--where FILE:LINE] [--config DIR] [--dry-run] [--no-eval]
```

Sets OPTION to a Nix expression (the remaining arguments joined with spaces, checked
with `nix-instantiate --parse`). If a module already defines OPTION, its value is
rewritten in place (wrappers like `lib.mkForce`/`lib.mkIf c` kept). Otherwise the
definition goes into the generated module as an attribute path
(`services.openssh.settings.X11Forwarding = true;`).

### `unset`

```
nixin unset OPTION [--home USER] [--where FILE:LINE] [--config DIR] [--dry-run] [--no-eval]
```

Removes OPTION's definition: from state.json, or in place from a hand-written module.

#### Writer result (`add`, `rm`, `set`, `unset`)

| field | meaning |
|---|---|
| `config` | the workspace path |
| `scope`, `user` | `nixos` (user null) or `home` (user = `--home`) |
| `dry_run` | true with `--dry-run`: nothing was written and no eval check ran |
| `packages[]` | (`add`, `rm`) the attribute paths asked for |
| `option`, `value` | (`set`) the option and the Nix text; (`unset`) `option` only |
| `actions[]` | per name: `name`, `action` (`added`, `already-added`, `already-declared`, `removed`, `set`, `unset`, `unchanged`), `file`, `line`; `set` adds `old` (previous Nix text or null) and `in_place` |
| `changes[]` | `file` (repo-relative), `kind` (`created`, `modified`, `deleted`), `diff` (unified). Empty when nothing changed |
| `imports_added[]` | `{file, import}` |
| `imports_removed[]` | `{file, line, restored}` (`restored`: a one-line list put back exactly) |
| `checks` | `parse[]` = `{file, ok, error}` per written `.nix` file; `eval` = null (dry run, `--no-eval`, nothing changed) or the eval proof. For packages: `ok`, `attr`, `command` (to rerun it by hand), `packages` (count), `present{attr: name}`, `absent[]`, and `missing[]`/`still_installed{}` when it did not work; for options: `ok`, `attr`, `value` or `defined: false`, `note`. When the eval itself fails: `ok: false`, `code`, `message`, `attr` |
| `warnings[]` | strings, always present: a conditional list, a kept wrapper, a hand-edited generated file, an eval failure, … |

`checks.eval.ok: false` does not make the verb fail. The text change stands, `ok` stays
true, exit 0, and the failure is repeated in `warnings` (open question 1 in
`docs/HANDOFF-integrate.md`). A UI must look at `checks.eval.ok`.

Errors (an error writes nothing): `usage`, `config-not-found`, `not-a-workspace`,
`unknown-host`, `unknown-user` (the message names the hosts where USER exists),
`unknown-package` (`add`), `invalid-nix` (bad value, bad name, or a file that would not
parse after the edit; then `file`), `not-found` (`rm`/`unset`: with `hints[]`, e.g.
`programs.kitty.enable is set at …`, or `candidates[]` when `--where` matched nothing),
`ambiguous` (with `candidates[]` = `{file, line, option, text}`),
`import-site-missing` (with the exact `line` to add, and `file`), `file-conflict` (an
`installer/` file nixin did not generate), `invalid-state` (unreadable state.json),
`eval-failed` (only when listing the flake's hosts needs an eval), `nix-missing`,
`internal`.

Costs: reads the workspace, runs `nix-instantiate --parse` and nixfmt, and (unless
`--no-eval`) runs nix: one eval before an `add` and one after every write. Without
`--dry-run` it writes in the workspace (`installer/*`, the edited modules) and in its git
index (`git add --intent-to-add` for created files, `git rm --cached` for deleted
never-committed ones). It never commits. The writer keeps no cache. Evals time out after
900 s (`eval-timeout`).

### `plan`

```
nixin plan [--config DIR] [--authoritative DIR]
```

Builds the workspace host's `config.system.build.toplevel` without root and diffs its
closure against `/run/current-system`. The build is the workspace as a git+file flake:
tracked files with their working-tree contents (the writer's new files are
intent-to-add, so they count), nothing untracked. That is exactly what `apply` commits
and what the authoritative tree builds after pulling it, so plan's `out` is the path
`nixos-rebuild` will produce there. Two nix steps: evaluate `toplevel.drvPath`
(`eval-failed`), then `nix build <drv>^out` (`build-failed`). In human mode nix's build
output streams to stderr; under `--json` nothing does.

The out path is cached by the flake's *source store path* (a content hash of that tree,
dirty edits included) and by commit (see [Caches](#caches)), so `apply` right after
`plan` does not rebuild even though it commits first, and a second `plan` of the same
tree is instant. A tree whose only change is `installer/history.jsonl` is answered as
its HEAD commit (no module reads that file).

Result:

| field | meaning |
|---|---|
| `out`, `drv` | the built toplevel and its derivation |
| `cached` | `out` came from plan's cache (nothing evaluated or built); `cache_key` says which entry (`source <hash>` or `commit <sha>`) |
| `up_to_date` | `out` is `/run/current-system` |
| `duration_s`, `timings` | total seconds; `eval_s`, `build_s` when it built |
| `workspace` | `path`, `branch`, `head`, `dirty[]` (tracked files that differ from HEAD), `untracked[]` (not part of the build) |
| `authoritative` | `path`, `exists`, `head`, `branch`, `dirty[]`, `relation`: `same`, `ahead` (the workspace has commits to apply), `behind` (the authoritative tree has commits the workspace lacks), `diverged`, `unknown` (its HEAD is not a commit the workspace has) |
| `running` | `store_path` (`/run/current-system`), `generation` (the newest system profile generation pointing at it, or null), `profile_generation`, `profile` |
| `pending_commits` | `[{commit, subject}]` the workspace has and the authoritative tree does not; null when `relation` is `diverged`/`unknown` |
| `diff` | `{added: [{name, version, size}], removed: [{name, version, size}], changed: [{name, from, to, size_delta}], size_delta}` from `nix store diff-closures`; sizes in bytes, null where nix printed none (it omits deltas under 8 KiB); `from`/`to` are nix's version sets as one string (`"6.7.5, 6.7.5-qt5"`), both null for a size-only change; `unparsed[]` if nix printed a line nixin could not read. null for another host (`--host` is not this machine) |
| `warnings[]` | e.g. untracked files, another host |

Errors: `usage` (any positional argument), `config-not-found`, `not-a-workspace` (also: not
a git checkout, detached HEAD), `unknown-host`, `eval-failed`, `eval-timeout` (900 s),
`build-failed` (the failing builder's block with nix's log tail), `not-a-flake`,
`nix-missing`, `git-failed`, `internal`.

Costs: runs nix (evaluation, then a build: from nothing for a no-op to hours for a new
kernel) and git (read only). Writes nix's store and plan's cache, nothing in the trees.

### `apply`

```
nixin apply [-m MESSAGE] [--dry-run] [--no-switch] [--config DIR] [--authoritative DIR]
```

The one privileged step. In order:

1. **Commit** (in the workspace, as you): if tracked files differ from HEAD, `git add
   -u` (plus `installer/history.jsonl` the first time), `git commit` with the
   workspace's own git identity and hooks. The message is `-m`, else generated from what
   `installer/state.json` gained or lost (`nixin: mbp13: +cowsay`; in-place edits of
   hand-written modules name the files), with every item and file in the body.
   Untracked files are never committed.
2. **Refuse** with `nothing-to-apply` when there is nothing to commit, the authoritative
   tree already has the workspace HEAD, and `/run/current-system` already is its build.
   (Running a different system, e.g. after a failed switch, re-switches.)
3. **Build**: plan's out path (cache), else the same build as `plan`.
4. **Pull** (root): `git -C <authoritative> pull --ff-only <workspace> <branch>`.
   Refused before anything runs when that cannot fast-forward
   (`authoritative-diverged`) or when the authoritative tree has uncommitted changes
   (`authoritative-dirty`).
5. **Switch** (root): `nixos-rebuild switch --flake <authoritative>#<host>`;
   `--no-switch` runs `nixos-rebuild boot` instead (the new generation becomes the boot
   default; nothing is activated now).
6. **Verify**: `/run/current-system` (`--no-switch`: the system profile) must be the out
   path nixin built; otherwise `verify-failed`.
7. **Record**: one line appended to `<workspace>/installer/history.jsonl`: `{ts, host,
   out, generation, commit, message, mode}`. It is committed by the *next* apply that
   commits, never on its own.

Root steps go through a backend. The default is `sudo -n` (never prompts). When a
`nixos-switch` executable is on PATH, the host is this machine and the authoritative
tree is `/etc/nixos`, the switch step (`switch` only) uses that helper instead (19BK's
Touch ID switch: a transient user unit that leaves `status`/`log`/`auth` in
`$XDG_RUNTIME_DIR/nixos-switch/`; apply waits up to 4 h for `status`). The pull always
uses sudo. When sudo refuses for lack of a password, apply stops with `needs-sudo`: the
`message` and `commands[]` are the exact commands still to run (what a person types,
without `-n`), and nothing after that point has happened. Running those commands by
hand finishes the job; so does `sudo -v` and apply again (the commit and the build are
done and cached by then).

`--dry-run` runs none of the steps. It reads the trees and the caches, probes `sudo -n
true`, and prints the commands in order.

Result:

| field | meaning |
|---|---|
| `committed` | the new commit, or null when nothing was committed; `commit_message` its subject |
| `authoritative_head` | the authoritative HEAD after the pull (= the workspace HEAD) |
| `out`, `generation` | the system that now runs (`--no-switch`: the boot default) and its profile generation |
| `switched`, `mode` | true / `switch`; false / `boot` with `--no-switch` |
| `previous`, `running` | the `running` block (as in `plan`) before and after |
| `privileged_commands[]` | the root commands exactly as run (`sudo -n git -C /etc/nixos pull --ff-only /home/me/nixos main`, …) |
| `backends` | `{pull, switch}`: `sudo` or `nixos-switch` |
| `switch_log` | the last 20 lines nixos-rebuild printed (human mode streams all of it to stderr) |
| `diff` | the closure diff from the previous system to `out` (as in `plan`) |
| `cached` | the out path came from plan's cache |
| `history` | the line appended to `installer/history.jsonl` |
| `timings` | seconds: `prepare` (checks), `commit`, `build`, `pull`, `switch`, `total` |
| `warnings[]` | e.g. the authoritative tree is on another branch name |

With `--dry-run`: `dry_run: true`, `commands[]` (`{step, privileged, argv, command}`;
privileged steps add `backend` and `by_hand`, the form to type), `commit` (`{files[],
message}` or null), `out` (null when it would have to build), `cached`, `workspace`,
`authoritative` (`path`, `relation`), `pending_commits`, `running`, `privilege`
(`{pull|switch: {backend, ready, why}}`), `diff` (when `out` is known), `warnings[]`.

Errors (`ok: false`; fields after the first root step tell how far it got: `committed`,
`out`, `authoritative_head`, `privileged_commands[]`): `usage`, `config-not-found`,
`not-a-workspace`, `unknown-host`, `host-not-local` (apply switches *this* machine;
`--host` names another), `authoritative-missing` (no git checkout at the authoritative
path), `authoritative-dirty` (with `files[]`), `authoritative-diverged` (with
`authoritative_head` and `state`: `behind`, `diverged` or `unknown`; the message has
the git commands that bring those commits into the workspace), `nothing-to-apply`,
`eval-failed`, `eval-timeout`, `build-failed`, `needs-sudo` (with `commands[]`),
`switch-failed` (nixos-rebuild's last lines; the pull has happened), `verify-failed`,
`git-failed` (a git command failed, e.g. a commit hook or no identity), `busy` (another
apply holds the lock), `nix-missing`, `internal`.

Costs: git in the workspace (commit) and, read-only, in the authoritative tree; nix
(plan's build unless cached); two root commands. `nixos-rebuild` evaluates the config
again as root (its own eval cache) before it switches. Writes: one commit in the
workspace, the authoritative tree (root's pull), a new system generation, `/boot` (the
bootloader step), `installer/history.jsonl`, plan's cache.

### `sync`

```
nixin sync [--pull] [--push] [--remote NAME] [--config DIR] [--authoritative DIR]
```

`git fetch` of the remote (`--remote`, else the branch's upstream remote, else
`origin`) into the workspace, then a report. `--pull`: `git pull --ff-only` from that
remote's branch of the same name (`diverged` if it cannot fast-forward). `--push`: push
the workspace branch to the same name there (`diverged` if the remote has commits the
workspace lacks). Both: pull first. The authoritative tree is only read: getting
commits into it is `apply`'s job.

Result: `done[]` (`fetch`, `pull`, `push`), `workspace` = `{path, branch, head, ahead,
behind, dirty[]}` (ahead/behind of `<remote>/<branch>`; null when the remote has no such
branch), `authoritative` = `{path, head, relation, behind_workspace}` (`relation` as in
`plan`; `behind_workspace`: how many workspace commits an apply would bring in, null
when unknown), `remote` = `{name, url, head}`, `duration_s`, `warnings[]`.

Errors: `usage` (any positional argument, or no such remote), `config-not-found`,
`not-a-workspace`, `diverged`, `git-failed` (fetch/pull/push failed otherwise, e.g. the
network or authentication), `internal`.

Costs: network (the remote), git in the workspace only.

## Error codes

| code | from | when |
|---|---|---|
| `usage` | all | bad command line, or the verb's own argument check; exit 2 |
| `internal` | all | an unexpected exception (a bug, or a source that changed its response shape); the message names the exception and `file:line` |
| `not-implemented` | cli | a verb without a handler (none today; kept for UIs built against the stubs) |
| `needs-sudo` | apply | a root step and `sudo -n` cannot get root without a password; `message` and `commands[]` are what is left to run by hand |
| `network` | discovery | a source is unreachable, timed out, answered 5xx after retries, or sent non-JSON |
| `http-<status>` | discovery | a 4xx the caller may act on (`http-400`, …) |
| `bad-channel` | discovery | unknown `--channel`, or a bare-rev `--nixpkgs` for a channel-based source |
| `not-found` | discovery, truth, writer | `info`/`versions`: no such name. `where`: no such option. `rm`/`unset`: not declared in any module nixin can edit (`hints[]`) |
| `nix-missing` | all | `nix` (or `nix-instantiate`) is not on PATH |
| `nix-failed` | discovery | `nix search`/`nix eval`/`nix flake metadata` on `--nixpkgs` failed or printed non-JSON |
| `nix-timeout` | discovery | `nix flake metadata` > 120 s, `nix search` > 900 s, `nix eval` > 300 s |
| `config-not-found` | truth, writer | no `flake.nix` in the config dir |
| `not-a-flake` | truth | `nix flake metadata`/`nix flake archive` failed on the config |
| `unknown-host` | truth, writer | `--host` is not in `nixosConfigurations` |
| `eval-failed` | truth, writer | the host does not evaluate (assertions, syntax, an untracked file the config imports) |
| `eval-timeout` | writer, apply | a writer eval or plan's toplevel eval took longer than 900 s (truth evals have no limit) |
| `not-a-workspace` | writer, apply | `--config` is `/etc/nixos` (also the default without `$NIXIN_CONFIG`), is not writable, or an edit would leave the tree; apply also: not the top of a git checkout, HEAD detached, or the authoritative tree itself |
| `unknown-user` | writer | `--home USER` is not an HM user of the host |
| `unknown-package` | writer | `add`: not an attribute of the host's `pkgs` |
| `invalid-nix` | writer | the value or name does not parse, or an edited file would not parse |
| `ambiguous` | writer | several declarations match; `candidates[]`, repeat with `--where` |
| `import-site-missing` | writer | no module or no `imports = [ … ];` to import the generated file from; `line` to add |
| `file-conflict` | writer | an `installer/` file exists that nixin did not generate |
| `invalid-state` | writer | `installer/state.json` is not valid JSON or has the wrong version |
| `build-failed` | apply | `nix build` of the toplevel failed; the message is the failing builder's block with nix's log tail |
| `nothing-to-apply` | apply | no uncommitted change, the authoritative tree has the workspace HEAD, and it is what runs |
| `host-not-local` | apply | `apply --host H` for a host that is not this machine |
| `authoritative-missing` | apply | no git checkout at the authoritative path |
| `authoritative-dirty` | apply | the authoritative tree has uncommitted changes (`files[]`); nixin only fast-forwards it |
| `authoritative-diverged` | apply | root's `git pull --ff-only` could not take the workspace (`state`: `behind`, `diverged`, `unknown`); the message has the commands to fix it |
| `switch-failed` | apply | `nixos-rebuild` (or the `nixos-switch` helper) failed, or the helper is already running |
| `verify-failed` | apply | after the switch the system is not the out path nixin built |
| `git-failed` | apply | a git command failed (or git is not on PATH); the message has git's last lines |
| `diverged` | apply (`sync`) | `--pull` cannot fast-forward, or `--push` was rejected |
| `busy` | apply | another `nixin apply` holds the lock |

In a multi-source discovery answer, any discovery code can also appear as one source's
`sources[...].error` while `ok` is true.

## Costs (measured)

Measured on mbp13 (nix 2.34.9, 8 cores, 15 GiB) on 2026-11-01, merged binary, with an
empty nixin cache (fresh `$XDG_CACHE_HOME`; nix's own store and eval caches as they
were). Three other agent sessions were running nix evals, so the 1-minute load was 14–20
for the cold runs. Treat the times as upper bounds; peak RSS does not depend on load.
Truth ran on `~/nixos` for host 19BK, the writer on a clone for mbp13 and 19BK/brucking.

The apply rows are from later that evening on the same host, quiet (load 0.4–1.4), the
real `~/nixos` workspace and `/etc/nixos`, plan's cache cold for the first plan.

| verb | network | runs nix | writes | cold | warm (nixin cache) | peak RSS |
|---|---|---|---|---|---|---|
| `search` (pkg, opt, all) | yes | — | cache | 0.9–1.2 s | 0.4 s | 40–50 MiB |
| `search --kind hm` | yes (HM option JSON, cached 1 d) | — | cache | 1.3 s | 0.4 s | 66 MiB |
| `search --kind fn` | yes (noogle data, cached 1 d) | — | cache | 5.1 s | 0.4 s | 71 MiB |
| `search --nixpkgs REV` | — | `nix search` | cache | **204 s** (first search on a rev builds nix's eval cache; 46 s on a quiet box) | a few s for another query, 0.2 s cached | **4.6 GiB** |
| `info` | yes | — | cache | 1.2–1.9 s | 0.2 s | 40–50 MiB |
| `info --kind pkg --nixpkgs REV` | — | `nix eval` | cache | 1.2 s | 0.2 s | 105 MiB |
| `versions` | yes | — | cache | 1.3 s | 0.2 s | 40 MiB |
| `status` | — | yes | cache | **77 s** (toplevel 68 s, packages 7 s) | 0.8 s | 1.4 GiB |
| `status --full` | — | yes | cache | **92 s** more (NixOS index 61 s, HM 29 s) | 1.1 s | 1.7 GiB |
| `where PACKAGE` | — | yes | cache | 12 s (packages + 2 prefix evals) | 0.6–1 s | 440 MiB |
| `where OPTION` | — | yes | cache | 3.5–9 s | 0.6–1 s | 200–230 MiB |
| `add --dry-run --no-eval` | — | parse only | — | 0.8 s | — | 36 MiB |
| `add --dry-run` | — | 1 small eval | — | 1.7 s | — | 200 MiB |
| `add`, `rm` (NixOS) | — | 1–2 evals | workspace + git index | 5–7 s (2.4–3.3 s at load 2.6) | — | 350 MiB |
| `add`, `rm --home` (HM) | — | 1–2 evals | workspace + git index | 8–10 s | — | 440 MiB |
| `set`, `unset` | — | 1 eval | workspace + git index | 3–3.5 s | — | 200 MiB |
| `plan` | — | nix eval + build | nix store, plan cache | **38 s** no-op tree (eval 32 s, build 5 s); **85 s** with cowsay (39 derivations: three `system-path`s, units, etc); 37 s for `configurationLimit` | 0.6–1.1 s (cached; also a tree that only adds `history.jsonl`) | — |
| `apply` | — | git, nix (unless cached), sudo | workspace commit, authoritative tree, system profile, /boot | **30–31 s** after `plan` or for a tree plan's cache knows: build 0.4–0.9 s (cache), pull 0.05 s, `nixos-rebuild switch` 29–30 s (root's own eval + activation) | `nothing-to-apply` 0.2 s | — |
| `apply --dry-run` | — | `nix flake metadata`, `sudo -n true` | — | 1.3 s | — | — |
| `sync` | yes (the remote) | git | workspace refs | 1.3 s fetch from GitHub; 0.4 s push to a local bare repo | — | — |

nixin streams no progress under `--json` (in human mode `plan`/`apply` tee nix's build
output and nixos-rebuild's log to stderr). A UI should run the slow verbs (`search
--nixpkgs` on a new rev, cold `status`, `status --full`, `plan`, `apply`) in the
background with a spinner and say up front that they can take minutes (a kernel change
in `plan`: hours). `status` reports `timings` and `cache.hits`/`misses`.
Discovery adds a `notes[]` line after a slow first `nix search`. A cold `where` or writer
verb takes seconds. Everything else is sub-second warm. Concurrent runs are safe for the
caches (per-process temp files). Two writer runs on one workspace are not safe: they race
on `installer/state.json`.

## Caches

Everything lives under `$XDG_CACHE_HOME/nixin/` (a relative or empty `XDG_CACHE_HOME` is
ignored, so `~/.cache/nixin/`). It is safe to delete at any time.

| path | what | lifetime / key |
|---|---|---|
| `es/<sha1>.json` | search.nixos.org: channel alias list, searches, counts | aliases 6 h, searches 1 h |
| `hm/<sha1>.json` | the Home Manager option JSON per release | 1 day |
| `nixhub/<sha1>.json` | NixHub version history | 6 h |
| `noogle/<sha1>.json` | noogle function data | 1 day |
| `local/<sha1>.json` | `--nixpkgs`: flake metadata (1 h per flakeref string), search and eval answers | 30 days, keyed by resolved rev |
| `truth/<host>-<key>-<name>.json` | truth evals: `toplevel`, `packages`, `prefix-<scope>-<hash>`, `options-<scope>` (the index, ~10 MB), `options-<scope>-summary` | keyed by nix's flake fingerprint (tracked tree + lock) and host: any tracked edit misses; older copies of the same name are pruned |
| `truth/exclude-<nixpkgs12>-<hm12>.json` | options the full index skips (their definitions throw), per (nixpkgs rev, home-manager rev), seeded | until deleted; delete it to re-learn |
| `apply/out-<host>-<source-hash>.json` | plan's build: `{out, drv}` per flake source store path (a content hash of the tracked tree as nix copies it, dirty edits included) | last 40 per host; an entry whose `out` was garbage-collected counts as a miss |
| `apply/commit-<host>-<sha>.json` | the same, per commit (written by `plan` on a clean tree and by `apply`); `status.pending` and `nothing-to-apply` read it without nix | last 40 per host |
| `apply/apply.lock` | `flock` held while `apply` runs (`busy`) | — |

Flags: discovery `--refresh` ignores cached answers, refetches and stores; `--no-cache`
neither reads nor writes. Truth `--refresh` skips reads and still writes the fresh
results. The writer has no cache. Plan's cache has no flag: an entry only answers when
its out path is still in the store, and the build it saves is deterministic. nix keeps its own caches (`~/.cache/nix`, the store).
Those make a second cold-nixin run of the same eval faster, and nixin does not manage them.
