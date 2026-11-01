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
`*.nix` files, lowest confidence).

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

Truth: `configrepo.py` and `nixin/truth/` are written and unit-tested against recorded
evals; `status`/`where` have not yet been run end to end on a real host (see
`HANDOFF.md` for the measured eval costs and the next steps).

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
