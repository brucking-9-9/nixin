# nixin CLI contract

The one reference for UI authors (TUI, GUI, agents) who drive `nixin --json`. It covers
the envelope, every verb's arguments and result fields, every error code, exit codes,
what each verb costs (network, nix, disk writes, time) and where the caches are.
README.md is the overview and points here. When code and this file disagree, it is a bug
in one of them. `tests/test_integration.py` checks the envelope rules and the error-code
list against this file.

Status: all four layers are implemented: discovery, truth, writer and apply (`plan`,
`apply`, `sync`, `history`, `rollback`), plus `outdated`/`update`, `explain` (which
joins truth and discovery), `try`, and the AppStream layer (`info`'s `appstream`, `search
--appstream`, `browse`).

## Invocation

```
nixin [--json] [--host H] VERB [ARGS...]
nixin --version | --help | VERB --help
```

| verb | layer | does |
|---|---|---|
| `search`, `info`, `versions` | discovery | what exists upstream, in which versions; `info` adds a package's AppStream data (`appstream`), `search --appstream` marks GUI apps |
| `browse` | appstream | applications by freedesktop category: installed, a shipped list of well-known ones, search.nixos.org's most popular |
| `status`, `where`, `list` | truth | what this config gives a host (it evaluates the config flake); `list --diff A B`: two hosts compared |
| `explain` | explain | one option or package: the config's side (truth) joined with upstream's docs (discovery) |
| `add`, `rm`, `set`, `unset` | writer | edit a user-owned workspace clone of the config |
| `plan`, `apply`, `sync` | apply | build + closure diff + boot check; commit, pull into the authoritative tree and switch (root); for another host: push to the hub, pull and switch there over ssh; git sync with the hub |
| `history`, `rollback` | apply | this machine's system generations with the commits nixin applied; switch back to one (root) and revert the config with it |
| `generations`, `gc` | apply | the system generations with their closure sizes, boot entries and what `gc` would drop; delete old ones, rewrite the boot menu, collect the garbage (root) |
| `outdated` | outdated | what a nixpkgs lock update would change for a host (two package-list evals); `--published`: installed versions against a search.nixos.org channel |
| `update` | outdated | take it: move the nixpkgs input in the workspace's `flake.lock` (the command `outdated` prints); no build, no switch |
| `try` | try | build a package from the host's locked nixpkgs into the store without installing it, then run it (`nix run`) or a shell with it (`nix shell`); `--json`: build and report only |
| `doctor` | doctor | read-only health check of this machine: option indexes, the timers and their state, the trees, hosts.toml over ssh (`--full`), the hand-installed secrets the config names, /boot, failed units |

- `--json` and `--host H` are global. They go before the verb, or after it, in any order
  with the verb's own flags (`nixin where kitty --host 19BK --json`).
- `--host` names `nixosConfigurations.<H>`. The default is this machine's hostname
  (`socket.gethostname()`) when the config (`--config`, else `$NIXIN_CONFIG`, else
  `/etc/nixos`) has a host of that name. When it has not, and the config has exactly one
  host, that host is the default. With several hosts and none of them this machine,
  nixin never picks one: the verb fails with `unknown-host`, whose message lists the
  hosts and says to pass `--host`. Finding the default costs a text scan of `flake.nix`,
  plus one `nix eval` of the host names when the scan does not find this machine.
  Discovery uses it only to decide whether this machine's [option index](#option-indexes)
  answers (one built for another host does not), and echoes it in the envelope (as this
  machine's hostname, nothing read).
- `--config DIR` (truth, writer, apply verbs, `outdated`, `update` and `try`; before or after the
  verb, the verb's own wins; discovery ignores it) is the config flake: `--config`, else
  `$NIXIN_CONFIG`, else `/etc/nixos`. Truth, `outdated` and `try` only read it (`try`:
  only its `flake.lock`). The writer, apply
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
- `NIXIN_INDEX_NIXOS` / `NIXIN_INDEX_HOME` name other option index files than
  `/etc/nixin/options-nixos.json` / `$XDG_CONFIG_HOME/nixin/options-home.json`;
  `NIXIN_NO_INDEX=1` ignores both (see [Option indexes](#option-indexes)).

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
| `ok` | yes | `true`: the verb did what was asked. `false`: `error` says why (the writer's `eval-failed` after a write is the one failure that also carries the verb's result fields) |
| `verb` | yes | the verb, or `null` for a usage error where no verb was recognised (`nixin --json`, `nixin --json frobnicate`) |
| `host` | yes | the `--host` value (default: this machine's hostname, or the config's only host; see Invocation) |
| `error` | when `ok` is false | `code` (a slug from [Error codes](#error-codes)), `message` (one human sentence, may contain newlines from nix), and for some writer codes extra fields: `hints[]`, `candidates[]`, `file`, `line`, `undo[]`, and `type-mismatch`'s `expected`, `type_info`, `default`, `example`, `option`, `value` |

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

## Option indexes

nixin's modules (`nixosModules.default`, `homeModules.default`; README, "Modules") write
an index of every option of the system, and of each Home Manager user that imports the
HM module, when the generation is built (`lib/options-index.nix`):

| file | scope | written by |
|---|---|---|
| `/etc/nixin/options-nixos.json` | `nixos`: the system's options | `programs.nixin.optionIndex.enable` in the NixOS module (default on) |
| `$XDG_CONFIG_HOME/nixin/options-home.json` | `home`: this user's Home Manager options | the same option in the HM module (`xdg.configFile`) |

One JSON object (`builtins.toJSON`: compact, keys sorted): `format`
(`"nixin-options/1"`), `scope`, `host` (`networking.hostName`; for HM the NixOS host when
HM runs as a NixOS module, else null), `user` (HM: `home.username`; NixOS: null),
`system`, `nixpkgs` = `{version, release, revision}`, `home_manager` = `{release,
revision}` or null, `nixin` (the package version), `roots[]` = `{source, path}`, `count`,
and `options[]`. Each option, from `lib.optionAttrSetToDocList` (so submodule options
are templates: `users.users.<name>.home`, `swapDevices.*.size`; the submodules' own
`_module.*` options are left out):

| field | meaning |
|---|---|
| `name`, `loc[]` | the option path; for HM as a NixOS module without the `home-manager.users.<name>.` prefix |
| `type` | the type's description (`boolean`, `list of package`, …) |
| `description` | Markdown as the module wrote it, or null |
| `default`, `example` | the literalExpression (or literalMD) text, or null |
| `declarations[]` | `{source, file, line}`: `source` = `config` (the config's own files, relative to its root), `nixpkgs`, `home-manager`, `nixin`, or `other` (`file` is then the full path); `line` from the option's declaration position, null for templates and for helper-made options (aliases), which name the declaring module instead |
| `readOnly`, `internal`, `visible` | as declared |
| `errors[]` | only when a field threw while the index was built: those fields are null |

The config's root is `programs.nixin.optionIndex.configRoot`, else the store path (a
flake's source) or `/etc/nixos` of the file that sets `programs.nixin.enable`.

**Which generation.** An index belongs to the generation it was built with. nixin finds
it: the system generation whose `etc/nixin/options-nixos.json` is the same store file
(the running one first: `fresh`), and for Home Manager the generation behind
`$XDG_STATE_HOME/home-manager/gcroots/current-home` (`fresh` when its `home-files` link
the same file; its number from `$XDG_STATE_HOME/nix/profiles/home-manager-N-link`). The
`generation` block is `{kind: "system" | "home-manager", number, path, date, fresh}`
(`fresh` null when Home Manager has no activation on record). An index is used only
for the host it was built for (`--host`; a standalone HM index, which names no host, for
this machine's hostname) and, for HM, for the user running nixin.

**Who reads it.** `search --kind opt|hm` and `info` answer from the index ([`search`](#search));
`where OPTION` takes each option's documentation from it and skips the scopes it rules
out ([`where`](#where)); `status` reports both files ([`status`](#status)). Values never
come from an index: `where` and `status --full` still evaluate the config for them (and
the exclusion list stays theirs). `--no-index` (search, info) or `NIXIN_NO_INDEX=1`
turns it off. Reading costs one JSON parse per process (0.16 s for mbp13's 13 MB); a
header read (`status`) does not parse the list.

## Verbs

Each verb lists its arguments, its result fields (besides `ok`/`verb`/`host`), its error
codes, and its costs. "Network" means nixin's own HTTPS calls. Any verb that runs nix may
also make nix fetch flake inputs that are not in the store yet. Timings are in
[Costs](#costs-measured).

### `search`

```
nixin search QUERY [--kind pkg|opt|hm|program|fn|all] [--channel C] [--limit N]
                   [--nixpkgs FLAKEREF] [--no-index] [--appstream] [--refresh | --no-cache]
```

| argument | meaning |
|---|---|
| `QUERY` | free text; a leading `-` needs `--` before it (`search -- -grep`) |
| `--kind` | `pkg` (default), `opt` (NixOS options), `hm` (Home Manager options), `program` (which package ships a binary: `rg` → ripgrep), `fn` (lib/builtins, from noogle; not part of `all`), `all` = pkg + opt + hm + program |
| `--channel` | `unstable` (default), `stable` (newest release), `NN.MM`, a branch name (`nixos-25.11`) or a branch flakeref |
| `--limit` | rows per kind, default 20, at least 1 |
| `--nixpkgs` | packages come from `nix search --json` on that exact nixpkgs (the `local-nix` source), typically the host's locked rev. A branch-named ref also picks the channel for options; a bare rev does not (pass `--channel`, else options and HM fail with `bad-channel` while packages still answer) |
| `--no-index` | options (`opt`, `hm`) from upstream even when this machine has an [option index](#option-indexes) for `--host`. `--channel` and `--nixpkgs` imply it |
| `--appstream` | every `pkg` row gets an `appstream` object (see [`info`](#info)) from search.nixos.org's desktop data, asked for in the same request, else nixin's shipped list of well-known apps; no nix, no other request. Its `gui` marks GUI apps: `true` (a listed desktop entry, or the shipped list), `false` (no programs at all: nothing to launch), `null` (unknown) |

**The option index first.** When this machine has an index for `--host` (`opt`: the
system's; `hm`: the user's), `opt` and `hm` are answered from it: source `index`,
`channel` null, `rev` the generation's nixpkgs commit, `cached` true, no network. The
index ranks like the Home Manager source (exact path, leading prefix, whole segments,
substring, description), then shallower paths first, and leaves out internal and hidden
options. An index with no hit falls through to upstream with a note ("nothing in this
machine's option index; asked …").

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
  `total` (local-nix: the system searched, the hit count before `--limit`), `indexes`
  (`index`: `{nixos|home: {scope, path, store_path, count, host, user, nixpkgs,
  home_manager, generation, fresh}}`, the files that answered).
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
| `opt`, `hm` from `index` | the `opt`/`hm` fields (`declarations[]` relative to their source, `declaration_urls[]` on GitHub at the generation's revision with `#L<line>`, nothing for the config's own files; `description` as Markdown, `read_only` for both), plus `locations[]` (`{source, file, line}`), `in_config` (declared by the config itself), `scope`, `user`, `generation` |

Errors: `usage`, `bad-channel`, `network`, `http-<status>`, `nix-missing`, `nix-failed`,
`nix-timeout`, `internal`. No hits is `ok: true` with `count: 0`, not an error.

Costs: network (search.nixos.org, the HM option JSON, noogle) except `--kind pkg
--nixpkgs`, which runs nix only (`nix flake metadata`, `nix search`), and options the
option index answers (no network, no nix). Writes cache files only.
`--appstream` adds no request (search.nixos.org returns the desktop fields with the
rest; a few KiB more per row).

Why `gui` is often `null`: search.nixos.org (index generation 52 and later; 51 and older
have no desktop data at all) has desktop entries only for packages whose outputs its
indexer read. Unfree packages and wrappers come back empty: firefox, chromium,
thunderbird, steam, lutris, heroic, discord, spotify, vscode, obsidian, zotero, bottles,
kicad (checked 2026-11-02). nixin's shipped list (`appstream/curated.py`, about a hundred
well-known apps) covers the best known of those; anything else without desktop data is
`null`, and "has binaries" is not taken to mean "has a GUI".

### `info`

```
nixin info NAME [--kind pkg|opt|hm|fn|all] [--channel C] [--nixpkgs FLAKEREF] [--no-index]
                [--appstream local|fetch|hint|off] [--config DIR] [--refresh | --no-cache]
```

Exact-name lookup. `--kind` defaults to `all` = pkg + opt + hm (no `program`; `fn` only
when asked). `--channel`, `--nixpkgs` and `--no-index` work as for `search`: options come
from this machine's option index when it has NAME (hidden and internal options too),
else from upstream. With `--nixpkgs`, the
package comes from `nix eval` on that rev (about 0.5 s once the rev is in the store) and
gains `broken`, and `unfree` becomes a bool.

Package rows carry `appstream`: the package as an application, from the AppStream
metainfo (`share/metainfo/*.xml`, `share/appdata/*.xml`) and desktop entries
(`share/applications/*.desktop`) its output ships. Where nixin looks, by `--appstream`:

| mode | looks at |
|---|---|
| `local` (default) | 1. an installed output: a store path named after the package (`pname`, or `pname-unwrapped`, where wrappers keep their metainfo) that a profile links desktop entries or metainfo from (`/run/current-system/sw`, `/etc/profiles/per-user/*`, `~/.nix-profile`, `~/.local/state/nix/profile`); no nix. 2. the package's outputs in the locked nixpkgs (`--nixpkgs`, else the nixpkgs input of the config's `flake.lock`: `--config`, `$NIXIN_CONFIG`, `/etc/nixos`): `nix eval` of their out paths (unfree, insecure and broken packages again with `--impure` and `NIXPKGS_ALLOW_*`: the paths do not depend on it), read if they are in the store. Nothing is downloaded or built. 3. search.nixos.org's desktop data for the row (the hint) |
| `fetch` | as `local`, and when the outputs are not in the store: `nix build --dry-run` first; if nothing has to be built and the download is at most 50 MiB, `nix build --no-link --print-out-paths --max-jobs 0 --builders ''` substitutes them from the binary cache and they are read (they stay in the store, unrooted, until the next GC) |
| `hint` | search.nixos.org's desktop data only; no nix, no profiles |
| `off` | no `appstream` field (`info` as before this field existed) |

`appstream` (the files win over the hint field by field; the hint adds the icon URL and
search.nixos.org's screenshot copies):

| field | meaning |
|---|---|
| `source` | `installed`, `store` (read from an output), `search.nixos.org` (its desktop data), `curated` (only nixin's list of well-known apps knows it), or null (nothing) |
| `gui` | `true`: a desktop entry a launcher lists (or a `desktop-application` component, or the shipped list); `false`: the outputs read have neither, or the package has no programs; `null`: unknown |
| `store_path` | the output read (null for the hint) |
| `installed_for` | `installed` only: the profiles that link it (`system`, user names) |
| `id`, `name`, `summary`, `description` | the AppStream component id (else the desktop file's name), its name and summary (else the desktop entry's `Name`, `Comment`), the description as plain text (paragraphs, `• ` list items) |
| `categories[]` | AppStream `<categories>` then the desktop entry's `Categories=`, as written (main and additional categories, toolkit names included) |
| `icon` | null or `{name, path, url}`: the icon name; `path` an SVG or PNG in the output's `share/icons` (hicolor first, scalable then the largest) or `share/pixmaps`, null when the output has none (a theme icon); `url` search.nixos.org's copy (`https://search.nixos.org/icons/…`) or null |
| `screenshots[]` | `{url, caption, default, thumbnail, large}`: the upstream URL from the metainfo, and search.nixos.org's small WebP copies when it has them (null otherwise); the default one first |
| `license` | the metainfo's `project_license` (SPDX expression), or null |
| `launchable` | the desktop file id (`org.kde.konsole.desktop`) |
| `homepage` | the metainfo's homepage URL |
| `desktop_ids[]` | the listed desktop entries in the output |
| `files[]` | the files read, relative to `store_path` |
| `notes[]` | why there is not more: `not in the local store (…)`, `no locked nixpkgs to look in (…)`, `the out paths: nix-failed: …`, `not fetched: 6 path(s), 690.9 MiB to download (the cap is 50.0 MiB)`, `fetched 1 path(s), 323.2 KiB, from the binary cache`, `this search.nixos.org index has no desktop data …` |

AppStream problems never fail `info`: they end up in `notes`. nixpkgs' kitty ships a
desktop entry and icons but no metainfo, so `info kitty` has no description, license or
screenshots there; konsole, steam, gnome-mines and others have them.

Result: `name`, `kind`, `results[]` (usually one; a name can be both a package and an
option), `count`, `source`, `channel`, `rev`, `cached`, `sources`. Package rows add
`matched_by` (`attr` or `pname`). With `pname`, rows also get `alternatives[]` (the other
attrs with that pname); an `attr` match leaves that field out.

Errors: `not-found` (none of the asked kinds has NAME; the message also lists the kinds
whose sources failed), and the `search` codes.

Costs: as `search`, plus for `--appstream local` (the default) a scan of the profiles'
desktop entries (~50 ms) and, unless an installed output answers, one `nix eval` of the
package's out paths on the locked nixpkgs (about 3 s, 380 MiB, then cached 30 days);
`fetch` adds `nix build --dry-run` and the substitution (seconds to tens of seconds). See
[Costs](#costs-measured).

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

### `browse`

```
nixin browse [CATEGORY] [--channel C] [--limit N] [--refresh | --no-cache]
```

Applications by freedesktop main category. CATEGORY is a key (`AudioVideo`, `Development`,
`Education`, `Game`, `Graphics`, `Network`, `Office`, `Science`, `Settings`, `System`,
`Utility`), its label (`Games`, `Internet`, `Utilities`, …) or an alias (`audio`, `video`,
`games`, `dev`), any case. `AudioVideo` also takes the `Audio` and `Video` categories.

Without CATEGORY: `category` (null), `categories[]` = `{key, label, matches[], installed,
curated, published}` (apps installed on this machine in it, by pname; how many the
shipped list names; how many packages search.nixos.org has in it, null when it could not
say), `installed` (installed apps in all), and `source`, `channel`, `rev`, `cached`,
`sources`.

With CATEGORY: `category` = `{key, label, matches[]}`, `results[]`, `count`, `limit`, and
`source`, `channel`, `rev`, `cached`, `sources`. The rows are `pkg` rows (the
search.nixos.org fields, so `info`/`add` take their `name`) with `appstream` (the hint, or
for installed apps their desktop entry and its icon file), `installed`, `installed_for[]`,
`curated` (on the shipped list) and `listed_by[]` (`installed`, `curated`,
`search.nixos.org`, in the order they found it). Order: the installed apps (by name, the
attribute of the wrapper when the desktop entry comes from `x-unwrapped`), then the
shipped list's in its order, then search.nixos.org's packages in the category, most
popular first (repology), at most `--limit` (default 30). One row per application: a row
whose attribute, pname, AppStream id or app name is already listed only adds to
`listed_by` (so `firefox-esr`, `nethack-qt` do not repeat their app).

`sources` has `search.nixos.org` (`{kinds, channel, rev, cached, count, error}`),
`installed` (`{count}`, and `profiles[]` without CATEGORY) and `curated` (`{count}`). Without
the network `ok` stays true: the installed apps and the shipped names (no version, no
summary) answer, and `sources["search.nixos.org"].error` says why.

Limitation, stated plainly: search.nixos.org's categories come from the desktop entries
its indexer read, which it does not have for unfree packages and wrappers (see `search
--appstream`). The shipped list exists for those; it is about ten names per category,
checked against nixpkgs on 2026-11-02, and a name that leaves nixpkgs drops out (it is
looked up, not trusted).

Errors: `usage` (unknown category, `--limit` below 1), `bad-channel`, `internal`; network
failures only in `sources`.

Costs: no nix. The profiles' desktop entries (~50 ms); one search.nixos.org request without
CATEGORY (an aggregation), two with one (the shipped and installed names; the category).
Cached as searches (1 h). 0.6–0.8 s cold, 0.3 s warm.

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
| `lock_change` | the nixpkgs lock move that is not applied yet, or null: `{from, to, committed, running}`. `from`/`to` are `{rev, short_rev, date, last_modified, nar_hash}`; `committed: false` = the working `flake.lock` differs from `HEAD:flake.lock` (an `update` not committed yet: `from` is the committed node; `nixin update --undo` takes it back), `committed: true` = the lock is committed but the authoritative tree's `flake.lock` still has `from` (not applied yet); `running` says whether `from` is what the running system was built from (`nixos-version`'s short rev; null when unknown or `--host` is another machine). No nix: git and the file system only |
| `eval` | `ok` (always true here; a host that does not evaluate is an `eval-failed` error instead), `toplevel` (drvPath), `seconds` |
| `hm_users[]` | Home Manager users of the host |
| `packages` | counts: `system`, `home.<user>` |
| `programs` | names with `programs.<x>.enable = true`: `system[]`, `home.<user>[]` |
| `options` | per scope (`nixos`, `home.<user>`): `total`, `defined` (set beyond the option's own default), `in_config` (defined in a file of the config tree), `excluded`, `definitions_error`, `seconds`. Without `--full`: the cached counts of an earlier `--full`, else `{}` |
| `indexes` | this machine's [option indexes](#option-indexes), no nix: `nixos`, `home`, each `{path, present}` and, when present, `scope`, `store_path`, `count`, `host`, `user`, `nixpkgs`, `home_manager`, `generation`, `fresh` (it is the running generation's), `applies` (built for `--host`, and for HM for the user running nixin); `home.user` always; `disabled: true` under `NIXIN_NO_INDEX` |
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
(the real option), `type` and `description` (the real option's), `type_info`,
`default`, `example` and, in `sub` mode when NAME is a declared sub-option,
`sub_description` (these four describe NAME as asked: in `sub` mode the type is followed
through the option's type, i.e. a submodule's options or freeform type, `attrsOf`
elements, `nullOr`; `type_info` is null where it cannot be followed). `type_info` is
the type as a tree: `name` (nixpkgs' type name: `bool`, `str`, `enum`, `listOf`,
`attrsOf`, `nullOr`, `either`, `submodule`, `unsignedInt16`, …), `description` (the
manual's wording), `values[]` for an `enum`, `nested` (`elemType`, `left`/`right`,
`coercedType`/`finalType`, `freeformType`: each again a `type_info`). `default` and
`example` are text: Nix source (the module's `literalExpression`/`defaultText`, else
the value rendered as Nix with the placeholders below), or Markdown for a `literalMD`;
null when there is none or it does not evaluate. Then `isDefined`, `highestPrio`, `value` (JSON,
size-capped), `value_found` (sub mode), `value_error` (when the value does not
evaluate), `declarations[]` (`source` = `config` | `nixpkgs` | `home-manager` | another
input | `inline` | `other`; `file` relative to that source, a directory module mapped to
its `default.nix`; `line`/`column`; `raw` store path) and `definitions[]`. Each
definition has the same location fields, `value` (what that file contributes), and for
config files `line`, `line_heuristic: true` and `text` (from a text scan). `inline`
definitions (attrsets written inside the `nixosSystem` call) get `candidates[]` instead.

With an [option index](#option-indexes) for the scope (this machine, `--host` it was built
for; HM: the user running nixin), each option also has `docs` = `{source: "index", type,
description (whole, Markdown), default, example, read_only, declarations[] ({source,
file, line, url}), in_config}`, and the document has `index[]` (the indexes used, as in
`status.indexes`). `skipped_scopes[]` = `{scope, user, reason}`: scopes not evaluated
because the running generation's index of that scope has no option at, above or under
NAME while another scope's index has it (only fresh indexes count; a scope without one
is always evaluated). Both are empty lists without an index. `docs` describes the real
option (`option`), not a path inside it; next to the eval's `default`/`example` above it
is the same text from the generation the index was built with. The human output shows
the eval's `default`/`example` and falls back to `docs`' only for the option itself, and
prefers `docs.description` (whole) to the eval's first paragraph.

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
(an option; none for a skipped scope) or per list option plus each matching
`programs.x.enable` (a package). Reads the config (and greps it) and the option indexes,
writes cache files. A few seconds cold, under a second warm.

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

### `explain`

```
nixin explain NAME [--kind option|package] [--home USER] [--config DIR] [--channel C]
                   [--offline] [--refresh]
```

Everything nixin knows about one option or package, in one document: the config's side
(truth: this host's eval) joined with upstream's documentation (discovery), both asked
at the same time. A NAME with a dot (or `--kind option`) is an option; an option name
that no side knows is tried as a package (`python3Packages.requests`) unless `--kind`
or `--home` was given. Upstream is search.nixos.org (NixOS options, packages) and the
Home Manager option index, on `--channel`, else the channel the config's nixpkgs lock
follows (`nixos-unstable` → `unstable`), else `unstable`. `--offline` skips upstream;
`--refresh` skips both sides' caches.

Either side may be missing: no config (`config-not-found`), a host the config does not
have, an eval error, no network. The document then has what the other side knows, and
says what is missing in `sources` and `view.notes`. Only when neither side can answer is
it an error: the config's error when upstream was not reached either (or `--offline`),
else `not-found`.

Result for an option: `name`, `kind: "option"`, `type` (the type's description),
`type_info` (as in `where`), `default`, `example` (Nix text; the config's eval first, as
it is exact for the locked nixpkgs, else upstream's), `description` (upstream's full
text, else the eval's first paragraph), `read_only`, `scopes[]` (every tree of the host
that has NAME: `nixos`, then each HM user, or only `--home USER`; each `scope`, `user`,
`mode` (`exact`/`sub`), `option` (the real option), `value` (JSON), `value_found`,
`value_error`, `declarations[]` (as in `where`, plus `url`: the file on GitHub at the
locked revision and line, for an input locked as `github:`), `definitions[]` (`source`,
`file`, `line`, `value`)), `declarations[]` (the first scope's, or upstream's
`{source, file, url}` without a config), `upstream` (the discovery row, `kind` `opt` or
`hm`, or null), `sources`, `view`.

Result for a package: `name`, `kind: "package"`, `description`, `homepage`, `license[]`,
`programs[]` (binaries), `installed[]` (as `where`'s: `scope`, `user`, `name`, `pname`,
`version`), `channel_version` (search.nixos.org's on the channel), `declared[]` (as
`where`'s), `enabled_by[]` (the enabled `programs.NAME.enable` that bring it in, shaped
like `where`'s `programs[]`), `mentions[]` (the first 5), `position` (`file:line` in
nixpkgs), `source_url` (that line on GitHub at the channel's revision), `upstream`,
`sources`, `view`.

`sources`: `config` = `{ok, config, error, cache}` (`error` null or `{code, message}`;
`cache` as in `status`); `upstream` = `{ok, channel, sources: {<source>: {channel, rev,
cached, error}}}` (or `skipped: "--offline"`).

`view` is the display form, for a help pane: `title`, `subtitle` (`NixOS option`, `Home
Manager (USER) option`, `NixOS and Home Manager (USER) option`, `package`, `package (not
installed)`), `rows[]` = `{label, text, url}` in display order (labels: `type`,
`default`, `example`, `value`, `set in`, `declared`, `installed`, `channel`, `listed
in`, `via`, `programs`, `homepage`, `license`, `source`; `text` may contain newlines, an
example attrset keeps its own; `url` null or a link), `about` (the description,
paragraphs separated by blank lines) and `notes[]` (what could not be asked, one line
each). The human output is exactly this, wrapped to the terminal width (`NIXIN_WIDTH`).

Errors: `usage` (no NAME, or more than one), `not-found`, `unknown-user` (`--home`),
and, when no side could answer, the config's own (`config-not-found`, `not-a-flake`,
`unknown-host`, `eval-failed`, `nix-missing`); `internal`.

Costs: an option: one eval of the host names, one of the HM users (unless `list`/`status`
cached the package eval), one prefix eval per tree (≈1 s each cold on mbp13, cached by
the flake fingerprint), and one search.nixos.org query plus the Home Manager option
index (cached). A package: the package eval (`status`'s, cached), the prefix evals of
`where PACKAGE`, one search.nixos.org query. Measured on mbp13: 2–4 s for an option,
about 7 s for an installed package with a cold package eval; a second run is under a
second. Writes cache files only.

### `add`

```
nixin add PKG... [--home USER] [--config DIR] [--dry-run] [--no-check]
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
(skipped with `--no-check`). A PKG that a hand-written module already lists is reported
as `already-declared` and left alone.

### `rm`

```
nixin rm PKG... [--home USER] [--where FILE:LINE]... [--config DIR] [--dry-run] [--no-check]
```

Removes packages wherever the host's (or user's) modules declare them. A generated entry
is dropped from state.json. A hand-written list element is removed in place, comments
kept. When the generated module becomes empty, its file and its import line go away
(so `add` then `rm` leaves the tree byte for byte as it was). A package declared in
several places is `ambiguous`; repeat with `--where FILE:LINE` (from `candidates[]`).

### `set`

```
nixin set OPTION NIX-EXPR... [--home USER] [--where FILE:LINE] [--config DIR] [--dry-run]
                             [--no-check] [--force]
nixin set OPTION [--home USER] [--config DIR]
```

Sets OPTION to a Nix expression (the remaining arguments joined with spaces), written
exactly as given. Before anything is written, three checks:

1. Syntax (`nix-instantiate --parse` of `with { }; VALUE`), so the value may name the
   module's arguments: `pkgs.linuxPackages_7_2`, `lib.mkForce true`,
   `config.networking.hostName`. Whether those names exist where the value lands is the
   parse of the edited file (`invalid-nix` with `file`: a hand-written `{ ... }:` module
   has no `lib`; the generated module binds `config`, `lib` and `pkgs`).
2. The option (one prefix eval of the host's or user's option tree, as `where`'s):
   OPTION must be an option of the scope, or a path inside one (an `attrsOf`/submodule
   option), else `not-found`.
3. The type: the value's text is classified (a literal bool, int, float, string, null,
   path, list, attrset, function; a bare word that is no builtin and no module argument,
   e.g. `yes`; anything else, a name, a call, `if`, is an expression and not judged),
   looking through `lib.mkForce`, `mkDefault`, `mkOverride N`, `mkBefore`/`mkAfter`,
   `mkOrder N`, `mkIf COND` and `mkMerge [ … ]`, and checked against the option's type:
   `bool`, the integer types (with their range: `port` is 0…65535), `float`, `number`,
   the string types (a bare word is not a string: quote it, `'"box"'`), `enum`
   (membership), `listOf X`/`attrsOf X` (the shape, then each element),
   `nullOr`, `either`/`oneOf`, `coercedTo`, `unique`, `attrs`. Package, path,
   submodule, function and any other type are not checked. A mismatch is `type-mismatch`
   with `expected` (the type's description), `type_info`, `default`, `example`, `option`
   and `value`, and nothing is written.

`--force` skips 2 and 3 (no eval before the write). `--no-check` skips every eval, before
and after the write (also for `add`, `rm`, `unset`; `--no-eval` is the old name).

If a module already defines OPTION, its value is rewritten in place (wrappers like
`lib.mkForce`/`lib.mkIf c` kept). Otherwise the definition goes into the generated module
as an attribute path (`services.openssh.settings.X11Forwarding = true;`). The eval after
the write (`checks.eval`) then shows the option's new value.

Without a value, `set OPTION` writes nothing and describes the option for the scope:
`option`, `real_option` (the option NAME is inside, in `sub` mode), `mode`, `type` (the
description of NAME's type), `type_info`, `default`, `example`, `description`,
`sub_description`, `value`, `declarations[]`, `definitions[]` (where it is set, as in
`where`), `target` (where `set OPTION VALUE` would write: `{file, line, in_place}`, or
`{candidates[]}` when several hand-written definitions match), `scope`, `user`,
`config`, `warnings[]`. For a path with only options below it, `mode: "prefix"` and
`options[]` (their names). Errors: `not-found` (no such option) and the writer's.
Costs: one prefix eval (cached like `where`'s).

### `unset`

```
nixin unset OPTION [--home USER] [--where FILE:LINE] [--config DIR] [--dry-run] [--no-check]
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
| `checks` | `type` (`set`) = null (`--no-check`, other verbs), or `{ok, option, expected, note}`: `ok` true (the value fits, or is an expression the eval judges: see `note`) or null (not checked: `--force`, a type nixin cannot follow, a failed option query; `note` says which); `parse[]` = `{file, ok, error}` per written `.nix` file; `eval` = null (dry run, `--no-check`, nothing changed) or the eval proof. For packages: `ok`, `attr`, `command` (to rerun it by hand), `packages` (count), `present{attr: name}`, `absent[]`, and `missing[]`/`still_installed{}` when it did not work; for options: `ok`, `attr`, `value` or `defined: false`, `note`; in `value`, functions, derivations, package sets (anything with `callPackage`) and parts past depth 5 or 60 items are the strings `<function>`, `<derivation NAME>`, `<package set>`, `<…>`. When the eval itself fails: `ok: false`, `code`, `message`, `attr` |
| `warnings[]` | strings, always present: a conditional list, a kept wrapper, a hand-edited generated file, an eval failure, … |

When the eval after a write fails (`checks.eval` has `code` `eval-failed` or
`eval-timeout`), the verb fails: `ok: false`, exit 1, `error.code` is that code. The
write stays: the document still has every result field (`changes[]` lists what was
written, `actions[]`, `checks`), and `error.undo[]` holds the shell commands that take it
back: the inverse nixin command where that restores exactly (`rm` what `add` added,
`unset` what `set` newly set, `set OPTION OLD [--where FILE:LINE]` for a changed value,
`add` what `rm` dropped from the generated module), else `git -C WORKSPACE checkout --
FILE` for a hand-written file an element or binding was removed from (it also drops any
other uncommitted change in that file). The message says the same, and that
`--no-check` skips the eval. An eval that runs but does not show the wanted state
(`still_installed`, `missing`) is not a failure: `ok` stays true and `warnings` says it.
The eval proves only the list or option it reads, not that the host builds (that is
`plan`).

Errors (an error writes nothing): `usage`, `config-not-found`, `not-a-workspace`,
`unknown-host`, `unknown-user` (the message names the hosts where USER exists),
`unknown-package` (`add`), `invalid-nix` (a value that is not well-formed Nix, a bad
name, or a file that would not parse after the edit, e.g. a variable it does not bind;
then `file`), `not-found` (`rm`/`unset`: with `hints[]`, e.g.
`programs.kitty.enable is set at …`, or `candidates[]` when `--where` matched nothing),
`ambiguous` (with `candidates[]` = `{file, line, option, text}`), `type-mismatch`
(`set`, see above),
`import-site-missing` (with the exact `line` to add, and `file`), `file-conflict` (an
`installer/` file nixin did not generate), `invalid-state` (unreadable state.json),
`eval-failed`/`eval-timeout` (listing the flake's hosts needed an eval and it failed,
or the eval after a write failed: see above), `nix-missing`, `internal`.

Costs: reads the workspace, runs `nix-instantiate --parse` and nixfmt, and (unless
`--no-check`) runs nix: one eval before an `add` or a `set` (the package names; the
option's type, ≈1 s cold, plus `nix flake metadata`) and one after every write. Without
`--dry-run` it writes in the workspace (`installer/*`, the edited modules) and in its git
index (`git add --intent-to-add` for created files, `git rm --cached` for deleted
never-committed ones). It never commits. The writer keeps no cache. Evals time out after
900 s (`eval-timeout`).

### `plan`

```
nixin plan [--remote [--all|--split] | --builders SPEC] [--config DIR] [--authoritative DIR]
```

Builds the workspace host's `config.system.build.toplevel` without root and diffs its
closure against `/run/current-system`. The build is the workspace as a git+file flake:
tracked files with their working-tree contents (the writer's new files are
intent-to-add, so they count), nothing untracked. That is exactly what `apply` commits
and what the authoritative tree builds after pulling it, so plan's `out` is the path
`nixos-rebuild` will produce there. Two nix steps: evaluate `toplevel.drvPath`
(`eval-failed`), then `nix build <drv>^out --log-format internal-json -v`
(`build-failed`), whose events nixin parses into a build tracker: every derivation of
the plan with a status (queued, building and on which builder, done, failed with nix's
log tail, skipped because a dependency failed), the current build's phase and log tail,
the paths fetched from the binary caches, and an ETA from the durations of earlier builds
of the same names (kept in the cache, `apply/durations.json`). In human mode nix's
readable output streams to stderr as before (the "will be built" lists, `building '…'`,
`copying path …`, warnings and errors; not the per-line build logs) plus one live status
line, `[7/39 built, 2 building, 12/40 fetched 35.1 MiB] system-path: buildPhase · 1m12s
(eta 3m)`, redrawn in place when stderr is a terminal and printed as a plain line when the
counts change (at most every 10 s, and once at the end) otherwise. Under `--json` nothing
goes to stderr; the document gets the final `build` summary.

**Stopping.** Ctrl-C while `plan` builds (or evaluates) stops nix (its process group gets
SIGTERM, SIGKILL after 5 s) and the verb answers `cancelled` with the summary so far;
nothing is written but caches. The UIs do the same with their Stop button. `apply` can be
stopped until its build is done: the commit stays (`committed` in the error) and the next
`apply` continues from it. From the pull on a stop is refused: Ctrl-C prints one line
(`pull`/`switch` "is running and cannot be stopped; waiting for it") and the root steps
run in their own process group, so the terminal's Ctrl-C never reaches `nixos-rebuild`
half-way through an activation; the UIs' Stop button is disabled meanwhile.

The out path is cached by the flake's *source store path* (a content hash of that tree,
dirty edits included) and by commit (see [Caches](#caches)), so `apply` right after
`plan` does not rebuild even though it commits first, and a second `plan` of the same
tree is instant. A tree whose only change is `installer/history.jsonl` is answered as
its HEAD commit (no module reads that file).

#### The remote builder

`--remote` builds with a remote nix builder (the user's `rbuild`, as a verb flag; the
switch step is unchanged, and `plan`'s cache still applies). The `--builders` spec comes
from a command, run only when a build is really needed (a cached plan contacts nothing):
`$NIXIN_BUILDERS_CMD`, else `builders_cmd` (a string, top level) in
`installer/state.json`, else `mbp13-builders`; it is shell-split, gets the mode as its
argument and prints the spec on stdout. The mode: none (`big`): only `big-parallel`
derivations (kernels) go to the remote, the rest builds or substitutes here; `--all`:
plus `--max-jobs 0`, everything not substituted builds there; `--split`: any derivation
may go there (its slots fill first), this machine builds too. `--builders SPEC` passes a
literal spec instead of running the command (`--all`/`--split` still set the mode;
`--all` without `--remote`/`--builders` is `usage`). `--builders-use-substitutes` is
always on. The command failing (the builder asleep or offline: exit 1; not on PATH; no
answer in 30 s) means a plain local build with a line in `warnings[]` (and on stderr in
human mode), never an error. nix honours `--builders` only for a trusted user: the verb
checks `nix store info --json` first and answers `builders-untrusted` with the fix
(`nix.settings.trusted-users`) before anything is built or committed. The flags apply to
a build on this machine only: with `--host H` for another host they are `usage` (H
builds its own system). (`sync --remote NAME` is unrelated: a git remote.)

The remote's `name` is the command's basename without a `-builders` suffix (`mbp13`),
or a literal spec's hosts; the tracker names a builder after it where the spec's host
is an address (`drvs[].host`), and the UIs' selector and the tracker's header say
"building on mbp13 · big jobs".

Result:

| field | meaning |
|---|---|
| `out`, `drv` | the built toplevel and its derivation |
| `cached` | `out` came from plan's cache (nothing evaluated or built); `cache_key` says which entry (`source <hash>` or `commit <sha>`) |
| `up_to_date` | `out` is `/run/current-system` |
| `duration_s`, `timings` | total seconds; `eval_s`, `build_s` when it built |
| `build` | the tracker's summary when it built, null when `cached`: `state` (`done`, `failed`, `cancelled`), `cancelled`, `duration_s`, `total` (derivations in the plan), `counts` (`queued`, `building`, `done`, `failed`, `skipped`), `failed[]` (names), `downloads` (`done` paths, `bytes_done`), `remote` (below). The same object is in `error.build` of `build-failed` and `cancelled` |
| `build.remote` | null for a local build; with `--remote`/`--builders`: `mode` (`big`, `all`, `split`), `name` (`mbp13`), `builders` (the spec nix got; null after a fallback), `command` (what printed it, with its mode flag; null for a literal spec), `fell_back` (the command failed: built locally, see `warnings[]`) |
| `workspace` | `path`, `branch`, `head`, `dirty[]` (tracked files that differ from HEAD), `untracked[]` (not part of the build) |
| `authoritative` | `path`, `exists`, `head`, `branch`, `dirty[]`, `relation`: `same`, `ahead` (the workspace has commits to apply), `behind` (the authoritative tree has commits the workspace lacks), `diverged`, `unknown` (its HEAD is not a commit the workspace has) |
| `running` | `store_path` (`/run/current-system`), `generation` (the newest system profile generation pointing at it, or null), `profile_generation`, `profile` |
| `pending_commits` | `[{commit, subject}]` the workspace has and the authoritative tree does not; null when `relation` is `diverged`/`unknown` |
| `diff` | `{added: [{name, version, size}], removed: [{name, version, size}], changed: [{name, from, to, size_delta}], size_delta}` from `nix store diff-closures`; sizes in bytes, null where nix printed none (it omits deltas under 8 KiB); `from`/`to` are nix's version sets as one string (`"6.7.5, 6.7.5-qt5"`), both null for a size-only change; `unparsed[]` if nix printed a line nixin could not read. null for another host (`--host` is not this machine) |
| `reboot_needed` | `boot.reboot_needed`; null for another host |
| `pins[]` | the host's pins as `nixin pins` lists them (no eval); a stale one (its revision is the lock's: it holds nothing) adds a warning. `apply` and `apply --dry-run` carry it too |
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

Errors: `usage` (any positional argument; `--all`/`--split` without `--remote`;
`--remote` for another host), `config-not-found`, `not-a-workspace` (also: not a git
checkout, detached HEAD), `unknown-host`, `builders-untrusted`, `eval-failed`,
`eval-timeout` (900 s), `build-failed` (the failing builder's block with nix's log
tail), `not-a-flake`, `nix-missing`, `git-failed`, `internal`.

Costs: runs nix (evaluation, then a build: from nothing for a no-op to hours for a new
kernel) and git (read only). Writes nix's store and plan's cache, nothing in the trees.

### `apply`

```
nixin apply [-m MESSAGE] [--dry-run] [--no-switch] [--remote [--all|--split] | --builders SPEC]
            [--config DIR] [--authoritative DIR]
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
3. **Build**: plan's out path (cache), else the same build as `plan` (`--remote`,
   `--all`, `--split`, `--builders` as there: [the remote builder](#the-remote-builder);
   the document's `build.remote`; the untrusted check runs before the commit).
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
true`, and prints the commands in order. With `--remote`/`--builders` the `build`
command carries the builders flags (`--builders "$(mbp13-builders --all)"` when the
spec is still to be fetched: the command is not run) and the document has `remote`
(the block of `build.remote` before the spec is known); null without the flags.

Result:

| field | meaning |
|---|---|
| `committed` | the new commit, or null when nothing was committed; `commit_message` its subject |
| `build` | as `plan`'s: the tracker's summary when apply built, null when plan's cache had the out path |
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
connection: exit 255, or the session ended otherwise; exit 255 after a step started says
the connection dropped and which step, not "is H reachable"). Before anything runs: `needs-tty`,
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

### `branches`

```
nixin branches [list] [--into BRANCH] [--config DIR]
nixin branches merge BRANCH... [--into BRANCH] [--no-build] [--push] [--dry-run] [--config DIR]
nixin branches prune [--hub] [--dry-run] [--into BRANCH] [--config DIR]
```

The workspace's git branches against the target branch (`--into`, else the hub's HEAD
`refs/remotes/<hub>/HEAD`, else `main`; the hub is the target's upstream remote, else
`origin`). Read only for `list`; `merge` and `prune` take apply's lock.

`list`: every local branch, and every hub branch with no local branch of that name
(`where: "hub"`), unmerged ones first, newest first. Only git runs (no network: the
remote-tracking refs as the last fetch left them).

`merge`: merges each BRANCH (a local branch, else `<hub>/BRANCH`) into the target in
order, in the object store (`git merge-tree --write-tree`, then `commit-tree`: one merge
commit per branch, first parent along the target, message `Merge branch 'NAME'` plus
the merged subjects). No checkout is touched, so it runs from any branch and with
uncommitted changes. A BRANCH the target already contains is `skipped[]`. Then, unless
`--no-build`, the merged commit is built for `--host` from a detached worktree under the
cache (`<cache>/branches/<sha12>`, removed again) and its out path goes into plan's
commit cache. Only then does the target move: a fast-forward `git fetch . REF:TARGET`
(`diverged` when it moved meanwhile; `branch-checked-out` when a worktree other than the
workspace has it checked out, also mid-rebase or bisect, checked before the build too),
or `git merge --ff-only` when the workspace itself is on the target (its files move
with it). A conflict or a failed build changes nothing. `--push`: push the target to the
same name on the hub. It never switches: `nixin apply` from the target branch does; a
`warnings[]` line says so when the workspace is on another branch. `--dry-run`: the
merge check only (no commits, no build, nothing moves).

`prune`: deletes every local branch whose tip the target contains (nixin checks the
ancestry itself, then `git branch -D`), except the target, the workspace's branch and
a branch a worktree has checked out (`skipped[]` with the reason). `--hub`: the same on
the hub (`git push --delete`, one call), never on another remote; a hub branch whose
local branch was kept stays too. Stale worktrees (their directory gone) are pruned
first and never hold a branch. `--dry-run`: what it would delete.

Result:

| action | fields |
|---|---|
| `list` | `into`, `into_head`, `hub` (null: no hub), `workspace` = `{path, branch}`, `branches[]` = `{name, where (local, hub), commit, subject, date, upstream, hub_commit (the hub's tip of that name, null: not there), ahead, behind (of the target), merged, files[] (first 200 the branch changes since the merge base), files_total, merges_clean (null when merged), conflicts[], checked_out_in (a worktree path, null)}`, `counts` = `{unmerged, merged}`, `duration_s`, `warnings[]` |
| `merge` | `dry_run`, `into`, `old`, `new` (the target before and after), `merged[]` = `{branch, commit, merge (the merge commit; null in a dry run), commits, files[]}`, `skipped[]` = `{branch, reason}`, `build` = `{host, out, drv, eval_s, build_s}` (null: `--no-build`, dry run, nothing merged), `pushed`, `hub`, `workspace`, `duration_s`, `warnings[]` |
| `prune` | `dry_run`, `into`, `hub` (null without `--hub`), `deleted[]` = `{branch, where (local, hub), commit}`, `skipped[]` = `{branch, where, reason}`, `unmerged` (local branches kept because the target lacks them), `duration_s`, `warnings[]` |

Human output: `list` one line per unmerged branch (`clean`/`conflict`, +ahead -behind,
files, age, subject; conflicting files and other worktrees under it) and a `prunable`
line with the merged ones; `merge` the move, one line per merged/skipped branch, the
build, the push and `next`; `prune` one line per deleted and kept branch. With a build,
nix's progress streams on stderr as in `plan`.

Errors: `usage` (an unknown action, `merge` without BRANCH, branch names after `list`
or `prune`, a flag of another action, `--push`/`--hub` without a hub remote, the target
as BRANCH), `config-not-found`, `not-a-workspace`, `unknown-branch` (no such BRANCH, or
`--into` names no local branch), `unknown-host` (merge with a build), `merge-conflict`
(`branch`, `files[]`), `eval-failed`/`build-failed`/`cancelled` (the merged commit;
`build` summary), `branch-checked-out` (`path`), `diverged`, `busy`, `git-failed`,
`internal`.

Costs: git only for `list` (one `merge-tree` per unmerged branch; 0.16 s for 11
branches on 19BK's ~/nixos); `merge` adds a whole host build (as `plan`, mostly substituted or
cached); `--push` and `prune --hub` are network (the hub).

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

### `generations`

```
nixin generations [--keep N] [--older-than DAYS] [--config DIR] [--authoritative DIR]
```

This machine's system generations, newest first: `history`'s rows (the same reader),
plus each one's closure size, whether the boot menu has an entry for it, and whether
`nixin gc` with the same `--keep`/`--older-than` would delete it. Without either flag it
shows `gc --keep 3`. The commits are joined from `installer/history.jsonl` of `--config`
or `$NIXIN_CONFIG` (which must then exist: `config-not-found`), else of `/etc/nixos` when
it is there; without a config the rows have no commits. No root, no network.

The policy, shared with `gc`: `--keep N` keeps the newest N generations (by number);
`--older-than DAYS` keeps every generation made (link mtime) in the last DAYS days and
the newest one made before that, which was running at the cutoff (the rule of
`nix-collect-garbage --delete-older-than`); with both, a generation is kept when either
keeps it. The running generation (`current`), the booted one and the boot default are
never dropped.

Result:

| field | meaning |
|---|---|
| `generations[]` | `history`'s row (`generation`, `date`, `store_path`, `current`, `booted`, `default`, `kernel`, `nixos_version`, `commit`, `message`, `mode`, `applied`, `applied_host`, `match`, `match_generation`, `entries[]`), plus `closure_size` (bytes, `nix path-info -S`: the whole closure, so shared paths count in every row; null when nix could not say), `boot_entry` (bool; null when unknown), `drop` (the policy deletes it), `kept_by[]` (why it stays: `current`, `booted`, `default`, `newest`, `recent`, `cutoff`; empty when it drops) |
| `total` | how many generations there are |
| `current`, `booted`, `default` | as in `history` |
| `policy` | `{keep, older_than_days}` as applied (`keep: 3` without flags) |
| `drop[]` | the generation numbers the policy deletes, oldest first |
| `boot` | `{source, limit, generations[]}`: which generations have a boot entry. `source` `entries` (systemd-boot's `loader/entries/nixos-generation-N[-specialisation-*].conf`, when /boot is readable), `configurationLimit` (the newest `limit` generations: the window the boot default's systemd-boot builder keeps, read as in [the boot block](#the-boot-block); a /boot mounted root-only, as on mbp13), or null (another bootloader: `generations` null) |
| `sizes` | `{cached, measured, seconds, error}`: how many closure sizes came from the cache, how many nix measured now |
| `history` | `{path, entries, unmatched}` as in `history`; null without a config |
| `warnings[]` | the config is the authoritative tree; closure sizes unknown |

Human output: `history`'s table with a closure column, `drop` in a `gc` column and an
`e` flag for a boot entry; the first line says what the policy would delete
(`nixin gc --keep 3 would delete 31 (87–117)`).

Errors: `usage` (a positional argument, `--keep` below 1, `--older-than` below 0),
`config-not-found`, `host-not-local`, `internal`.

Costs: `history`'s reads, `/boot/loader/entries` (when readable), and one `nix path-info
--closure-size` for the store paths it has not sized before. A closure never changes, so
the sizes are cached by store path (see [Caches](#caches)).

### `doctor`

```
nixin doctor [--full] [--config DIR] [--authoritative DIR]
```

One read-only health check of this machine: what the modules, the timers and the apply
verbs assume. Nothing is written (not even the state directory), nothing prompts, nix
is not evaluated: files are stat'ed or parsed, git and `systemctl` only read. Without
`--full` it is local and takes well under 2 s (27 ms on mbp13); `--full` adds the
network: `git ls-remote` of the workspace's upstream (5 s, ssh in BatchMode) and `ssh -o
BatchMode=yes -o ConnectTimeout=5 DEST true` to each other host in `hosts.toml`, up to 8
at a time. `--config` is the workspace (`$NIXIN_CONFIG`, else `~/nixos` when it is a git
checkout, else the authoritative tree), `--authoritative` as for `apply`. `--host`, when
given, must be this machine (`host-not-local`).

Result: `checks[]`, each `{name, ok, detail, fix}`. `ok` is `true` (fine), `false`
(broken, or will break a switch) or `null` (could not tell, or information: never a
failure). `fix` is the one command to run, or null. `healthy` (no `ok: false`),
`failed[]` (their names), `unknown` (how many are null), `full`, `workspace`,
`authoritative`, `elapsed_ms`. A finding is not an error: `ok` stays true and the exit
code 0; a UI shows `healthy`.

| name | ok false when | null when |
|---|---|---|
| `index.nixos`, `index.home` | the file is there but unreadable, built for another host, from a generation that is not the running one (HM: not the active one), or from another nixpkgs revision than the authoritative `flake.lock` (the lock moved since the switch) | absent: the module is not in use; `NIXIN_NO_INDEX` |
| `timer.nixin-outdated`, `timer.nixin-status` | the timer is not enabled or not active; the service's last run did not succeed (`systemctl --user show` `Result`, `ExecMainStatus`; `detail` has `ExecMainExitTimestamp`) | not installed; no user manager |
| `state.files` | a `*.json` in the state directory does not parse | the directory is not there |
| `state.summary` | `summary.json` has `errors` (a failed nixin run inside the timer, which still exits 0), or is more than 2 days old | no `summary.json` yet |
| `tree.dirty` | — | the workspace has uncommitted files |
| `tree.authoritative` | it is not a git checkout, or its HEAD is not in the workspace's history (`apply` cannot fast-forward it) | the workspace is ahead (commits to apply), or the commit was never fetched into it |
| `tree.lock` | — | the two `flake.lock` differ (`detail` names the inputs), or one is missing |
| `tree.hub` | the hub has commits the workspace lacks | without `--full`; no upstream; offline or unreachable (`unknown`); the workspace is ahead |
| `tree.workspace` | — | the workspace is not a git checkout (the only tree row then) |
| `host.H` | `ssh` failed (the last stderr line) | without `--full` |
| `hosts` | `hosts.toml` does not parse | — |
| `secret PATH` | PATH does not exist | the user cannot look into its directory |
| `secrets` | — | (`ok: true`: the config names no such file) |
| `boot.space` | /boot's free space is below the biggest generation's kernels + initrds (its specialisations' too): the next kernel change of that size would not fit | statvfs failed |
| `boot.generations` | — | more system generations than `configurationLimit` (`fix`: `nixin gc --keep N`) |
| `boot` | — | not systemd-boot (no `configurationLimit` in the running system's builder) |
| `units.system`, `units.user` | — | `systemctl [--user] --failed` lists units (`detail`), or systemctl failed |

**Secrets** are found without an eval: the tracked `.nix` files that
`nixosConfigurations.<host>` reaches (the `./…` paths in its block of `flake.nix`, then
every `./…`/`../…` path those files name; all tracked files when `flake.nix` has no
such block), every string given to an option named `*environmentFile`,
`*passwordFile`, `*keyFile`, `*secretFile`, `*credentialsFile`, `sshKey` or
`identityPaths` (either capitalisation), and every `"/etc/….env"`. Comment lines are
skipped; `${config.home.homeDirectory}`, `${config.xdg.configHome}`, `$HOME` and `~/`
become the user's home; other interpolations and relative paths are skipped (they need
an eval), as are paths Nix makes itself (`/nix/store`, `/run/agenix`, `/run/secrets`).
Each is `os.stat`ed, never read: a root-only file counts as present; `detail` names the
first `file:line`.

Human output: one line per check (`ok`, `BAD`, `-`), the fix under each that is not ok.

Errors: `host-not-local`, `usage`, `internal`. A check that raises becomes its own row
(`ok: null`, `the check failed: …`); the others still run.

Costs: about 12 subprocesses (`systemctl` 3, git 4–7, one `ssh` per host and one `git
ls-remote` with `--full`), the option index headers, the state files, the config's
tracked `.nix` files, `/boot` statvfs and the generations' kernel/initrd sizes. No
network without `--full`. The groups run side by side.

### `gc`

```
nixin gc [--keep N] [--older-than DAYS] [--dry-run]
```

Deletes the system generations `generations`' policy drops (same flags; never the
running, the booted or the boot-default generation), rewrites the boot menu, and
collects the garbage. Without `--keep` or `--older-than` no generation is deleted: it
only collects what is garbage already (a warning says so). No `--config`: it acts on
the system profile and the store. Shares apply's lock (`busy`).

1. **Preview** (as you): the generations that go, and an estimate of what the store
   frees: the closures of those generations minus everything the GC roots that remain
   still reach (`nix-store --gc --print-roots` without their links, `nix-store -qR`);
   with `--dry-run` also what is garbage already (`nix-store --gc --print-dead`, the
   list `nix-collect-garbage --dry-run` prints, which cannot see what deleting
   generations frees and prints no sizes); sized with `nix-store -q --size`. An
   estimate: with `auto-optimise-store`, a file hard-linked into a live path frees
   nothing; the derivations nix keeps for the deleted systems (`keep-derivations`) come
   on top.
2. **delete** (root): `nix-env --profile /nix/var/nix/profiles/system
   --delete-generations N…` (only when the policy drops some).
3. **bootloader** (root): `/nix/var/nix/profiles/system/bin/switch-to-configuration
   boot`, the boot default's own bootloader step through the profile link (the default
   entry stays what it was; `/run/current-system`'s would make the running system the
   default after an `apply --no-switch`). The deleted generations' entries go, and the
   kernels and initrds only they used leave /boot. Before the collection, so no entry
   ever points at a collected system. Only after a delete.
4. **collect** (root): `nix-collect-garbage`, plain: no `-d`, every other profile is
   left alone. Its "N store paths deleted, X MiB freed" is parsed.

The root commands go through apply's backend (`sudo -n`; `needs-sudo` with `commands[]`,
nothing past that point ran; `nixin gc` again finishes: a generation already deleted is
simply no longer on the list). `--dry-run` runs nothing as root: it lists, estimates,
probes `sudo -n true` and prints the commands.

Result:

| field | meaning |
|---|---|
| `dry_run` | `--dry-run` was given |
| `policy` | `{keep, older_than_days}` (both null without flags) |
| `delete[]` | the generations that go (went), oldest first: `generation`, `date`, `store_path`, `kernel`, `nixos_version`, `commit`, `message`, `closure_size`, `boot_entry` |
| `keep[]` | the generation numbers that stay |
| `protected` | `{current, booted, default}` |
| `estimate` | `{generations: {paths, bytes}, dead: {paths, bytes} \| null, total: {paths, bytes}, roots, seconds, note}`: what only the deleted generations reach, what is garbage already (`--dry-run` only: it scans the whole store), the sum, how many GC roots stay; null when nix could not be asked (then a warning) |
| `store`, `boot` | `{path, total, free_before, free_after, freed}` of the store's filesystem and of /boot (`df`'s free space; `free_after`/`freed` null with `--dry-run`); `boot` also `entries_before[]`, `entries_after[]` and `entries_source` (as `generations`' `boot`) |
| `commands[]`, `privilege`, `duration_s` | `--dry-run`: `{step, privileged, argv, command, backend, by_hand}` for `delete`, `bootloader`, `collect`; `{backend, ready, why}` |
| `deleted[]` | the generation numbers deleted (verified: their links are gone) |
| `freed` | `{paths, bytes, text}`: what nix-collect-garbage said it freed (`bytes` only as precise as nix prints it: `2.4 GiB` is 0.1 GiB; with auto-optimise-store nix also frees hard-link targets it does not count, so `store.freed`, `df`'s delta, is the better number); null (and a warning) when it did not say |
| `generations_left` | how many system generations remain |
| `privileged_commands[]`, `backends` | the root commands as run; `{delete, bootloader, collect}` → `sudo` |
| `timings` | seconds: `prepare` (listing, sizes, the estimate), `delete`, `bootloader`, `collect`, `total` |
| `warnings[]` | no policy; no estimate; nothing said what was freed |

Errors (`ok: false`; after a root step: `step`, `deleted[]`, `done[]`,
`privileged_commands[]`): `usage` (a positional argument, `--keep` below 1,
`--older-than` below 0), `host-not-local`, `needs-sudo` (with `commands[]`), `gc-failed`
(a root step failed, or nix-env left a generation it was asked to delete; `step` says
which), `busy`, `internal`.

Costs: the preview runs `nix path-info` (cached), `nix-store --gc --print-roots`,
`nix-store -qR` over every root and `-q --size`; `--dry-run` adds the whole-store dead
scan. Root: the three steps. Writes: the system profile, /boot, the store.

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
evals (`outdated.nix`), run side by side (the lock side in a thread while the target is
resolved and evaluated; an error of the lock side still comes before the target's);
`system.build.toplevel` is never evaluated. The target is:

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

**The pending move.** While the working `flake.lock` differs from `HEAD:flake.lock` (an
`update` that is not committed yet) and no target is named, the comparison is the move
itself: the *committed* node, evaluated with `--override-input <input>
github:<owner>/<repo>/<rev>?narHash=<its narHash>` (nix takes the tree from the store, no
network: it was the lock until the update), against the working lock as the host evaluates
it. `changed[]`, `kernel` and the rest then describe what the uncommitted move brings;
`lock_change.compared` is true, `target.from` is `pending` (the working lock itself: there
is nothing to update, only to apply or to undo), `up_to_date` false, `commands[]` are
`nixin plan`, `nixin apply` and `nixin update --undo`, and `notes[]` says so. `nixpkgs
REV`/`--channel` compare as asked, with `lock_change` informational (`compared: false`).
`--published` is not affected. The branch's head is not resolved in that state: deal with
the pending move (apply or undo) before the next update.

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
| `lock_change` | as in `status` (`{from, to, committed, running}` or null), plus `compared`: true when this document compares the pending move (above) |
| `target` | lock mode: `input`, `flakeref` (as asked), `from` (`lock` \| `nixpkgs` \| `channel` \| `pending`), `rev`, `short_rev`, `date`, `last_modified`, `locked` (the fully locked ref given to `--override-input`, with `narHash`), `release`, `days_newer` (target date minus lock date). `--published`: `source` (`search.nixos.org`), `channel` (concrete: `stable` → `26.05`), `index`, `rev` (the commit the index was built from), `short_rev`, `queries`, `cached` |
| `up_to_date` | lock mode: the target commit is the locked one (no target eval; everything empty) |
| `changed[]` | lock mode: `{pname, from, to, scope, user, direction}`. `--published`: `{pname, installed, published, attr, alternatives, channel, rev, scope, user, direction}` (`installed`: the versions, comma-separated when several; `attr`: the index's attribute compared; `alternatives`: how many other attributes share the pname). `scope` is `system` \| `home`, `user` null or the HM user; `direction` `up` \| `down` \| `same` (different text, same order) |
| `added[]`, `removed[]` | lock mode: `{pname, version, scope, user}`. Always `[]` with `--published` |
| `unpublished[]` | `--published`: installed pnames the channel has no package for (local packages, flake inputs' packages) |
| `kernel` | `from`, `to`, `changed`; `--published` also `latest` (`to` is null when the series is gone from the channel). Lock mode with a kernel pin: `held` = `{pin, available}` (the target's version of the pinned kernel attribute; both evals see the held one, so `changed` is false) |
| `holds[]` | lock mode: the host's pins (`nixin pins`' fields) plus `available` (`{attr: version}` in the target nixpkgs without the overlay, one extra eval, cached as `outdated-holds`) and `behind` (the target has another version than the held one). A held package never appears in `changed[]`; this is where it shows |
| `count` | `len(changed)` |
| `total` | distinct pnames compared (the lock side, the scopes asked for) |
| `scopes[]` | `{scope, user, total, changed}` plus `added`, `removed` in lock mode; system first, then each HM user (only `--home USER`'s with `--home`) |
| `commands[]` | shell lines to take the update. Lock mode: `nix flake update <input> --flake DIR` for the default target, else `nix flake lock DIR --override-input <input> <ref>/<rev>` (writes exactly the commit compared and keeps flake.nix's ref), then `nixin plan`, `nixin apply`. `--published`: `nixin outdated` (the exact answer) first |
| `notes[]` | for a target other than the lock's ref: how to follow it for good (change `inputs.<input>.url`); `--published`: the attribute caveat |
| `cache` | as in `status` (`outdated-lock`, `outdated-target`) |
| `timings` | seconds: `resolve` (the target's `nix flake metadata`), `lock`, `target` (0 when cached), `search` (`--published`), `total` (the lock eval runs alongside `resolve` + `target`, so `total` is about the longer of the two, not the sum) |

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
nixin update [--input NAME] [--to FLAKEREF|REV] [--hold NAME,...] [--allow-others] [--dry-run] [--config DIR]
nixin update --undo [--dry-run] [--config DIR]
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
| `--allow-others` | keep the new lock even when nix moved other nodes too (see `lock-moved-others`); they are listed in `moved[]` and a warning |
| `--hold NAME,...` | `nixin pin NAME` for each (at the lock as it is before the move) first, then the update, then the pins' checks against the new lock: `holds[]`, `checks[]` (see `pin`) |
| `--undo` | the update back, before it is committed: `git checkout HEAD -- flake.lock` in the workspace (index and working tree; staged or not, HEAD's lock wins). Every node that differs from `HEAD:flake.lock` goes back, not only the nixpkgs one. Nothing else changes: pins, other edits and the caches stay. `nothing-to-undo` when the lock is as committed; the message then says what applies instead: nothing (the lock is also the applied one), or, when the move is a commit already, `nixin pin NAME --rev <the applied revision>` or `git revert`. With `--dry-run` nothing runs. `--to`, `--input`, `--hold`, `--allow-others` are `usage` with it |

Only the named input may move. After nix ran, both locks are compared node by node: the
input's node and the nodes reachable only through it (its private sub-inputs, in the new
lock) may change; any other node that moved (a `follows` target, another root input) makes
the verb put the saved `flake.lock` back and fail with `lock-moved-others`.

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
| `moved[]` | other lock nodes that moved too: `{node, from, to}` (short revs); `[]` normally; only non-empty with `--allow-others` |
| `holds[]`, `checks[]` | with `--hold`: the pins made (as `pins` lists them) and their checks against the new lock (`{id, level, text, data}`; a `block` adds a warning, the lock stays moved) |
| `dry_run` | `--dry-run` was given |
| `undo` | true for `--undo`: `command` is the git line, `to_ref` is `HEAD:flake.lock`, `from`/`to` the input's working and committed node, `restored[]` every node that goes back (`{node, from, to}`, short revs), `commands[]` = `nixin outdated …` after the undo |
| `config` | the workspace path |
| `commands[]` | after a change: `nixin plan`, `nixin apply` with `--config`/`--host`; `[]` when nothing changed; with `--dry-run`: `[command]` |
| `timings` | seconds: `nix` (the update command; `git` for `--undo`), `total` |

Without `--json`: `nixpkgs (github:nixos/nixpkgs/nixos-unstable): 3ec6363 (2026-10-23) →
0bb2eed (2026-11-01)`, `ran: …`, and `next:` (or "already at …; flake.lock unchanged").
`--undo`: `nixpkgs: 0bb2eed (2026-11-01) → 3ec6363 (2026-10-23) (flake.lock back to
HEAD's)`, `ran: …`, `moved back: nixpkgs_2 0bb2eed → 3ec6363`, `next:`.

Errors: `usage` (a positional argument; `--to REV` for an input that is not on
github/gitlab/sourcehut; `--undo` with another flag), `config-not-found`, `not-a-workspace`,
`not-found` (no readable `flake.lock`, no nixpkgs input, or `--input` names no root input of
the lock; `--undo`: `flake.lock` is not tracked by git), `nothing-to-undo` (`--undo`: the
lock is as committed), `git-failed` (`--undo`: the checkout failed),
`lock-moved-others` (above), the `pin` errors with `--hold`, `network`
(nix could not fetch the new commit for lack of network), `bad-channel` (GitHub does not
know the ref: HTTP 404/422), `nix-failed` (nix failed otherwise; nix's message),
`nix-missing`, `internal`.

Costs: runs nix once (no time limit) and writes `flake.lock` in the workspace. Network:
nix asks GitHub for the ref's head (cached for its `tarball-ttl`, 1 h) and downloads the
nixpkgs tarball for a commit it has not fetched yet: about 60 s cold, a second or two
when `outdated` already fetched that commit. See [Costs](#costs-measured). The truth and
`outdated` caches are keyed by the flake fingerprint, which includes the lock, so the next
`status`, `list` or `outdated` evaluates the new lock.

### `pin`

```
nixin pin NAME [--attr ATTR]... [--rev REV] [--whole-set] [--why TEXT] [--config DIR] [--dry-run] [--no-check]
```

Holds a package, or the kernel, at a nixpkgs revision while the rest of the lock moves on.
The pin goes to `installer/state.json` (`nixos.<host>.pins.<NAME>`) and the generated
`installer/<host>.nix` gains a second nixpkgs, fetched by the lock's narHash
(`builtins.fetchTarball`: fixed-output, pure, offline once nix has the tree, which it does: it
is the very tree the flake input had), and one overlay per pinned revision that takes the
pinned attributes from it. On first use the generated module is imported from the host's
entry module as `add` does. flake.nix and flake.lock are never touched by a pin. Pins are per
host: a Home Manager user on a host with `home-manager.useGlobalPkgs` sees the overlay
through the shared pkgs (`--home` is a `usage` error).

| NAME | holds |
|---|---|
| `kernel` | the kernel attribute the host's top-level `boot.kernelPackages` names (read from the declaration's text: `pkgs.linux_7_2.override …` → `linux_7_2`; `pkgs.linuxPackages_7_2` → the same pair), as both aliases: `linux_X = pinned.linux_X` and `linuxPackages_X = final.linuxPackagesFor final.linux_X` (mode `kernel`: out-of-tree modules from the new nixpkgs, built against the held kernel). `--whole-set`: `linuxPackages_X = pinned.linuxPackages_X` (mode `kernel-set`, modules from the pin too). `--attr` picks the kernel attribute when the config names several. A user's `lib.mkForce`/`override` on `boot.kernelPackages` stays in charge: the overlay changes what `pkgs.linux_X` is, nothing else |
| anything else | a top-level attribute of `pkgs` (NAME itself, or the `--attr`s under that name): `ATTR = pinned.ATTR` (mode `attr`) |

`--rev REV` pins another commit of the locked repository; the default is the revision
`flake.lock` has now, so the flow is pin first, `nixin update` second (`update --hold
NAME,…` does both). After an update that is not applied yet the old revision is the way
back for one thing: `pin kernel --rev <old>` holds the kernel the running system has while
the rest of the lock moves. A `--rev` that is the committed lock's (`HEAD:flake.lock`) or
the applied lock's (the authoritative tree's `flake.lock`) revision takes narHash and date
from that lock, no network; any other commit is `nix flake prefetch`ed (network). Before writing, one eval
reads what the attributes are now (`version`, `pname`; `unknown-package` when one is not in
the host's pkgs; for the kernel, that `pkgs.<attr>` is the host's kernel). After the write,
one eval checks that the pins hold and runs the compatibility checks (below); a `block`
leaves the write in place and shows as `checks.eval.code: "pin-blocked"` plus a warning.

Result: as `add` (`changes[]`, `actions[]` with `pinned`/`already-pinned`, `imports_added[]`,
`checks`), plus `pin` (NAME), `pins[]` (the pin as `pins` lists it) and `hold_checks[]`:
`{id, level, text, data}`, `level` ∈ `info`, `warn`, `block`:

| id | level | when |
|---|---|---|
| `pin-stale` | info | the pin's revision is the lock's: it holds nothing until the lock moves |
| `kernel-toolchain` | info | mode `kernel`: modules are built with the new toolchain against the held kernel (vermagic is by version) |
| `eval-cost` | info | always: a second nixpkgs tree per pinned revision costs 1-2 s and ~300 MiB per eval, a re-download after gc |
| `glibc-skew` / `gl-skew` | info / warn | the pinned tree's glibc differs from the host's; `gl-skew` when the package's build inputs name mesa, libglvnd, wayland, qt, gtk (the program may not find a GL/EGL driver at run time) |
| `rebuilds-dependents` | info / warn (≥ 5) | installed packages (the running system's closure) depend on the pinned attribute and rebuild against it (`nix-store -q --referrers-closure`; skipped when the output is not in the store) |
| `kernel-set-mismatch` | warn | a hand-written module takes `pkgs.linuxPackages_X` beside `config.boot.kernelPackages` (`file`, `line`) |
| `kernel-modules-dropped` | block | with the kernel held, an out-of-tree module (`boot.extraModulePackages`, `hardware.nvidia.package`, the specialisations that share the kernel) does not evaluate to a derivation: hold the set too (`--whole-set`) |
| `pin-not-holding` | block | the host evaluates to another version than the pin's: another overlay or override wins, or the attribute is gone |

Errors: `usage`, `config-not-found`, `not-a-workspace`, `unknown-host`, `pin-unsupported`
(a nested attribute or a language package set member — an overlay does not reach them —, a
kernel from another input or an expression naming no `pkgs.linux…` attribute, several kernel
attributes without `--attr`, a kernel attribute that is not the host's kernel, a nixpkgs
input not locked as a github tree), `unknown-package`, `not-found` (no nixpkgs input),
`network` / `bad-channel` / `nix-failed` (`--rev`), `eval-failed` (the host does not evaluate),
the writer's `invalid-nix`, `import-site-missing`, `file-conflict`, `invalid-state`, `nix-missing`.

Costs: two evals of the host (≈ 3-6 s each here; `--no-check` skips both), nixfmt, writes
`installer/`. Network only with a `--rev` no lock of the workspace knows.

### `unpin`

```
nixin unpin NAME... | --all [--config DIR] [--dry-run] [--no-check]
```

Drops pins. The generated module is re-rendered (and removed, with its import line, when
nothing of nixin's is left: `pin` then `unpin` leaves the tree byte for byte as it was). One
eval after the write reports what the attributes are without the pin (`checks.eval.note`).
Result: as `pin`, with `unpinned[]` and `actions[]` of `unpinned` (each with `rev`, so
`error.undo[]` can pin again). Errors: `usage`, `not-found` (no such pin; the message lists
the host's pins), the workspace and writer errors as `pin`.

### `pins`

```
nixin pins [--check] [--config DIR]
```

Every pin of the host, read from `installer/state.json` of any config (`/etc/nixos` too: no
workspace needed, nothing is written). Result: `pins[]` of `{name, mode, attrs[], pname,
version, rev, short_rev, date, why, since, stale, stale_why, drop}` (`drop`: the `nixin
unpin NAME` line; `stale`: the pin's revision is the lock's, so it holds nothing yet),
`count`, `stale` (how many), `lock` (`input`, `node`, `rev`, `short_rev`, `date` of the
nixpkgs input), `revisions[]` (the distinct nixpkgs revisions the host knows, the lock's
first: `{source, rev, short_rev, date, last_modified, nar_hash, running}` with `source` ∈
`lock`, `committed` (`HEAD:flake.lock`), `applied` (the authoritative tree's `flake.lock`),
`running` (the running system's short revision alone, when it is none of the others);
`running` says which one the running system was built from: the UIs' revision chooser for
`pin NAME --rev`), `checked`, `checks[]`. Without `--check` nothing is evaluated. `--check` runs
the eval `pin` runs after a write (`now`: the versions the host evaluates to, `size`: the
closure size of outputs in the store) and the checks above against the lock as it is now:
after `nixin update`, that is where `kernel-modules-dropped`, `gl-skew` and `pin-not-holding`
show. Human: `19BK: 2 pins, 1 stale (flake.lock: nixpkgs 0bb2eed)`, an aligned table (`pin
attrs  held at  nixpkgs`, `(stale: …)` and the `why`), the checks, the unpin line.

Errors: `usage`, `config-not-found`, `eval-failed` (`--check`), `nix-missing`.

### `try`

```
nixin try PKG [-- ARGS...] [--program NAME] [--nixpkgs FLAKEREF|REV] [--impure] [--config DIR]
nixin try PKG --shell [--nixpkgs FLAKEREF|REV] [--impure] [--config DIR]
nixin try PKG --build-only …          (what --json always does)
```

Runs a package without installing it (nix-software-center's "launch without installing").
PKG is an attribute path (`cowsay`, `python3Packages.httpie`) of the host's *locked*
nixpkgs: the config flake's nixpkgs input as `flake.lock` pins it (found as `outdated`
finds it), written `github:nixos/nixpkgs/<rev>`. So what you try is what `nixin add`
would install. `--nixpkgs FLAKEREF` (or a bare commit of nixos/nixpkgs) uses another
nixpkgs and needs no config. `--host` is not used: the lock is the flake's.

Three steps. (1) A probe: one `nix eval` of `probe.nix` on
`<flakeref>#legacyPackages.<system>` (meta, `meta.outputsToInstall` and their out paths;
nothing built). (2) `nix build --no-link --json <flakeref>#PKG`: nix fetches the outputs
from the binary cache or builds them. Nothing roots them (no profile, no GC root), so the
next garbage collection removes them. In human mode nix's progress goes to stderr: on a
terminal nix draws its own progress bar, otherwise (a pipe, the TUI/GUI log view) its lines
are teed. (3) From the outputs: the programs in `bin/`, the desktop entries in
`share/applications/`, and the closure size (`nix path-info --recursive --size`).

Then, in human mode, nixin **gives the terminal away**. It is the one verb that does: it
execs `nix run <flakeref>#PKG -- ARGS`, or `nix shell <flakeref>#PKG --command PROG ARGS`
when PROG is not the program `nix run` would pick, or with `--shell` `nix shell
<flakeref>#PKG`, which starts `$SHELL` with the package on PATH. The exit status is the
program's. The environment is nixin's minus what its own wrapper added: leading
`/nix/store` entries of `PATH`, the `/nix/store` entries of `QT_PLUGIN_PATH`,
`NIXPKGS_QT6_QML_IMPORT_PATH` and `QML2_IMPORT_PATH`, and `PYTHONNOUSERSITE`. Without
that, a Qt app built against another Qt would load nixin's Qt plugins. Under `--json`,
and with `--build-only`, nixin stops after step 3 and returns the document.

The program to run is `--program NAME`, else `meta.mainProgram`, else the only program in
`bin/`, else the program of the one graphical desktop entry. If several are left that is
`ambiguous-program`; if `bin/` is empty, `no-program` (except with `--shell`, which runs
none of them).

| argument | meaning |
|---|---|
| `PKG` | the attribute path; letters, digits, `_ ' + -`, dots between components |
| `ARGS` | after `--`: given to the program (`nixin try cowsay -- moo`); `--json` after `--` is an ARG |
| `--shell` | a shell with the package on PATH instead of running it; takes no ARGS, no `--program` |
| `--program NAME` | which program of the package to run (one of `programs[]`) |
| `--nixpkgs FLAKEREF\|REV` | build from this nixpkgs (`nixpkgs`, `github:nixos/nixpkgs/nixos-unstable`, a commit) instead of the lock's |
| `--impure` | passes `--impure` to nix, so `NIXPKGS_ALLOW_UNFREE=1` (`_BROKEN`, `_INSECURE`) in the environment reaches nixpkgs |
| `--build-only` | stop after the build and print the result (human text); `--json` implies it |

Result (`--json`, `--build-only`):

| field | meaning |
|---|---|
| `attr` | PKG as given |
| `name`, `pname`, `version`, `description`, `license[]` | from the package (license: SPDX ids or nixpkgs short names) |
| `unfree` | `meta.unfree` |
| `nixpkgs` | `flakeref` (what every command uses), `from` (`lock` or `nixpkgs`), `rev`, `short_rev`, `date`, `input` (the input's name in flake.nix), `config` (the config path; null with `--nixpkgs`), `label` (one human line) |
| `system` | the system it was built for (`x86_64-linux`) |
| `mode` | `run` or `shell` |
| `build_only` | true under `--json` and `--build-only` |
| `impure` | `--impure` was given |
| `outputs` | output name → out path, the outputs `nix run`/`nix shell` use (`meta.outputsToInstall`: `out`, often `man`) |
| `out` | the `out` output's path (else the first) |
| `programs[]` | the executables in the outputs' `bin/`, sorted |
| `main_program` | `meta.mainProgram`, or null |
| `program` | what runs (see above); null with `--shell` when nothing can be picked |
| `desktop[]` | the desktop entries: `file`, `name`, `exec` (the `Exec=` line), `program` (its binary), `terminal` (`Terminal=true`), `hidden` (`NoDisplay`/`Hidden`) |
| `gui` | some entry is neither `terminal` nor `hidden`: a graphical app (the UIs launch it detached; CLI programs get a terminal) |
| `size` | bytes in the outputs' closure (each store path once), null if nix could not say |
| `cached` | every output was in the store before this run: nothing was fetched or built |
| `fetched` | what nix said it would do: `paths` fetched, `download` and `unpacked` bytes, `built` derivations (all 0 when cached); null when nix wrote to the terminal itself |
| `command` | the command line that runs it, as a person would type it (`nix run github:nixos/nixpkgs/<rev>#cowsay -- moo`; prefixed with `NIXPKGS_ALLOW_UNFREE=1` etc. when it needs that) |
| `argv` | the same as a list (what human mode execs; no environment prefix) |
| `shell_command` | `nix shell <flakeref>#PKG` (with `--impure` and the prefix as needed) |
| `exec` | the program's path in the store, then ARGS: what a UI can start directly (it is built); null without a program |
| `timings` | seconds: `probe`, `build`, `size`, `total` |

Without `--json`: with `--build-only`, `cowsay 3.8.4: <description>` and `from`, `out`,
`size` (plus "already in the store" or what was fetched), `programs` (`(runs)` marks the
one), `app` for a graphical one, `run`, `shell`. Otherwise one `nixin try: <command>` line
on stderr (`nixin try: a shell with … on PATH` for `--shell`; `nixin try: fetching …`
before a build that has work to do), then the program.

Errors: `usage` (no PKG, not an attribute path, `--shell` with ARGS or `--program`),
`config-not-found` (no `--nixpkgs` and no config flake), `not-found` (no such attribute:
`error.suggestions[]` has up to 5 close names; a package set such as `python3Packages`; an
alias nixpkgs removed or renamed, with its message; `--program` naming none of
`programs[]`, then `error.programs[]`; no nixpkgs input in the lock; `--nixpkgs` without
`legacyPackages`), `unfree` (nixpkgs refuses an unfree package. `error.commands[]` has
`NIXPKGS_ALLOW_UNFREE=1 nixin try PKG --impure` and the `nix run --impure` equivalent; the
message says which half is missing. The config's own `allowUnfree` does not apply: `try`
uses nixpkgs' `legacyPackages`, which ignores it), `ambiguous-program` and `no-program`
(after the build: `error.programs[]`, `out`, `size`, `cached`, `shell_command`; repeat
with `--program NAME`, or `--shell`), `build-failed` (nix build failed: the failing
builder's block with nix's log tail; in human mode on a terminal, where nix printed it
already, "its log is above". Also a package nixpkgs refuses as broken or insecure:
`error.commands[]` with `NIXPKGS_ALLOW_BROKEN=1`/`NIXPKGS_ALLOW_INSECURE=1`), `network`
(nix could not fetch the nixpkgs source or a store path), `bad-channel` (`--nixpkgs` names
a ref GitHub does not know), `eval-failed` (the probe failed otherwise; nix's message),
`nix-missing`, `internal`.

Costs: two to four nix calls (probe, build, path-info; nothing else). Writes the nix store
only (and nix's own caches). Network: none for the lock's commit when its source and the
package are in the store. Otherwise nix downloads what is missing, from the binary cache or
by building it. See [Costs](#costs-measured).

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
Its Updates tab holds a row (`h`: `pin PNAME`, or `unpin` for a held row) and the kernel
(`k`: `pin kernel`) through the writer's diff modal, and its Pins tab (`n`) shows `pins`
(`c`: `pins --check`, `u`: `unpin` of the row); after an `update` with holds it runs `pins
--check` and says how many warnings and blocks there are. While the moved lock is not
committed, `status.lock_change` is a `lock` line in Pending's header and `z` (Pending, and
the Updates tab, where `outdated` then compares the pending move) runs `update --undo`
after `update --undo --dry-run` and a confirmation. In that state the row hold and `k`
pin at the committed revision (`pin NAME --rev <old>`: "hold kernel at 7.2.14 (0bb2eed)"),
and the Pins tab's `p` (Pin…) asks for a name and a revision — `pins`' `revisions[]`
(the lock's, the committed, the applied, the running system's) or a typed commit — and
runs `pin NAME --rev REV` through the diff modal.
The Options page shows `search --kind opt|hm --limit 50` and, for the selected option,
`where NAME --kind option` (no `info`: the search row has the upstream fields); Set and
Unset are `set`/`unset` (with `--home USER` for a Home Manager option) after their
`--dry-run` diff. A template name (`users.users.<name>.shell`) gets no `where`; Set asks
for the full name first.

Try (Details, `t`) runs `try PKG --build-only` (with `--program NAME` once one is picked
from an `ambiguous-program`) and then starts a graphical app detached (its own session,
output to `$XDG_CACHE_HOME/nixin/try/<program>.log`) when the terminal has a display;
otherwise it shows the `command` and `shell_command` to copy. It installs nothing.

Under `--json`, or when stdin or stderr is not a terminal (Textual draws on stderr, so
`nixin tui 2>file` would show nothing), it is a `usage` error (exit 2). When the person
quits, it prints one line about the session on stdout (`nixin tui: wrote 1: +cowsay (…) ·
not applied yet (nixin plan, nixin apply)`, `nixin tui: applied: generation 113`) and
exits 0. While it runs it sets `GIT_TERMINAL_PROMPT=0` (unless already set) so a git
fetch cannot prompt on the terminal it owns.

Costs: whatever the verbs it calls cost (see each verb). `plan`, `apply` and `try` run in
human mode under the TUI so that nix's build output and nixos-rebuild's log stream into its
log view; the document is the same apart from `message`.

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
confirmation). Its Packages and Options pages use the verbs the TUI's do, with the holds
(an update row's hold / unhold button, "hold kernel" beside the kernel line, the update
dialog's `hold` lines) and a Pins page (Ctrl+6: `pins`, Check = `pins --check`, Unpin
through the diff dialog). "Undo lock move…" (Pending's header and the Updates tab, while
`lock_change` is not committed) runs `update --undo` through the update dialog, after
`update --undo --dry-run`; in that state the row hold reads "hold at 158.0 (0bb2eed)" and
"hold kernel at …" names the committed revision: `pin NAME --rev <old>`. The Pins page's
"Pin…" dialog (a name, a revision from `pins`' `revisions[]` or a typed commit) runs `pin
NAME --rev REV` through the diff dialog. Try (the
details pane) runs `try PKG --build-only` as the TUI does, then starts a graphical app
detached, or opens a CLI program's `command` in the person's terminal (`$TERMINAL -e`,
else the first of kitty, foot, alacritty, wezterm, ghostty, konsole, gnome-terminal,
xfce4-terminal, xterm on PATH), held open until Enter; "Shell in terminal" opens
`shell_command` there.

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
`outdated` when its Updates tab is, `search` and `where` on the Options page when typed
and selected, `browse` when Browse is opened (then `browse CATEGORY` per category). It
passes `--config` to `info` (the lock its AppStream lookup evaluates). Images are not a
verb: when a package's details open, the details pane fetches its icon (when the output
has no icon file) and its first three screenshots (search.nixos.org's WebP copies when
there are some, 2–20 KiB each, else the upstream URL), each once, into `images/` in the
cache; the Browse list fetches nothing. `plan`, `apply` and `try` run in human mode, as
under `tui`, so their logs stream into the window.

## `nixin-state`

A second program in the package, for the modules' timers (`programs.nixin.outdated`,
`programs.nixin.pending`) and for anything that shows nixin's numbers without running
nixin itself (a bar, a dashboard). It runs the verbs in-process and keeps their documents
in `$NIXIN_STATE_DIR`, else `$XDG_STATE_HOME/nixin` (`~/.local/state/nixin`):

```
nixin-state outdated [--timeout MS] [--quiet-hours H-H] [--no-notify]
nixin-state status [--force]
nixin-state summary
nixin-state report LOCK.json PUBLISHED.json
```

`-h`/`--help` anywhere on the line prints this usage on stdout and exits 0 without running
anything (a `nixin-state outdated --help` used to run the job, notification included).

| file | written by | what |
|---|---|---|
| `outdated-lock.json`, `outdated-published.json` | `outdated` | the `outdated` and `outdated --published` documents for the authoritative tree (`$NIXIN_AUTHORITATIVE`, else `/etc/nixos`) |
| `status.json` | `status` | the `status` document for the workspace (`$NIXIN_CONFIG`, else `~/nixos` when it is a git checkout, else the authoritative tree); skipped while neither tree's HEAD, the workspace's files nor `/run/current-system` changed and the last run is under an hour old |
| `summary.json` | both, under an `flock` (`.summary.lock`) | `outdated` = `{count, source (lock\|published), total, kernel {from, to} \| null, days_newer, at}` (lock mode when it worked); `lock`, `published` (the same per document); `pending` = `{count, commits, dirty, untracked, apply, workspace, at}` (`count` = commits + changed files, `installer/history.jsonl` aside; `apply`: the running system is not the authoritative HEAD's build); `pins` = `{count, stale, at}` (the workspace's `pins`, written with `status`; `stale`: pins at the lock's own revision); `errors` = `{lock\|published\|status\|pins: {code, message, at}}` (the last failure, dropped on the next success); `notified` = `{count, at}`; `at`, `host` |

`count` is packages whose version goes up. A failed verb keeps the last good numbers and
still exits 0. `outdated` sends `notify-send -a nixin` when that count changed since the
last notification (`--no-notify`: never; `--quiet-hours 23-8`: not between those hours,
the next run outside them does). `report` prints Markdown of two outdated documents.

## Error codes

| code | from | when |
|---|---|---|
| `usage` | all | bad command line, or the verb's own argument check; exit 2 |
| `internal` | all | an unexpected exception (a bug, or a source that changed its response shape); the message names the exception and `file:line` |
| `not-implemented` | cli | a verb without a handler (none today; kept for UIs built against the stubs) |
| `needs-sudo` | apply | a root step and `sudo -n` cannot get root without a password; `message` and `commands[]` are what is left to run by hand |
| `network` | discovery, outdated, try | a source is unreachable, timed out, answered 5xx after retries, or sent non-JSON; `update`: nix could not fetch the new commit; `try`: nix could not fetch the nixpkgs source or a store path |
| `http-<status>` | discovery, outdated | a 4xx the caller may act on (`http-400`, …) |
| `bad-channel` | discovery, outdated, try | unknown `--channel`, or a bare-rev `--nixpkgs` for a channel-based source; `outdated`, `update`, `try --nixpkgs`: a ref GitHub does not know |
| `not-found` | discovery, truth, writer, explain, outdated, try | `info`/`versions`: no such name. `where`: no such option. `explain`: neither the config nor upstream has NAME. `rm`/`unset`: not declared in any module nixin can edit (`hints[]`). `set`: OPTION is not an option of the host (or HM user); `--force` writes it anyway. `outdated`: no `flake.lock`, or no nixpkgs input in it; `update`: also no such `--input`. `try`: no such attribute (`suggestions[]`), a package set, a removed alias, or `--program` not among `programs[]` |
| `nix-missing` | all | `nix` (or `nix-instantiate`) is not on PATH |
| `nix-failed` | discovery, outdated | `nix search`/`nix eval`/`nix flake metadata` on `--nixpkgs` failed or printed non-JSON; `outdated`: `nix flake metadata` on the target; `update`: `nix flake update`/`lock` failed |
| `nix-timeout` | discovery | `nix flake metadata` > 120 s, `nix search` > 900 s, `nix eval` > 300 s |
| `config-not-found` | truth, writer, explain, outdated, try | no `flake.nix` in the config dir (`explain`: only when upstream was not reached either; `try`: and no `--nixpkgs`) |
| `not-a-flake` | truth, outdated | `nix flake metadata`/`nix flake archive` failed on the config |
| `unknown-host` | truth, writer, explain, outdated | `--host` is not in `nixosConfigurations`; when `--host` was not given (this machine's name, and the config has several hosts) the message says to pass it |
| `eval-failed` | truth, writer, outdated, try | the host does not evaluate (assertions, syntax, an untracked file the config imports); `outdated`: or not with the target nixpkgs (the update would break it); `try`: the probe of PKG failed. Writer: the eval after a write failed; the write stays (the document still has `changes[]`, `actions[]`, `checks`) and `error.undo[]` has the commands that take it back |
| `eval-timeout` | writer, apply | a writer eval or plan's toplevel eval took longer than 900 s (truth evals have no limit); after a write, as `eval-failed` (the write stays, `undo[]`) |
| `not-a-workspace` | writer, apply, outdated (`update`) | `--config` is `/etc/nixos` (also the default without `$NIXIN_CONFIG`), is not writable, or an edit would leave the tree; apply also: not the top of a git checkout, HEAD detached, or the authoritative tree itself |
| `unknown-user` | truth, writer, explain, outdated | `--home USER` is not an HM user of the host |
| `unknown-package` | writer | `add`: not an attribute of the host's `pkgs` |
| `invalid-nix` | writer | the value is not well-formed Nix (syntax only) or a name does not parse, or an edited file would not parse (e.g. a variable it does not bind; then `file`) |
| `type-mismatch` | writer | `set`: the value's text does not fit the option's type (`expected`, `type_info`, `default`, `example`, `option`, `value`); nothing written; `--force` writes it anyway |
| `ambiguous` | writer | several declarations match; `candidates[]`, repeat with `--where` |
| `import-site-missing` | writer | no module or no `imports = [ … ];` to import the generated file from; `line` to add |
| `file-conflict` | writer | an `installer/` file exists that nixin did not generate |
| `invalid-state` | writer | `installer/state.json` is not valid JSON or has the wrong version |
| `build-failed` | apply, try | `nix build` of the toplevel (`try`: of PKG) failed; the message is the failing builder's block with nix's log tail. `try`: also a package nixpkgs refuses as broken or insecure (`commands[]`) |
| `unfree` | try | nixpkgs refuses PKG's unfree license; `commands[]`: `NIXPKGS_ALLOW_UNFREE=1 nixin try PKG --impure` and the `nix run` equivalent |
| `ambiguous-program` | try | PKG has several programs and none is meta.mainProgram; `programs[]`, repeat with `--program NAME` (the build is done: `out`, `size`, `cached`, `shell_command`) |
| `no-program` | try | PKG has nothing in `bin/` to run; `programs[]` is empty, `shell_command` puts it on PATH |
| `cancelled` | apply | `plan`/`apply` was stopped on request (Ctrl-C, a UI's Stop) while nix built: nix's process group was killed, nothing was applied; `build` is the tracker's summary so far (`cancelled: true`) and, for `apply`, `committed` says whether the commit had been made (it stays). Only the build can be stopped: a request during the commit, the pull or the switch is refused and the run finishes |
| `nothing-to-apply` | apply | no uncommitted change, the authoritative tree has the workspace HEAD, and it is what runs; `rollback`: GEN runs, is the boot default, and the config builds it |
| `host-not-local` | apply, doctor | `rollback`, `history`, `generations`, `gc`, `doctor` with `--host H` for a host that is not this machine (`apply` goes there over ssh instead) |
| `authoritative-missing` | apply | no git checkout at the authoritative path |
| `authoritative-dirty` | apply | the authoritative tree has uncommitted changes (`files[]`); nixin only fast-forwards it |
| `authoritative-diverged` | apply | root's `git pull --ff-only` could not take the workspace (`state`: `behind`, `diverged`, `unknown`); the message has the commands to fix it |
| `switch-failed` | apply | `nixos-rebuild` (or the `nixos-switch` helper) failed, or the helper is already running; `rollback`: nix-env or switch-to-configuration failed |
| `verify-failed` | apply | after the switch the system is not the out path nixin built |
| `git-failed` | apply | a git command failed (or git is not on PATH); the message has git's last lines |
| `diverged` | apply (`sync`, `branches`) | `--pull` cannot fast-forward, or `--push` was rejected; `branches merge`: the target moved while merging |
| `merge-conflict` | apply (`branches`) | `merge`: BRANCH does not merge cleanly into the target (`branch`, `files[]`); nothing changed |
| `branch-checked-out` | apply (`branches`) | `merge`: another worktree has the target branch checked out (`path`), so it cannot move under it |
| `unknown-branch` | apply (`branches`) | no such branch (BRANCH, `--into`), or no main branch to default to |
| `busy` | apply | another `nixin apply`, `rollback`, `gc` or `branches merge`/`prune` holds the lock |
| `boot-space` | apply | `apply`/`rollback`: the new system's kernels and initrds do not fit on /boot (`boot_space` has the numbers); nothing ran as root |
| `unknown-generation` | apply | `rollback GEN`: no such system generation, or none before the running one |
| `workspace-dirty` | apply | `rollback`: the workspace has uncommitted changes (`files[]`) or unapplied commits (`commits[]`) its revert would sweep in or drop |
| `builders-untrusted` | apply | `plan`/`apply` with `--remote` or `--builders`: `nix store info --json` says the daemon does not trust this user, so it would ignore `--builders`; the message says to add the user to `nix.settings.trusted-users` (or build without `--remote`). Nothing was built or committed |
| `needs-tty` | apply | `apply --host H` for another host, not `--dry-run`, under `--json`, from a UI (a worker thread) or without a terminal: the sudo on H may ask for a password on this terminal |
| `push-failed` | apply | `apply --host H` for another host: no remote to push the workspace branch to, or the push failed (`rejected: true`: the hub has commits the workspace lacks) |
| `ssh-failed` | apply | `apply --host H` for another host: `ssh` is missing, could not connect (exit 255), or the session ended otherwise; `committed`, `pushed` say how far it got |
| `invalid-hosts-config` | apply | `~/.config/nixin/hosts.toml` (`$XDG_CONFIG_HOME/nixin/`) does not parse, or a value is of the wrong type or not a usable path / ssh destination |
| `lock-moved-others` | outdated (`update`) | nix moved lock nodes other than the requested input and its private sub-inputs; `flake.lock` was put back as it was (`moved[]`: `{node, from, to}`); `--allow-others` takes nix's result |
| `nothing-to-undo` | outdated (`update --undo`) | `flake.lock` is as committed: nothing to take back. The message says whether the move is a commit already (then `nixin pin NAME --rev <the applied revision>` holds something at the old nixpkgs, or `git revert` the commit) or the lock is also the applied one |
| `pin-unsupported` | pins | `pin`: an overlay cannot hold this: a nested attribute or a language package set member, a kernel from another flake input or an expression naming no `pkgs.linux…` attribute, a kernel attribute that is not the host's kernel, a nixpkgs input not locked as a github tree; nothing written |
| `pin-blocked` | pins | not an error of the verb: `checks.eval.code` after a `pin` (and a warning) when a `block` check fired (`kernel-modules-dropped`, `pin-not-holding`); the write stays, `hold_checks[]` says what to do |
| `gc-failed` | apply | `gc`: nix-env `--delete-generations`, the bootloader step or nix-collect-garbage failed, or a generation was still there after the delete; `step` says which, `deleted[]`/`done[]` how far it got |

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

The `generations` and `gc` rows, and the second warm figures of `status`, `list`,
`history` and `outdated` (after the start-up work: lazy imports, the hosts list cached),
are from the evening of 2026-11-02 on mbp13 (load 0.6–0.9, nix 2.34.9), the real
`~/nixos`, the built binary; warm = median of 9 runs.

The `try` rows are from 2026-11-02 on mbp13, quiet (load 0.5–0.8), from the tree with
`--json` against `~/nixos`'s lock (nixpkgs 3ec6363, its source in the store), nix's eval
cache warm for that commit. Peak RSS is the largest of nixin and its nix children.

The AppStream rows (`info`'s `appstream`, `search --appstream`, `browse`) are from
2026-11-02 late evening on mbp13 (load 1.0), `$NIXIN_CONFIG` unset (so `/etc/nixos`'s lock:
nixpkgs 3ec6363), nixin's cache cold by `--no-cache`, nix's caches warm. The profiles hold
51 listed desktop entries from 90 store paths: the scan takes 43 ms plus 14 ms.

The `outdated` rows are from 2026-11-02 on mbp13, quiet (load 0.4–0.7), against `~/nixos`
(lock nixpkgs 3ec6363, target nixos-unstable 0bb2eed): nixin's cache empty, nix's own
fetcher cache warm unless said otherwise. The 65 s run had nix's cache empty too (load 2.4).

| verb | network | runs nix | writes | cold | warm (nixin cache) | peak RSS |
|---|---|---|---|---|---|---|
| `search` (pkg, opt, all) | yes | — | cache | 0.9–1.2 s | 0.4 s | 40–50 MiB |
| `search --kind opt`, `info --kind opt` from the option index | — | — | — | 0.43–0.49 s (one 13 MB parse: 0.16 s) | — | 100 MiB |
| `search --kind hm` | yes (HM option JSON, cached 1 d) | — | cache | 1.3 s | 0.4 s | 66 MiB |
| `search --kind fn` | yes (noogle data, cached 1 d) | — | cache | 5.1 s | 0.4 s | 71 MiB |
| `search --nixpkgs REV` | — | `nix search` | cache | **204 s** (first search on a rev builds nix's eval cache; 46 s on a quiet box) | a few s for another query, 0.2 s cached | **4.6 GiB** |
| `info` | yes | — | cache | 1.2–1.9 s | 0.2 s | 40–50 MiB |
| `info --kind pkg --nixpkgs REV` | — | `nix eval` | cache | 1.2 s | 0.2 s | 105 MiB |
| `info --kind pkg` (`--appstream local`), installed app (kitty) | yes | — | cache | 0.71 s (0.67 s with `--appstream off`) | 0.25 s | 42 MiB |
| `info --kind pkg`, an app not installed (luanti) | yes | `nix eval` of the out paths (locked nixpkgs) | cache | 3.4 s | 0.25 s | 380 MiB |
| `info --kind all` (kitty) | yes | — | cache | 1.5 s | — | 72 MiB |
| `info --appstream fetch` (gnome-mines, 323 KiB substituted) | yes | `nix eval`, `nix build --dry-run`, `nix build` (impure) | cache, nix store | 26 s | 0.25 s | 380 MiB |
| `info --appstream fetch`, over the cap (supertuxkart: 690.9 MiB) | yes | `nix eval`, `nix build --dry-run` | cache | 6.2 s (nothing fetched) | — | 380 MiB |
| `search --appstream` | yes | — | cache | 0.58 s | 0.4 s | 42 MiB |
| `browse` | yes (1 request) | — | cache | 0.60 s | 0.3 s | 43 MiB |
| `browse CATEGORY` | yes (2 requests) | — | cache | 0.76 s | 0.28 s | 43 MiB |
| `versions` | yes | — | cache | 1.3 s | 0.2 s | 40 MiB |
| `status` | — | yes | cache | **77 s** (toplevel 68 s, packages 7 s) | 0.8 s under load; 0.2 s quiet (one `nix flake metadata`, no eval) | 1.4 GiB |
| `status --full` | — | yes | cache | **92 s** more (NixOS index 61 s, HM 29 s) | 1.1 s | 1.7 GiB |
| `where PACKAGE` | — | yes | cache | 12 s (packages + 2 prefix evals) | 0.6–1 s | 440 MiB |
| `where OPTION` | — | yes | cache | 3.5–9 s | 0.6–1 s | 200–230 MiB |
| `where OPTION` with the option index (mbp13, no HM users: nothing to skip) | — | yes | cache | 3.3 s (3.0 s without) | 0.59 s (0.36 s without) | 340 MiB cold, 100 MiB warm (+58 MiB: the index) |
| `status` reading the indexes' headers | — | — | — | — | 0.32 s | 43 MiB |
| `list` | — | yes (the package lists) | cache | 2.5 s (mbp13) / 3.2 s (19BK: system + brucking in one eval); quiet box (load 1.5), 2026-11-02 | 0.36–0.41 s; 0.23 s after the start-up work | 340–450 MiB (cold), 42 MiB (warm) |
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
| `history` | — | — | — | 0.2 s (35 generations); 0.14 s after the start-up work | — | — |
| `generations` | — | `nix path-info -S` for closures not sized before | cache (`closure-sizes.json`) | 3.2 s (35 generations, 33 closures sized: nix 3.05 s) | 0.14 s | — |
| `gc --dry-run` | — | `nix-store --gc --print-roots`, `-qR` over the roots, `--gc --print-dead`, `-q --size`; `sudo -n true` | — | **30 s** (the whole-store dead scan; it runs alongside the rest, ~6 s) | — | — |
| `gc` | — | the preview without the dead scan; sudo: nix-env, switch-to-configuration boot, nix-collect-garbage | system profile, /boot, the store | **77.5 s** for 31 generations (preview 6 s, delete 0.05 s, bootloader 0.23 s, collect 71 s: 7972 paths) | — | — |
| `rollback --dry-run` | — | `sudo -n true`, `nix store diff-closures` | — | 1.3 s | — | — |
| `rollback` | — | git, sudo (no nix eval or build: the generation is in the store) | workspace commit, system profile, /boot, authoritative tree | **2.9 s** (switch-to-configuration 1.7 s, pull 0.05 s); `nixos-rebuild`'s root eval is skipped | `nothing-to-apply` 0.2 s | — |
| `sync` | yes (the remote) | git | workspace refs | 1.3 s fetch from GitHub; 0.4 s push to a local bare repo | — | — |
| `outdated` | GitHub via nix (the target branch head; the nixpkgs tarball once per new commit) | 2 package-list evals (side by side since `gc-generations-speed`: **2.3 s** on mbp13) + `nix flake metadata` | cache | **4.3 s** mbp13, **6.0 s** 19BK (evals 1.9 + 2.0 s, 2.7 + 3.0 s); **65 s** when the target is a commit nix has not fetched (the tarball: resolve 58 s) | 0.37 s (`--refresh`: 4.4 s, the branch re-asked; 2.3 s with the evals side by side) | 410 MiB mbp13, 540 MiB 19BK |
| `outdated --published` | yes (search.nixos.org: 3–6 queries) | the lock-side eval only (shared with lock mode) | cache | 1.1–1.2 s with the eval cached, 1–3 s for the queries; + 2–3 s for a cold eval | 0.33 s | 43 MiB (+ the eval's) |
| `try --json` | the binary cache, through nix, for what is not in the store | probe eval, `nix build`, `nix path-info` | nix store (unrooted) | **0.95 s** when it is in the store (cowsay: probe 0.6 s, build 0.1 s); 2.3 s for one 0.7 MiB path (toilet); 5.8 s for gnome-calculator (3 paths, 2.3 MiB; its first probe 2.8 s); **16.5 s** when nix builds (lolcat: 2 derivations, 8 paths, 13 MiB) | — (no nixin cache) | 105–206 MiB |
| `try` not-found | — | the probe | — | 0.5 s (with the suggestions) | — | 105 MiB |

The option index rows are from 2026-11-03 on mbp13 (load 1.4), generation 125 (the
NixOS module from branch `modules-optindex`, `/etc/nixin/options-nixos.json`: 28 042
options, 13 MB). What the index costs the build itself is in README, "Modules".

nixin streams no progress under `--json` (in human mode `plan`/`apply` tee nix's build
output and nixos-rebuild's log to stderr; `try` lets nix draw its progress bar on a terminal
and tees its lines otherwise). A UI should run the slow verbs (`search
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
| `local/<sha1>.json` | `--nixpkgs`: flake metadata (1 h per flakeref string), search and eval answers; `info`'s AppStream lookup: a package's out paths per (flakeref, attribute) | 30 days, keyed by resolved rev (the lock's flakeref names a commit) |
| `appstream/<sha1>.json` | what one output ships (the parsed metainfo, desktop entry, icon path), per store path | 1 year: a store path never changes; a path not in the store is not cached |
| `images/<sha1>.<ext>` | the GUI's screenshots and icons, fetched when a package's details open (never ahead) | at most 4 MiB each (larger ones are refused), 64 MiB in all, least recently used pruned first |
| `truth/<host>-<key>-<name>.json` | truth evals: `toplevel`, `packages` (shared by `status`, `where`, `list` and `explain`), `prefix-<scope>-<hash>` (shared by `where`, `explain` and `set`'s type check), `hm-users` (`explain`, when there is no `packages` entry), `options-<scope>` (the index, ~10 MB), `options-<scope>-summary`; `outdated`'s `outdated-lock` and `outdated-target` (the target's key also has the target commit) | keyed by nix's flake fingerprint (tracked tree + lock) and host: any tracked edit misses; older copies of the same name are pruned |
| `truth/exclude-<nixpkgs12>-<hm12>-<host>.json` | options the full index skips (their definitions throw), per (nixpkgs rev, home-manager rev, host), seeded; files without the host (`exclude-<nixpkgs12>-<hm12>.json`, before 2026-11-02) are ignored | until deleted; delete it to re-learn |
| `apply/out-<host>-<source-hash>.json` | plan's build: `{out, drv}` per flake source store path (a content hash of the tracked tree as nix copies it, dirty edits included) | last 40 per host; an entry whose `out` was garbage-collected counts as a miss |
| `apply/commit-<host>-<sha>.json` | the same, per commit (written by `plan` on a clean tree, by `apply`, and by `rollback` for the authoritative HEAD it leaves); `status.pending`, `nothing-to-apply` and rollback's commit lookup read it without nix | last 40 per host |
| `apply/apply.lock` | `flock` held while `apply`, `rollback` or `gc` runs (`busy`) | — |
| `apply/closure-sizes.json` | `generations`/`gc`: `{store path: closure size}` from `nix path-info -S` (a closure never changes) | pruned to the generations on disk at each write |
| `apply/durations.json` | `plan`/`apply`: `{derivation name without its version: seconds}` of the builds the tracker saw, for the ETA (smoothed 0.7 new / 0.3 old) | the newest 2000 names at each write; safe to delete (the next plans have no ETA until names repeat) |

Flags: discovery `--refresh` ignores cached answers, refetches and stores; `--no-cache`
neither reads nor writes. Truth `--refresh` skips reads and still writes the fresh
results. `outdated --refresh` does the same for its evals and search.nixos.org answers, and
passes `--refresh` to the target's `nix flake metadata` (re-asks GitHub for the branch head). The writer has no cache. Plan's cache has no flag: an entry only answers when
its out path is still in the store, and the build it saves is deterministic. nix keeps its own caches (`~/.cache/nix`, the store).
Those make a second cold-nixin run of the same eval faster, and nixin does not manage them.
