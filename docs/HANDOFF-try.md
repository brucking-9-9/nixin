# HANDOFF: `nixin try` + Try in the TUI/GUI (branch `try-verb`)

Written 2026-11-02 on mbp13, headless, as `master`. The branch was cut from `origin/main` @
318e6cc (fetched first). Nothing is pushed. This session only read `~/nixos`: no writes,
no switch, no sudo. `~/nixos` was at a198757 ("Revert "test: cowsay"") on `main` with an
uncommitted `installer/history.jsonl` edit. That edit was there before this session started
and is still there, untouched.

The job: nix-software-center's "launch without installing". `nixin try PKG` builds a package
from the host's locked nixpkgs into the store and runs it, and the details pane of both
front ends gets a Try action next to Add.

## State

Done. Nothing is half-written.

Gates at HEAD:
- `nix build`: 655 passed, 29 skipped (live) in the sandbox, then `nixin gui: self-test
  ok`. The output ships `nixin/tryout/probe.nix`; its `bin/nixin --json try cowsay
  --config ~/nixos` answers like the tree (0.65 s).
- `nix develop -c pytest`: the same 655 passed, 29 skipped. Of those, `tests/test_try.py`
  (38 + 2 live), `tests/test_try_ui.py` (11 + 1 live) and `tests/test_try_gui.py` (6 + 1
  live) are new on this branch.
- `NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos QT_QPA_PLATFORM=offscreen nix develop -c pytest
  tests/test_try.py tests/test_try_ui.py tests/test_try_gui.py -k live`: 4 passed (the CLI
  on cowsay and on a typo, then the TUI and the GUI building `hello` for real through the
  in-process backend, with nix's lines streamed into their log views).
- `ruff check`, `ruff format --check`, `nix fmt -- --check`: clean. qmllint is clean (a
  test in `test_gui.py` covers the new `TryDialog.qml`).

Files (new unless marked):
- `src/nixin/tryout/__init__.py`: `register()` and the verb: argument checks, probe, the
  refusals (unfree/broken/insecure), build, program choice, the document, the handoff.
  The package is `tryout` because `try` is a keyword.
- `src/nixin/tryout/probe.nix`: one eval of the attribute (meta, `outputsToInstall` and
  their out paths under `tryEval`; when the attribute is missing, the names beside it).
- `src/nixin/tryout/nixpkgs.py`: which nixpkgs (the lock's input via
  `outdated.target.nixpkgs_input`, or `--nixpkgs`), the probe call and its error mapping,
  the not-found suggestions (difflib, then prefix and substring matches).
- `src/nixin/tryout/build.py`: `nix build --no-link --json`, nix's fetch/build summary
  lines, `bin/`, the desktop entries, the closure size (`nix path-info --recursive --size`,
  text: `--json` warns about `--json-format` on nix 2.34).
- `src/nixin/tryout/launch.py`: `handoff` (exec), `detached` (setsid + a log file),
  `terminal`/`in_terminal`, and `child_env` (drops what nixin's wrapper put in the
  environment). `EXEC` and `SPAWN` are the test seams.
- `src/nixin/tryout/runner.py`: the nix seam (`RUN`), capture / tee / inherit.
- `src/nixin/tryout/render.py`: human text.
- `src/nixin/tui/trying.py`: `TryScreen`. `src/nixin/gui/trying.py`: `TryVM`.
  `src/nixin/gui/qml/Nixin/TryDialog.qml`.
- Edited: `src/nixin/cli.py` (the import, one `VERBS` entry, `tryout.register(sub)` before
  the stub loop), `pyproject.toml` (package data), `src/nixin/ui/view.py` (a "try" section;
  `notice()` lists `suggestions[]`), `src/nixin/ui/backend.py` (`try` in `STREAMING` and
  `CONFIG_VERBS`), `src/nixin/tui/screens.py` (Details: the Try button, `t`),
  `src/nixin/tui/modals.py` (the keys list), `src/nixin/tui/nixin.tcss` (Details' buttons
  take two rows on a phone), `src/nixin/gui/bridge.py` (the `trying` area, 6 lines),
  `DetailsPane.qml` (the button), `Main.qml` (the dialog), `qmldir`,
  `scripts/{tui,gui}-screenshots.py` (`--try`), `tests/test_cli.py` (`try` has its own
  usage tests), `tests/test_integration.py` (the three codes; `TryError` in the source
  scan), `README.md`, `docs/CONTRACT.md`, `HANDOFF.md` (the index line).
- Tests: `tests/test_try.py`, `tests/test_try_ui.py`, `tests/test_try_gui.py`;
  fixtures `tests/fixtures/try/` (probes, a desktop entry) and `tests/fixtures/tui/try_*`
  (documents for the front ends). All recorded on mbp13 today; the test docstrings say
  what was trimmed.
- Screenshots: `docs/tui/{phone,desktop}-{19-try-cli,20-try-gui-app,21-try-pick}.svg`,
  `docs/gui/{18-try-cli,19-try-gui-app,20-try-pick}.png`.

## What it does

`nixin try PKG [-- ARGS] [--shell] [--program NAME] [--nixpkgs FLAKEREF|REV] [--impure]
[--build-only]`. The steps:

1. PKG is resolved on the config's nixpkgs input as locked:
   `github:nixos/nixpkgs/3ec636362e18…` here (input `nixpkgs`, lock node `nixpkgs_2`).
2. One probe eval, about 0.5 s on a locked commit.
3. `nix build --no-link --json`. Nothing is rooted, so the next GC removes it.
4. The outputs say what the package has: programs, desktop entries, closure size.
5. Human mode execs `nix run <ref>#PKG -- ARGS`. With `--program NAME` it execs `nix shell
   … --command NAME` when NAME is not nix run's own pick. With `--shell` it execs `nix
   shell`.

`--json` (and `--build-only`) stop after step 4 with `{out, programs, size, cached,
fetched, command, argv, shell_command, exec, gui, desktop, …}`. Errors: `not-found` (with
`suggestions[]`), `unfree` (with `commands[]`), `ambiguous-program`, `no-program`,
`build-failed`, plus the shared ones. docs/CONTRACT.md "`try`" has every field and code.

The front ends call `try PKG --build-only` in human mode, so nix's lines stream into their
log views, under `busy="trying"` (one stderr capture at a time). Then:
- **TUI** (Details → `t`, or the Try button): a graphical app starts detached when
  `WAYLAND_DISPLAY`/`DISPLAY` is set; otherwise the screen shows the `nix run` and `nix
  shell` lines (`c` copies, OSC 52). For `ambiguous-program` it lists the programs; Enter
  re-runs with `--program`.
- **GUI** (Try, next to Add): a dialog. A graphical app is launched detached. A CLI program
  is opened in the terminal: `$TERMINAL -e`, else the first of kitty, foot, alacritty, …
  on PATH. The terminal runs the `nix run` line and stays open until Enter. The dialog
  also has Launch again, Run in terminal, Shell in terminal, Copy command, and a program
  list.

## Live run on mbp13 (`~/nixos`'s lock, nixpkgs 3ec6363; nothing installed)

| run | result |
|---|---|
| `nixin try cowsay --config ~/nixos -- moo` | the cow says moo; exit 0. stderr: `nixin try: nix run github:nixos/nixpkgs/3ec6363…#cowsay -- moo` |
| `… try cowsay --program cowthink -- hmm` | `nix shell …#cowsay --command cowthink hmm`: the thinking cow |
| `… try cowsay --shell` (stdin a script, `SHELL=/bin/sh`) | the shell had `/nix/store/4lbicqd7…-cowsay-3.8.4/bin/cowsay` on PATH |
| `… try figlet -- nixin` on a pty (`script -qec`) | not in the store: nix drew its own progress bar (1.5 MiB), then the exec; figlet printed "nixin" |
| `--json try gnome-calculator` | 3 paths fetched (2.3 MiB) in 2.9 s, 5.8 s total; `gui: true` (org.gnome.Calculator.desktop), program `gnome-calculator` (`gcalccmd` beside it); 904 MiB closure |
| `--json try lolcat` | built 2 derivations and fetched 8 paths (13 MiB): 16.5 s |
| `--json try steam` | `unfree` (unfreeRedistributable) with `NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure` |
| `--json try coreutils` / `zlib` | `ambiguous-program` (108 programs) / `no-program` with the `nix shell` line |
| `--json try cowsy` / `python3Packages.reqests` | `not-found`, did you mean cowsay, xcowsay, cow / python3Packages.requests, … |
| `launch.detached([gnome-calculator, "--version"])`, nixin's wrapper env simulated | its own session (sid = pid), "gnome-calculator 50.0" in `~/.cache/nixin/try/…log`, the wrapper's Qt plugin dir dropped from `QT_PLUGIN_PATH` |
| TUI and GUI `try hello` against the real backend (live tests, offscreen) | built and streamed; the GUI's launch recorded, not run |
| the built (wrapped) `bin/nixin try coreutils --program env` | what a tried program gets: PATH starts with nix shell's coreutils and then the session's own entries (none of the wrapper's python3/nixfmt), `QT_PLUGIN_PATH` holds only the session's entries, and `NIXPKGS_QT6_QML_IMPORT_PATH` and `PYTHONNOUSERSITE` are gone |

Not run: a real window on the Hyprland session. Launching one is optional per the task,
and it would appear on mbp13's screen, which shows the kiosk. The detached launch path ran
for real (above) with `--version`.

Costs are in CONTRACT's table: 0.95 s when the package is in the store, seconds for a
download, as long as nix needs for a build. Peak RSS is 105–206 MiB.

## Judgement calls

1. **Build first, then exec `nix run`.** Exec'ing `nix run` directly would make nix the
   one to report an unknown, unfree or failing package, in its own words. Building first
   lets nixin answer `not-found`/`unfree`/`build-failed` with suggestions and commands, and
   the `command` in the document is exactly what runs. The cost is a second eval, which
   nix's eval cache answers in ~0.2 s for a locked ref.
2. **`ambiguous-program` is an error under `--json` too** (the spec: "ask via error"). It
   comes after the build and carries `programs[]`, `out`, `size`, `cached` and
   `shell_command`. The front ends show a picker, and the second call takes about a second.
3. **Program choice**: `--program`, mainProgram, the only program, then (my addition) the
   program of the one graphical desktop entry. The command stays `nix run` when the
   program is what nix run would pick (mainProgram, else pname). Otherwise it becomes `nix
   shell … --command PROG`, because `nix run` would look for `bin/<pname>`.
4. **Broken and insecure** are refused like unfree, with their `NIXPKGS_ALLOW_*` command,
   but under `build-failed` rather than two more codes. Other refusals (platform,
   assertions) go to `nix build`, which says why.
5. **The environment of what nixin starts** (`launch.child_env`). nixin's wrapper prefixes
   `QT_PLUGIN_PATH`/`NIXPKGS_QT6_QML_IMPORT_PATH` with its Qt 6.12, and `PATH` with its
   Python and nixfmt. A Qt app tried from `nixin-gui` would load the wrong plugins. So
   nixin drops store entries from the Qt variables, drops leading store entries from PATH,
   and drops `PYTHONNOUSERSITE`. The executable itself is resolved on nixin's own PATH
   first. Side effect: from inside a `nix shell`/`nix develop`, the tried program does not
   inherit that shell's leading store paths.
6. **`try` uses nixpkgs as published, not the host's `pkgs`.** The spec says so ("the
   flake's nixpkgs input"), and it means the host's overlays and `nixpkgs.config`
   (`allowUnfree`) do not apply. The `unfree` message says this. `nix build
   ~/nixos#nixosConfigurations.<host>.pkgs.<attr>` would be the host's own package; a
   `--host-pkgs` flag is a possible next step.
7. **`size` is the whole closure** (cowsay's is 107 MiB because of perl). What this run
   added is in `fetched`, parsed from nix's "these N paths will be fetched (X download, …)"
   lines. It is null in human mode on a terminal, where nix's own bar replaces those lines.
8. **The GUI opens CLI programs in a terminal at once** (the spec). For a program that
   reads stdin when given no arguments (cowsay), that means a terminal waiting for input.
   "Shell in terminal" is the other button. The TUI only shows the command, since it is
   the terminal.
9. **`try` registers before cli.py's stub loop**, unlike the other layers. That gives it
   its own positionals: PKG, then ARGS after `--`, with `--json` after `--` treated as an
   ARG. Using the stub's single `args` positional breaks on `cowsay --json -- --json`.
10. **The existing screenshots were not regenerated** (`docs/gui/2-details.png` and the
    others predate the Try button). The new shots come from `--try`, to keep binary churn
    out of the merge. Run both scripts without `--try` to refresh everything.

## Next

- A real launch on the desktop session, from `nixin-gui` (installed, i.e. wrapped):
  gnome-calculator, then a Qt app built against another Qt, to see `child_env` matter.
- Cancel for a long build. Today both UIs refuse to close the screen/dialog while the build
  runs; Cancel would kill nix. That needs a handle on the subprocess in `runner`.
- `--host-pkgs` (judgement call 6).
- The stderr router has one sink, so a try and a plan cannot stream at the same time
  (`busy`). A router with a sink per job would let Try run beside a write.

## How to test

```
nix develop -c pytest tests/test_try.py tests/test_try_ui.py tests/test_try_gui.py
NIXIN_LIVE=1 NIXIN_CONFIG=~/nixos QT_QPA_PLATFORM=offscreen \
  nix develop -c pytest tests/test_try.py tests/test_try_ui.py tests/test_try_gui.py -k live
nix run . -- try cowsay --config ~/nixos -- moo        # on a terminal: nix's bar, then the cow
nix run . -- try cowsay --config ~/nixos --shell       # $SHELL with cowsay; exit leaves
nix run . -- --json try gnome-calculator --config ~/nixos
nixin-tui --config ~/nixos   → / cowsay → Enter → t      (gnome-calculator: launches it)
nixin-gui --config ~/nixos   → search → Try              (CLI: opens kitty; GUI app: launches)
nix develop -c python3 scripts/tui-screenshots.py --try   # and gui-screenshots.py --try
```

## Left behind

- `/tmp/trycost/` (`measure.py`, `cache/` from the detached-launch check, `nix-build.log`).
  This run could not `rm -rf`: `rm -rf /tmp/trycost`.
- Store paths fetched or built by the live runs: figlet, toilet, lolcat (and its 2 built
  derivations), hello, sl, gnome-calculator with libgee and libmpc. Nothing roots them;
  the next garbage collection removes them.
