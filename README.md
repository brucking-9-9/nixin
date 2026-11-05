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
| **appstream** | what a package is as an application | the AppStream metainfo and desktop entries its output ships (installed, or the locked nixpkgs' output in the store), search.nixos.org's desktop data, a shipped list of well-known apps per category |
| **apply** | make it so | `plan` = build the workspace + `nix store diff-closures` + a boot check (reboot needed? fits on /boot?); `apply` = commit the workspace, root pulls it into the authoritative checkout and switches (sudo); `sync` = fetch/pull/push the config hub repo; `history` = the system generations with the commits nixin applied; `rollback` = back to a generation, the config reverted with it; `generations` = each generation's closure size, boot entry and whether `gc` would drop it; `gc` = delete old generations, rewrite the boot menu, collect the garbage |

Why two kinds of search: search.nixos.org indexes channel-*latest*, so a hit can
name a version your `flake.lock` does not have yet. The truth layer evaluates
your own flake, which is what makes this an installer rather than another
search box — it knows what would actually change.

## CLI

```
nixin [--json] [--host H] VERB [ARGS...]

search info versions        discovery   what exists upstream, in which versions (info: + AppStream; search --appstream)
browse                      appstream   applications by category: installed, well-known, search.nixos.org's
status where list           truth       what this config gives a host (evaluates the flake); list --diff A B
explain                     explain     one option or package: the config's side and upstream's docs together
add rm set unset            writer      edit a user-owned workspace clone of the config
plan apply sync             apply       build + closure diff + boot check; commit, pull into /etc/nixos, switch
                                        (another host: push to the hub, pull and switch there over ssh); hub sync
history rollback            apply       the system generations with nixin's commits; back to one, config reverted with it
generations gc              apply       sizes, boot entries, what gc drops; delete old generations + collect garbage (sudo)
outdated update             outdated    what a nixpkgs lock update would change (--published: newer on a channel); take it
                                        (update --hold NAME,...: pin first, then move; only the named input may move)
pin unpin pins              pins        hold a package or the kernel at the lock's nixpkgs while the rest moves on; list pins
try                         try         run a package from the locked nixpkgs without installing it (--shell: a shell with it)
doctor                      doctor      read-only health check: index, timers, trees, hosts (--full), secrets, /boot
tui                         tui         the interactive terminal UI over the same documents (also nixin-tui)
gui                         gui         the desktop UI (Qt 6, QML) over the same documents (also nixin-gui)
```

With `--json` every invocation prints exactly one document on stdout, nothing on
stderr: `{"ok": true, "verb": ..., "host": ..., ...}` or `{"ok": false, "verb": ...,
"host": ..., "error": {"code": "...", "message": "..."}}`. Exit codes: 0 ok, 1 the verb
failed, 2 usage. Nothing prompts; privileged steps will fail with `needs-sudo` and the
command to run (the exception: `apply` on another host, over `ssh -t`, see below).
**[docs/CONTRACT.md](docs/CONTRACT.md)** is the contract: every verb's
arguments and result fields, every error code, exit codes, which verbs are slow (measured),
which touch the network, run nix or write files, and the cache locations.

- **discovery** asks search.nixos.org (packages, NixOS options, programs), the Home
  Manager option JSON behind home-manager-options.extranix.com, NixHub (version history)
  and noogle (lib functions). With `--nixpkgs github:nixos/nixpkgs/<rev>` it answers
  packages with `nix search` / `nix eval` on that exact rev, typically the host's lock.
  Options come from this machine's option index instead when the [modules](#modules)
  wrote one for `--host` (`source: "index"`; `--no-index` asks upstream).
  Every result carries `kind`, `source`, `channel` and `rev`.
- **appstream** (borrowed from nix-software-center and GNOME Software): `info` gives
  package rows an `appstream` object: name, summary, description, categories, icon (a
  file in the output's `share/icons`, and search.nixos.org's copy), screenshots, license,
  the desktop id. It reads the AppStream metainfo and desktop entries of an installed
  output (the profiles' links into the store, no nix), else of the locked nixpkgs' output
  if it is in the store (`nix eval` of the out paths on the config's `flake.lock`
  nixpkgs, about 3 s once, then cached), else it keeps search.nixos.org's desktop data
  (index generation 52 has desktop entries, categories, icons and screenshot copies; not
  for unfree packages or wrappers such as firefox and steam). `--appstream fetch` also
  substitutes the output when nothing has to be built and it is at most 50 MiB; `hint`
  asks search.nixos.org only; `off` leaves the field out. `search --appstream` marks GUI
  apps from search.nixos.org's data alone. `browse` lists the freedesktop main categories
  and, per category, the installed apps, a shipped list of well-known ones and
  search.nixos.org's most popular. Nothing scrapes an AppStream pool.
- **truth** evaluates the config flake (`--config`, else `$NIXIN_CONFIG`, else
  `/etc/nixos`) with the host's own `nix`. `status` reports the lock, the running
  generation, package/program counts, and with `--full` the option index. `where NAME`
  traces a package or option to the files that declare and define it, with values, and
  for an option its type (as a tree: `enum` values, list/attrset elements), default and
  example, also for a path inside a submodule (`services.openssh.settings.X11Forwarding`),
  and with an option index the option's documentation from it.
  `list` shows the installed packages themselves (system and each Home Manager user;
  `--home USER`, `--scope`, `--filter TEXT`, `--programs`), sorted by name, each with the
  line that lists it where a text scan finds one; it shares `status`'s cached eval.
  `list --diff A B` compares two hosts of the config: per scope (the system, each Home
  Manager user by name) the packages only one of them has and the versions that differ
  (rare, as they share the lock), from the same cached package lists.
- **apply** builds the workspace host (`plan`: a git+file flake, i.e. what a commit would
  contain; the out path is cached by content, so `apply` right after does not rebuild)
  and diffs its closure against `/run/current-system`. `apply` commits the workspace,
  then runs the only two root commands nixin has: `git -C /etc/nixos pull --ff-only
  <workspace> <branch>` and `nixos-rebuild switch --flake /etc/nixos#<host>`, through
  `sudo -n` (or 19BK's `nixos-switch` helper for the switch). It checks that the system
  now running is the one it built and appends `installer/history.jsonl`. Without a sudo
  ticket it stops with `needs-sudo` and the exact commands; `--dry-run` prints them.
  While nix builds, `plan` and `apply` read its events (`--log-format internal-json`)
  into a build tracker: human mode shows nix's output plus a live line, `[7/39 built,
  2 building, 12/40 fetched 35.1 MiB] system-path: buildPhase · 1m12s (eta 3m)`, redrawn
  in place on a terminal; the documents end with a `build` summary. Ctrl-C stops the
  build cleanly (`cancelled`; an apply keeps its commit and continues next time); once
  apply pulls and switches it cannot be stopped any more, and says so.
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
  **Another host**: `apply --host H` for a host that is not this machine commits the
  workspace, pushes its branch to the hub, and then over one `ssh -t` session on H runs
  `git -C ~/nixos pull --ff-only`, `sudo git -C /etc/nixos pull --ff-only ~/nixos
  <branch>` and `sudo nixos-rebuild switch --flake /etc/nixos#H`, checking between them
  that both trees end at the pushed commit. The sudo there may ask for a password on
  your terminal, so this needs one (`needs-tty` under `--json` or from a UI); `--dry-run`
  prints every command and the script and contacts nothing. How to reach H:
  `~/.config/nixin/hosts.toml`, e.g. `[mbp13]` `ssh = "mbp13"` `workspace =
  "/home/master/nixos"` (defaults: the host name, `~/nixos`, `/etc/nixos`).
  `generations` lists the same generations with each one's closure size (`nix path-info
  -S`, cached by store path), whether the boot menu has an entry for it, and what `gc`
  with the same `--keep N` / `--older-than DAYS` would drop (`--keep 3` without flags).
  `gc` deletes those generations (never the running, booted or boot-default one) with
  `nix-env --delete-generations`, rewrites the boot menu with the boot default's own
  `switch-to-configuration boot` (that also frees /boot) and runs `nix-collect-garbage`,
  as root through the same `sudo -n` backend (`needs-sudo` with the commands). It reports
  what nix freed and the free space of the store and /boot before and after;
  `--dry-run` lists what goes and estimates what it frees (the closures only those
  generations reach, plus what is garbage already) without root.
- **outdated** answers "what needs updating" for a host. By default it evaluates the
  installed package lists (system and each Home Manager user) and the kernel twice: as
  locked, and with `--override-input nixpkgs` at the lock's own ref as it resolves now
  (`--nixpkgs FLAKEREF|REV`, `--channel NAME` pick another). It compares them by pname:
  `pname  from → to` per scope, added and removed, the kernel, and the commands to take
  the update (`nix flake update nixpkgs`, `nixin plan`, `nixin apply`). Two package-list
  evals, side by side, no build: about 2.5 s, plus a nixpkgs tarball download for a new
  commit.
  `--published` compares the installed versions with a search.nixos.org channel instead
  (one query per 50 names; a heuristic, since many attributes share a pname).
  **update** takes it: it runs the command `outdated` prints (`nix flake update nixpkgs
  --flake WORKSPACE`, or `nix flake lock --override-input nixpkgs REF` for `--to`) in the
  workspace only, and returns the input's commit before and after. It never builds or
  switches: `plan` and `apply` do that, as for any other change.
- **explain NAME** puts both sides of one option or package in one document: for an
  option its type, default, example, value per scope (NixOS, each Home Manager user),
  where the config sets it and where it is declared (linked on GitHub at the locked
  revision), with upstream's full description; for a package what is installed (and
  which `programs.NAME.enable` brings it in, or which file lists it) against the
  channel's version, homepage and license. Either side may be missing (no config,
  `--offline`); `view` is the display form a help pane can show as is.
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
  or change what they declare. A `set` value is checked for syntax (so it can use
  `pkgs`, `lib` and `config`), then against the option's type before anything is
  written: `set services.openssh.enable yes` is a `type-mismatch` that shows the type,
  default and example (`--force` writes it anyway); `set OPTION` without a value shows
  them, the current value and where `set` would write. Each write is re-parsed and
  proven by one eval; when that eval fails the change stays, the verb fails
  (`eval-failed`) and says how to undo it. It refuses `/etc/nixos`: it edits a clone
  you own.

`nixin doctor` is the one read-only check of this machine, a line per check with the
command that fixes it: the option indexes belong to the running generation and the
lock; the outdated and status timers are enabled, their last run succeeded and
`~/.local/state/nixin` parses and is recent; `~/nixos` vs `/etc/nixos` (dirty, ahead,
diverged, lock drift); the hand-installed files the host's `.nix` files name
(`environmentFile`, `passwordFile`, `keyFile`, `sshKey`, `/etc/*.env`, …) exist, with the
`file:line` of each missing one; /boot can take the next kernel; failed units. It
evaluates nothing and stays local (milliseconds); `--full` also asks the hub (`git
ls-remote`) and the hosts in `hosts.toml` (`ssh -o BatchMode=yes`). Findings leave `ok`
true; `healthy` and `failed[]` say whether something needs you.

`--host` defaults to this machine; for a config without this machine but with exactly
one host, to that host. With several, nixin asks for `--host` (`unknown-host`).

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
Manager user toggle → Pending (`status.pending`, `plan` with the build tracker — built/total,
fetched, the derivations building, the log tail, the ETA — and nix's output streamed, then
the closure diff; `x` / Stop stops the build) → Apply (confirm with `apply --dry-run`'s
commands and sudo readiness, a commit message, the tracker and the switch log streamed,
the new generation; Stop works until the pull starts; without a sudo ticket the exact
root commands to copy). Status shows the lock, HEAD, the running system,
counts and caches. Packages shows what is installed (`list`: system and each Home Manager
user, a filter and a for-whom toggle on the loaded rows; Enter opens the row in Details
for its user, with Remove) and, on its Updates tab, what a nixpkgs update would change
(`outdated`, the lock's next commit or `--published`; the kernel line, the summary) with
"Update nixpkgs lock": `update --dry-run` → confirm → `update` → Pending, where Plan and
Apply take it as any other change; `h` holds a row back (`pin`, through the diff modal),
`k` the kernel, and the Pins tab (`n`) lists what is held, checks it (`c`) and unpins (`u`).
Options browses NixOS or Home Manager options (`search
--kind opt|hm`, `k` toggles; name, type and the description) and shows one in full: type,
default, example, the whole description, its value on this host and the file and line
that set it (`where`; for a Home Manager option, the user's tree), and the module that
declares it with upstream's link. Set opens the value dialog (the current value, the type
and the example under it; for a template like `users.users.<name>.shell` the name first)
→ the `set --dry-run` diff → write; Unset the same; both land on Pending. Try (Details,
`t`) builds the package with nix's output streaming (nothing is installed), then starts a
graphical app detached (`setsid`) or shows the exact `nix run` / `nix shell` lines to
copy; several programs and no mainProgram: pick one. A package's details list its
AppStream data as text (app name, summary, categories, the number of screenshots; no
images). Error documents show as `code` + message.

Keys: `/` search, `i` packages (`ctrl+4`), `o` options (`ctrl+5`), `p` pending, `s` status,
`?` all keys, `ctrl+p` command palette, `esc` back, `q` quit (`ctrl+q` while typing or
while something runs). Below 80 cells the footer groups the page keys (`i o p s go`). It is laid out for a
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
**Pending** shows `status.pending`, this session's writes and applies, and Plan (the build
tracker while nix runs: built/total, fetched, the derivations building, the log tail, the
ETA, with a Stop button next to Plan; nix's output streamed; then the closure diff);
Apply… opens the apply dialog (`apply --dry-run`: what is committed, what root runs, sudo
readiness; a commit message; the tracker and the switch log streamed, Stop until the pull
starts; the new generation; without a sudo ticket the exact root commands, with Copy and
Retry). **Status** shows the lock, HEAD, the running system, pending,
counts and caches. **Packages** has the installed list (`list`: a filter and a for-whom
combo; the selected row opens in the details pane for its user, with Remove) and Updates
(`outdated` against the lock's next commit or search.nixos.org, the kernel line, the
summary; "Update nixpkgs lock…" opens a dialog with `update --dry-run` and the holds, then
`update` runs and the window goes to Pending; a row's hold button and "hold kernel" pin
things through the diff dialog). **Pins** (Ctrl+6) lists what is held back, at which
nixpkgs, whether it still holds anything; Check runs `pins --check`, Unpin drops one.
**Options** searches NixOS or Home Manager options
(a combo picks which) and shows the selected one beside the list: type, default, example,
the description, its value on this host and where it is set (`where`; for a Home Manager
option the picked user's), the declaring module with upstream's link; Set… (prefilled with
the current value, the example and default under it, a template's `<name>` selected to
type over) and Unset go through the diff dialog and on to Pending. **Try** (next to Add)
builds the package in a dialog with nix's output streaming, then launches a graphical app
detached or opens a CLI program in your terminal (`$TERMINAL -e …`, else kitty, foot, …)
running the `nix run` line; Shell in terminal, Copy command, and a program picker when
there are several. **Browse** (`Ctrl+B`) lists the categories (installed and
search.nixos.org counts), a category's apps (● installed, ★ well-known, then the most
popular; icons only for installed apps, nothing is fetched for the list) and the selected
app in the details pane. The details pane shows a package's AppStream data when it has
some: the icon, name, summary, categories as chips, and up to three screenshots, fetched
when the details open (search.nixos.org's small WebP copies when it has them), cached
under `images/` in the nixin cache, at most 4 MiB each. Error documents appear as a notice
over the page (`code` + message, the candidates to pick for an ambiguous write, commands
to copy).

Keys: `/` search, `Ctrl+1/2/3/4/5` the pages, `Ctrl+B` Browse, `Esc` closes a dialog, `Ctrl+Enter` writes
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

Consume from a config flake: the modules below, or (package only) add `nixin` as an
input and `nixpkgs.overlays = [ nixin.overlays.default ]`, then `pkgs.nixin`.

## Modules

The flake has a NixOS module and a Home Manager module. Both declare `programs.nixin`.

```nix
# flake.nix of the config: inputs.nixin.url = "github:brucking-9-9/nixin";
nixosConfigurations.host = nixpkgs.lib.nixosSystem {
  modules = [
    inputs.nixin.nixosModules.default
    { programs.nixin.enable = true; }
  ];
};
```

```nix
# a Home Manager user (standalone, or home-manager.users.<name> / sharedModules)
{ inputs, ... }:
{
  imports = [ inputs.nixin.homeModules.default ];
  programs.nixin.enable = true;
}
```

| option | default | does |
|---|---|---|
| `enable` | false | installs nixin (`nixin`, `nixin-tui`, `nixin-gui`, `nixin-state`) |
| `package` | nixin built with the config's own `pkgs` (no overlay needed) | |
| `optionIndex.enable` | true | writes the [option index](docs/CONTRACT.md#option-indexes): NixOS `/etc/nixin/options-nixos.json`, Home Manager `$XDG_CONFIG_HOME/nixin/options-home.json` |
| `optionIndex.configRoot` | the source of the file that sets `enable` | the config's top directory, for config-relative declarations |
| `optionIndex.prune` | NixOS: `[ [ "home-manager" "users" ] ]` | option paths whose sub-options the index leaves out |
| `outdated.enable` | false | a user timer: `nixin-state outdated` at `outdated.interval` (OnCalendar, `*-*-* 09:00:00`) and 10 min after login (OnStartupSec: the user manager's start) |
| `outdated.notify`, `.notifyTimeout`, `.quietHours` | true, null, null | notify-send when the number of newer packages changes; how long it stays (ms); hours (`23-8`) it waits out |
| `pending.enable`, `pending.interval` | false, `10min` | a user timer: `nixin-state status` (the workspace's unapplied changes), and right after a switch or a commit in either tree (a path unit) |
| `authoritative`, `workspace` | `/etc/nixos`, null (`~/nixos` if a clone) | the trees the timers read |
| `user` (NixOS only) | null (every user) | the account whose user manager runs the timers (`ConditionUser`) |

**The option index.** Built with each generation from the module system's own `options`
(`lib/options-index.nix`, also the flake's `lib.optionsIndex`; nixpkgs'
`optionAttrSetToDocList`), it holds every option that generation has, the config's own
included, with type, description, default, example and where it is declared (file and
line; the config's files relative to its root). `search --kind opt|hm` and `info` answer
from it with no network (`source: "index"`, the generation's number and date), `where`
takes the options' documentation from it and does not evaluate a scope the index rules
out, and `status` says whether the files are there and belong to the running
generation. Values still come from evaluating the config. `--no-index`, `--channel` or
`--nixpkgs` ask upstream instead. Cost, measured on mbp13 (2026-11-03): its system index
has 28 042 options (13 MB) and adds 8–11 s and 0.6 GiB to the system's evaluation
(toplevel drvPath 33–35 s → 42–47 s, peak 3.0 → 3.6 GiB); 19BK's Home Manager index for
brucking has 6 286 options and adds about 0.5 s (eval 15–17 s). `optionIndex.enable =
false` turns it off.

**The timers and what reads their files.** `nixin-state` keeps `summary.json`,
`outdated-lock.json`, `outdated-published.json` and `status.json` in
`$XDG_STATE_HOME/nixin` (`~/.local/state/nixin`); CONTRACT.md, "`nixin-state`", has the
fields. The bars and dashboards stay in the config, since they are UI. On the hub's
`nixin-integration` branch 19BK's bar island
(`home/config/quickshell/bottom/NixinIsland.qml`, `services/Nixin.qml`) and mbp13's
companion footer (`hosts/mbp13/companion/local.py` → `qml/Footer.qml`) read only
`~/.local/state/nixin/summary.json` (`outdated`, `pending`, `errors`), and the weekly
flake report (`home/modules/terminal/ai/flake-report.sh`) runs `nixin-state report` on the
two outdated documents. Running the outdated check as a user on a root-owned `/etc/nixos`
needs `safe.directory = /etc/nixos` in a git config libgit2 reads (`/etc/gitconfig`,
or the user's own): nix refuses the repository otherwise (`eval-failed`, "not owned by
current user"; seen on mbp13 with nix 2.34.9). The modules leave git's config to the
config: the hub's `nixin-integration` branch sets it in `hosts/mbp13/dots.nix`.

## Status

All four layers are in. Discovery, truth and writer were run end to end as one CLI on
mbp13 (`docs/HANDOFF-integrate.md`: fixes and open questions); the apply layer then
switched mbp13 for real from its workspace (add → plan → apply → rm → apply, and
`configurationLimit 2` to free `/boot`): `docs/HANDOFF-apply.md`. The TUI ran the same
round trip on mbp13 from a 50×25 terminal (generations 113 and 114):
`docs/HANDOFF-tui.md`. The GUI ran on mbp13 against the real config (search, details,
`where`, the add preview) and on its Wayland session: `docs/HANDOFF-gui.md`. The
type-aware `set`, `explain` and the three judgement calls (an eval failure after a write
fails the verb, exclusions per host, the `--host` default) were run on mbp13 against
`~/nixos` read-only and a clone: `docs/HANDOFF-typed.md`. See `docs/HANDOFF-*.md` for
each layer.

[search.nixos.org]: https://search.nixos.org
[Home Manager Option Search]: https://home-manager-options.extranix.com
[NixHub]: https://www.nixhub.io
[nix-versions]: https://lazamar.co.uk/nix-versions
[nix-search-tv]: https://github.com/3timeslazy/nix-search-tv
[Noogle]: https://noogle.dev
[NüschtOS Search]: https://github.com/NuschtOS/search
[Searchix]: https://searchix.ovh
