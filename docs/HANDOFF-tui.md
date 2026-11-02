# HANDOFF: TUI (branch `tui`)

> Since the GUI branch, `backend.py` and `view.py` live in `src/nixin/ui/` (shared by the TUI
> and the GUI); the paths below are as they were when this was written.

Written 2026-11-01 (late evening) on mbp13, headless, as `master`. The branch was cut from
`apply` @ 1f47173 (all four backend layers). Nothing is pushed. This session switched mbp13
twice, both from the TUI (generations 113 and 114). It was the only session running.

## State

`nixin tui` / `nixin-tui` works on mbp13 against the real config, at phone size (50×25)
and desktop size (120×40). Every screen in the brief is there. The cowsay round trip ran
from a real terminal: add → plan → apply → rm → apply. Nothing is half-done.

Gates at HEAD:
- `nix build`: 385 passed, 22 skipped (live) in the sandbox; Textual runs headless there,
  offline. `nix develop -c pytest`: the same. `tests/test_tui.py` is 53 of them, ~30 s.
- `ruff check`, `ruff format --check`, `nix fmt -- --check`: clean.
- The backend's behaviour is unchanged. One shared-code change: `cli.main`'s dispatch
  moved into `cli.document(ns)` (dcd9334), which `main` calls. Human CLI output is
  byte-identical.

## Files

| file | what |
|---|---|
| `src/nixin/tui/__init__.py` | `register()` (the `tui` verb: `--config`, `--channel`; `--host` is global), `run()` (refuses `--json` and a missing terminal with `usage`; imports Textual only here), `main()` for `nixin-tui` |
| `tui/backend.py` | **the only data path**: `Backend.call(verb, *args, stream=False)` builds the argv a person would type, parses it with `cli._parser()` and runs `cli.document(ns)`. Returns the `--json` document with its envelope. `stream=True` runs the verb in human mode (see judgement call 2). Keeps `calls[]` |
| `tui/view.py` | **the view-model**: documents → display rows, pure, no Textual (see "For the GUI") |
| `tui/theme.py` | the palette as a Textual `Theme`, plus `STYLES` (view-model style → colour) |
| `tui/nixin.tcss` | layout; breakpoints `Screen.-narrow` (< 80 cells) and `Screen.-wide`; standard theme variables only |
| `tui/app.py` | `NixinApp`: `fetch()` (worker thread → callback on the UI thread; exclusive groups; spinner; log capture; `busy`), the writer flow, the apply flow, status loading, the HM users, navigation, command palette, quit guard |
| `tui/screens.py` | `SearchScreen`, `DetailsScreen`, `PendingScreen`, `ApplyScreen`, `StatusScreen`, `TopBar` |
| `tui/modals.py` | `DiffModal` (writer preview), `SetOptionModal`, `ApplyModal` (confirm + message), `NoticeModal` (error documents; candidates → `--where`), `HelpModal` |
| `tui/widgets.py` | `Fields`, `Spans`, `Line`, `NoticeBox`, `Spinner`, `LogView`; Rich `Text` throughout (document text is never parsed as markup) |
| `cli.py` | `cli.document(ns)` (refactor, own commit); `tui` in `VERBS`; `tui.register(sub)` |
| `pyproject.toml`, `package.nix` | `textual` dependency (nixpkgs: 8.2.8), `nixin-tui` script, `*.tcss` package data |
| `tests/test_tui.py` | view-model on recorded documents; the app through `App.run_test()` at both sizes |
| `tests/tui_fake.py` | `FakeBackend` (routes `"verb [word…]"` → fixture names, successive answers, `<name>.log` streamed to stderr when `stream=True`), `screen_text()` |
| `tests/fixtures/tui/` | documents recorded on mbp13 (search, info, versions, where, status, `add --dry-run`, plan, `apply --dry-run`), and hand-made ones in the CONTRACT shape for the cowsay flow (`add`, `plan_cowsay`, `apply_dry_cowsay`, `apply_needs_sudo`, `apply_ok`, `sync`, `rm_git_ambiguous`). 128 K |
| `scripts/tui-screenshots.py` | SVG screenshots at 50×25 and 120×40: the cowsay flow on the fixtures (default), or `--live --config DIR` (search, details, `add --dry-run`; writes nothing) |
| `docs/tui/*.svg` | 14 fixture + 6 live screenshots, 15–36 KiB each |
| `tests/test_cli.py` | one line: `tui` is left out of the every-verb-speaks-JSON test (it has its own) |
| `docs/CONTRACT.md`, `README.md` | a `tui` section each |

## Screens

- **Search**: a one-line query box, kind tabs `pkg opt hm program all` (`k` cycles), the
  channel in the top bar (`c` cycles unstable/stable). Typing is debounced (0.35 s) and
  Enter searches at once. Results go into a table; at < 60 cells it is name + version/type
  and the description goes under it. The footer is the CLI's own
  (`discovery.format._footer`): `10 result(s) · search.nixos.org unstable@1c89083e (cached)`.
  Enter moves the focus to the results even with no hits, so `k` works at once.
- **Details**: discovery's row (refreshed by `info`), `versions` (NixHub, packages),
  and `where` for this host. The title line carries a cue (`● installed [system]`,
  `○ not installed`, `= true`). Wide: two panes; narrow: stacked. Buttons and keys:
  Add/Remove (packages), Set/Unset (options), `h` cycles the Home Manager user.
- **Writer**: every write is `VERB … --dry-run` first, then the diff modal (actions,
  imports, warnings, the unified diff coloured), then `w` writes, then `where` runs
  again. An eval check that failed (`ok: true`, `checks.eval.ok: false`) opens the modal
  again, titled "written, but the eval check FAILED". Writer errors open a notice;
  `ambiguous` (and `--where` misses) list the candidates, and picking one re-runs with
  `--where FILE:LINE`.
- **Pending**: `status.pending`, the writes and applies of this session, `plan` (`b`)
  with nix's output streamed into a log view, then the plan fields and the closure diff
  table. Sync (`y`) shows `sync`'s report.
- **Apply** (`a`): `apply --dry-run` → confirm dialog (files to commit, closure, the root
  commands as a person types them, sudo readiness, cached or still to build, the
  generated commit message as the placeholder) → the apply screen streams git's and
  nixos-rebuild's output and ends with the commit, the generation and the timings. On
  `needs-sudo` it shows the commands in a read-only text box: `c` copies them (OSC 52,
  works over ssh where the terminal allows it), `r` retries after `sudo -v`.
  `nothing-to-apply` is a calm notice, not an error.
- **Status** (`s`): config path/HEAD/dirty/hosts, the lock (rev, ref, date), the running
  system (version, nixpkgs rev vs lock, generation — see design gap 1), pending, counts,
  caches and timings; `r` refresh, `f` `--full`.
- Everywhere: the top bar (`nixin · mbp13 · ~/nixos · unstable [planning]`), `?` keys,
  `ctrl+p` palette (Search, Pending, Plan, Apply…, Status, Sync, Channel, Keys, Quit),
  `esc` back, `q` quit. `q` refuses while a write or an apply runs (`ctrl+q` forces).

## Live run on mbp13

All from a real terminal (tmux panes, the `nix build` binary, `--config ~/nixos --host mbp13`).

| step | result |
|---|---|
| 50×25: search `cowsay` | 10 results from search.nixos.org (cached), name + version columns, description under the table |
| details | info, 7 versions from NixHub, `where`: "not installed on mbp13" (4 s) |
| `a` | the real `add --dry-run` diff (import line in `hosts/mbp13/default.nix`, new `installer/mbp13.nix`, `state.json`); cancelled: nothing written |
| `q` | `nixin tui: nothing changed`, exit 0 |
| 120×40: search `openssh`, `k` → opt | 20 options; `services.openssh.enable` details: `= true`, set in `hosts/mbp13/default.nix:248` |
| `e`, value `false` | the Set modal prefilled `true` (from `where`); preview: in-place edit of line 248; cancelled |
| a mistyped value | `invalid-nix` notice with nix's message |
| Status, Pending | warm `status` 0.15 s |
| **round trip, 50×25** | |
| add cowsay → `w` | written; the cue turns `● installed [system]` |
| Pending → `b` | plan: eval 36.1 s, build 2.4 s (gen 110's outputs were still in the store); closure `+ cowsay 3.8.4 +51.2 KiB`. A second plan: cached by source hash, 2 s |
| `a` | confirm: 4 files, `root: sudo: ready`, build cached |
| Enter, message `test(tui): +cowsay` | commit **b638def**, pulled, switched: **generation 113** (`9c8liyka…`), 30.3 s (build 0.5 s, switch 29.6 s); the log streamed live |
| `a` again | `nothing-to-apply` (generation 113) |
| rm cowsay → `w` | written in 6 s (generated files deleted, import removed) |
| Pending → `a` (no plan) | confirm says "not built yet: apply builds first" |
| Enter, `test(tui): -cowsay` | commit **f2e48e8**, **generation 114** = `zknqa0x8…` (gen 112's path), 64.9 s (build 34.5 s, switch 30.3 s) |
| `q` | `nixin tui: applied: generation 113` (first session), exit 0 |

Checked outside the TUI after each switch: `readlink /run/current-system`, `cowsay` in
`/run/current-system/sw/bin` and in a login shell's PATH (then gone), `/etc/nixos` at
the new commit and clean, the `history.jsonl` line, 0 failed units.

Bugs the live run found, fixed in their own commits: Esc toggling back into the query box
(then `s`/`p` became text) and Enter not moving to empty results (1454f4e); Textual's 5%
focus tint painting the results table as a lighter block (1454f4e); `nixin tui 2>file`
showing nothing because Textual draws on stderr, so the terminal check is stdin + stderr
(04f229b); Pending still showing the pre-apply plan after an apply (155f14b).

### Final state

- Running: **generation 114**, `/nix/store/zknqa0x828qvan1f12iwwra35dpn4hap-nixos-system-mbp13-26.11.20261023.3ec6363`
  (the same closure as gen 112 before the test). No cowsay. `/boot` 81M free (60%),
  unchanged.
- `~/nixos` `main` = `/etc/nixos` `main` = **f2e48e8**, `/etc/nixos` clean. The
  workspace's one uncommitted change is the `history.jsonl` line for gen 114 (by design:
  the next committing apply takes it along). `installer/` holds only `history.jsonl`.
- `~/nixos` `main` is 5 commits ahead of `origin/main`: the apply session's 716ed68,
  ac28f48, 7da2b4c and this session's b638def `test(tui): +cowsay`, f2e48e8
  `test(tui): -cowsay`. Not pushed. The two cowsay pairs cancel out except for
  `history.jsonl`; squash them before pushing if you like.

## For the GUI: what to reuse

`src/nixin/tui/view.py` is the part to mirror (or import, if the GUI has a Python
backend). Pure functions over documents, each returning plain data with a semantic
`style` per piece (`dim accent option add del warn error ok head`; theme.STYLES maps them
to the palette: cyan accent/add/ok, pink del/error, purple option/warn):

| function | document | gives |
|---|---|---|
| `notice(doc)` → `Notice` | any error | `code`, `message`, `brief` (without the listed commands), `severity` (`error`, `info` for `nothing-to-apply`, `action` for `needs-sudo`), `commands[]`, `items[]` (hints, candidates, files), `candidates[]` + `candidate_where()` |
| `hits(doc)` → `Hit` | search | `kind`, `name`, `target` (what info/where/add/set take: a program row's package), `detail`, `description` |
| `hit_columns(kind, width)`, `hit_cells()` | search | the column plan for a width (description dropped < 60 cells), clipped cells |
| `source_lines(doc)` | search/info | the CLI's footer lines |
| `info_fields(rec)`, `version_rows(doc)` | info row, versions | label/value fields |
| `where_section(doc, host)`, `where_cue(doc)`, `current_value(doc)`, `installed(doc)` | where | the "on HOST" section, the one-line cue, the value to prefill `set` |
| `diff_spans(doc)`, `writer_fields(doc)`, `writer_summary(doc)`, `writer_eval_failed(doc)` | add/rm/set/unset | the coloured diff, what happened, a session line, the `ok`-but-eval-failed case |
| `closure_head(diff)`, `closure_rows(diff)`, `plan_fields(doc)` | plan (and apply's `diff`) | the closure summary and rows, the plan fields |
| `pending_fields(p)`, `has_pending(p)` | `status.pending` | fields; whether anything but `history.jsonl` is pending |
| `dry_run_fields(doc)`, `generated_message(doc)` | `apply --dry-run` | the confirmation, most important first |
| `apply_fields(doc)`, `progress_fields(doc)` | apply / its error | the result; how far a failed apply got |
| `sync_fields(doc)`, `status_sections(doc, running)`, `farewell()` | sync, status | |

The flows in `app.py` are the other thing worth copying: a write is always dry run →
preview → write → `where` again; an apply is always `apply --dry-run` → confirm (message)
→ apply; writer, plan and apply runs are serialised (two writer runs on one workspace
race on `state.json`); `status` is stale after any write or apply, and a plan is dropped
after an apply.

## Judgement calls

1. **In-process, not `nixin --json` subprocesses.** `cli.document(ns)` is the code `main()`
   runs, so the documents are the same with no JSON round trip and no second
   implementation. A GUI in another language uses `nixin --json` and gets the same dicts.
2. **plan and apply run in human mode under the TUI.** That is the only way to get
   nix's build output and nixos-rebuild's log: the backend tees them to `sys.stderr` in
   human mode and streams nothing under `--json` (CONTRACT.md). Textual redirects
   `sys.stderr` while it runs; `App.begin_capture_print` routes it into the log view. The
   document is identical apart from `message`, which the TUI ignores. No backend change.
3. **Status's generation comes from the session's plan/apply `running` block**
   (design gap 1); otherwise Status says "— (plan shows it)".
4. **Pending loads `status` on entry** (about a minute cold after an edit, under a second
   warm, with a spinner saying so). The screen is empty without it.
5. **HM toggle**: packages cycle off → each HM user of the host (users from `status`,
   loaded on the first `h`); HM options always need a user (from `where`'s scopes, else
   status). mbp13 has no HM users, so this is covered by tests only.
6. **`q` is a key, not a reflex**: Textual's text inputs take `q` as a letter, so `q`
   quits from anywhere except while typing; `ctrl+q` always quits. Esc from the query
   moves to the results and never back (`/` goes back), so a second Esc cannot turn keys
   into text.
7. **Screenshots**: Rich's SVG export with the window painted in the theme background and
   the per-cell background rectangles of that colour dropped (half the size).
   `simplify=True` was not usable: viewers then collapse the padding between labels and
   values.

## Design gaps (backend; not changed here)

1. `status.running` has no generation; only plan/apply's `running` block has it. Adding
   `generation` (a readlink of the profile links, no nix) would let Status show it always.
2. `status.pending` needs no nix but only comes with a full `status` (the toplevel eval:
   ~35–77 s cold after any edit). A cheap pending-only path would make Pending instant.
3. No progress under `--json`. The TUI gets it only because it is in-process (call 2). A
   GUI driving `nixin --json` gets nothing for minutes. Suggest a structured progress
   channel: step events (`commit`, `eval`, `build`, `pull`, `switch`) and log lines as
   JSON lines on a separate fd or on stderr behind a flag.
4. During apply's own eval (no plan first) nix prints nothing: the log stays empty for
   ~35 s, only the spinner moves. Step events (gap 3) would fix that too.
5. A `sync` over ssh with a passphrase-protected key: ssh opens `/dev/tty` itself and
   would draw over the TUI. The TUI sets `GIT_TERMINAL_PROMPT=0`; starting children with
   `start_new_session=True` in `apply/proc.py` (no controlling terminal) would close it.
6. The writer's `ok: true` with `checks.eval.ok: false` (open question 1 in
   HANDOFF-integrate) stays a special case the UI has to look for.
7. Not in the TUI yet: `search --nixpkgs <lock>` (the host's exact rev; the first search
   on a rev takes minutes and 4.6 GiB), `versions` beyond 8 rows, `sync --pull/--push`
   (fetch only), `apply --no-switch`, `fn` search.

## How to test

```
nix develop -c pytest tests/test_tui.py          # 53 tests, offline, ~30 s
nix build                                         # all tests in the sandbox
nix develop -c python3 scripts/tui-screenshots.py # docs/tui/*.svg from the fixtures
nix develop -c python3 scripts/tui-screenshots.py --live --config ~/nixos   # read-only
nix run .#default -- --config ~/nixos tui         # or result/bin/nixin-tui --config ~/nixos
```

A phone-sized check from a desktop: `tmux new-session -x 50 -y 25 'result/bin/nixin-tui
--config ~/nixos'`. Do not redirect stderr (`2>file`): Textual draws there, and the TUI
refuses to start.

## Packages page (branch `ui-packages`, 2026-11-02, mbp13)

`list` and `outdated` were not in the front ends; now they are, in both, with `update`
(new verb, `src/nixin/outdated/update.py`, CONTRACT "`update`") as the one button that
changes something. The view-model is shared (`ui/view.py`: `package_rows`,
`filter_packages`, `package_scopes`, `package_columns`/`cells`, `list_footer`,
`package_hit`; `update_rows`, `update_columns`/`cells`, `outdated_head`, `kernel_line`,
`outdated_summary`, `outdated_notes`, `update_args`; `update_confirm_fields`,
`update_fields`, `update_summary`).

- **Screen** `tui/packages.py` (`PackagesScreen`): `i` from anywhere (also `ctrl+4`; a
  terminal without the kitty keyboard protocol sends that as `ctrl+\`, bound too). Tabs
  **installed** / **updates** (`t` names the other one in the footer).
  - installed: one `list` on entry (every scope); the filter box (`f`) and the for-whom
    button (`h`: all → system → each HM user) narrow the loaded rows with `list
    --filter`/`--scope` semantics, so they never evaluate again. Columns name, version,
    FOR (when several scopes show), LISTED IN (from 80 cells: the first `declared`
    file:line, the path's head clipped so the file and line stay). Under the table the
    highlighted row (`ripgrep-15.2.0 · home brucking · listed in …`, or "added by a
    module") and the CLI's own footer (`78 packages: 26 system, 52 home brucking · 67
    repeats folded · cached`). Enter opens **Details** for the row's user
    (`DetailsScreen(hit, user=)`): info/versions take the attribute from `declared`,
    `where` the pname (`Hit.where_target`), Remove previews `rm ATTR --home USER`.
  - updates: `outdated` on first view (lock mode); `m` toggles `--published` (each
    mode's document is kept); the head line (`mbp13: nixpkgs (…nixos-unstable) 3ec6363
    (2026-10-23) → 0bb2eed (2026-11-01), 8.9 days newer`), the table (package, from, to,
    FOR, and the note from 80 cells: `(older)`, `as kdePackages.x`; removed in pink),
    the kernel line, the summary, notes. `u` / the button: `update --dry-run` →
    `UpdateModal` (the command, the commit now, the one it moves to with what
    `outdated` said, "nothing is built or switched", what comes next, the cost) → `u` or
    Enter runs `update` (busy `updating`; `q` refuses while it runs) → the session's
    writes get `nixpkgs 3ec6363 → 0bb2eed (flake.lock)` → **Pending**. A moved lock
    makes `list`, both `outdated` documents, `status` and the plan stale. An `outdated`
    that failed (offline) leaves Update enabled (the lock can move without a
    comparison); `up_to_date` disables it.
  - `r` re-evaluates (`--refresh`) the tab you are on; `?` lists the keys.
- **Footer at phone width**: with `i` the footers no longer fit 50 cells (`s Status` was
  cut). Below 80 cells the page keys show as one group, `i p s go`; from 80 by name.
  Two bindings per key, `check_action` enables one by width and the key falls through to
  the other (`app.py`: `PAGE_KEYS`, `on_resize` refreshes the footer).
- **Hidden tables**: a table filled while its tab is hidden has no width; it gets a
  guess and its columns are planned again once it has one (`_width`, `_replan`).
- `app.hm_users()` also takes `list`'s `users[]`, so Details knows the HM users without
  a `status`.
- Tests: `tests/test_tui.py` +6 (installed with filter, scope and Details/Remove at both
  sizes; updates → `--published` → back (kept) → update → Pending at both sizes;
  nothing to update / `network` error; the folded footer), on `tui_fake.PACKAGES`:
  19BK's `list`/`outdated`/`update` documents recorded on mbp13, plus ripgrep's real
  `where`/`info`/`versions` (19BK). Screenshots `docs/tui/{phone,desktop}-8…12-*.svg`;
  every other fixture SVG was redrawn (the footers carry the page keys).

### Live on mbp13

- Read-only on `~/nixos` (`scripts/tui-screenshots.py --live --config ~/nixos --host
  mbp13`): the Packages page lists mbp13's packages and the updates tab says `57 of 337
  packages would change; kernel 7.2.12 → 7.2.14` (`outdated` 4 s with the target cached).
  `docs/tui/live-{phone,desktop}-4-packages.svg`, `-5-updates.svg`. `~/nixos` unchanged
  (`git status` and the `flake.lock` hash compared before and after).
- The update round trip on a throwaway clone (`git clone ~/nixos ~/nixos-ui-test`,
  deleted afterwards), driven at 50×25 on the real backend: list 2.0 s, where 2.2 s,
  outdated 4.4 s, `--published` 0.9 s, `update --dry-run` 0.02 s, `update` 0.3 s (the
  tarball was already in the store; ~60 s cold), then Pending: **`status` failed with
  `eval-failed`**: at 0bb2eed nixpkgs refuses `gtk+-2.24.33` (gtk2 is marked broken
  ahead of its removal, nixpkgs#410814), and something in mbp13's closure still uses
  it. The TUI shows that as the notice it should be; `outdated` did not see it (next
  point). Back on Packages: "the lock already has 0bb2eed: nothing would change", Update
  disabled. `docs/tui/live-phone-6-update-confirm-clone.svg`, `-7-update-pending-clone.svg`.
  Apply was not run: its dry run asks `sudo -n true`, and this run had no sudo at all.

### Gaps found (not changed here)

1. `outdated` reads package lists (`pname`/`version`) and never forces the toplevel, so it
   says "57 would change" for an update whose config no longer evaluates (a package
   newly marked broken or insecure). A `--check` that evaluates the target's
   `system.build.toplevel.drvPath` (≈ `status`'s eval, 15–60 s) would say so before the
   lock moves. Today the first to notice is `status`/`plan` after `update`.
2. mbp13 at nixos-unstable 0bb2eed: gtk2 is refused (above). Find the user (`nix why-depends`
   or `--show-trace`) before moving mbp13's lock, or allow it
   (`nixpkgs.config.problems.handlers."gtk+".broken = "warn"`) knowingly.
3. `update` cannot be undone from the UIs: the way back is `git -C WORKSPACE checkout
   flake.lock` (or `update --to <old rev>`). A "revert the lock" button on Pending would
   be one call.
4. Selecting an Updates row opens Details with the pname as the attribute (no `declared`
   there); for `kdePackages.*` and other nested attributes `info`/`versions` may miss.

How to test the page: `nix develop -c pytest tests/test_tui.py -k "packages or page_keys"`;
by hand `nixin-tui --config ~/nixos`, `i`, `t`, `m` (read-only); `u` only on a clone.
