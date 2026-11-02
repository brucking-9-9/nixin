"""The TUI, offline: the view-model on recorded documents, then the app driven with
App.run_test() against a backend that answers from tests/fixtures/tui (no nix, no network,
no config). Two sizes: a phone over ssh (50x25) and a desktop terminal (120x40).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, TextArea
from tui_fake import COWSAY, FakeBackend, load, screen_text

from nixin.cli import main
from nixin.tui import backend, view
from nixin.tui.app import NixinApp
from nixin.tui.modals import ApplyModal, DiffModal, NoticeModal
from nixin.tui.screens import ApplyScreen, DetailsScreen, PendingScreen, SearchScreen
from nixin.tui.widgets import Fields, Line, LogView, NoticeBox, Spans

PHONE = (50, 25)
DESKTOP = (120, 40)

# --- view-model --------------------------------------------------------------------------------


def test_hits_and_targets():
    hits = view.hits(load("search_all_openssh"))
    assert {h.kind for h in hits} == {"pkg", "opt", "hm"}
    opt = next(h for h in hits if h.name == "services.openssh.enable")
    assert opt.is_option and opt.target == opt.name and opt.detail == "boolean"
    prog = view.hit({"kind": "program", "name": "rg", "package": "ripgrep", "version": "15"})
    assert prog.target == "ripgrep" and prog.detail == "ripgrep 15" and prog.is_package


def test_source_line_is_the_cli_footer():
    assert view.source_lines(load("search_pkg_firefox")) == [
        "8 result(s) · search.nixos.org unstable@1c89083e"
    ]
    multi = view.source_lines(load("search_all_openssh"))[0]
    assert "home-manager-options unstable (cached)" in multi
    assert "search.nixos.org unstable@1c89083e" in multi


@pytest.mark.parametrize("width", [40, 50, 59, 60, 80, 120, 200])
@pytest.mark.parametrize("kind", ["pkg", "opt", "all"])
def test_columns_fit_the_width(kind, width):
    cols = view.hit_columns(kind, width)
    used = sum(c.width + 2 for c in cols) + 1  # 2 cells of padding each, 1 for the scrollbar
    assert used <= max(width, 40)
    keys = [c.key for c in cols]
    assert ("description" in keys) == (width >= view.NARROW)
    assert ("kind" in keys) == (kind == "all")


def test_cells_are_clipped_with_an_ellipsis():
    h = view.hit({"kind": "pkg", "name": "a" * 80, "version": "1", "description": "d"})
    cells = view.hit_cells(h, view.hit_columns("pkg", 50))
    assert len(cells[0].text) <= view.hit_columns("pkg", 50)[0].width
    assert cells[0].text.endswith("…")


def test_notices():
    assert view.notice(load("search_pkg_firefox")) is None
    sudo = view.notice(load("apply_needs_sudo"))
    assert sudo.severity == "action" and len(sudo.commands) == 2
    assert sudo.commands[0] == "sudo git -C /etc/nixos pull --ff-only /home/master/nixos main"
    assert "nixos-rebuild" not in sudo.brief and "password" in sudo.brief
    done = view.notice(load("apply_nothing"))
    assert done.severity == "info" and done.code == "nothing-to-apply"
    amb = view.notice(load("rm_git_ambiguous"))
    assert amb.severity == "error" and len(amb.candidates) == 2
    assert amb.items[0].startswith("hosts/mbp13/default.nix:203")
    assert view.candidate_where(amb.candidates[1]) == "configuration.nix:406"


def test_where_package():
    s = view.where_section(load("where_cowsay"), "mbp13")
    assert s.fields == [] and s.note == "not installed on mbp13"
    git = view.where_section(load("where_git"), "mbp13")
    installed = [f for f in git.fields if f.label == "installed"]
    assert [f.value for f in installed] == ["git-2.56.0 [system]"]  # listed twice, shown once
    listed = [f.value for f in git.fields if f.label == "listed"]
    assert "environment.systemPackages in hosts/mbp13/default.nix:203 [system]" in listed
    assert any(v.startswith("environment.systemPackages in nixpkgs ") for v in listed)
    assert view.where_cue(load("where_cowsay")) == view.Span("○ not installed", "dim")
    assert view.where_cue(load("where_cowsay_added")).text == "● installed [system]"


def test_where_option():
    doc = load("where_openssh_enable")
    s = view.where_section(doc, "mbp13")
    assert view.Field("value", "true [nixos]", "option") in s.fields
    assert view.Field("set in", "hosts/mbp13/default.nix:248", "") in s.fields
    assert view.current_value(doc) == "true"
    assert view.where_cue(doc).text == "= true"
    err = view.where_section(load("info_notfound"), "mbp13")
    assert err.note.startswith("no package or NixOS option") and err.style == "dim"


def test_writer_preview_and_result():
    spans = view.diff_spans(load("add_cowsay_dry"))
    assert view.Span("installer/mbp13.nix  (created)", "head") in spans
    assert view.Span("+    pkgs.cowsay", "add") in spans
    assert not any(s.text.startswith(("+++", "---")) for s in spans)
    done = load("add_cowsay")
    fields = view.writer_fields(done)
    assert view.Field("added", "cowsay → installer/mbp13.nix", "add") in fields
    assert any(f.label == "eval" and f.style == "ok" for f in fields)
    assert view.writer_summary(done) == (
        "+cowsay (hosts/mbp13/default.nix, installer/mbp13.nix, installer/state.json)"
    )
    bad = {**done, "checks": {"parse": [], "eval": {"ok": False, "message": "boom"}}}
    assert view.writer_eval_failed(bad) and not view.writer_eval_failed(done)


def test_plan_closure():
    doc = load("plan_cowsay")
    assert view.closure_head(doc["diff"]) == "1 added, 0 removed, 0 changed, +51.2 KiB"
    assert view.closure_rows(doc["diff"]) == [
        view.ClosureRow("+", "cowsay", "3.8.4", "+51.2 KiB", "add")
    ]
    fields = {f.label: f.value for f in view.plan_fields(doc)}
    assert fields["running"] == "generation 112" and fields["result"] == "a new system"
    clean = {f.label: f.value for f in view.plan_fields(load("plan_clean"))}
    assert clean["result"] == "up to date: nothing to apply"
    assert clean["built"] == "cached (commit 7da2b4cdd8b2)"


def test_apply_dry_run_and_result():
    dry = load("apply_dry_cowsay")
    fields = view.dry_run_fields(dry)
    roots = [f for f in fields if f.label.endswith("(root)")]
    assert [f.value for f in roots] == [
        "sudo git -C /etc/nixos pull --ff-only /home/master/nixos main",
        "sudo nixos-rebuild switch --flake /etc/nixos#mbp13",
    ]
    assert view.Field("root", "sudo: a password is required", "warn") in fields
    assert [f.label for f in fields][:3] == ["commit", "closure", "pull (root)"]
    assert view.generated_message(dry) == "nixin: mbp13: +cowsay"
    ok = {f.label: f.value for f in view.apply_fields(load("apply_ok"))}
    assert ok["system"] == "switched to generation 113"
    assert ok["committed"] == "a1f0c3e nixin: mbp13: +cowsay"
    prog = view.progress_fields(load("apply_needs_sudo"))
    assert [f.label for f in prog] == ["committed", "built", "ran"]


def test_status_and_pending():
    secs = {s.title: s for s in view.status_sections(load("status_clean"), {"generation": 112})}
    assert list(secs) == ["config", "lock", "running", "pending", "counts", "caches"]
    lock = {f.label: f.value for f in secs["lock"].fields}
    assert lock == {"nixpkgs": "3ec636362e18 (nixos-unstable)", "date": "2026-10-23"}
    run = {f.label: f.value for f in secs["running"].fields}
    assert run["generation"] == "112"
    no_gen = view.status_sections(load("status_clean"))[2]
    assert any(f.value == "— (plan shows it)" for f in no_gen.fields)
    assert not view.has_pending(load("status_clean")["pending"])  # only history.jsonl
    assert view.has_pending(load("status_added")["pending"])


def test_farewell():
    assert view.farewell([], []) == "nixin tui: nothing changed"
    assert view.farewell(["+cowsay (x)"], []).endswith("not applied yet (nixin plan, nixin apply)")
    assert view.farewell(["+cowsay (x)"], [load("apply_ok")]).endswith("applied: generation 113")


# --- the backend shim on the real handlers (no network, no nix: usage and config errors) --------


def test_backend_runs_the_real_verb_handlers(tmp_path):
    usage = backend.run(["--host", "h", "search", ""])
    assert usage["ok"] is False and usage["error"]["code"] == "usage"
    assert usage["verb"] == "search" and usage["host"] == "h"
    bogus = backend.run(["--host", "h", "frobnicate"])
    assert bogus["error"]["code"] == "usage" and bogus["verb"] is None
    b = backend.Backend(host="h", config=str(tmp_path / "none"))
    assert b.argv("where", "x") == ["--host", "h", "--config", str(tmp_path / "none"), "where", "x"]
    assert b.argv("search", "x") == ["--host", "h", "search", "x"]  # discovery takes no --config
    doc = b.call("where", "x")
    assert doc["ok"] is False and doc["error"]["code"] == "config-not-found"
    assert doc["verb"] == "where" and doc["host"] == "h"
    assert b.calls[-1].doc is doc


def test_tui_verb_has_no_json_form(capsys):
    assert main(["--json", "--host", "testhost", "tui"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc == {
        "ok": False,
        "verb": "tui",
        "host": "testhost",
        "error": {
            "code": "usage",
            "message": "tui is interactive and has no --json form; the other verbs have one",
        },
    }
    assert main(["tui"]) == 2  # pytest's stdin is not a terminal
    assert "needs a terminal" in capsys.readouterr().err


# --- the app ---------------------------------------------------------------------------------------


def drive(script, routes=None, size=DESKTOP):
    """Run `script(app, pilot, fake)` inside App.run_test; returns (app, fake)."""
    fake = FakeBackend(dict(COWSAY if routes is None else routes))
    app = NixinApp(fake)

    async def go():
        async with app.run_test(size=size) as pilot:
            await script(app, pilot, fake)

    asyncio.run(go())
    return app, fake


async def settle(app, pilot, t: float = 0.0):
    await pilot.pause(t)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def search_cowsay(app, pilot):
    await pilot.press(*"cowsay", "enter")
    await settle(app, pilot)


async def open_details(app, pilot):
    await search_cowsay(app, pilot)
    await pilot.press("enter")
    await settle(app, pilot)
    assert isinstance(app.screen, DetailsScreen)


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_search(size):
    async def script(app, pilot, fake):
        await search_cowsay(app, pilot)
        screen = app.screen
        assert isinstance(screen, SearchScreen)
        table = screen.query_one("#results", DataTable)
        assert table.row_count == 2 and table.has_focus
        assert screen.query_one("#source", Line).value == (
            "2 result(s) · search.nixos.org unstable@1c89083e"
        )
        cols = [str(c.key.value) for c in table.columns.values()]
        assert cols == (["name", "detail"] if size == PHONE else ["name", "detail", "description"])
        text = screen_text(app)
        assert "neo-cowsay" in text and "Program which generates ASCII pictures" in text

    _, fake = drive(script, size=size)
    assert fake.verbs() == [["search", "--kind", "pkg", "--channel", "unstable", "--", "cowsay"]]


def test_typing_is_debounced_and_kind_and_channel_rerun():
    async def script(app, pilot, fake):
        await pilot.press(*"cow")
        await pilot.pause(0.05)
        await pilot.press(*"say")
        await settle(app, pilot, 0.5)  # one search after the pause, not one per key
        assert len(fake.calls) == 1
        await pilot.press("escape", "k")  # to the results; next kind
        await settle(app, pilot)
        await pilot.press("c")  # channel
        await settle(app, pilot)

    _, fake = drive(script)
    assert [v[:5] for v in fake.verbs()] == [
        ["search", "--kind", "pkg", "--channel", "unstable"],
        ["search", "--kind", "opt", "--channel", "unstable"],
        ["search", "--kind", "opt", "--channel", "stable"],
    ]


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_details_add_with_diff_preview(size):
    async def script(app, pilot, fake):
        await open_details(app, pilot)
        screen = app.screen
        assert "○ not installed" in screen_text(app)
        assert "3.8.4 2026-10-02  55ba7f49" in screen.query_one("#versions", Fields).text
        await pilot.press("a")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, DiffModal)
        spans = modal.query_one("#diff", Spans).spans
        assert view.Span("+    pkgs.cowsay", "add") in spans
        assert fake.verbs()[-1] == ["add", "cowsay", "--dry-run"]
        await pilot.press("w")
        await settle(app, pilot)
        assert app.screen is screen
        assert "● installed [system]" in screen_text(app)
        assert "installer/mbp13.nix:10" in screen.query_one("#where", Fields).text

    app, fake = drive(script, size=size)
    assert fake.verbs()[-3:] == [
        ["add", "cowsay", "--dry-run"],
        ["add", "cowsay"],
        ["where", "cowsay", "--kind", "package"],
    ]
    assert app.writes == [
        "+cowsay (hosts/mbp13/default.nix, installer/mbp13.nix, installer/state.json)"
    ]


def test_cancel_in_the_diff_modal_writes_nothing():
    async def script(app, pilot, fake):
        await open_details(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)
        assert isinstance(app.screen, DetailsScreen)

    app, fake = drive(script)
    assert ["add", "cowsay"] not in fake.verbs() and app.writes == []


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_pending_plan_apply_needs_sudo(size):
    async def script(app, pilot, fake):
        await pilot.press("escape", "p")
        await settle(app, pilot)
        screen = app.screen
        assert isinstance(screen, PendingScreen)
        assert "authority /etc/nixos @ 7da2b4c" in screen.query_one("#pending", Fields).text
        await pilot.press("b")
        await settle(app, pilot, 0.1)
        log = screen.query_one("#log", LogView)
        assert log.lines_seen[0] == "these 39 derivations will be built:"
        assert len(log.lines_seen) == 5  # streamed through Textual's stderr capture
        closure = screen.query_one("#closure", DataTable)
        assert closure.row_count == 1 and closure.display
        assert "1 added, 0 removed, 0 changed, +51.2 KiB" in screen_text(app)
        await pilot.press("a")
        await settle(app, pilot)
        assert isinstance(app.screen, ApplyModal)
        assert "sudo nixos-rebuild switch" in screen_text(app)
        await pilot.press("enter")  # empty message: generated
        await settle(app, pilot)
        apply = app.screen
        assert isinstance(apply, ApplyScreen)
        notice = apply.query_one("#notice", NoticeBox)
        assert notice.display and notice.notice.code == "needs-sudo"
        cmds = apply.query_one("#commands", TextArea)
        assert cmds.display and cmds.text == (
            "sudo git -C /etc/nixos pull --ff-only /home/master/nixos main\n"
            "sudo nixos-rebuild switch --flake /etc/nixos#mbp13"
        )
        await pilot.press("c")
        assert app.clipboard == cmds.text
        assert "needs-sudo" in screen_text(app)

    app, fake = drive(script, size=size)
    assert fake.verbs() == [["status"], ["plan"], ["apply", "--dry-run"], ["apply"]]
    assert app.applied == []


def test_apply_with_message_and_result():
    routes = {**COWSAY, "apply": "apply_ok"}

    async def script(app, pilot, fake):
        await pilot.press("escape", "p")
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press(*"test: cowsay", "enter")
        await settle(app, pilot)
        text = app.screen.query_one("#result", Fields).text
        assert "system switched to generation 113" in text
        assert app.screen.query_one("#log", LogView).lines_seen[-1] == (
            "reloading user units for master..."
        )
        await pilot.press("escape", "q")

    app, fake = drive(script, routes)
    # back on Pending, status is loaded again: the apply made it stale
    assert fake.verbs()[-2:] == [["apply", "-m", "test: cowsay"], ["status"]]
    assert app.return_value == "nixin tui: applied: generation 113"


def test_nothing_to_apply_is_a_calm_notice():
    async def script(app, pilot, fake):
        await pilot.press("escape", "p")
        await settle(app, pilot)
        await pilot.press("a")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, NoticeModal)
        assert modal.notice.severity == "info"
        assert modal.query_one("#notice", NoticeBox).has_class("-info")

    _, fake = drive(script, {**COWSAY, "apply --dry-run": "apply_nothing"})
    assert fake.verbs()[-1] == ["apply", "--dry-run"]


def test_writer_error_with_candidates_retries_with_where():
    routes = {
        "search": "search_pkg_firefox",
        "info": "info_firefox",
        "versions": "versions_cowsay",
        "where": "where_git",
        "rm --dry-run": ["rm_git_ambiguous", "add_cowsay_dry"],
    }

    async def script(app, pilot, fake):
        await pilot.press(*"git", "enter")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await pilot.press("r")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, NoticeModal) and modal.can_pick
        assert modal.query_one("#notice", NoticeBox).notice.code == "ambiguous"
        await pilot.press("down", "enter")  # the second candidate
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)

    _, fake = drive(script, routes)
    assert fake.verbs()[-2:] == [
        ["rm", "firefox", "--dry-run"],
        ["rm", "firefox", "--where", "configuration.nix:406", "--dry-run"],
    ]


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_error_documents_render_as_notices(size):
    network = {"ok": False, "error": {"code": "network", "message": "search.nixos.org: timed out"}}

    async def script(app, pilot, fake):
        await pilot.press(*"x", "enter")
        await settle(app, pilot)
        notice = app.screen.query_one("#notice", NoticeBox)
        assert notice.display and notice.has_class("-error")
        assert "network" in screen_text(app) and "timed out" in screen_text(app)

    drive(script, {"search": network}, size=size)


def test_info_error_shows_in_details():
    routes = {**COWSAY, "info": "info_notfound"}

    async def script(app, pilot, fake):
        await open_details(app, pilot)
        assert "info: not-found: no package" in app.screen.query_one("#info", Fields).text

    drive(script, routes)


def test_layout_narrow_and_wide():
    def check(size):
        async def script(app, pilot, fake):
            await open_details(app, pilot)
            screen = app.screen
            w, _ = size
            assert screen.has_class("-narrow" if w < 80 else "-wide")
            for widget in screen.query("*"):
                if widget.display and widget.region.width:
                    assert widget.region.right <= w, widget
            left, right = screen.query_one("#left"), screen.query_one("#right")
            if w < 80:  # stacked
                assert left.region.x == right.region.x and right.region.y > left.region.y
            else:  # side by side
                assert right.region.x > left.region.x and right.region.y == left.region.y

        drive(script, size=size)

    check(PHONE)
    check(DESKTOP)


def test_escape_goes_back_and_q_quits():
    async def script(app, pilot, fake):
        await open_details(app, pilot)
        await pilot.press("escape")
        assert isinstance(app.screen, SearchScreen)
        await pilot.press("s")
        await settle(app, pilot)
        assert "3ec636362e18 (nixos-unstable)" in screen_text(app)
        await pilot.press("escape", "q")

    app, _ = drive(script)
    assert app.return_value == "nixin tui: nothing changed"


def test_screenshots_export_at_both_sizes():
    for size in (PHONE, DESKTOP):

        async def script(app, pilot, fake):
            await open_details(app, pilot)
            svg = app.export_screenshot()
            assert svg.startswith("<svg") and "cowsay" in svg
            assert "#120b10" in svg.lower()  # the theme's background

        drive(script, size=size)
