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
| **apply** | make it so | `plan` = build the workspace + `nix store diff-closures` + a boot check (reboot needed? fits on /boot?); `apply` = commit the workspace, root pulls it into the authoritative checkout and switches (sudo); `sync` = fetch/pull/push the config hub repo; `history` = the system generations with the commits nixin applied; `rollback` = back to a generation, the config reverted with it |

Why two kinds of search: search.nixos.org indexes channel-*latest*, so a hit can
name a version your `flake.lock` does not have yet. The truth layer evaluates
your own flake, which is what makes this an installer rather than another
search box — it knows what would actually change.

## CLI

```
nixin [--json] [--host H] VERB [ARGS...]

search info versions        discovery   what exists upstream, in which versions
status where list           truth       what this config gives a host (evaluates the flake)
add rm set unset            writer      edit a user-owned workspace clone of the config
plan apply sync             apply       build + closure diff + boot check; commit, pull into /etc/nixos, switch; hub sync
history rollback            apply       the system generations with nixin's commits; back to one, config reverted with it
outdated update             outdated    what a nixpkgs lock update would change (--published: newer on a channel); take it
try                         try         run a package from the locked nixpkgs without installing it (--shell: a shell with it)
tui                         tui         the interactive terminal UI over the same documents (also nixin-tui)
gui                         gui         the desktop UI (Qt 6, QML) over the same documents (also nixin-gui)
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
  `list` shows the installed packages themselves (system and each Home Manager user;
  `--home USER`, `--scope`, `--filter TEXT`, `--programs`), sorted by name, each with the
  line that lists it where a text scan finds one; it shares `status`'s cached eval.
- **apply** builds the workspace host (`plan`: a git+file flake, i.e. what a commit would
  contain; the out path is cached by content, so `apply` right after does not rebuild)
  and diffs its closure against `/run/current-system`. `apply` commits the workspace,
  then runs the only two root commands nixin has: `git -C /etc/nixos pull --ff-only
  <workspace> <branch>` and `nixos-rebuild switch --flake /etc/nixos#<host>`, through
  `sudo -n` (or 19BK's `nixos-switch` helper for the switch). It checks that the system
  now running is the one it built and appends `installer/history.jsonl`. Without a sudo
  ticket it stops with `needs-sudo` and the exact commands; `--dry-run` prints them.
  `plan` and `apply` also compare the new system's kernel, initrd, modules, kernel
  params, bootspec and specialisations with the running one: `reboot_needed` and a
  "needs a reboot" line; and they add up the new kernel/initrd files the bootloader will
  copy to /boot (systemd-boot's `configurationLimit` window, read from the built system)
  against `df /boot`: a warning above 80 %, and `apply` refuses (`boot-space`) when they
  do not fit. `sync` fetches the hub and pulls (`--ff-only`) or pushes the workspace
  branch. `history` lists the generations (date, kernel, NixOS version, `*` running, `b`
  booted) with the commit and message from `installer/history.jsonl`. `rollback [GEN]`
  (default: the previous generation) runs `nix-env --switch-generation GEN` and GEN's
  `switch-to-configuration switch` as root, and when nixin knows the commit GEN was built
  from (history, plan's cache) it makes one revert commit in the workspace back to that
  tree and pulls it into `/etc/nixos`, so the next `apply` does not switch back; without
  such a commit it says so and leaves the config.
- **outdated** answers "what needs updating" for a host. By default it evaluates the
  installed package lists (system and each Home Manager user) and the kernel twice: as
  locked, and with `--override-input nixpkgs` at the lock's own ref as it resolves now
  (`--nixpkgs FLAKEREF|REV`, `--channel NAME` pick another). It compares them by pname:
  `pname  from → to` per scope, added and removed, the kernel, and the commands to take
  the update (`nix flake update nixpkgs`, `nixin plan`, `nixin apply`). Two package-list
  evals, no build: about 5 s, plus a nixpkgs tarball download for a new commit.
  `--published` compares the installed versions with a search.nixos.org channel instead
  (one query per 50 names; a heuristic, since many attributes share a pname).
  **update** takes it: it runs the command `outdated` prints (`nix flake update nixpkgs
  --flake WORKSPACE`, or `nix flake lock --override-input nixpkgs REF` for `--to`) in the
  workspace only, and returns the input's commit before and after. It never builds or
  switches: `plan` and `apply` do that, as for any other change.
- **try** runs a package without installing it (nix-software-center's "launch without
  installing"): `nixin try cowsay -- moo`, `nixin try htop --shell`. PKG is an attribute
  of the host's *locked* nixpkgs (the config's nixpkgs input as `flake.lock` pins it;
  `--nixpkgs` picks another), so you try what `nixin add` would install. One probe eval
  (meta, outputs), `nix build --no-link` (nix's progress on stderr; nothing is rooted, the
  next garbage collection removes it), then nixin execs `nix run <flakeref>#PKG -- ARGS`,
  or `nix shell` with `--shell`. It picks `meta.mainProgram`, else the only program, else
  asks (`ambiguous-program`, `--program NAME`). Unfree packages need
  `NIXPKGS_ALLOW_UNFREE=1 … --impure`, and the `unfree` error says so. `--json` only builds
  and returns `out`, `programs`, `size`, `cached`, `command`, `gui`: what a UI needs to say
  "ready, 12 MiB" and offer to launch.
- **writer** puts new things into generated `installer/<host>.nix` /
  `installer/<user>-home.nix` (from `installer/state.json`, nixfmt'd, imported once from
  the host's or user's entry module). It edits hand-written modules in place to remove
  or change what they declare. A `set` value is checked for syntax only, so it can use
  `pkgs`, `lib` and `config`. Each write is re-parsed and proven by one eval. It
  refuses `/etc/nixos`: it edits a clone you own.

Answers and evals are cached under `$XDG_CACHE_HOME/nixin/` (see CONTRACT.md, "Caches").

## TUI

```
nixin-tui --config ~/nixos          # = nixin --config ~/nixos tui; --host H, --channel stable
```

A Textual UI that shows the same documents the CLI prints with `--json`: it calls the verb
handlers in-process, in worker threads, and renders their documents
(`src/nixin/ui/view.py`, shared with the GUI, turns them into display rows). Search (kind filter, channel,
typing debounced, the CLI's `source channel@rev` footer) → Details (info, versions, and
`where` for this host: installed? declared where?) → Add / Remove / Set / Unset, each
previewed as the writer's `--dry-run` diff before anything is written, with a Home
Manager user toggle → Pending (`status.pending`, `plan` with nix's build output streamed
and the closure diff) → Apply (confirm with `apply --dry-run`'s commands and sudo
readiness, a commit message, the switch log streamed, the new generation; without a sudo
ticket the exact root commands to copy). Status shows the lock, HEAD, the running system,
counts and caches. Packages shows what is installed (`list`: system and each Home Manager
user, a filter and a for-whom toggle on the loaded rows; Enter opens the row in Details
for its user, with Remove) and, on its Updates tab, what a nixpkgs update would change
(`outdated`, the lock's next commit or `--published`; the kernel line, the summary) with
"Update nixpkgs lock": `update --dry-run` → confirm → `update` → Pending, where Plan and
Apply take it as any other change. Try (Details, `t`) builds the package with nix's output
streaming (nothing is installed), then starts a graphical app detached (`setsid`) or shows
the exact `nix run` / `nix shell` lines to copy; several programs and no mainProgram: pick
one. Error documents show as `code` + message.

Keys: `/` search, `i` packages (`ctrl+4`), `p` pending, `s` status, `?` all keys, `ctrl+p`
command palette, `esc` back, `q` quit (`ctrl+q` while typing or while something runs).
Below 80 cells the footer groups the page keys (`i p s go`). It is laid out for a
phone over ssh (50×25: panes stacked, the description column dropped) as well as 120+
columns. Colours: `src/nixin/tui/theme.py`. Screenshots of both sizes: `docs/tui/`
(`scripts/tui-screenshots.py` makes them, from the test fixtures or `--live`).

## GUI

```
nixin-gui --config ~/nixos          # = nixin --config ~/nixos gui; --host H, --channel stable
nixin-gui --self-test               # load the window, draw one frame, exit (checks the Qt setup)
```

A Qt 6 window (PySide6 + QML, Wayland or X11) over the same documents, through the same
data path and view-model as the TUI (`src/nixin/ui/`: the verb handlers in-process, and
the documents turned into display rows). The Python side (`src/nixin/gui/bridge.py`)
exposes them as Qt objects and list models; verbs run on a thread pool, with a busy
indicator in the top bar saying what runs, for how long, and what to expect.

The left rail has **Search** (the query, typing debounced; the kind filter; the channel;
the results; the CLI's source footer) with the selected row's **details** beside it (info,
versions from NixHub, `where` on this host: installed? set where? the value), and Add /
Remove / Set… / Unset for the system or a Home Manager user. Every write opens the
writer's `--dry-run` diff in a dialog first (the user can be changed there), then Write.
**Pending** shows `status.pending`, this session's writes and applies, and Plan (nix's
build output streamed, then the closure diff); Apply… opens the apply dialog (`apply
--dry-run`: what is committed, what root runs, sudo readiness; a commit message; the
switch log streamed; the new generation; without a sudo ticket the exact root commands,
with Copy and Retry). **Status** shows the lock, HEAD, the running system, pending,
counts and caches. **Packages** has the installed list (`list`: a filter and a for-whom
combo; the selected row opens in the details pane for its user, with Remove) and Updates
(`outdated` against the lock's next commit or search.nixos.org, the kernel line, the
summary; "Update nixpkgs lock…" opens a dialog with `update --dry-run`, then `update`
runs and the window goes to Pending). **Try** (next to Add) builds the package in a dialog
with nix's output streaming, then launches a graphical app detached or opens a CLI program
in your terminal (`$TERMINAL -e …`, else kitty, foot, …) running the `nix run` line; Shell
in terminal, Copy command, and a program picker when there are several. Error documents
appear as a notice over the page (`code` + message, the candidates to pick for an
ambiguous write, commands to copy).

Keys: `/` search, `Ctrl+1/2/3/4` the pages, `Esc` closes a dialog, `Ctrl+Enter` writes
in the diff dialog, `Ctrl+Q` quits (a second time while a write, a plan or an apply runs).
The palette is the quickshell one (`src/nixin/gui/qml/Nixin/Theme.qml`: background
`#120B10`, cyan accent, pink for errors and destructive actions, purple for options), and
the controls are a Qt Quick Controls style of their own (`qml/NixinStyle/`). The Wayland
app id is `nixin`. Screenshots: `docs/gui/` (`scripts/gui-screenshots.py`, offscreen,
from the test fixtures or `--live`).

## Develop

```
nix develop                     # python + requests + textual + pyside6, pytest(-qt), qmllint, ruff, nixfmt
pytest                          # offline: fixtures and fakes, no nix, no network; the GUI offscreen
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
`configurationLimit 2` to free `/boot`): `docs/HANDOFF-apply.md`. The TUI ran the same
round trip on mbp13 from a 50×25 terminal (generations 113 and 114):
`docs/HANDOFF-tui.md`. The GUI ran on mbp13 against the real config (search, details,
`where`, the add preview) and on its Wayland session: `docs/HANDOFF-gui.md`. See
`docs/HANDOFF-*.md` for each layer.

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
