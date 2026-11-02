# HANDOFF: GUI (branch `gui`)

Written 2026-11-01 (late evening) on mbp13, headless, as `master`. The branch was cut from
`tui` @ 9f5fafc (all four backend layers and the TUI). Nothing is pushed. This session did
not switch mbp13 and wrote nothing in `~/nixos` (see "Live run": the real apply round
trip was not safe tonight).

## State

`nixin gui` / `nixin-gui` works on mbp13: offscreen against the real config, and as a
window on the kiosk's Hyprland session. Every window, page and dialog in the brief is
there. The flows are the TUI's, on the same data path and view-model (`src/nixin/ui/`).
What is not done: the real cowsay apply round trip from the GUI (another session had
`~/nixos` mid-change; see "Live run"). Nothing is half-written.

Gates at HEAD:
- `nix build`: 445 passed, 22 skipped (live) in the sandbox, then `nixin-gui --self-test`
  offscreen: `nixin gui: self-test ok (offscreen, 1200x780, first frame drawn)`.
- `nix develop -c pytest`: the same 445/22. `tests/test_gui.py` is 60 of them, ~27 s.
- `ruff check`, `ruff format --check`, `nix fmt -- --check`: clean. `qmllint` over every
  QML file: no warnings (a test runs it with `--max-warnings 0`).
- Backend behaviour unchanged. The TUI's tests pass unchanged (only their import lines
  moved with the shared modules, fad9035). Human CLI output is unchanged.

**Read this if you build an older commit:** from 7d22ff3 to 6545cdb `package.nix`
defined its own `installCheckPhase` (for the self-test), and that is the phase
`buildPythonApplication` runs pytest in: those builds ran no tests at all and still
succeeded. 6545cdb moved the self-test to `postCheck`; the log now shows the pytest
summary, then the self-test line.

## Files

| file | what |
|---|---|
| `src/nixin/ui/backend.py`, `ui/view.py` | moved from `tui/` (fad9035): the one data path (`Backend.call` → `cli.document`) and the view-model, shared. `view.farewell(…, prog=)` is the only change |
| `src/nixin/gui/__init__.py` | `register()` (the `gui` verb: `--config`, `--channel`, `--self-test`; `--host` is global), `run()` (refuses `--json` and a missing display with `usage`; imports PySide6 only here), `main()` for `nixin-gui` |
| `gui/bridge.py` | **the GUI's state and flows** as Qt objects: `Gui` (the root: session, `fetch`, activity, navigation, quit guard) and one `VM` per area: `SearchVM`, `DetailsVM`, `WriterVM` (diff + set dialogs), `PendingVM`, `ApplyVM`, `StatusVM`, `NoticeVM`. State in a dict, read by QML through properties notifying on the object's own `changed` signal; actions are slots |
| `gui/models.py` | `Rows` (one generic `QAbstractListModel`: dicts with fixed keys as roles), `LogModel` (streamed log: chunks in, whole lines out, ANSI dropped, `\r` lines collapsed; fed through a queued signal from any thread), converters from view.py's dataclasses |
| `gui/jobs.py` | `Job` (one backend call on `QThreadPool`, the document back by a signal), `StderrRouter` (`sys.stderr` while the GUI runs: a streaming verb's writes go to its log, also from `apply.proc`'s pump threads) |
| `gui/app.py` | `application()`, `Window` (style, engine, `loadFromModule("Nixin", "Main")`, `find()`, `grab()`, `close()`), `run_app()`, the self-test |
| `gui/qml/Nixin/` | the module: `Theme.qml` (singleton), `Main.qml`, `TopBar`, `NavRail`, `SearchPage` + `HitRow`, `DetailsPane`, `PendingPage`, `StatusPage`, `DiffDialog`, `SetDialog`, `ApplyDialog`, `NoticeBar`; building blocks `FieldList`, `SectionHead`, `Card`, `LogView`, `DiffView`, `ClosureTable`, `DangerButton`, `DialogFooter` |
| `gui/qml/NixinStyle/` | the Qt Quick Controls style (`QQuickStyle.setStyle("NixinStyle")`): Button, BusyIndicator, ComboBox, Dialog, ItemDelegate, Label, Popup, ScrollBar, ScrollView, TabBar, TabButton, TextArea, TextField, ToolTip, from `QtQuick.Templates`; anything else falls back to Basic (`import QtQuick.Controls.Basic auto` in its qmldir) |
| `cli.py` | `gui` in `VERBS`, `gui.register(sub)` (one import, one call) |
| `pyproject.toml`, `package.nix`, `flake.nix` | `pyside6`; `nixin-gui`; QML as package data. `package.nix`: `qt6.wrapQtAppsHook`, `qtbase qtdeclarative qtwayland qtsvg`, `dontWrapQtApps`, the Qt wrapper args folded into the Python wrapper (`preFixup`), `passthru.qtEnv` (plugin and QML paths) for `preCheck` and the dev shell, `pytest-qt` and `qtdeclarative` (qmllint) in the check, the self-test as `postCheck`. Dev shell: pytest-qt, qmllint, `env = nixin.qtEnv` |
| `tests/test_gui.py` | the GUI offscreen with pytest-qt, on `tests/tui_fake.py`'s `FakeBackend` and the TUI's fixtures |
| `tests/test_cli.py` | one line: `gui` left out of the every-verb-speaks-JSON test, as `tui` |
| `scripts/gui-screenshots.py` | offscreen PNGs: the cowsay flow on the fixtures (default), or `--live --config DIR` (search, details, `add --dry-run`, an option and its Set dialog; writes nothing) |
| `docs/gui/*.png` | 9 fixture + 5 live offscreen screenshots and one from the Wayland session, 38–140 KiB each (1.4 MiB in all) |
| `docs/CONTRACT.md`, `README.md` | a `gui` section each |

## The window

1200×780 by default, at least 900×600 (search with details, the diff dialog and
Pending checked at the minimum: they fit). App
id `nixin`. Top bar: `nixin · mbp13 · ~/nixos · unstable`, `[busy]` while a write, plan
or apply runs. On the right, whenever any verb runs: the busy indicator, the newest verb
as it would be typed (`where: evaluating ~/nixos`), its seconds, and the CONTRACT's
estimate (`about a minute cold (after an edit), under a second warm`). Left rail:
Search `/`, Pending `Ctrl+2`, Status `Ctrl+3`.

- **Search** (left half): the query field (debounced 350 ms; Enter searches at once and
  moves to the results), the channel (unstable/stable), the kind tabs `pkg opt hm program
  all`, the results (badge, name in cyan or purple for options, version or type,
  description under it), the CLI's footer (`10 result(s) · search.nixos.org
  unstable@1c89083e (cached)`). Enter, Space or a click selects a row.
- **Details** (right half): title with the cue (`○ not installed`, `● installed
  [system]`, `= true`), Add / Remove (packages) or Set… / Unset (options), and "for": the
  system or a Home Manager user (the users come from `status`, loaded on the first click
  if not known; HM options take theirs from `where`). Sections info, versions (NixHub,
  packages) and "on mbp13" (`where`), each with its own spinner; "where again".
- **Diff dialog**: every write is `VERB … --dry-run` first: the command, what it does
  (actions, imports, the eval proof, warnings), the unified diff coloured per file. The
  "for" picker re-runs the preview for another user (the details' picker follows). Write
  (or `Ctrl+Enter`) writes, then `where` runs again and a passing notice says what was
  written. If the eval check failed after writing, the dialog stays, titled so.
- **Set dialog**: type, for whom, the value prefilled from `where` (else the default),
  selected; Enter or Preview → the diff dialog.
- **Pending**: `status.pending`, this session's writes and applies, Plan (nix's output
  streams into the log, then the plan fields, the closure head and table), Sync (fetch),
  Apply….
- **Apply dialog**: `apply --dry-run` (files to commit, closure, the root commands, sudo
  readiness, build cached or not) → the message field (placeholder: the generated
  message; empty uses it) → Apply → the log streams → the result (commit, generation,
  out, timings, closure). `needs-sudo`: the code and message, the exact commands in a
  read-only box, **Copy commands**, **Retry**, and "the commit and the build are done".
  `nothing-to-apply` closes the dialog into a calm (cyan) notice.
- **Status**: cards config, lock, running, pending, counts, caches (two columns when
  wide); Refresh, Full (`--full`).
- **Notice bar** (over the bottom of the page, non-modal): `code` + message, the hints and
  files, the candidates of an `ambiguous` write as buttons (a click re-runs with `--where
  FILE:LINE`), commands with Copy. Passing messages go after 5 s; errors stay until ✕.
- Keys: `/` search (a letter inside the query field), `Ctrl+1/2/3` pages, `Esc` closes a
  dialog (not while it writes or applies), `Ctrl+Enter` writes, `Ctrl+Q` quits; while a
  write, plan or apply runs, the first `Ctrl+Q` (or window close) only says so and a
  second within 3 s quits after the verb finishes.

## Live run on mbp13

Read-only against the real `~/nixos`, with the real verb handlers
(`scripts/gui-screenshots.py --live --config ~/nixos --host mbp13`, offscreen):

| step | result |
|---|---|
| search `cowsay` | 10 results from search.nixos.org (cached) |
| details | info, 7 versions from NixHub, `where`: "not installed on mbp13" |
| Add | the real `add --dry-run`: import line in `hosts/mbp13/default.nix`, new `installer/mbp13.nix`, `state.json`; cancelled, nothing written |
| kind opt, `services.openssh.enable` | `= true`, set in `hosts/mbp13/default.nix:248`, declared in nixpkgs `sshd.nix:247` |
| Set… | the dialog prefilled `true`, type `boolean`; cancelled |

On the Wayland session (`WAYLAND_DISPLAY=wayland-1`, `XDG_SESSION_TYPE=wayland`, the
Hyprland signature from `/run/user/1000/hypr/`):

- The built `result/bin/nixin-gui --config ~/nixos` started on the wayland platform as
  class `nixin`, title `nixin · mbp13`. Hyprland tiled it at 1416×850 on the kiosk's
  `kiosk-agents` workspace, *under* the kiosk's two floating panels (the agents board
  and terminal cover the screen), so a grim of its area showed the kiosk instead. That
  grab was deleted unseen by anyone else: it held another session's screen.
- So the shot in `docs/gui/wayland-mbp13.png` is from a session driven through its own
  slots (search `cowsay`, select; the dev shell's Python, the same code, the real
  backend, no keys injected into the kiosk), floated, sized 1200×780 and raised with
  `hyprctl` for the grim, then closed: `nixin gui: nothing changed`. No nixin window was
  left (`hyprctl clients`). PNG reduced to 256 colours with the theme's colours kept.

### The apply round trip: not run

The brief allowed one real apply (add cowsay → apply → rm → apply) if `agent-run ls`
showed no other agent and nothing ran `nixos-rebuild`. Both held literally, but a
`claude --agent kiosk` session (not under agent-run, so not in its list) was working on
`~/nixos` the whole evening: during this session it committed 7b6f7d6 (kernel 7.2,
switched as generation 115 at 22:47) and e5e6b85 (agents board, **not** in `/etc/nixos`),
and at the end it had uncommitted edits in six tracked files (`hosts/mbp13/agents/qml/…`,
`hosts/mbp13/kiosks/…`) and four untracked ones. An apply commits the workspace's tracked
changes, pulls into `/etc/nixos` and switches: it would have committed and switched
that session's work in progress under "+cowsay". So no write, no apply. The apply path is
covered by the fixture tests (`needs-sudo` with Copy and Retry, a successful apply with
its log and generation, `nothing-to-apply`) and was not exercised live.

To run it next time: `git -C ~/nixos status --short` shows only `installer/history.jsonl`,
`/etc/nixos` is at `~/nixos`'s HEAD, no other session is on the machine (check `ps` for
`claude`, not only `agent-run ls`). Then in `nixin-gui --config ~/nixos`: search cowsay
→ Add → Write → Pending → Plan → Apply… → Apply → generation N; Remove → Write → Apply… →
generation N+1. Check `readlink /run/current-system`, `cowsay` in
`/run/current-system/sw/bin`, `/etc/nixos` clean, the `history.jsonl` line.

### State left behind

- Running: generation 115 (`fhswnpaq…-nixos-system-mbp13-26.11.20261023.3ec6363`, the
  kiosk session's kernel-7.2 switch at 22:47; kernel 7.3.0 still booted). Not touched here.
- `~/nixos`: HEAD e5e6b85 (the kiosk session's), `/etc/nixos` at 7b6f7d6; the
  uncommitted and untracked files above are the kiosk session's; `installer/history.jsonl`
  carries the TUI session's gen-114 line as before. This session only read it.
- Caches: `~/.cache/nixin/` (search, NixHub, truth evals of the dirty tree).

## Judgement calls

1. **Python owns state and flows, QML only draws.** Everything a test needs to check is
   a Python object (`Gui.search["count"]`, `writer.rows("diff")`); QML binds and calls
   slots. The flows are the TUI's, in the same order, against the same view-model.
2. **One `changed` signal per object, not one per property.** QML re-reads that object's
   properties on any change; the objects are small. PySide 6.12 *silently drops* a
   notify signal declared on a base class (or shadowing one), so every `VM` subclass
   declares its own `changed = Signal()` and makes its properties with `state(type,
   name, changed)`. (Found by a spike: no error, the binding just never updates.)
3. **The Python root is a required property (`backend`)** of `Main.qml`, passed down
   explicitly; no context properties, no Python-registered QML module. qmllint can then
   check every file (the Python objects are `var`), and the tests can build several
   windows in one process.
4. **A real style module** (`NixinStyle`) rather than restyling controls at each use: the
   pages use plain `Button`, `TextField`, `ComboBox`…; a destructive button is the same
   Button with `palette.accent: Theme.pink` (`DangerButton`). Theme lives in `Nixin`;
   the style imports it, and Main is loaded by module URI so there is one Theme.
5. **Logs**: a `sys.stderr` proxy for the GUI's lifetime, attached to one streaming verb
   at a time (busy serialises plan and apply). Same document as `--json` apart from
   `message` (TUI judgement call 2).
6. **Nothing loads at start.** Status (a minute cold, 1.4 GiB) only when Pending or
   Status opens or HM users are needed. Selecting a result is Enter/click, not arrow
   movement: every selection runs `where` (seconds cold, ~440 MiB).
7. **Errors are a notice bar, not a dialog**, except inside the apply dialog, where
   `needs-sudo` and failures are shown in place with Copy/Retry.
8. **Offscreen = Qt Quick's software renderer**: no GPU, no EGL in the sandbox, and
   `grabWindow()` works. On Wayland the default (OpenGL) is used.
9. **qmllint runs `--bare` with absolute paths** and the Qt QML dirs from
   `NIXPKGS_QT6_QML_IMPORT_PATH`: otherwise it adds the current directory as an import
   and reports Theme.qml as "not declared as singleton in qmldir" (it is).
10. **Typed text stays plain**: every document string is `Text.PlainText` (Qt's default
    `AutoText` would render a description containing `<b>` as rich text).

## Open items / gaps

- **The live apply round trip** (above). The GUI's apply has never switched a system.
- Backend design gaps from `HANDOFF-tui.md` still stand (status has no generation; no
  cheap pending-only status; no progress under `--json`; apply's own eval is silent;
  `sync` over ssh with a passphrase; the `ok`-but-eval-failed writer case).
- Not in the GUI: `search --nixpkgs <lock>`, `fn` in the kind filter, versions beyond 8,
  `sync --pull/--push`, `apply --no-switch`. No `.desktop` file or icon is installed (the
  app id `nixin` is set; a desktop entry would be a small addition to `package.nix`).
- Fonts: Theme asks for CaskaydiaCove Nerd Font Propo/Mono (installed on mbp13; not
  checked elsewhere); without it Qt falls back to the default fonts. The package does not depend on the font.
  In the sandbox fontconfig has no config (one harmless line in the log).
- Clipboard on Wayland only works while the window has focus (compositor rule); Copy is
  a button in the focused window, so that holds, but it is tested offscreen only.
- `bridge.py` is ~1400 lines, most of it property declarations; split per area if it grows.
- On close with a verb still running, `run_app` waits for it (prints `nixin gui: waiting
  for … to finish`): a plan can take minutes. Killing it would leave nix running anyway.

## How to test

```
nix develop -c pytest tests/test_gui.py          # 60 tests, offscreen, ~27 s
nix build                                         # everything in the sandbox, then the self-test
nix develop -c python3 scripts/gui-screenshots.py                        # docs/gui/*.png (fixtures)
nix develop -c python3 scripts/gui-screenshots.py --live --config ~/nixos  # read-only
QT_QPA_PLATFORM=offscreen result/bin/nixin-gui --self-test
result/bin/nixin-gui --config ~/nixos            # on a Wayland or X11 session
```

On mbp13 from ssh: `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1
XDG_SESSION_TYPE=wayland result/bin/nixin-gui --config ~/nixos &`; the kiosk's floating
panels cover tiled windows, so float and raise it (`hyprctl dispatch setfloating
address:…`, `alterzorder top,…`) before a grim, and kill it afterwards.
