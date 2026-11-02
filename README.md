# nixin

Package and option installer for NixOS + Home Manager flake configs. One CLI with
a `--json` contract; a TUI and a GUI sit on top of it.

It is built for a config that lives in a git repo with several hosts
(`nixosConfigurations.<host>`) and Home Manager users, where the authoritative
checkout is root-owned and the person edits a user-owned workspace clone.
nixin edits the workspace, builds and diffs there without root, and does the
privileged part — commit into the authoritative checkout and `nixos-rebuild
switch` — as one explicit step.

Inspired by [search.nixos.org], [Home Manager Option Search], [NixHub] /
[nix-versions], [nix-search-tv], [Noogle], [NüschtOS Search] and [Searchix]; it
talks to the first few and borrows the local-eval idea from NüschtOS.

## Layers

| layer | answers | from |
|---|---|---|
| **discovery** | what exists, in which versions | search.nixos.org (packages, NixOS options, programs), Home Manager options, NixHub version history, noogle; `nix search` on the host's *locked* nixpkgs as the exact-rev fallback |
| **truth** | what *this* host has | `nix eval` of the config flake: installed packages, every option's current value and `declarations` (file:line), lock rev, generation |
| **writer** | how to change it | new things go to generated `installer/*.nix` (nixfmt'd, from `state.json`); removing or changing something a hand-written module declares edits that module in place |
| **apply** | make it so | `plan` = build + `nix store diff-closures`; `apply` = commit into the authoritative checkout + switch (sudo); `sync` = push/pull the config hub repo |

Why two kinds of search: search.nixos.org indexes channel-*latest*, so a hit can
name a version your `flake.lock` does not have yet. The truth layer evaluates
your own flake, which is what makes this an installer rather than another
search box — it knows what would actually change.

## CLI

```
nixin [--json] [--host H] VERB [ARGS...]

search info versions        discovery
status where                truth
add rm set unset            writer
plan apply sync             apply
```

With `--json` every verb prints exactly one document:
`{"ok": true, "verb": ..., "host": ..., ...}` or
`{"ok": false, "verb": ..., "error": {"code": "...", "message": "..."}}`.
Exit codes: 0 ok · 1 verb failed (JSON error printed) · 2 usage. Nothing
prompts; privileged steps fail with `needs-sudo` and the command to run.

## Discovery

```
nixin [--json] search QUERY [--kind pkg|opt|hm|program|fn|all] [--channel C] [--limit N]
                            [--nixpkgs FLAKEREF] [--no-cache|--refresh]
nixin [--json] info NAME    [--kind pkg|opt|hm|fn|all] [--channel C] [--nixpkgs FLAKEREF]
nixin [--json] versions PKG [--limit N]
```

| kind | source | what |
|---|---|---|
| `pkg` | search.nixos.org (`search.nixos.org`) | packages of a channel; with `--nixpkgs` the host's `nix search` on that exact tree (`local-nix`) |
| `opt` | search.nixos.org | NixOS options |
| `hm` | `home-manager-options` (the option JSON behind home-manager-options.extranix.com) | Home Manager options |
| `program` | search.nixos.org | which package ships a binary (`rg` → ripgrep) |
| `fn` | `noogle.dev` | lib / builtins functions; opt-in, not part of `all` |
| versions | `nixhub.io` | which nixpkgs commit shipped which version |

`--kind all` = pkg + opt + hm + program. `--channel` takes `unstable` (default),
`stable`, `NN.MM`, or a nixpkgs branch name / flakeref (`nixos-25.11`,
`github:nixos/nixpkgs/nixos-25.11`); the document reports the concrete channel
it searched (`stable` → `26.05`). `--nixpkgs` is for the locked rev
(`github:nixos/nixpkgs/<rev>`, already in the store): packages are answered by
`nix search --json` / `nix eval --json` there; a branch-named ref also implies
the channel for options, a bare rev does not (pass `--channel`). The first
`nix search` on a rev builds nix's eval cache and takes about a minute — the
document says so in `notes`; later queries take seconds and are cached by rev.

Answers are cached under `$XDG_CACHE_HOME/nixin/` (channel list 6 h, searches
1 h, Home Manager index 1 d, NixHub 6 h, noogle 1 d, local by rev 30 d);
`--refresh` refetches and stores, `--no-cache` touches no files.

### Discovery output

One document per call. Every result carries `kind`, `source`, `channel` and
`rev` so a UI can tell *unstable says 145.0* from *your lock has 144.0*.

```json
{"ok": true, "verb": "search", "host": "19BK",
 "query": "firefox", "kind": "pkg", "limit": 20,
 "source": "search.nixos.org", "channel": "unstable", "rev": "<40-hex>", "cached": false,
 "count": 2, "results": [ ... ],
 "sources": {"search.nixos.org": {"kinds": ["pkg"], "channel": "unstable", "rev": "<40-hex>",
                                   "cached": false, "count": 2, "error": null}},
 "notes": ["..."]}
```

`source`/`channel`/`rev`/`cached` at the top are the single-source shortcut.
When several sources answer (`--kind all`, `info` without `--kind`) `source` is
`"all"`, `channel` is the concrete channel the channel-based sources searched,
`rev` is null, and `sources` carries each source's channel, rev, cache state,
count and `error` (`null`, or the first `{"code", "message"}` that source hit
while something else answered). One source can serve several kinds and fail
for only some of them, so a source may carry both an `error` and a non-zero
`count`. `ok` stays true; it is false only when no source answered at all.
`channel` is null for sources without channels (nixhub,
noogle); for `local-nix` it is the flakeref as given. A `sources` entry may
also carry that source's extras: `updated` (home-manager-options: when the
index was generated), `system` and `total` (local-nix: the system searched and
the hit count before `--limit`), and for nixhub.io the version summary fields
listed under `version` below. `info` returns the exact
matches as `results` (usually one; `programs.git.enable` and `git` cannot
collide but a name may be both a package and an option).

Result records by `kind`:

| kind | fields (besides `kind`, `source`, `channel`, `rev`) |
|---|---|
| `pkg` | `name` (attribute path), `pname`, `version`, `description`, `homepage`, `license` (SPDX ids), `unfree` (null from the index, bool from `--nixpkgs`), `programs`, `main_program`, `platforms`, `outputs`, `position` (`file:line` in nixpkgs); `info` adds `matched_by` (`attr`/`pname`), with `pname` also `alternatives` (the other attrs with that pname; absent on an `attr` match, which does not look), and, from `--nixpkgs`, `broken`. `local-nix` search rows carry only name/pname/version/description (that is all `nix search` gives). |
| `opt` | `name`, `type`, `default`, `example` (Nix text or null), `description` (plain text), `declarations` (`nixos/modules/...nix`), `declaration_urls` |
| `hm` | `name`, `type`, `default`, `example`, `description` (Markdown as published), `declarations` (`modules/...nix`), `declaration_urls`, `read_only` |
| `program` | `name` (the binary), `package` (attribute), `pname`, `version`, `description`, `exact` (false for substring matches) |
| `fn` | `name`, `aliases`, `signature`, `description` (first paragraph), `position` (`lib/strings.nix:225`), `primop` |
| `version` | `name`, `version`, `date` (`YYYY-MM-DD`), `rev` (nixpkgs commit), `attr`, `platforms`, `programs`; the document adds `package`, `total`, `latest`, and the latest release's `summary`, `homepage`, `license`, `programs` |

Errors: `{"ok": false, "verb", "host", "error": {"code", "message"}}` with codes
`network` (unreachable, 5xx, non-JSON), `bad-channel`, `not-found`, `usage`,
`nix-missing`, `nix-failed`, `nix-timeout`, `http-<status>`, and `internal`
(an unexpected exception, e.g. a source changed its response shape; the
message names the exception and where it was raised). In a multi-source call
any of these can appear as one source's `sources[...].error`.

Without `--json` the same data is a compact table (`PACKAGE VERSION
DESCRIPTION`, `OPTION TYPE DESCRIPTION`, ...; `KIND NAME VERSION/TYPE
DESCRIPTION` for `all`) with a `# N result(s) · source channel@rev` footer;
`info` prints a key/value block. `NIXIN_WIDTH` overrides the terminal width.
## Truth output

`nixin [--json] [--host H] status [--config DIR] [--refresh] [--full]` and
`nixin [--json] [--host H] where NAME [--kind package|option] [--config DIR] [--refresh]`
evaluate the config flake (`--config`, else `$NIXIN_CONFIG`, else `/etc/nixos`) with the
host's own `nix`. Results are cached under `$XDG_CACHE_HOME/nixin/truth/` keyed by nix's
flake fingerprint (tree as nix sees it + lock) and the host, so any tracked edit misses;
`--refresh` skips reads. Errors: `config-not-found`, `unknown-host`, `eval-failed` (the
last `error:` block of nix's stderr, traces and warnings stripped), `not-found`,
`nix-missing`, `eval-timeout`.

`status` is quick by default (`--quick` is accepted and means the same): it evaluates
`system.build.toplevel` and the package lists, 25–70 s cold depending on the host and its
load, well under a second from the cache. `--full` also builds the option index of the
NixOS tree and of each Home Manager user (one eval per tree, ~30 s / 1.6 GiB for NixOS,
~20 s for HM on 19BK; under load on mbp13 much slower); later quick runs show those counts
from the cache for as long as the tree is unchanged. Some nixpkgs/HM options raise an error
nix cannot catch when their service is off; the index skips them. Which ones is a property
of the module set, so the list is kept per (nixpkgs rev, home-manager rev) in
`$XDG_CACHE_HOME/nixin/truth/exclude-<nixpkgs>-<hm>.json`, seeded with the ones known on
nixpkgs 3ec6363; a new one costs one extra eval once and is added. Delete the file to
re-learn. `where` does not use the list (it asks per prefix and still answers for an
option that does evaluate on that host).

`status` fields (`ok: true`):

| field | meaning |
|---|---|
| `config` | `path`, `head` (git HEAD), `dirty`, `source` (`/nix/store/…-source` nix evaluates), `source_hash`, `fingerprint`, `last_modified`, `hosts`, `host_is_local` |
| `lock` | the root flake's nixpkgs node (`nodes.root.inputs.nixpkgs`, here `nixpkgs_2`): `node`, `rev`, `ref`, `date`, `last_modified`, `flakeref`, `nar_hash` |
| `running` | `/run/current-system`: `system`, `version` (nixos-version), `nixpkgs_rev`, `hostname`, `matches_lock` |
| `eval` | `ok`, `toplevel` (drvPath of `system.build.toplevel`; evaluating it runs the assertions), `seconds` — a host that does not evaluate makes the whole verb fail with `eval-failed` |
| `hm_users` | Home Manager users of the host (`[]` without HM) |
| `packages` | counts: `system` (`environment.systemPackages`), `home.<user>` (`home.packages`) |
| `programs` | names with `programs.<x>.enable = true`: `system`, `home.<user>` |
| `options` | per scope (`nixos`, `home.<user>`): `total`, `defined` (set by more than the option's own default — `isDefined` alone is true for every option that has a default), `in_config` (defined in a file of the config tree), `excluded` (options whose definitions cannot be evaluated), `definitions_error`, `seconds`; without `--full` the cached counts of an earlier `--full`, else `{}` |
| `cache`, `timings` | cache dir + hits/misses; seconds per eval |

`where NAME` with a dot (or `--kind option`) answers for every scope the option exists in
(`scopes[]`, each `scope` nixos/home + `user`): `mode` is `exact` (the option itself),
`sub` (a path inside an `attrsOf` option, e.g. `systemd.services.foo.enable`, dug out of
the parent's value) or `prefix` (a non-leaf path: the options under it that something sets
beyond their own default; `under_prefix` counts all of them). Each
option carries `name`, `option`, `type`, `description`, `isDefined`, `highestPrio`,
`value` (size-capped; `<derivation foo-1.2>`, `<function>`, `<… N more>` placeholders),
`declarations[]` (`source` = `config` | `nixpkgs` | `home-manager` | other input | `inline`,
`file` relative to that source — a module imported as a directory is reported as its
`default.nix` — `line`/`column` from `declarationPositions`) and
`definitions[]` — the file that sets it (same mapping), `value` that file contributes,
and for config files a `line` found by a brace-tracking text scan (`line_heuristic:
true`; `inline` definitions, i.e. attrsets written inside the `nixosSystem` call that
nixpkgs attributes to its own flake.nix, get `candidates[]` from a repo-wide scan).

`where NAME` without a dot is a package: `installed[]` (`scope`, `user`, `name`, `pname`,
`version` — attribute paths are not recoverable from derivations), `programs[]` (the
`programs.NAME.enable` options that are true, with their definitions as above),
`declared[]` (files whose definition of `environment.systemPackages`/`home.packages`
lists the derivation, with candidate `lines`), and `mentions[]` (ranked grep of tracked
`*.nix` files, lowest confidence; files under another host's directory, i.e. a path
component naming another host, are left out).

Without `--json`, `status` is one line per fact and `where` one line per declaration /
definition / mention, values in Nix syntax and capped to a terminal line; the `--json`
document has everything in full.

## Writer output

`nixin [--json] [--host H] add PKG... [--home USER]`, `rm PKG... [--home USER]`,
`set OPTION NIX-EXPR [--home USER]`, `unset OPTION [--home USER]`; each also takes
`--config DIR`, `--dry-run` (print the diff, write nothing), `--no-eval` (skip the nix
checks) and, for `rm`/`set`/`unset`, `--where FILE:LINE`. They edit a **user-owned
workspace clone** only: `--config` (or `$NIXIN_CONFIG`) pointing at `/etc/nixos` or at a
tree you cannot write is refused with `not-a-workspace`.

New things go into tool-owned modules rendered from `installer/state.json` and run through
`nixfmt`: `installer/<host>.nix` (`environment.systemPackages` + options) and
`installer/<user>-home.nix` (`home.packages` + options). Option values are raw Nix
expressions, stored as strings and written verbatim as attribute paths
(`services.openssh.settings.X11Forwarding = true;`), each checked with
`nix-instantiate --parse` first. The generated module is imported once, from the host's
entry module (the first file in its `modules = [ … ]` in flake.nix) or the HM user's
(`home-manager.users.<user> = import ./…`), with a trailing `# generated by nixin` comment;
when the scope becomes empty again the file and the import line go away, so `add` then
`rm` leaves the tree as it was. Removing or changing something a hand-written module
declares edits that module in place: just that list element or binding, comments around
it kept; `set` on an option some module already defines rewrites that definition's value
(keeping `lib.mkForce`/`lib.mkDefault`/`lib.mkIf c` around it) instead of adding a
conflicting one. Which modules belong to a host is read from the text (flake.nix
`modules`, then `imports` path literals), so it works for a host that does not evaluate.
New files are `git add --intent-to-add`ed so the flake sees them.

After writing, every touched `.nix` file is parsed again, and one eval proves the result:
the host's `environment.systemPackages` (or the user's `home.packages`) for `add`/`rm`,
the option's value for `set`/`unset`. An eval failure is reported in `checks.eval` and
`warnings`; the text change still stands and is reported.

Result (`ok: true`): `config`, `scope` (`nixos`|`home`), `user`, `dry_run`, `packages` or
`option`/`value`, `actions[]` (`name`, `action` = `added` | `already-added` |
`already-declared` | `removed` | `set` | `unset` | `unchanged`, `file`, `line`),
`changes[]` (`file` repo-relative, `kind` = `created` | `modified` | `deleted`, `diff`
unified), `imports_added[]`, `imports_removed[]`, `checks` (`parse[]`, `eval`) and
`warnings[]`. Errors: `not-a-workspace`, `config-not-found`, `unknown-host`,
`unknown-user`, `unknown-package` (not in the host's `pkgs`), `invalid-nix`, `not-found`
(with `hints`: `programs.<x>.enable`, the other scope, mentions), `ambiguous` (with
`candidates[]`; repeat with `--where FILE:LINE`), `import-site-missing` (with the exact
`line` to add), `file-conflict`, `invalid-state`. An error writes nothing.

## Develop

```
nix develop            # python, pytest, ruff, nixfmt
pytest                 # or: nix build  (runs the tests in checkPhase)
nix run . -- --json status
nix fmt
```

Consume from a config flake: add `nixin` as an input and
`nixpkgs.overlays = [ nixin.overlays.default ]`, then `pkgs.nixin`.

## Status

Skeleton: the CLI contract and the package build. The layers land in order,
discovery → truth → writer → apply, each with `--json` and tests, tested on a
second machine before touching the main one.

Truth: `configrepo.py` and `nixin/truth/` are unit-tested against recorded evals and
`status`/`where` were run end to end against the real config on mbp13 (both hosts, NixOS
and Home Manager, the `eval-failed` path); `HANDOFF.md` has the measured eval costs and
the next steps.

Writer: `nixin/writer/` is written, unit-tested on a fixture tree and run end to end
against a clone of the real config on mbp13 (both hosts, Home Manager); see
`HANDOFF.md`.

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
