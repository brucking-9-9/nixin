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
noogle); for `local-nix` it is the flakeref as given. `info` returns the exact
matches as `results` (usually one; `programs.git.enable` and `git` cannot
collide but a name may be both a package and an option).

Result records by `kind`:

| kind | fields (besides `kind`, `source`, `channel`, `rev`) |
|---|---|
| `pkg` | `name` (attribute path), `pname`, `version`, `description`, `homepage`, `license` (SPDX ids), `unfree` (null from the index, bool from `--nixpkgs`), `programs`, `main_program`, `platforms`, `outputs`, `position` (`file:line` in nixpkgs); `info` adds `matched_by` (`attr`/`pname`), `alternatives` (other attrs with that pname) and, from `--nixpkgs`, `broken`. `local-nix` search rows carry only name/pname/version/description (that is all `nix search` gives). |
| `opt` | `name`, `type`, `default`, `example` (Nix text or null), `description` (plain text), `declarations` (`nixos/modules/...nix`), `declaration_urls` |
| `hm` | `name`, `type`, `default`, `example`, `description` (Markdown as published), `declarations` (`modules/...nix`), `declaration_urls`, `read_only` |
| `program` | `name` (the binary), `package` (attribute), `pname`, `version`, `description`, `exact` (false for substring matches) |
| `fn` | `name`, `aliases`, `signature`, `description` (first paragraph), `position` (`lib/strings.nix:225`), `primop` |
| `version` | `name`, `version`, `date` (`YYYY-MM-DD`), `rev` (nixpkgs commit), `attr`, `platforms`, `programs`; the document adds `package`, `total`, `latest`, `summary`, `homepage`, `license` |

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

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
