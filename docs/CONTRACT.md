# nixin CLI contract

The one reference for UI authors (TUI, GUI, agents) who drive `nixin --json`. It covers
the envelope, every verb's arguments and result fields, every error code, exit codes,
what each verb costs (network, nix, disk writes, time) and where the caches are.
README.md is the overview and points here. When code and this file disagree, it is a bug
in one of them. `tests/test_integration.py` checks the envelope rules and the error-code
list against this file.

Status: all four layers are implemented: discovery, truth, writer and apply (`plan`,
`apply`, `sync`, `history`, `rollback`).

## Invocation

```
nixin [--json] [--host H] VERB [ARGS...]
nixin --version | --help | VERB --help
```

| verb | layer | does |
|---|---|---|
| `search`, `info`, `versions` | discovery | what exists upstream, in which versions |
| `status`, `where`, `list` | truth | what this config gives a host (it evaluates the config flake); `list --diff A B`: two hosts compared |
| `add`, `rm`, `set`, `unset` | writer | edit a user-owned workspace clone of the config |
| `plan`, `apply`, `sync` | apply | build + closure diff + boot check; commit, pull into the authoritative tree and switch (root); for another host: push to the hub, pull and switch there over ssh; git sync with the hub |
| `history`, `rollback` | apply | this machine's system generations with the commits nixin applied; switch back to one (root) and revert the config with it |
| `outdated` | outdated | what a nixpkgs lock update would change for a host (two package-list evals); `--published`: installed versions against a search.nixos.org channel |
| `update` | outdated | take it: move the nixpkgs input in the workspace's `flake.lock` (the command `outdated` prints); no build, no switch |

- `--json` and `--host H` are global. They go before the verb, or after it, in any order
  with the verb's own flags (`nixin where kitty --host 19BK --json`).
- `--host` names `nixosConfigurations.<H>`. The default is this machine's hostname
  (`socket.gethostname()`), even when `--config` points at a config that has no such
  host (see open question 3 in `docs/HANDOFF-integrate.md`). Discovery ignores it but
  still echoes it in the envelope.
- `--config DIR` (truth, writer, apply verbs, `outdated` and `update`; before or after the
  verb, the verb's own wins; discovery ignores it) is the config flake: `--config`, else
  `$NIXIN_CONFIG`, else `/etc/nixos`. Truth and `outdated` only read it. The writer, apply
  and `update` refuse `/etc/nixos` and any tree you cannot write (`not-a-workspace`). They work on a clone
  (`git clone /etc/nixos ~/nixos`, then `--config ~/nixos` or `NIXIN_CONFIG=~/nixos`).
- `--authoritative DIR` (apply verbs) is the root-owned clone the system is switched
  from: `--authoritative`, else `$NIXIN_AUTHORITATIVE`, else `/etc/nixos`. nixin only
  reads it; root `git pull --ff-only`s the workspace into it during `apply` and
  `rollback`.
- Nothing prompts, ever. nix and git run with stdin closed, sudo always as `sudo -n`. A
  step that needs root and cannot get it fails with `needs-sudo` and the commands to run
  (only `apply` and `rollback` need root). The one exception is `apply --host H` for
  another host: it runs over `ssh -t`, and the sudo there may ask for a password on the
  terminal, so it refuses (`needs-tty`) under `--json`, from a UI and without a terminal;
  its `--dry-run` runs nothing and works everywhere.
- `~/.config/nixin/hosts.toml` (`$XDG_CONFIG_HOME/nixin/hosts.toml`) tells `apply` how to
  reach another host: `[H] ssh = "alias-or-user@address"  workspace = "~/nixos"
  authoritative = "/etc/nixos"` (those are the defaults, with `ssh` = the host name). No
  file is needed. Only `apply` reads it.
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
`NIXIN_WIDTH` overrides the terminal width for discovery's tables and for `list`.

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
| a package's attribute path | `name` on `pkg` rows; `package` on `program` rows (`name` is the binary there); `attr` on `version` rows | not recoverable from derivations: `installed[].name` (`where`) and `packages[].name` (`list`) are the derivation name (`cowsay-3.8.4`), with `pname`/`version` | `packages[]` and `actions[].name` (`cowsay`, `python3Packages.requests`) |
| what NAME is | `--kind`/`kind`: `pkg`, `opt`, `hm`, `program`, `fn`, `version`; `all` | `--kind`/`kind`: `package`, `option` | implied by the verb |
| NixOS vs Home Manager | `kind` `opt` vs `hm` | option answers: `scope` = `nixos` \| `home`, plus `user`. Package answers: `scope` = `system` \| `home`, plus `user`; `status` uses `packages.system`, `programs.system`; `list` selects with `--home USER` / `--scope system\|home\|all` | `scope` = `nixos` \| `home`, plus `user`; select with `--home USER` |
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

### `list`

```
nixin list [--home USER] [--scope system|home|all] [--filter TEXT] [--programs]
           [--config DIR] [--refresh]
nixin list --diff [HOST_A] HOST_B [--home USER] [--scope …] [--filter TEXT] [--programs]
```

The packages the host installs, one row per package: `environment.systemPackages` and
each Home Manager user's `home.packages` as evaluated (what `status` counts). The default
scope is `all`: the system and every HM user of the host. `--scope system` and `--scope
home` (every HM user) narrow it. `--home USER` keeps that user only, and implies `--scope
home` unless `--scope all` is given (then the system and that user). `--filter TEXT`
keeps the rows whose `name` or `pname` contains TEXT, ignoring case. It uses the same
package-list eval and cache entry as `status` (and `where`): after either of them, or a
first `list`, the next `list` for an unchanged tree runs no eval. `--refresh` evaluates
again.

Result:

| field | meaning |
|---|---|
| `packages[]` | sorted by `pname` (else `name`), case-insensitive: `scope` (`system` \| `home`), `user` (null for `system`), `name` (derivation name, `ripgrep-15.2.0`), `pname`, `version` (null for derivations without them, e.g. `writeShellApplication` scripts), `declared[]` |
| `declared[]` | **best effort.** The list elements in the config's modules that probably are this package: `file` (repo-relative), `line`, `text` (the element, whitespace dropped: `pkgs.ripgrep`), `conditional: true` under `mkIf`/`optionals`. Found by the writer's text scanner, no eval: the modules of the host (and of the user) reachable from `flake.nix` through `imports` path literals, generated `installer/*.nix` included, and the elements of every list assigned to `environment.systemPackages` / `home.packages` there. An element matches a package by name: `ripgrep` / `pkgs.ripgrep` → pname `ripgrep`, `kdePackages.dolphin` → `dolphin`, a camelCase let binding `nightSchedule` → `night-schedule`, `polkit_gnome` → `polkit-gnome`, `callPackage ./x/package.nix {}` → `x`, `writeShellScriptBin "x" …` → `x`. Empty for what a module adds (`programs.x.enable`, services, NixOS defaults), and for an element whose derivation name differs from its attribute (`dust` is `du-dust`) or that is a function call (`mode "music" [ … ]`). Several entries when several lines list it. For the evaluated answer per file use `where NAME` (`declared[]` there comes from the module system) |
| `count` | `len(packages)` |
| `folded` | list entries dropped because they repeat a row (same scope, name, pname and version: a package listed by two modules is installed once). `status.packages` counts list entries, so for the listed scopes it is `count + folded` without a filter |
| `scope`, `users[]`, `filter` | what was listed: the scope as resolved, the HM users included, the filter (null without one) |
| `programs[]` | only with `--programs`: the enabled `programs.<name>` of the listed scopes (`status.programs`, same scope and filter rules), sorted by name: `scope`, `user`, `name`, `option` (`programs.<name>.enable`) |
| `warnings[]` | the scanner's: an import it could not follow, or a host/user whose modules it could not read from `flake.nix` (then that scope's `declared[]` stay empty) |
| `cache` | as in `status` |
| `timings` | `packages` (seconds the package-list eval took when it ran, possibly an earlier run: see `cache.hits`), `declared` (the scan), `total` |

Human output: one line per package, `scope  name  version  (user)  file:line` (pname or
the derivation name; `(user)` for home rows; the first `declared` entry, `+N` for more),
columns aligned and sorted by name, then a footer with the count per scope, the folded
repeats and whether the list came from the cache. The location column is cut from the
left to fit `NIXIN_WIDTH` (or the terminal) and dropped when fewer than 12 columns are
left. `--programs` adds a block of `scope  name  (user)` lines.

Errors: `usage` (a positional argument, `--home` with `--scope system`, an unknown
`--scope`), `unknown-user` (`--home USER` is not an HM user of the host), `config-not-found`,
`not-a-flake`, `unknown-host`, `eval-failed`, `nix-missing`, `internal`.

Costs: runs nix once per tree change (the package-list eval, shared with `status` and
`where`), reads the config's modules, writes the cache file. A few seconds cold, under
half a second warm.

#### `list --diff`

`--diff HOST_A HOST_B` compares two hosts of the same config (`--diff HOST_B`: HOST_A is
`--host`). Per scope: the system with the system, each Home Manager user with the user
of the same name on the other host. Packages are matched by `pname` (else the
derivation name) and compared as sets of versions. The hosts share the config's
`flake.lock`, so versions normally match; an override, an overlay or another nixpkgs
input shows up as `differ`. `--scope`, `--home USER` (a user of either host) and
`--filter` narrow it as for `list`; `--programs` adds the `programs.<name>.enable` that
only one host has. It reads the same package-list cache entry as `list`, `status` and
`where` for each host (`--refresh` re-evaluates both). No `declared[]`.

Result:

| field | meaning |
|---|---|
| `a`, `b` | the two hosts; `a`/`b` below refer to them |
| `packages[]` | one row per package that only one host has or whose versions differ, by scope (system first), then name: `scope`, `user`, `name` (pname, else the derivation name), `state` (`only_a` \| `only_b` \| `differ`), `a`, `b` (the versions on each host, sorted; `[]` absent; `null` in the list for a derivation without a version) |
| `scopes[]` | per scope: `scope`, `user`, `on` (`["a", "b"]`, or one side for a user only one host has), `only_a`, `only_b`, `differ`, `same` (counts) |
| `counts` | `only_a`, `only_b`, `differ`, `same` over all scopes |
| `scope`, `users[]`, `filter` | as resolved (`users`: the HM users compared, from both hosts) |
| `programs[]` | only with `--programs`: `scope`, `user`, `name`, `state` (`only_a` \| `only_b`) |
| `cache` | `{a: …, b: …}`, each as in `status` |
| `timings` | `a`, `b` (seconds each package-list eval took when it ran), `total` |
| `warnings[]` | none today |

Human output: `scope  name  <HOST_A's version>  <HOST_B's version>  (user)`, `-` for
absent and `yes` for a package without a version, then a line per scope (`system: 21
only on 19BK, 223 only on mbp13, 0 versions differ, 114 the same`), then whether each
list came from the cache. A user only one host has is one line (`home brucking: only
19BK has this user (190 packages, none on mbp13; --home brucking lists them)`) unless
`--home` names it.

Errors: `usage` (`--diff` with no host, three hosts, the same host twice; `--home` with
`--scope system`), `unknown-host` (either host), `unknown-user` (`--home USER` on neither
host), `config-not-found`, `not-a-flake`, `eval-failed`, `nix-missing`, `internal`.

Costs: the package-list eval of each host once per tree change (both cold: 4.9 s, 452
MiB on mbp13 for 19BK and mbp13), 0.3 s and 42 MiB warm.

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

Sets OPTION to a Nix expression (the remaining arguments joined with spaces), written
exactly as given. The value is checked for syntax only (`nix-instantiate --parse` of
`with { }; VALUE`), so it may name the module's arguments: `pkgs.linuxPackages_7_2`,
`lib.mkForce true`, `config.networking.hostName`. Whether those names exist where the
value lands is the parse of the edited file before the write (`invalid-nix` with `file`:
a hand-written `{ ... }:` module has no `lib`; the generated module binds `config`, `lib`
and `pkgs`). Whether the value suits the option (type, conflicts) is the eval check after
the write: `checks.eval`, repeated in `warnings`. If a module already defines OPTION, its
value is rewritten in place (wrappers like `lib.mkForce`/`lib.mkIf c` kept). Otherwise the
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
| `checks` | `parse[]` = `{file, ok, error}` per written `.nix` file; `eval` = null (dry run, `--no-eval`, nothing changed) or the eval proof. For packages: `ok`, `attr`, `command` (to rerun it by hand), `packages` (count), `present{attr: name}`, `absent[]`, and `missing[]`/`still_installed{}` when it did not work; for options: `ok`, `attr`, `value` or `defined: false`, `note`; in `value`, functions, derivations, package sets (anything with `callPackage`) and parts past depth 5 or 60 items are the strings `<function>`, `<derivation NAME>`, `<package set>`, `<…>`. When the eval itself fails: `ok: false`, `code`, `message`, `attr` |
| `warnings[]` | strings, always present: a conditional list, a kept wrapper, a hand-edited generated file, an eval failure, … |

`checks.eval.ok: false` does not make the verb fail. The text change stands, `ok` stays
true, exit 0, and the failure is repeated in `warnings` (open question 1 in
`docs/HANDOFF-integrate.md`). A UI must look at `checks.eval.ok`.

Errors (an error writes nothing): `usage`, `config-not-found`, `not-a-workspace`,
`unknown-host`, `unknown-user` (the message names the hosts where USER exists),
`unknown-package` (`add`), `invalid-nix` (a value that is not well-formed Nix, a bad
name, or a file that would not parse after the edit, e.g. a variable it does not bind;
then `file`), `not-found` (`rm`/`unset`: with `hints[]`, e.g.
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
| `reboot_needed` | `boot.reboot_needed`; null for another host |
| `boot` | what the new system does to boot, against `/run/current-system` ([The boot block](#the-boot-block)); null for another host |
| `warnings[]` | e.g. untracked files, another host, the boot block's `warnings[]` (needs a reboot, /boot tight or too small) |

#### The boot block

`plan`, `apply` (and its `--dry-run`) and `rollback` compare the new toplevel with
`/run/current-system`, reading both (no nix, no root):

| field | meaning |
|---|---|
| `kernel` | `{changed, from, to, from_path, to_path}`: the `kernel` links; `from`/`to` the release (`7.2.12`, as `uname -r`: the directory under `kernel-modules/lib/modules`) |
| `initrd`, `modules` | `{changed, from_path, to_path}`: the `initrd` and `kernel-modules` links |
| `kernel_params` | `{changed, from[], to[]}`: `kernel-params` |
| `bootloader` | `{bootspec, changed, keys[]}`: the `boot.json` keys that differ (paths that differ for every system left out) |
| `specialisations[]` | `{name, state, changed[]}` per `specialisation/*`: `state` `same`, `changed` (then `changed[]` names `kernel`/`initrd`/`modules`, and `kernel: {from, to}`), `added`, `removed` |
| `reboot_needed`, `reasons[]` | the default entry's kernel, initrd, module tree or kernel params change: they take effect at the next boot (`reasons`: `kernel`, `initrd`, `modules`, `kernel_params`) |
| `pending_reboot` | the running system's kernel/initrd/modules already differ from `/run/booted-system`'s (a reboot is owed anyway) |
| `boot_space` | `{path, limit, window[], files[], needed, freed, free, total, ratio, state}` (below) |
| `boot_entry` | rollback only: whether the target is among the generations the bootloader keeps (`window`); `false`: it gets no boot entry and the next boot starts the newest generation. null when unknown or not a rollback |
| `warnings[]` | the human sentences for the above (also in the verb's `warnings[]`) |

`boot_space` models systemd-boot's install step. `limit` is the `configurationLimit`
built into the new system (its `bin/switch-to-configuration` → installer script →
builder's `CONFIGURATION_LIMIT`; 0 = no limit, null = not systemd-boot or not found).
The builder keeps the newest `limit` generations (`window`, the numbers after the
switch), first deletes the kernel/initrd files none of them uses, then copies the
missing ones. On /boot now = the files of the newest generations as the *running*
system's limit kept them; after = the newest `limit - 1` plus the new system (a
rollback: the newest `limit`, it adds no generation). `files[]` = `{path, size, for[]}`
of every distinct store file (by path) after minus now, `for` naming the entries
(`default`, a specialisation) that use it; `needed` their sum in bytes; `freed` what
the builder deletes first; `free`/`total` from `statvfs` (`df /boot`'s Avail/Size);
`ratio` = needed / (free + freed). `state`: `ok`; `warn` above 80 %; `full` when it
does not fit (`apply` and `rollback` refuse with `boot-space`; `plan` and `--dry-run`
report it); `unknown` when /boot cannot be read. With `limit` null, on /boot now = the
running system's files and nothing counts as freed. A user cannot list /boot; the
model assumes the last bootloader install ran after the newest generation was made.

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
   **Boot check** ([the boot block](#the-boot-block)): `boot-space` when the new
   kernels/initrds do not fit on /boot. When plan's cache already knows the out path
   this runs before the commit (nothing has happened); otherwise after the build, before
   anything runs as root (the commit stays; `committed` in the error).
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
| `boot`, `reboot_needed` | [the boot block](#the-boot-block) from the previous system to `out` |
| `cached` | the out path came from plan's cache |
| `history` | the line appended to `installer/history.jsonl` |
| `timings` | seconds: `prepare` (checks), `commit`, `build`, `pull`, `switch`, `total` |
| `warnings[]` | e.g. the authoritative tree is on another branch name |

With `--dry-run`: `dry_run: true`, `commands[]` (`{step, privileged, argv, command}`;
privileged steps add `backend` and `by_hand`, the form to type), `commit` (`{files[],
message}` or null), `out` (null when it would have to build), `cached`, `workspace`,
`authoritative` (`path`, `relation`), `pending_commits`, `running`, `privilege`
(`{pull|switch: {backend, ready, why}}`), `diff`, `boot`, `reboot_needed` (when `out`
is known; a `full` /boot is reported, not refused), `warnings[]`.

#### Another host: `apply --host H`

When H is not this machine, `apply` does not switch anything here. It commits the
workspace as above (but not `installer/history.jsonl`: that records this machine's
generations and waits for the next local apply), pushes the branch to the hub, and runs
the rest on H over one `ssh -t <ssh>` session (`hosts.toml`: `ssh`, `workspace`,
`authoritative`; see [Invocation](#invocation)):

1. **Commit** (here, as for a local apply).
2. **Push**: `git push --porcelain <remote> HEAD:refs/heads/<branch>` (the branch's
   upstream remote, else `origin`). `push-failed` when there is no such remote or the push
   fails (`rejected: true` when the hub has commits the workspace lacks: `sync --pull`
   first).
3. **On H**, one POSIX `sh -c` script, so sudo asks at most once:
   `git -C <workspace> pull --ff-only`, then `sudo git -C <authoritative> pull --ff-only
   <workspace> <branch>`, then `sudo nixos-rebuild switch --flake <authoritative>#H`
   (`--no-switch`: `boot`). Between them it checks that H's workspace is on `<branch>`
   and that each tree ends at the pushed commit, so H switches to exactly what was
   pushed. Its output streams to stderr as it comes (the sudo prompt included).

Nothing is built here and no history line is written: no `diff`, `boot`, `cached`,
`history` (null). The other fields are those of a local apply, from the remote end:
`authoritative_head` (the pushed commit, checked there), `out` (H's `/run/current-system`
after the switch; `--no-switch`: its profile), `generation`, `previous` and `running`
(the same `running` block, read on H), `privileged_commands[]` (the sudo commands as
typed there), `switch_log`. Also: `remote` = `{host, ssh, workspace, authoritative,
config}` (`config`: the hosts.toml read, null for the defaults), `pushed` = `{remote,
url, branch, flag, up_to_date, commits[]}`, `changed` (H's profile moved), `ssh` =
`{argv, script}`, `backends` `ssh+sudo`, and `timings` = `prepare`, `commit`, `push`,
`remote`, `total`.

It needs a person at a terminal: `needs-tty` under `--json`, from a UI (a worker thread)
and when stdin or stderr is not a terminal. `--dry-run` works everywhere and contacts
nothing (no fetch, no push, no ssh, no nix build): `remote`, `commands[]` (local `commit`
and `push`, the `ssh` command, then H's three steps with `where: "<H>"`), `script` (with
`NEW_COMMIT` where the commit does not exist yet), `commit`, `push` (`{remote, url,
branch, tracking, commits}`: the commits the last fetch says the hub lacks),
`needs_tty: true`, `privilege` (`ssh+sudo`, `ready: false`: not probed), `out`, `diff`,
`boot`, `running` null.

Errors on H carry `step` (`workspace-pull`, `authoritative-pull`, `switch`), `committed`,
`pushed`, `remote_steps_started[]` (`1/3`…) and `privileged_commands[]`:
`git-failed` (H's workspace is on another branch, cannot fast-forward, or a tree is not
at the pushed commit afterwards; root's pull failed, e.g. a wrong password. When H's own
uncommitted `installer/history.jsonl` lines block the pull the message says how to set
them aside), `authoritative-diverged` (root's pull cannot fast-forward), `switch-failed`
(nixos-rebuild failed; both trees are at the pushed commit), `ssh-failed` (no `ssh`, no
connection: exit 255, or the session ended otherwise). Before anything runs: `needs-tty`,
`push-failed` (no remote), `invalid-hosts-config`. A dropped connection during the
switch leaves the switch to nixos-rebuild on H.

Errors (`ok: false`; fields after the first root step tell how far it got: `committed`,
`out`, `authoritative_head`, `privileged_commands[]`): `usage`, `config-not-found`,
`not-a-workspace`, `unknown-host`, `authoritative-missing` (no git checkout at the
authoritative path), `authoritative-dirty` (with `files[]`), `authoritative-diverged` (with
`authoritative_head` and `state`: `behind`, `diverged` or `unknown`; the message has
the git commands that bring those commits into the workspace), `nothing-to-apply`,
`eval-failed`, `eval-timeout`, `build-failed`, `needs-sudo` (with `commands[]`),
`switch-failed` (nixos-rebuild's last lines; the pull has happened), `verify-failed`,
`git-failed` (a git command failed, e.g. a commit hook or no identity), `busy` (another
apply or rollback holds the lock), `boot-space` (with `boot_space`), `nix-missing`,
`internal`; for another host also `needs-tty`, `push-failed`, `ssh-failed`,
`invalid-hosts-config` (above).

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

### `history`

```
nixin history [--limit N] [--config DIR] [--authoritative DIR]
```

This machine's system generations, newest first, joined with
`<config>/installer/history.jsonl` (the workspace's: `--config`, `$NIXIN_CONFIG`, else
`/etc/nixos`, which is read-only here and works, but its copy lags the workspace's by one
apply). `--limit N`: the newest N (default 20; 0: all). No nix, no root, no network.

Result:

| field | meaning |
|---|---|
| `generations[]` | one row per `/nix/var/nix/profiles/system-N-link`, newest first (below) |
| `total` | how many generations there are (`generations[]` has at most `--limit`) |
| `current`, `booted`, `default` | the generation `/run/current-system` is, the one `/run/booted-system` is, the profile's (the boot default); null when unknown |
| `history` | `{path, entries, unmatched}`: the file, its well-formed lines, and how many name an out path no generation has any more (deleted generations) |
| `warnings[]` | no history file; the config is the authoritative tree |

A row: `generation`, `date` (the link's mtime, UTC ISO 8601), `store_path`,
`current`/`booted`/`default` (bools), `kernel` (the release), `nixos_version`, and from
the history line that matches: `commit`, `message`, `mode` (`switch`, `boot`,
`rollback`), `applied` (its `ts`), `applied_host`, `match` (`generation`: a line for this
generation; `out`: a line for another generation with the same store path, which is then
`match_generation`; null: none), `entries[]` (every line for this generation, oldest
first: an apply and later rollbacks to it). Two generations can share a store path (a
rollback or a revert rebuilds an older tree): `current` prefers the profile's generation,
`booted` the newest one made before `/run/booted-system` was.

Human output: a table (`*` runs now, `b` was booted, `d` boot default when it is not the
running one) with date (local time), kernel, NixOS version, commit and subject;
`(= gen N)` marks a match by store path, `[mode]` a mode other than `switch`.

Errors: `usage` (a positional argument, `--limit` below 0), `config-not-found`,
`host-not-local` (`--host` is not this machine), `internal`.

Costs: reads the profile links, each generation's toplevel (a few symlinks and files) and
the history file. ~0.1 s for 34 generations.

### `rollback`

```
nixin rollback [GEN] [--dry-run] [--config DIR] [--authoritative DIR]
```

Back to system generation GEN (default: the one before the running one), the config
with it. Two halves; the root commands go through the same backend as `apply` (`sudo -n`;
`needs-sudo` with `commands[]` when it cannot get root):

1. **Config** (in the workspace, as you; before anything runs as root): which commit
   builds GEN? First the `installer/history.jsonl` lines for GEN's store path (its own
   generation's first), then plan's commit cache over the first 200 first-parent
   ancestors of HEAD. It must be an ancestor of the workspace HEAD. If one is found and
   the workspace's tree differs from it (`installer/history.jsonl` aside), ONE new commit
   makes the tree that commit's again: `git restore --source=C --staged --worktree -- .
   ':(exclude)installer/history.jsonl'` and `git commit` (workspace identity and hooks).
   One reverted commit: `Revert "<its subject>"`; more: `nixin: rollback to generation
   N: revert K commits`; the body lists them. Never a reset or a force push.
   Refused first when the workspace has uncommitted changes (`workspace-dirty`, with
   `files[]`), has commits that are not applied yet and would be reverted
   (`workspace-dirty`, with `commits[]`), or cannot fast-forward the authoritative tree
   (`authoritative-diverged`, `authoritative-dirty`, `authoritative-missing`).
2. **System** (root): `nix-env --profile /nix/var/nix/profiles/system
   --switch-generation GEN`, then GEN's own `<store path>/bin/switch-to-configuration
   switch` (activation and the bootloader, as `nixos-rebuild switch --rollback` does).
   Refused before anything runs when GEN's kernels do not fit on /boot (`boot-space`).
3. **Pull** (root), when the config half made or has a commit to bring in: `git -C
   <authoritative> pull --ff-only <workspace> <branch>`, so the authoritative tree builds
   what runs again and the next `apply` does not switch back.
4. **Verify**: `/run/current-system` is GEN's store path, the profile is at GEN, the
   authoritative HEAD is the workspace HEAD.
5. **Record**: a line in `installer/history.jsonl`: `{ts, host, out, generation, commit,
   message, mode: "rollback", from_generation, config}` (`commit`: the authoritative
   HEAD that builds it, null when the config was left as it is). The commit map of plan's
   cache gets the authoritative HEAD → GEN's out, so `apply` answers `nothing-to-apply`
   and `status.pending.running_matches` is true without nix.

When no commit is known to build GEN, the system rolls back and the config is left as
it is: `config.state` is `untouched` with `why`, and a warning says that the next
`apply` would switch back to the config's system. A step that already happened is
skipped on a retry with the same GEN (`nixin rollback GEN` after a `needs-sudo` or a
failed activation finishes the rest). `nothing-to-apply` when GEN runs, is the boot
default and the config builds it (or has no commit for it).

`--dry-run` runs nothing: it reads the profile, the trees and the caches, probes `sudo -n
true`, and prints the commands in order (`[user]` the revert, `[root]` the rest).

Result:

| field | meaning |
|---|---|
| `generation`, `from_generation`, `out` | the target, the generation that ran before, GEN's store path |
| `switched` | the system half ran (false on a retry that only had the pull left) |
| `previous`, `running` | the `running` block (as in `plan`) before and after |
| `config` | `{state, why, commit, source, revert_commit, reverted[], authoritative_head}`: `state` `reverted` (the authoritative tree moved back), `in-sync` (it already builds GEN), `untouched` (`why`: `no-mapping`, nixin knows no commit for GEN; `not-in-workspace`, the history names commits the workspace's branch does not contain, listed in `candidates[]`); `commit` the commit that builds GEN, `source` `history` or `plan-cache`; `revert_commit` the commit made now (null on a retry that found it made); `reverted[]` `{commit, subject}` |
| `privileged_commands[]`, `backends` | the root commands as run; `{profile, activate, pull}` → `sudo` |
| `switch_log` | the last 20 lines of switch-to-configuration |
| `diff` | the closure diff from the previous system to GEN (null when the system half did not run) |
| `boot`, `reboot_needed` | [the boot block](#the-boot-block) from the running system to GEN, with `boot_entry` |
| `history` | the line appended |
| `timings` | seconds: `prepare`, `commit`, `switch`, `pull`, `total` |
| `warnings[]` | config untouched; the boot block's |

With `--dry-run`: `dry_run: true`, `generation`, `from_generation`, `out`, `switched`
(the system half would run), `commands[]` (as `apply --dry-run`'s; steps `revert`,
`profile`, `activate`, `pull`), `commit` (`{message}` or null), `config`, `running`,
`privilege` (`{profile|activate|pull: {backend, ready, why}}`), `boot`, `reboot_needed`,
`diff`, `duration_s`, `warnings[]`.

Errors (`ok: false`; after the revert commit: `committed`, `generation`,
`privileged_commands[]`): `usage` (GEN not a number, more than one), `config-not-found`,
`not-a-workspace`, `unknown-host`, `host-not-local`, `unknown-generation` (no such
generation, or none before the running one), `nothing-to-apply`, `workspace-dirty`,
`authoritative-missing`, `authoritative-dirty`, `authoritative-diverged`, `boot-space`
(with `boot_space`), `needs-sudo` (with `commands[]`), `switch-failed` (nix-env or
switch-to-configuration failed; the message says whether `/run/current-system` already
is GEN and to run `nixin rollback GEN` again), `verify-failed`, `git-failed` (the revert
commit, or the pull: then GEN already runs), `busy`, `internal`.

Costs: git in the workspace (one commit) and, read-only, in the authoritative tree; no
nix evaluation or build (GEN is in the store); three root commands. Writes: a commit in
the workspace, the system profile, `/run/current-system`, `/boot` (the bootloader step),
the authoritative tree, `installer/history.jsonl`, plan's cache.

### `outdated`

```
nixin outdated [--nixpkgs FLAKEREF|REV | --channel NAME] [--home USER] [--config DIR] [--refresh]
nixin outdated --published [--channel NAME] [--home USER] [--config DIR] [--refresh]
```

What needs updating, two ways. Neither builds anything nor writes the config.

**Lock mode** (the default) answers "what would the next `flake.lock` bump change for
this host". It evaluates the host's `environment.systemPackages` and every Home Manager
user's `home.packages`, plus `boot.kernelPackages.kernel.version`, twice: once as the
flake is locked, and once with `--override-input <input> <target>` (plus
`--no-write-lock-file`). Then it compares the two by pname. These are two package-list
evals (`outdated.nix`); `system.build.toplevel` is never evaluated. The target is:

| argument | target |
|---|---|
| none | the lock's own `original` for the nixpkgs input (`github:nixos/nixpkgs/nixos-unstable` here), as it resolves now: what `nix flake update nixpkgs` would write |
| `--nixpkgs REV` | a commit (7–40 hex) of the locked repository: `github:<owner>/<repo>/REV` |
| `--nixpkgs FLAKEREF` | any flakeref (`github:nixos/nixpkgs/nixos-26.05`) |
| `--channel NAME` | `unstable` → `nixos-unstable`, `NN.MM` → `nixos-NN.MM`, a branch name as is (`nixos-unstable-small`), `stable` → the newest release search.nixos.org lists (one HTTPS call) |

The nixpkgs input is the root input called `nixpkgs`, else the one locked to a `nixpkgs`
repository. Its lock node is resolved through `flake.lock` (`nixpkgs_2` in this config),
never assumed. Only that input moves; inputs that `follows` it move with it.
`nix flake update` without an input name would also move the others (home-manager,
nixos-hardware), and this comparison does not include them.

Comparison, per scope and pname (a package without a pname is keyed by its derivation
name, so it never shows as changed): versions present on both sides cancel out. What is
left pairs up in version order as `changed`. The rest is `removed` (lock side only) or
`added` (target side only). So `fuse` 2.9.9 + 3.18.2 → 3.18.2 is one removal, and a
pname nixpkgs renamed shows as one removal and one addition. `direction` uses nix's
`builtins.compareVersions`.

**`--published`** is brew-outdated style. It compares the installed versions (the lock
side alone, no second eval) with what search.nixos.org's channel index has: `--channel`,
default `unstable`. It sends one `terms` query per 50 pnames (`package_pname`), so 3–6
requests for these hosts. A pname is shared by many attributes (`python3` covers
python3, python314 and python316 at 3.16.0a1; `git` covers emacsPackages.git at
20140128), so the newest version for a pname would be wrong. Instead it compares the
attribute defined in the same nixpkgs file as the installed package (meta.position ↔
`package_position`). Failing that, the same major version; then the attribute named
like the pname; then a top-level one; then the shortest name. This is a heuristic: lock
mode is the exact answer. The kernel is compared with the newest patch of its own series
(`linuxKernel.kernels.linux_X_Y`), with `linux_latest` as `latest`.

Result:

| field | meaning |
|---|---|
| `mode` | `lock` \| `published` |
| `config` | `path`, then as in `status`: `head`, `dirty`, `source`, `source_hash`, `fingerprint`, `last_modified` |
| `lock` | the host's nixpkgs as locked: `input` (its name in flake.nix, what `--override-input` takes), `node` (`nixpkgs_2`), `rev`, `short_rev`, `date`, `last_modified`, `flakeref`, `original` (the lock's `original` as a flakeref), `release` (`system.nixos.release`) |
| `target` | lock mode: `input`, `flakeref` (as asked), `from` (`lock` \| `nixpkgs` \| `channel`), `rev`, `short_rev`, `date`, `last_modified`, `locked` (the fully locked ref given to `--override-input`, with `narHash`), `release`, `days_newer` (target date minus lock date). `--published`: `source` (`search.nixos.org`), `channel` (concrete: `stable` → `26.05`), `index`, `rev` (the commit the index was built from), `short_rev`, `queries`, `cached` |
| `up_to_date` | lock mode: the target commit is the locked one (no target eval; everything empty) |
| `changed[]` | lock mode: `{pname, from, to, scope, user, direction}`. `--published`: `{pname, installed, published, attr, alternatives, channel, rev, scope, user, direction}` (`installed`: the versions, comma-separated when several; `attr`: the index's attribute compared; `alternatives`: how many other attributes share the pname). `scope` is `system` \| `home`, `user` null or the HM user; `direction` `up` \| `down` \| `same` (different text, same order) |
| `added[]`, `removed[]` | lock mode: `{pname, version, scope, user}`. Always `[]` with `--published` |
| `unpublished[]` | `--published`: installed pnames the channel has no package for (local packages, flake inputs' packages) |
| `kernel` | `from`, `to`, `changed`; `--published` also `latest` (`to` is null when the series is gone from the channel) |
| `count` | `len(changed)` |
| `total` | distinct pnames compared (the lock side, the scopes asked for) |
| `scopes[]` | `{scope, user, total, changed}` plus `added`, `removed` in lock mode; system first, then each HM user (only `--home USER`'s with `--home`) |
| `commands[]` | shell lines to take the update. Lock mode: `nix flake update <input> --flake DIR` for the default target, else `nix flake lock DIR --override-input <input> <ref>/<rev>` (writes exactly the commit compared and keeps flake.nix's ref), then `nixin plan`, `nixin apply`. `--published`: `nixin outdated` (the exact answer) first |
| `notes[]` | for a target other than the lock's ref: how to follow it for good (change `inputs.<input>.url`); `--published`: the attribute caveat |
| `cache` | as in `status` (`outdated-lock`, `outdated-target`) |
| `timings` | seconds: `resolve` (the target's `nix flake metadata`), `lock`, `target` (0 when cached), `search` (`--published`), `total` |

Without `--json`: a header (lock → target commit and dates), one aligned `pname  from →
to` table per scope (`(removed)`, `(added)`, `(older)` marked), a summary line (`57 of 337
packages would change; kernel 7.2.12 → 7.2.14`), the notes, and `next:` with `commands[]`.

Errors: `usage` (a positional argument, `--nixpkgs` with `--channel`, `--published` with
`--nixpkgs`), `config-not-found`, `not-a-flake`, `unknown-host`, `unknown-user`
(`--home`), `not-found` (no readable `flake.lock`, or it locks no nixpkgs input),
`bad-channel` (an unknown `--channel`, or a target GitHub does not know: HTTP 404/422),
`network` (resolving the target, or fetching an input for the target eval, failed for
lack of network; with `--published`, search.nixos.org is unreachable), `http-<status>`
(`--published`: a 4xx from search.nixos.org), `nix-failed` (`nix flake metadata` on the
target failed otherwise), `eval-failed` (the lock side as in `status`, or the host does
not evaluate with the target. The message then says the update would break it and
carries nix's error, e.g. `undefined variable 'tuxedo'` at `configuration.nix:420` for
19BK on 26.05), `nix-missing`, `internal`.

Costs: runs nix (no time limit). It reads the config and writes cache files. Network:
the target's `nix flake metadata` asks GitHub for the branch head (nix caches that for
its `tarball-ttl`, 1 h). For a commit it does not have yet, nix downloads the nixpkgs
tarball (about 60 s here). The target eval itself then needs no network. `--published`
makes HTTPS calls to search.nixos.org and evaluates nothing new. See
[Costs](#costs-measured).

### `update`

```
nixin update [--input NAME] [--to FLAKEREF|REV] [--dry-run] [--config DIR]
```

Takes the update `outdated` describes: it runs the command `outdated` prints under
`next:`, in the workspace, and reports the input's locked commit before and after. It
writes `flake.lock` and nothing else: no build, no commit, no switch (`nixin plan`, then
`nixin apply`, as for any other change; `status.pending` shows `flake.lock` as changed).
Like the writer it works only on a workspace clone you own: `/etc/nixos` (also the default
without `$NIXIN_CONFIG`) and trees you cannot write are `not-a-workspace`.

| argument | runs |
|---|---|
| none | `nix flake update <input> --flake DIR`: the input's own ref (flake.nix's `inputs.<input>.url`, the lock's `original`) as it resolves now. What `outdated` compares by default |
| `--to REV` | `nix flake lock DIR --override-input <input> github:<owner>/<repo>/REV`: exactly that commit of the locked repository (`outdated --nixpkgs`/`--channel`'s pin). nix keeps `original`, so the next plain update goes back to flake.nix's ref |
| `--to FLAKEREF` | `nix flake lock DIR --override-input <input> FLAKEREF`, the ref as given (nix locks its current commit) |
| `--input NAME` | another root input of `flake.lock` (default: the nixpkgs input, found as `outdated` finds it) |
| `--dry-run` | nothing: the document says what would run (`command`), with `to` and `changed` null. Reads `flake.lock` only, no nix |

`--host` is not used (the lock is the flake's, shared by every host) except in the
`nixin plan`/`apply` lines of `commands[]`.

Result:

| field | meaning |
|---|---|
| `input`, `node` | the input moved (its name in flake.nix, what `--override-input` takes) and its lock node (`nixpkgs_2` here) |
| `original` | the lock's `original` for it as a flakeref (`github:nixos/nixpkgs/nixos-unstable`) |
| `to_ref` | what it was moved to: `original` (no `--to`), or the `--to` ref as passed to `--override-input` |
| `command` | the nix command line, as a person would type it |
| `from`, `to` | the locked input before and after: `rev`, `short_rev`, `date`, `last_modified`, `nar_hash` (all null for what the lock does not record). `to` is null with `--dry-run` |
| `changed` | whether the locked commit (or narHash) moved; false when the ref still resolves to the locked commit. null with `--dry-run` |
| `dry_run` | `--dry-run` was given |
| `config` | the workspace path |
| `commands[]` | after a change: `nixin plan`, `nixin apply` with `--config`/`--host`; `[]` when nothing changed; with `--dry-run`: `[command]` |
| `timings` | seconds: `nix` (the update command), `total` |

Without `--json`: `nixpkgs (github:nixos/nixpkgs/nixos-unstable): 3ec6363 (2026-10-23) →
0bb2eed (2026-11-01)`, `ran: …`, and `next:` (or "already at …; flake.lock unchanged").

Errors: `usage` (a positional argument; `--to REV` for an input that is not on
github/gitlab/sourcehut), `config-not-found`, `not-a-workspace`, `not-found` (no readable
`flake.lock`, no nixpkgs input, or `--input` names no root input of the lock), `network`
(nix could not fetch the new commit for lack of network), `bad-channel` (GitHub does not
know the ref: HTTP 404/422), `nix-failed` (nix failed otherwise; nix's message),
`nix-missing`, `internal`.

Costs: runs nix once (no time limit) and writes `flake.lock` in the workspace. Network:
nix asks GitHub for the ref's head (cached for its `tarball-ttl`, 1 h) and downloads the
nixpkgs tarball for a commit it has not fetched yet: about 60 s cold, a second or two
when `outdated` already fetched that commit. See [Costs](#costs-measured). The truth and
`outdated` caches are keyed by the flake fingerprint, which includes the lock, so the next
`status`, `list` or `outdated` evaluates the new lock.

### `tui`

```
nixin [--host H] [--config DIR] tui [--channel C]
nixin-tui [--host H] [--config DIR] [--channel C]
```

The interactive terminal UI (Textual). It is a consumer of this contract, not part of it:
every screen renders the documents above, got by calling the verb handlers in-process
(`cli.document`, the same code `main()` runs), with `--host` and `--config` passed on as
given. It writes only through `add`/`rm`/`set`/`unset` (after showing their `--dry-run`
diff) and `update` (after `update --dry-run` and a confirmation), and switches only
through `apply` (after `apply --dry-run` and a confirmation). The Packages page shows
`list` (one call, every scope; its filter and for-whom toggle narrow the loaded rows with
`--filter`/`--scope` semantics, so they cost nothing) and `outdated` (lock mode, or
`--published`); both documents are kept until a write or an `update` makes them stale.

Under `--json`, or when stdin or stderr is not a terminal (Textual draws on stderr, so
`nixin tui 2>file` would show nothing), it is a `usage` error (exit 2). When the person
quits, it prints one line about the session on stdout (`nixin tui: wrote 1: +cowsay (…) ·
not applied yet (nixin plan, nixin apply)`, `nixin tui: applied: generation 113`) and
exits 0. While it runs it sets `GIT_TERMINAL_PROMPT=0` (unless already set) so a git
fetch cannot prompt on the terminal it owns.

Costs: whatever the verbs it calls cost (see each verb). `plan` and `apply` run in human
mode under the TUI so that nix's build output and nixos-rebuild's log stream into its log
view; the document is the same apart from `message`.

### `gui`

```
nixin [--host H] [--config DIR] gui [--channel C] [--self-test]
nixin-gui [--host H] [--config DIR] [--channel C] [--self-test]
```

The desktop UI (Qt 6, PySide6 + QML). Like `tui`, a consumer of this contract: it calls
the same verb handlers in-process, on a thread pool, and renders their documents through
the same view-model (`nixin.ui.view`). It writes only through `add`/`rm`/`set`/`unset`
(after showing their `--dry-run` diff in a dialog) and `update` (after `update --dry-run`
in a dialog), and switches only through `apply` (after `apply --dry-run` and a
confirmation). Its Packages page uses `list` and `outdated` as the TUI's does.

Under `--json` it is a `usage` error, and so it is without a display (none of
`WAYLAND_DISPLAY`, `DISPLAY`, `QT_QPA_PLATFORM` set). `--self-test` loads the window,
draws one frame and exits 0 with `nixin gui: self-test ok (…)`; it calls no verb, so it
runs anywhere Qt does (the package's install check runs it with
`QT_QPA_PLATFORM=offscreen` in the build sandbox). A QML load error is `internal`
(message `QML: …`). When the window closes it prints one line about the session, as
`tui` does (`nixin gui: wrote 1: +cowsay (…) · not applied yet (nixin plan, nixin
apply)`), and exits 0; if a verb is still running it waits for it first. It sets
`GIT_TERMINAL_PROMPT=0` (unless already set).

Costs: whatever the verbs it calls cost. It loads nothing on start (no `status`): search
and its details (`info`, `versions`, `where`) when asked, `status` when Pending or
Status is opened or the Home Manager users are needed, `list` when Packages is opened,
`outdated` when its Updates tab is. `plan` and `apply` run in human
mode, as under `tui`, so their logs stream into the window.

## Error codes

| code | from | when |
|---|---|---|
| `usage` | all | bad command line, or the verb's own argument check; exit 2 |
| `internal` | all | an unexpected exception (a bug, or a source that changed its response shape); the message names the exception and `file:line` |
| `not-implemented` | cli | a verb without a handler (none today; kept for UIs built against the stubs) |
| `needs-sudo` | apply | a root step and `sudo -n` cannot get root without a password; `message` and `commands[]` are what is left to run by hand |
| `network` | discovery, outdated | a source is unreachable, timed out, answered 5xx after retries, or sent non-JSON; `update`: nix could not fetch the new commit |
| `http-<status>` | discovery, outdated | a 4xx the caller may act on (`http-400`, …) |
| `bad-channel` | discovery, outdated | unknown `--channel`, or a bare-rev `--nixpkgs` for a channel-based source; `outdated`, `update`: a target ref GitHub does not know |
| `not-found` | discovery, truth, writer, outdated | `info`/`versions`: no such name. `where`: no such option. `rm`/`unset`: not declared in any module nixin can edit (`hints[]`). `outdated`: no `flake.lock`, or no nixpkgs input in it; `update`: also no such `--input` |
| `nix-missing` | all | `nix` (or `nix-instantiate`) is not on PATH |
| `nix-failed` | discovery, outdated | `nix search`/`nix eval`/`nix flake metadata` on `--nixpkgs` failed or printed non-JSON; `outdated`: `nix flake metadata` on the target; `update`: `nix flake update`/`lock` failed |
| `nix-timeout` | discovery | `nix flake metadata` > 120 s, `nix search` > 900 s, `nix eval` > 300 s |
| `config-not-found` | truth, writer, outdated | no `flake.nix` in the config dir |
| `not-a-flake` | truth, outdated | `nix flake metadata`/`nix flake archive` failed on the config |
| `unknown-host` | truth, writer, outdated | `--host` is not in `nixosConfigurations` |
| `eval-failed` | truth, writer, outdated | the host does not evaluate (assertions, syntax, an untracked file the config imports); `outdated`: or not with the target nixpkgs (the update would break it) |
| `eval-timeout` | writer, apply | a writer eval or plan's toplevel eval took longer than 900 s (truth evals have no limit) |
| `not-a-workspace` | writer, apply, outdated (`update`) | `--config` is `/etc/nixos` (also the default without `$NIXIN_CONFIG`), is not writable, or an edit would leave the tree; apply also: not the top of a git checkout, HEAD detached, or the authoritative tree itself |
| `unknown-user` | truth, writer, outdated | `--home USER` is not an HM user of the host |
| `unknown-package` | writer | `add`: not an attribute of the host's `pkgs` |
| `invalid-nix` | writer | the value is not well-formed Nix (syntax only) or a name does not parse, or an edited file would not parse (e.g. a variable it does not bind; then `file`) |
| `ambiguous` | writer | several declarations match; `candidates[]`, repeat with `--where` |
| `import-site-missing` | writer | no module or no `imports = [ … ];` to import the generated file from; `line` to add |
| `file-conflict` | writer | an `installer/` file exists that nixin did not generate |
| `invalid-state` | writer | `installer/state.json` is not valid JSON or has the wrong version |
| `build-failed` | apply | `nix build` of the toplevel failed; the message is the failing builder's block with nix's log tail |
| `nothing-to-apply` | apply | no uncommitted change, the authoritative tree has the workspace HEAD, and it is what runs; `rollback`: GEN runs, is the boot default, and the config builds it |
| `host-not-local` | apply | `rollback`, `history` with `--host H` for a host that is not this machine (`apply` goes there over ssh instead) |
| `authoritative-missing` | apply | no git checkout at the authoritative path |
| `authoritative-dirty` | apply | the authoritative tree has uncommitted changes (`files[]`); nixin only fast-forwards it |
| `authoritative-diverged` | apply | root's `git pull --ff-only` could not take the workspace (`state`: `behind`, `diverged`, `unknown`); the message has the commands to fix it |
| `switch-failed` | apply | `nixos-rebuild` (or the `nixos-switch` helper) failed, or the helper is already running; `rollback`: nix-env or switch-to-configuration failed |
| `verify-failed` | apply | after the switch the system is not the out path nixin built |
| `git-failed` | apply | a git command failed (or git is not on PATH); the message has git's last lines |
| `diverged` | apply (`sync`) | `--pull` cannot fast-forward, or `--push` was rejected |
| `busy` | apply | another `nixin apply` or `rollback` holds the lock |
| `boot-space` | apply | `apply`/`rollback`: the new system's kernels and initrds do not fit on /boot (`boot_space` has the numbers); nothing ran as root |
| `unknown-generation` | apply | `rollback GEN`: no such system generation, or none before the running one |
| `workspace-dirty` | apply | `rollback`: the workspace has uncommitted changes (`files[]`) or unapplied commits (`commits[]`) its revert would sweep in or drop |
| `needs-tty` | apply | `apply --host H` for another host, not `--dry-run`, under `--json`, from a UI (a worker thread) or without a terminal: the sudo on H may ask for a password on this terminal |
| `push-failed` | apply | `apply --host H` for another host: no remote to push the workspace branch to, or the push failed (`rejected: true`: the hub has commits the workspace lacks) |
| `ssh-failed` | apply | `apply --host H` for another host: `ssh` is missing, could not connect (exit 255), or the session ended otherwise; `committed`, `pushed` say how far it got |
| `invalid-hosts-config` | apply | `~/.config/nixin/hosts.toml` (`$XDG_CONFIG_HOME/nixin/`) does not parse, or a value is of the wrong type or not a usable path / ssh destination |

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

The `history`/`rollback` rows and the boot check are from 2026-11-02 on mbp13 (load 0.9),
the real workspace and `/etc/nixos`: one cowsay apply (gen 121: 101 s, build 72 s,
switch 30 s) and the rollback to gen 120.

The `outdated` rows are from 2026-11-02 on mbp13, quiet (load 0.4–0.7), against `~/nixos`
(lock nixpkgs 3ec6363, target nixos-unstable 0bb2eed): nixin's cache empty, nix's own
fetcher cache warm unless said otherwise. The 65 s run had nix's cache empty too (load 2.4).

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
| `list` | — | yes (the package lists) | cache | 2.5 s (mbp13) / 3.2 s (19BK: system + brucking in one eval); quiet box (load 1.5), 2026-11-02 | 0.36–0.41 s | 340–450 MiB (cold), 42 MiB (warm) |
| `list --diff A B` | — | yes (both hosts' package lists, one after the other) | cache | 4.9 s (19BK 2.7 s + mbp13 1.9 s; load 0.3, 2026-11-02) | 0.33 s | 452 MiB (cold), 42 MiB (warm) |
| `add --dry-run --no-eval` | — | parse only | — | 0.8 s | — | 36 MiB |
| `add --dry-run` | — | 1 small eval | — | 1.7 s | — | 200 MiB |
| `add`, `rm` (NixOS) | — | 1–2 evals | workspace + git index | 5–7 s (2.4–3.3 s at load 2.6) | — | 350 MiB |
| `add`, `rm --home` (HM) | — | 1–2 evals | workspace + git index | 8–10 s | — | 440 MiB |
| `set`, `unset` | — | 1 eval | workspace + git index | 3–3.5 s | — | 200 MiB |
| `plan` | — | nix eval + build | nix store, plan cache | **38 s** no-op tree (eval 32 s, build 5 s); **85 s** with cowsay (39 derivations: three `system-path`s, units, etc); 37 s for `configurationLimit` | 0.6–1.1 s (cached; also a tree that only adds `history.jsonl`) | — |
| `apply` | — | git, nix (unless cached), sudo | workspace commit, authoritative tree, system profile, /boot | **30–31 s** after `plan` or for a tree plan's cache knows: build 0.4–0.9 s (cache), pull 0.05 s, `nixos-rebuild switch` 29–30 s (root's own eval + activation) | `nothing-to-apply` 0.2 s | — |
| `apply --dry-run` | — | `nix flake metadata`, `sudo -n true` | — | 1.3 s | — | — |
| `apply --host H` (another host) | yes: the push to the hub, ssh to H | git here; on H git, sudo, `nixos-rebuild` | workspace commit, the hub's branch, H's workspace, authoritative tree, system profile and /boot | not measured: H builds its own system (nixos-rebuild's time there), plus the push and the ssh setup | — | — |
| `apply --host H --dry-run` | — | git (read only); no nix, no ssh, no sudo | — | 0.29 s (19BK from mbp13, 2026-11-02) | — | 36 MiB |
| boot check (in `plan`, `apply`, `rollback`) | — | — (reads two toplevels, the profile's generations' kernel links, the installer scripts up to the builder, `statvfs /boot`) | — | under 0.1 s | — | — |
| `history` | — | — | — | 0.2 s (35 generations) | — | — |
| `rollback --dry-run` | — | `sudo -n true`, `nix store diff-closures` | — | 1.3 s | — | — |
| `rollback` | — | git, sudo (no nix eval or build: the generation is in the store) | workspace commit, system profile, /boot, authoritative tree | **2.9 s** (switch-to-configuration 1.7 s, pull 0.05 s); `nixos-rebuild`'s root eval is skipped | `nothing-to-apply` 0.2 s | — |
| `sync` | yes (the remote) | git | workspace refs | 1.3 s fetch from GitHub; 0.4 s push to a local bare repo | — | — |
| `outdated` | GitHub via nix (the target branch head; the nixpkgs tarball once per new commit) | 2 package-list evals + `nix flake metadata` | cache | **4.3 s** mbp13, **6.0 s** 19BK (evals 1.9 + 2.0 s, 2.7 + 3.0 s); **65 s** when the target is a commit nix has not fetched (the tarball: resolve 58 s) | 0.37 s (`--refresh`: 4.4 s, the branch re-asked) | 410 MiB mbp13, 540 MiB 19BK |
| `outdated --published` | yes (search.nixos.org: 3–6 queries) | the lock-side eval only (shared with lock mode) | cache | 1.1–1.2 s with the eval cached, 1–3 s for the queries; + 2–3 s for a cold eval | 0.33 s | 43 MiB (+ the eval's) |

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
| `truth/<host>-<key>-<name>.json` | truth evals: `toplevel`, `packages` (shared by `status`, `where` and `list`), `prefix-<scope>-<hash>`, `options-<scope>` (the index, ~10 MB), `options-<scope>-summary`; `outdated`'s `outdated-lock` and `outdated-target` (the target's key also has the target commit) | keyed by nix's flake fingerprint (tracked tree + lock) and host: any tracked edit misses; older copies of the same name are pruned |
| `truth/exclude-<nixpkgs12>-<hm12>.json` | options the full index skips (their definitions throw), per (nixpkgs rev, home-manager rev), seeded | until deleted; delete it to re-learn |
| `apply/out-<host>-<source-hash>.json` | plan's build: `{out, drv}` per flake source store path (a content hash of the tracked tree as nix copies it, dirty edits included) | last 40 per host; an entry whose `out` was garbage-collected counts as a miss |
| `apply/commit-<host>-<sha>.json` | the same, per commit (written by `plan` on a clean tree, by `apply`, and by `rollback` for the authoritative HEAD it leaves); `status.pending`, `nothing-to-apply` and rollback's commit lookup read it without nix | last 40 per host |
| `apply/apply.lock` | `flock` held while `apply` or `rollback` runs (`busy`) | — |

Flags: discovery `--refresh` ignores cached answers, refetches and stores; `--no-cache`
neither reads nor writes. Truth `--refresh` skips reads and still writes the fresh
results. `outdated --refresh` does the same for its evals and search.nixos.org answers, and
passes `--refresh` to the target's `nix flake metadata` (re-asks GitHub for the branch head). The writer has no cache. Plan's cache has no flag: an entry only answers when
its out path is still in the store, and the build it saves is deterministic. nix keeps its own caches (`~/.cache/nix`, the store).
Those make a second cold-nixin run of the same eval faster, and nixin does not manage them.
