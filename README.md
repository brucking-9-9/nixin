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
| **apply** | make it so | `plan` = build the workspace + `nix store diff-closures`; `apply` = commit the workspace, root pulls it into the authoritative checkout and switches (sudo); `sync` = fetch/pull/push the config hub repo |

Why two kinds of search: search.nixos.org indexes channel-*latest*, so a hit can
name a version your `flake.lock` does not have yet. The truth layer evaluates
your own flake, which is what makes this an installer rather than another
search box — it knows what would actually change.

## CLI

```
nixin [--json] [--host H] VERB [ARGS...]

search info versions        discovery   what exists upstream, in which versions
status where                truth       what this config gives a host (evaluates the flake)
add rm set unset            writer      edit a user-owned workspace clone of the config
plan apply sync             apply       build + closure diff; commit, pull into /etc/nixos, switch; hub sync
```

With `--json` every invocation prints exactly one document on stdout, nothing on
stderr: `{"ok": true, "verb": ..., "host": ..., ...}` or `{"ok": false, "verb": ...,
"host": ..., "error": {"code": "...", "message": "..."}}`. Exit codes: 0 ok, 1 the verb
failed, 2 usage. Nothing prompts; privileged steps will fail with `needs-sudo` and the
command to run. **[docs/CONTRACT.md](docs/CONTRACT.md)** is the contract: every verb's
arguments and result fields, every error code, exit codes, which verbs are slow (measured),
which touch the network, run nix or write files, and the cache locations.

- **discovery** asks search.nixos.org (packages, NixOS options, programs), the Home
  Manager option JSON behind home-manager-options.extranix.com, NixHub (version history)
  and noogle (lib functions). With `--nixpkgs github:nixos/nixpkgs/<rev>` it answers
  packages with `nix search` / `nix eval` on that exact rev, typically the host's lock.
  Every result carries `kind`, `source`, `channel` and `rev`.
- **truth** evaluates the config flake (`--config`, else `$NIXIN_CONFIG`, else
  `/etc/nixos`) with the host's own `nix`. `status` reports the lock, the running
  generation, package/program counts, and with `--full` the option index. `where NAME`
  traces a package or option to the files that declare and define it, with values.
- **apply** builds the workspace host (`plan`: a git+file flake, i.e. what a commit would
  contain; the out path is cached by content, so `apply` right after does not rebuild)
  and diffs its closure against `/run/current-system`. `apply` commits the workspace,
  then runs the only two root commands nixin has: `git -C /etc/nixos pull --ff-only
  <workspace> <branch>` and `nixos-rebuild switch --flake /etc/nixos#<host>`, through
  `sudo -n` (or 19BK's `nixos-switch` helper for the switch). It checks that the system
  now running is the one it built and appends `installer/history.jsonl`. Without a sudo
  ticket it stops with `needs-sudo` and the exact commands; `--dry-run` prints them.
  `sync` fetches the hub and pulls (`--ff-only`) or pushes the workspace branch.
- **writer** puts new things into generated `installer/<host>.nix` /
  `installer/<user>-home.nix` (from `installer/state.json`, nixfmt'd, imported once from
  the host's or user's entry module). It edits hand-written modules in place to remove
  or change what they declare. Each write is re-parsed and proven by one eval. It
  refuses `/etc/nixos`: it edits a clone you own.

Answers and evals are cached under `$XDG_CACHE_HOME/nixin/` (see CONTRACT.md, "Caches").

## Develop

```
nix develop                     # python + requests, pytest, ruff, nixfmt
pytest                          # offline: fixtures and fakes, no nix, no network
nix build                       # the same tests in the sandbox (checkPhase)
ruff check && ruff format --check
nix fmt -- --check              # or `nix fmt` to format; no path = every tracked *.nix
NIXIN_LIVE=1 pytest             # + live tests (real services; nix against $NIXIN_CONFIG)
nix run . -- --json status --config ~/nixos
PYTHONPATH=src:$PYTHONPATH python3 -m nixin.cli --json search firefox   # from the tree
```

Keep `$PYTHONPATH` when you put `src` on it: the dev shell carries `requests` there.

Consume from a config flake: add `nixin` as an input and
`nixpkgs.overlays = [ nixin.overlays.default ]`, then `pkgs.nixin`.

## Status

All four layers are in. Discovery, truth and writer were run end to end as one CLI on
mbp13 (`docs/HANDOFF-integrate.md`: fixes and open questions); the apply layer then
switched mbp13 for real from its workspace (add → plan → apply → rm → apply, and
`configurationLimit 2` to free `/boot`): `docs/HANDOFF-apply.md`. See `docs/HANDOFF-*.md`
for each layer.

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
