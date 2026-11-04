"""The TUI, offline: the view-model on recorded documents, then the app driven with
App.run_test() against a backend that answers from tests/fixtures/tui (no nix, no network,
no config). Two sizes: a phone over ssh (50x25) and a desktop terminal (120x40).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, TextArea
from tui_fake import COWSAY, HM_OPTIONS, OPTIONS, PACKAGES, FakeBackend, load, screen_text

from nixin.cli import main
from nixin.tui.app import NixinApp
from nixin.tui.modals import ApplyModal, DiffModal, NoticeModal, SetOptionModal, UpdateModal
from nixin.tui.options import OptionScreen, OptionsScreen
from nixin.tui.packages import PackagesScreen
from nixin.tui import screens
from nixin.tui.screens import ApplyScreen, DetailsScreen, PendingScreen, SearchScreen
from nixin.tui.widgets import Fields, Line, LogView, NoticeBox, Spans
from nixin.ui import backend, view

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


# --- view-model: packages (`list`), updates (`outdated`), `update` ------------------------------


def test_package_rows_from_list():
    doc = load("list_19BK")
    rows = view.package_rows(doc)
    assert len(rows) == doc["count"] == 78
    by = {(r.name, r.tag): r for r in rows}
    rg = by[("ripgrep", "system")]
    assert (rg.version, rg.target, rg.where) == ("15.2.0", "ripgrep", "configuration.nix:413")
    harper = by[("harper", "brucking")]
    assert harper.scope == "home" and harper.key == "home:brucking"
    csv = by[("csvmusic", "brucking")]  # `(pkgs.callPackage ./csvmusic/package.nix {})`
    assert csv.target == "csvmusic" and csv.where.startswith("home/")
    agent = by[("agent-status", "brucking")]  # no pname: the derivation name
    assert agent.version == "" and agent.target == "a.agent-status"
    acl = by[("acl", "system")]
    assert acl.where == "" and "added by a module" in view.package_detail(acl)
    assert view.attribute([{"text": "pkgs.kdePackages.dolphin"}]) == "kdePackages.dolphin"
    assert view.attribute([{"text": "(callPackage./x{})"}]) is None


def test_package_filter_scope_and_footer():
    doc = load("list_19BK")
    rows = view.package_rows(doc)
    scopes = view.package_scopes(doc["users"])
    assert [(s.key, s.label) for s in scopes] == [
        ("all", "all"),
        ("system", "system"),
        ("home:brucking", "home brucking"),
    ]
    assert scopes[2].user == "brucking" and scopes[1].user is None
    assert [r.name for r in view.filter_packages(rows, "FIRE")] == ["firefox"]
    assert {r.name for r in view.filter_packages(rows, "zsh")} == {"zsh"}  # both scopes
    assert len(view.filter_packages(rows, "zsh")) == 2
    system = view.filter_packages(rows, "", "system")
    assert system and all(r.user is None for r in system)
    home = view.filter_packages(rows, "zsh", "home:brucking")
    assert [(r.name, r.user) for r in home] == [("zsh", "brucking")]
    assert view.filter_packages(rows, "0.49") == [r for r in rows if "0.49" in r.drv]
    assert view.list_footer(doc, len(rows)) == (
        "78 packages: 26 system, 52 home brucking · 67 repeats folded · evaluated in 3.21 s"
    )
    assert view.list_footer(doc, 1, "fire", scopes[1]).startswith(
        "1 of 78 packages matching 'fire' (system) · "
    )
    cached = {**doc, "cache": {"hits": ["packages"]}, "folded": 0}
    assert view.list_footer(cached, 3, "", scopes[2]) == "3 of 78 packages (home brucking) · cached"


def test_package_hit_opens_details_with_the_pname_for_where():
    rows = {r.name: r for r in view.package_rows(load("list_19BK"))}
    h = view.package_hit(rows["ripgrep"])
    assert (h.kind, h.target, h.where_target, h.detail) == ("pkg", "ripgrep", "ripgrep", "15.2.0")
    assert h.is_package and h.record["name"] == "ripgrep"
    agent = view.package_hit(rows["agent-status"])
    assert agent.target == "a.agent-status" and agent.where_target == "agent-status"


@pytest.mark.parametrize("width", [40, 50, 59, 60, 80, 120, 200])
@pytest.mark.parametrize("tagged", [False, True])
def test_package_and_update_columns_fit(width, tagged):
    for cols in (view.package_columns(width, tagged), view.update_columns(width, tagged)):
        used = sum(c.width + 2 for c in cols) + 1
        assert used <= max(width, 44), (width, cols)
        keys = [c.key for c in cols]
        assert ("tag" in keys) == tagged
        assert ("where" in keys or "note" in keys) == (width >= 80)


def test_update_rows_lock_mode():
    doc = load("outdated_19BK")
    rows = view.update_rows(doc)
    assert len(rows) == 14  # 13 changed + fuse 2.9.9 removed
    assert [r.tag for r in rows] == ["system"] * 5 + ["brucking"] * 9  # system first
    names = [r.pname for r in rows[:5]]
    assert names == sorted(names, key=str.lower)
    ff = next(r for r in rows if r.pname == "firefox")
    assert (ff.old, ff.new, ff.style, ff.note) == ("158.0.1", "159.0", "accent", "")
    fuse = next(r for r in rows if r.pname == "fuse")
    assert (fuse.old, fuse.new, fuse.style) == ("2.9.9", "removed", "del")
    cells = view.update_cells(fuse, view.update_columns(50, True))
    assert [c.style for c in cells] == ["del", "del", "del", "dim"]
    assert view.outdated_head(doc, "19BK") == (
        "19BK: nixpkgs (github:nixos/nixpkgs/nixos-unstable) 3ec6363 (2026-10-23) → "
        "0bb2eed (2026-11-01), 8.9 days newer"
    )
    assert view.kernel_line(doc) == view.Span("kernel 7.2.12 → 7.2.14", "warn")
    assert view.outdated_summary(doc) == "13 of 325 packages would change; kernel 7.2.12 → 7.2.14"
    assert view.outdated_scopes(doc) == [
        "system: 4 of 135 would change (1 removed)",
        "home brucking: 9 of 190 would change",
    ]
    assert view.update_args(doc) == []  # the lock's own ref: `nix flake update nixpkgs`
    pinned = {**doc, "target": {**doc["target"], "from": "channel"}}
    assert view.update_args(pinned) == ["--to", doc["target"]["rev"]]
    assert view.update_args({**doc, "up_to_date": True}) is None
    assert view.outdated_summary({**doc, "up_to_date": True}) == (
        "the lock already has 0bb2eed: nothing would change"
    )


def test_update_rows_published():
    doc = load("outdated_19BK_published")
    rows = view.update_rows(doc)
    assert len(rows) == doc["count"] == 13
    assert all(r.style in ("accent", "warn") for r in rows)
    assert view.kernel_line(doc).text == "kernel 7.2.12 → 7.2.13, latest 7.3"
    assert view.outdated_summary(doc).startswith("13 of 275 packages differ on unstable; kernel")
    notes = view.outdated_notes(doc)
    assert notes[0].startswith("search.nixos.org has many attributes per pname")
    assert notes[-1].startswith("not on search.nixos.org: ")
    assert view.update_args(doc) == []


def test_update_documents():
    dry = load("update_dry")
    fields = {f.label: f for f in view.update_confirm_fields(dry, load("outdated_19BK"))}
    assert fields["runs"].value == "nix flake update nixpkgs --flake /home/master/nixos"
    assert fields["nixpkgs"].value == "now 3ec6363 (2026-10-23)"
    assert fields["to"].value.startswith("0bb2eed (2026-11-01), as compared: 13 of 325")
    assert "nothing is built or switched" in fields["writes"].value
    alone = {f.label: f.value for f in view.update_confirm_fields(dry)}
    assert alone["to"] == "github:nixos/nixpkgs/nixos-unstable, as it resolves now"
    ok = load("update_ok")
    assert view.update_fields(ok)[0] == view.Field(
        "nixpkgs", "3ec6363 (2026-10-23) → 0bb2eed (2026-11-01)", "add"
    )
    assert view.update_summary(ok) == "nixpkgs 3ec6363 → 0bb2eed (flake.lock)"
    same = view.update_fields(load("update_same"))
    assert same[0].value == "already at 0bb2eed (2026-11-01); flake.lock unchanged"
    assert view.has_pending(load("status_updated")["pending"])


# --- the backend shim on the real handlers (no network, no nix: usage and config errors) --------


def opt_rec(name: str, fixture: str = "options_search_openssh") -> dict:
    return next(r for r in load(fixture)["results"] if r["name"] == name)


def test_option_search_args_and_templates():
    assert view.option_search_args("ssh", "hm", "stable") == [
        "--kind",
        "hm",
        "--channel",
        "stable",
        "--limit",
        "50",
        "--",
        "ssh",
    ]
    assert view.option_template("users.users.<name>.shell")
    assert view.option_template("programs.git.includes.*.path")
    assert view.option_placeholder("users.users.<name>.shell") == (12, 18)
    assert view.option_placeholder("programs.git.includes.*.path") == (22, 23)
    assert view.option_placeholder("services.openssh.enable") is None
    assert view.option_set_name("programs.git.includes.*.path") == "programs.git.includes"
    assert view.option_set_name("users.users.<name>.shell") == "users.users.<name>.shell"
    assert not view.option_template("services.openssh.enable")


def test_option_sections_nixos():
    rec = opt_rec("services.openssh.enable")
    where = load("options_where_openssh_enable")
    option, about, host, declared = view.option_sections(rec, where, "mbp13", "opt")
    assert [(f.label, f.value) for f in option.fields] == [
        ("type", "boolean"),
        ("default", "false"),
        ("example", "—"),
    ]
    assert about.note.startswith("Whether to enable the OpenSSH secure shell daemon")
    assert [(f.label, f.value) for f in host.fields] == [
        ("value", "true"),
        ("set in", "hosts/mbp13/default.nix:249  enable = true;"),
    ]
    assert declared.fields[0].value == "nixpkgs nixos/modules/services/networking/ssh/sshd.nix:247"
    assert declared.fields[1].value.startswith("https://github.com/NixOS/nixpkgs/blob/0bb2eed")
    assert declared.fields[-1].value == "search.nixos.org unstable@0bb2eedc"
    assert view.option_cue(where, "opt", None) == view.Span("= true", "option")
    assert view.option_prefill(rec, where, "opt", None) == "true"
    # still evaluating, then the default from nixpkgs (mkOptionDefault: priority 1500)
    assert view.option_host(None, "mbp13", "opt").note == "evaluating…"
    ports = view.option_host(load("options_where_openssh_ports"), "mbp13", "opt")
    assert [(f.label, f.value) for f in ports.fields] == [
        ("value", "[ 22 ]"),
        ("set in", "nixpkgs nixos/modules/services/networking/ssh/sshd.nix (the option's default)"),
    ]


def test_option_sections_home_manager():
    rec = opt_rec("programs.git.lfs.enable", "options_search_hm_git")
    where = load("options_where_hm_git_lfs")  # NixOS has programs.git.lfs.enable too
    assert [s["scope"] for s in where["scopes"]] == ["nixos", "home"]
    assert view.option_users(where) == ["brucking"]
    _, _, host, declared = view.option_sections(rec, where, "19BK", "hm", "brucking")
    assert [(f.label, f.value) for f in host.fields] == [
        ("value", "true  [home brucking]"),
        ("set in", "home/modules/git.nix:6  lfs.enable = true;"),
    ]
    assert declared.fields[0].value == "home-manager modules/programs/git.nix:273"
    # the NixOS page sees the NixOS tree's value (the default) in the same document
    assert view.option_value_text(where, "opt", None) == "false"
    assert view.option_prefill(rec, where, "hm", "brucking") == "true"
    assert [f.label for f in view.option_set_hint(rec)] == ["type", "example", "default"]
    nobody = {**where, "scopes": where["scopes"][:1]}  # a host without HM users
    assert (
        view.option_host(nobody, "mbp13", "hm").note == "no Home Manager user of this host has it"
    )
    missing = {"ok": False, "error": {"code": "not-found", "message": "no option x.y on mbp13"}}
    assert view.option_host(missing, "mbp13", "opt").note.startswith("not an option of mbp13")


def test_option_prefill_never_writes_a_placeholder_back():
    rec = {"name": "x.y", "default": '"text"'}

    def where(value):
        opt = {"name": "x.y", "option": "x.y", "value": value, "value_found": True}
        return {"ok": True, "scopes": [{"scope": "nixos", "user": None, "options": [opt]}]}

    assert view.option_prefill(rec, where(["a", 2]), "opt", None) == '[ "a" 2 ]'
    assert view.option_prefill(rec, where({"a": True}), "opt", None) == "{ a = true; }"
    for lossy in ("<derivation hello-2.12>", ["<function>"], "two\nlines", {"<…>": 3}, "ab<…>"):
        assert view.option_prefill(rec, where(lossy), "opt", None) == '"text"'  # the default
    assert view.option_prefill({"name": "x.y", "default": "{\n}"}, None, "opt", None) == ""
    template = view.option_fields({"name": "users.users.<name>.shell", "type": "shell"})
    assert template[-1] == view.Field(
        "path", "a template: Set asks for the name in place of <name>", "warn"
    )
    element = view.option_fields({"name": "programs.git.includes.*.path", "type": "path"})
    assert element[-1].value == "a list element: Set writes the list, programs.git.includes"


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


def drive(script, routes=None, size=DESKTOP, host="mbp13"):
    """Run `script(app, pilot, fake)` inside App.run_test; returns (app, fake)."""
    fake = FakeBackend(dict(COWSAY if routes is None else routes), host=host)
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


def test_typing_is_debounced_and_kind_and_channel_rerun(monkeypatch):
    # A wide window: on a loaded builder the 0.05 s gap below can stretch past the real 0.35 s and the
    # first half of the word would get its own search. The behaviour under test is the debounce, not its length.
    monkeypatch.setattr(screens, "DEBOUNCE", 1.5)

    async def script(app, pilot, fake):
        await pilot.press(*"cow")
        await pilot.pause(0.05)
        await pilot.press(*"say")
        assert fake.calls == []  # nothing yet: still inside the quiet window
        await settle(app, pilot, 2.5)  # one search after the pause, not one per key
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


def test_no_results_leave_the_keys_usable():
    empty = {**load("search_pkg_firefox"), "results": [], "count": 0}

    async def script(app, pilot, fake):
        await pilot.press(*"services.openssh.enable", "enter")
        await settle(app, pilot)
        screen = app.screen
        assert screen.query_one("#results", DataTable).has_focus  # not the text box
        assert "k: other kinds" in screen.query_one("#preview", Line).value
        await pilot.press("k")  # a key, not a letter in the query
        await settle(app, pilot)
        await pilot.press("escape", "escape", "s")  # Esc twice stays out of the text box
        await settle(app, pilot)
        assert not isinstance(app.screen, SearchScreen)
        assert screen.query_one("#query").value == "services.openssh.enable"

    _, fake = drive(script, {"search": empty, "status": "status_clean"})
    assert [v[1:3] for v in fake.verbs()[:2]] == [["--kind", "pkg"], ["--kind", "opt"]]
    assert fake.verbs()[-1] == ["status"]


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


def test_home_manager_user_toggle():
    status = {**load("status_clean"), "hm_users": ["brucking"]}
    hm_where = {
        "ok": True,
        "kind": "option",
        "name": "programs.kitty.enable",
        "cache": {},
        "scopes": [
            {
                "scope": "home",
                "user": "brucking",
                "mode": "exact",
                "under_prefix": 1,
                "excluded": {},
                "options": [
                    {
                        "name": "programs.kitty.enable",
                        "option": "programs.kitty.enable",
                        "type": "boolean",
                        "value": True,
                        "value_found": True,
                        "declarations": [],
                        "definitions": [],
                    }
                ],
            }
        ],
    }
    routes = {**COWSAY, "status": status}

    async def script(app, pilot, fake):
        await open_details(app, pilot)  # a package: home is off until toggled
        home = app.screen.query_one("#home")
        assert str(home.label) == "home: off (h)"
        await pilot.press("h")  # no users known yet: status is loaded for them
        await settle(app, pilot)
        assert str(home.label) == "home: brucking (h)"
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press("escape", "h")  # cancel; back to off
        assert str(home.label) == "home: off (h)"

    _, fake = drive(script, routes)
    assert ["status"] in fake.verbs()
    assert ["add", "cowsay", "--home", "brucking", "--dry-run"] in fake.verbs()

    hm_routes = {
        "search": "search_hm_kitty",
        "info": {"ok": False, "error": {"code": "not-found", "message": "no"}},
        "where": hm_where,
        "set --dry-run": "add_cowsay_dry",
    }

    async def hm(app, pilot, fake):
        await pilot.press(*"kitty", "enter")
        await settle(app, pilot)
        assert app.screen.hits[0].target == "programs.kitty.actionAliases"
        await pilot.press("enter")
        await settle(app, pilot)
        assert str(app.screen.query_one("#home").label) == "home: brucking"  # from where
        await pilot.press("e")
        await pilot.pause()
        assert app.screen.query_one("#value").value == "true"  # where's value
        await pilot.press("backspace", *"false", "enter")
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)

    _, fake = drive(script=hm, routes=hm_routes)
    assert fake.verbs()[-1] == [
        "set",
        "programs.kitty.actionAliases",
        "false",
        "--home",
        "brucking",
        "--dry-run",
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
        await pilot.press("b")  # a plan first: the apply must make it stale
        await settle(app, pilot, 0.1)
        assert app.screen.query_one("#closure", DataTable).display
        await pilot.press("a")
        await settle(app, pilot)
        await pilot.press(*"test: cowsay", "enter")
        await settle(app, pilot)
        text = app.screen.query_one("#result", Fields).text
        assert "system switched to generation 113" in text
        assert app.screen.query_one("#log", LogView).lines_seen[-1] == (
            "reloading user units for master..."
        )
        await pilot.press("escape")
        await settle(app, pilot)
        pending = app.screen
        assert isinstance(pending, PendingScreen)  # the old plan is gone, the hint is back
        assert not pending.query_one("#closure", DataTable).display
        assert pending.query_one("#plan", Fields).note.startswith("b: build the workspace")
        assert "applied generation 113" in pending.query_one("#session", Fields).text
        await pilot.press("q")

    app, fake = drive(script, routes)
    # back on Pending, status is loaded again: the apply made it stale
    assert fake.verbs()[-2:] == [["apply", "-m", "test: cowsay"], ["status"]]
    assert ["plan"] in fake.verbs() and app.plan_doc is None
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


# --- the Packages screen: installed (`list`), updates (`outdated`), `update` ---------------------


async def open_packages(app, pilot, *keys: str):
    await pilot.press("escape", *(keys or ("i",)))  # out of the query box first
    await settle(app, pilot)
    assert isinstance(app.screen, PackagesScreen)
    return app.screen


def fits(app):
    w, _ = app.size
    for widget in app.screen.query("*"):
        if widget.display and widget.region.width:
            assert widget.region.right <= w, widget


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_packages_installed_filter_scope_details(size):
    async def script(app, pilot, fake):
        screen = await open_packages(app, pilot)
        table = screen.query_one("#pk-table", DataTable)
        assert table.row_count == 78 and table.has_focus
        cols = [str(c.key.value) for c in table.columns.values()]
        assert cols == (["name", "version", "tag"] + ([] if size == PHONE else ["where"]))
        assert screen.query_one("#pk-footer", Line).value.startswith(
            "78 packages: 26 system, 52 home brucking · 67 repeats folded"
        )
        assert "acl-2.4.0 · the system · added by a module" in screen_text(app)
        fits(app)
        await pilot.press("f", *"zsh")
        await settle(app, pilot)
        assert table.row_count == 2
        await pilot.press("enter", "h", "h")  # to the table; system, then home brucking
        await settle(app, pilot)
        assert [r.user for r in screen.shown] == ["brucking"]
        assert screen.query_one("#pk-footer", Line).value.startswith(
            "1 of 78 packages matching 'zsh' (home brucking)"
        )
        assert "home brucking" in str(screen.query_one("#pk-scope").label)
        await pilot.press("enter")
        await settle(app, pilot)
        details = app.screen
        assert isinstance(details, DetailsScreen) and details.user == "brucking"
        assert "home: brucking (h)" in screen_text(app)
        await pilot.press("r")  # Remove: the writer previews `rm zsh --home brucking`
        await settle(app, pilot)
        await pilot.press("escape", "escape")
        await settle(app, pilot)
        assert app.screen is screen and table.row_count == 1  # the filter stays

    _, fake = drive(script, PACKAGES, size=size, host="19BK")
    verbs = fake.verbs()
    assert verbs[0] == ["list"]  # one eval: filter and scope work on the loaded rows
    assert ["where", "zsh", "--kind", "package"] in verbs
    assert ["rm", "zsh", "--home", "brucking", "--dry-run"] in verbs
    assert verbs.count(["list"]) == 1  # back from Details: the kept document


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_packages_updates_then_update_lock_to_pending(size):
    async def script(app, pilot, fake):
        screen = await open_packages(app, pilot)
        await pilot.press("t")
        await settle(app, pilot)
        table = screen.query_one("#up-table", DataTable)
        assert table.row_count == 14 and table.has_focus
        assert screen.query_one("#up-kernel", Line).value == "kernel 7.2.12 → 7.2.14"
        assert screen.query_one("#up-summary", Line).value == (
            "13 of 325 packages would change; kernel 7.2.12 → 7.2.14"
        )
        assert "19BK: nixpkgs (github:nixos/nixpkgs/nixos-unstable) 3ec6363" in (
            screen.query_one("#up-head", Line).value
        )
        text = screen_text(app)
        assert "firefox" in text and "removed" in text and "Update nixpkgs lock (u)" in text
        fits(app)
        await pilot.press("m")  # --published
        await settle(app, pilot)
        assert table.row_count == 13
        assert screen.query_one("#up-kernel", Line).value.endswith("latest 7.3")
        assert "mode: published (m)" in screen_text(app)
        fits(app)
        await pilot.press("m")  # back to the lock: kept, not evaluated again
        await settle(app, pilot)
        assert table.row_count == 14
        await pilot.press("u")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, UpdateModal)
        text = modal.query_one("#update-fields", Fields).text
        assert "nix flake update nixpkgs --flake /home/master/nixos" in text
        assert "nothing is built or switched" in text
        await pilot.press("u")
        await settle(app, pilot)
        assert isinstance(app.screen, PendingScreen)
        assert "nixpkgs 3ec6363 → 0bb2eed (flake.lock)" in screen_text(app)
        assert "flake.lock" in app.screen.query_one("#pending", Fields).text
        await pilot.press("escape")  # back: the lock moved, so both documents are stale
        await settle(app, pilot)
        assert app.screen is screen

    app, fake = drive(script, PACKAGES, size=size, host="19BK")
    verbs = fake.verbs()
    assert verbs[:-2] == [
        ["list"],
        ["outdated"],
        ["outdated", "--published"],
        ["update", "--dry-run"],
        ["update"],
        ["status"],
    ]
    assert sorted(verbs[-2:]) == [["list"], ["outdated"]]  # both again, in two workers
    assert app.writes == ["nixpkgs 3ec6363 → 0bb2eed (flake.lock)"]
    assert app.return_value is None or "nothing changed" not in app.return_value


def test_packages_nothing_to_update_and_errors():
    routes = {
        **PACKAGES,
        "outdated": {**load("outdated_19BK"), "up_to_date": True, "changed": [], "count": 0},
        "outdated --published": {
            "ok": False,
            "error": {"code": "network", "message": "search.nixos.org: timed out"},
        },
    }

    async def script(app, pilot, fake):
        screen = await open_packages(app, pilot, "ctrl+backslash")  # ctrl+4, legacy terminal
        await pilot.press("t")
        await settle(app, pilot)
        assert screen.query_one("#up-update").disabled
        assert screen.query_one("#up-summary", Line).value.endswith("nothing would change")
        await pilot.press("u")
        await settle(app, pilot)
        assert app.screen is screen  # no dry run, no modal
        await pilot.press("m")
        await settle(app, pilot)
        notice = screen.query_one("#up-notice", NoticeBox)
        assert notice.display and notice.notice.code == "network"
        assert screen.query_one("#up-table", DataTable).row_count == 0

    _, fake = drive(script, routes)
    assert fake.verbs() == [["list"], ["outdated"], ["outdated", "--published"]]


def test_page_keys_fold_into_one_group_on_a_phone():
    def footer(size, keys):
        out = {}

        async def script(app, pilot, fake):
            await pilot.press("escape", *keys)
            await settle(app, pilot)
            out["text"] = screen_text(app).splitlines()[-1]

        drive(script, PACKAGES, size=size)
        return out["text"]

    assert "i Packages o Options p Pending s Status" in footer(DESKTOP, [])
    assert footer(PHONE, []).rstrip().endswith("? Keys  i o p s go")
    assert "esc Back t Updates q Quit" in footer(PHONE, ["i"])
    assert "t Installed" in footer(PHONE, ["i", "t"])
    assert " i o s go" in footer(PHONE, ["p"])  # the page you are on is left out
    assert footer(PHONE, ["o", "escape"]).rstrip().endswith("? Keys  i p s go")


def test_screenshots_export_at_both_sizes():
    for size in (PHONE, DESKTOP):

        async def script(app, pilot, fake):
            await open_details(app, pilot)
            svg = app.export_screenshot()
            assert svg.startswith("<svg") and "cowsay" in svg
            assert "#120b10" in svg.lower()  # the theme's background

        drive(script, size=size)


# --- the Options page: search NixOS / Home Manager options, details, set, unset ----------------


async def open_options(app, pilot, *keys: str):
    await pilot.press("escape", *(keys or ("o",)))  # out of the query box first
    await settle(app, pilot)
    assert isinstance(app.screen, OptionsScreen)
    return app.screen


async def pick_option(app, pilot, screen, name: str):
    """Move the cursor to `name` in the results and open it."""
    rows = [h.target for h in screen.hits]
    await pilot.press(*["down"] * rows.index(name), "enter")
    await settle(app, pilot)
    assert isinstance(app.screen, OptionScreen) and app.screen.hit.target == name
    return app.screen


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_options_search_details_set_then_pending(size):
    async def script(app, pilot, fake):
        screen = await open_options(app, pilot)
        assert screen.query_one("#op-query").has_focus
        assert "type to search NixOS options" in screen_text(app)
        await pilot.press(*"openssh", "enter")
        await settle(app, pilot)
        table = screen.query_one("#op-table", DataTable)
        assert table.row_count == 12 and table.has_focus
        cols = [str(c.key.value) for c in table.columns.values()]
        assert cols == (["name", "detail"] if size == PHONE else ["name", "detail", "description"])
        assert screen.query_one("#op-preview", Line).value == "OpenSSH package to use for sshd."
        assert screen.query_one("#op-source", Line).value.startswith(
            "12 result(s) · search.nixos.org unstable@0bb2eedc"
        )
        fits(app)
        details = await pick_option(app, pilot, screen, "services.openssh.enable")
        text = screen_text(app)
        assert "= true" in text and "Set (e)" in text and "home:" not in text
        assert "hosts/mbp13/default.nix:249" in details.query_one("#op-host", Fields).text
        assert "sshd.nix:247" in details.query_one("#op-declared", Fields).text
        assert "secure remote logins" in details.query_one("#op-about", Fields).text
        fits(app)
        await pilot.press("e")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, SetOptionModal)
        assert modal.query_one("#value").value == "true"  # the value on mbp13
        assert "type boolean" in modal.query_one("#set-hint", Fields).text
        await pilot.press(*["backspace"] * 4, *"false", "enter")
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)
        assert "+    enable = false;" in screen_text(app)
        await pilot.press("w")
        await settle(app, pilot)
        assert app.screen is details  # where again: the new value
        assert "= false" in screen_text(app)
        assert app.writes == ["services.openssh.enable = false (hosts/mbp13/default.nix)"]
        await pilot.press("p")
        await settle(app, pilot)
        assert isinstance(app.screen, PendingScreen)
        assert "hosts/mbp13/default.nix" in app.screen.query_one("#pending", Fields).text
        assert "services.openssh.enable = false" in app.screen.query_one("#session", Fields).text
        await pilot.press("escape", "escape")
        await settle(app, pilot)
        assert app.screen is screen and table.row_count == 12  # the results stay

    _, fake = drive(script, OPTIONS, size=size)
    assert fake.verbs() == [
        ["search", "--kind", "opt", "--channel", "unstable", "--limit", "50", "--", "openssh"],
        ["where", "services.openssh.enable", "--kind", "option"],
        ["set", "services.openssh.enable", "false", "--dry-run"],
        ["set", "services.openssh.enable", "false"],
        ["where", "services.openssh.enable", "--kind", "option"],
        ["status"],
    ]


def test_options_unset_and_the_page_kept():
    async def script(app, pilot, fake):
        screen = await open_options(app, pilot, "ctrl+right_square_bracket")  # ctrl+5, legacy
        await pilot.press(*"openssh", "enter")
        await settle(app, pilot)
        await pick_option(app, pilot, screen, "services.openssh.enable")
        await pilot.press("u")
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)
        assert "-    enable = true;" in screen_text(app)
        await pilot.press("w")
        await settle(app, pilot)
        assert app.writes == ["-services.openssh.enable (hosts/mbp13/default.nix)"]
        await pilot.press("escape", "escape", "slash")  # Options is popped: Search
        await settle(app, pilot)
        await pilot.press("escape", "o")  # a new Options page: the query and results kept
        await settle(app, pilot)
        again = app.screen
        assert isinstance(again, OptionsScreen) and again is not screen
        assert again.query_one("#op-query").value == "openssh"
        assert again.query_one("#op-table", DataTable).row_count == 12

    _, fake = drive(script, OPTIONS)
    verbs = fake.verbs()
    assert [v[0] for v in verbs] == ["search", "where", "unset", "unset", "where"]
    assert verbs[2] == ["unset", "services.openssh.enable", "--dry-run"]


@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_options_home_manager_for_a_user(size):
    async def script(app, pilot, fake):
        screen = await open_options(app, pilot)
        await pilot.press(*"git", "enter")
        await settle(app, pilot)
        await pilot.press("k")  # Home Manager options: the same query again
        await settle(app, pilot)
        assert "Home Manager (k)" in screen_text(app)
        assert screen.query_one("#op-table", DataTable).row_count == 12
        details = await pick_option(app, pilot, screen, "programs.git.lfs.enable")
        assert details.user == "brucking"  # the user whose tree has it (where)
        text = screen_text(app)
        assert "home: brucking (h)" in text and "[home brucking]" in text
        assert "home/modules/git.nix:6" in details.query_one("#op-host", Fields).text
        fits(app)
        await pilot.press("e")
        await settle(app, pilot)
        modal = app.screen
        assert modal.query_one("#value").value == "true"
        assert "example true" in modal.query_one("#set-hint", Fields).text
        await pilot.press(*["backspace"] * 4, *"false", "enter")
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)

    _, fake = drive(script, HM_OPTIONS, size=size, host="19BK")
    assert fake.verbs()[1][:3] == ["search", "--kind", "hm"]
    assert fake.verbs()[-1] == [
        "set",
        "programs.git.lfs.enable",
        "false",
        "--home",
        "brucking",
        "--dry-run",
    ]


def test_options_home_manager_without_users_and_templates():
    async def script(app, pilot, fake):
        screen = await open_options(app, pilot)
        await pilot.press(*"openssh", "enter")
        await settle(app, pilot)
        name = "users.users.<name>.openssh.authorizedKeys.keys"
        details = await pick_option(app, pilot, screen, name)
        assert "a template" in details.query_one("#op-host", Fields).text  # no where for it
        assert details.query_one("#op-unset").disabled
        await pilot.press("e")
        await settle(app, pilot)
        modal = app.screen
        assert isinstance(modal, SetOptionModal) and modal.query_one("#option").has_focus
        await pilot.press("enter", *'[ "ssh-ed25519 AAAA" ]', "enter")  # still <name>: refused
        await settle(app, pilot)
        assert app.screen is modal and modal.query_one("#option").has_focus
        await pilot.press(*"master", "enter", "enter")  # <name> was selected again
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)
        await pilot.press("escape", "escape", "k")  # Home Manager on mbp13: nobody has HM
        await settle(app, pilot)

    hm_nobody = {**load("options_where_hm_git_lfs"), "host": "mbp13"}
    hm_nobody["scopes"] = hm_nobody["scopes"][:1]
    _, fake = drive(script, OPTIONS)
    assert ["where", "users.users.<name>.openssh.authorizedKeys.keys", "--kind", "option"] not in (
        fake.verbs()
    )
    assert fake.verbs()[-2] == [
        "set",
        "users.users.master.openssh.authorizedKeys.keys",
        '[ "ssh-ed25519 AAAA" ]',
        "--dry-run",
    ]

    async def nobody(app, pilot, fake):
        screen = await open_options(app, pilot)
        await pilot.press(*"git", "enter", "k")  # the query, then Home Manager options
        await settle(app, pilot)
        details = await pick_option(app, pilot, screen, "programs.git.lfs.enable")
        assert details.user is None
        assert "no Home Manager user of this host has it" in screen_text(app)
        await pilot.press("e")  # refused: no user to write for
        await settle(app, pilot)
        assert app.screen is details

    _, fake = drive(nobody, {**HM_OPTIONS, "where": hm_nobody})
    assert not [v for v in fake.verbs() if v[0] == "set"]


def test_options_list_element_and_home_manager_template():
    includes = {
        **load("options_where_hm_git_lfs"),
        "name": "programs.git.includes",
    }
    for sc in includes["scopes"]:
        for o in sc["options"]:
            o.update(name="programs.git.includes", option="programs.git.includes", value=[])
    routes = {
        **HM_OPTIONS,
        "where": includes,
        "status": {**load("status_clean"), "hm_users": ["brucking"]},
    }

    async def script(app, pilot, fake):
        screen = await open_options(app, pilot)
        await pilot.press(*"git", "enter", "k")  # the query, then Home Manager options
        await settle(app, pilot)
        details = await pick_option(app, pilot, screen, "programs.git.includes.*.path")
        host = details.query_one("#op-host", Fields).text
        assert host.startswith("list programs.git.includes (Set writes it whole)")
        assert details.user == "brucking" and details.query_one("#op-unset").disabled
        await pilot.press("e")
        await settle(app, pilot)
        modal = app.screen
        assert modal.option == "programs.git.includes" and not modal.path  # the list itself
        assert modal.query_one("#value").value == "[ ]"  # the list's value, not the element's
        await pilot.press(*'[ { path = "~/.gitconfig.local"; } ]', "enter")
        await settle(app, pilot)
        assert isinstance(app.screen, DiffModal)

    _, fake = drive(script, routes, host="19BK")
    verbs = fake.verbs()
    assert ["where", "programs.git.includes", "--kind", "option"] in verbs
    assert verbs[-1] == [
        "set",
        "programs.git.includes",
        '[ { path = "~/.gitconfig.local"; } ]',
        "--home",
        "brucking",
        "--dry-run",
    ]

    hm_template = {
        "ok": True,
        "kind": "hm",
        "query": "matchBlocks",
        "count": 1,
        "results": [
            {
                "kind": "hm",
                "name": "programs.ssh.matchBlocks.<name>.hostname",
                "type": "null or string",
                "default": "null",
                "example": None,
                "description": "Specifies the real host name to log into.",
                "declarations": ["modules/programs/ssh.nix"],
                "declaration_urls": [],
                "source": "home-manager-options",
                "channel": "unstable",
                "rev": None,
            }
        ],
        "source": "home-manager-options",
        "channel": "unstable",
        "rev": None,
        "cached": True,
        "sources": {},
    }

    async def template(app, pilot, fake):
        screen = await open_options(app, pilot)
        await pilot.press(*"matchBlocks", "enter", "k")  # the query, then Home Manager options
        await settle(app, pilot)
        details = await pick_option(app, pilot, screen, "programs.ssh.matchBlocks.<name>.hostname")
        assert details.user is None  # no where for a template: nobody known yet
        await pilot.press("e")  # the users from status first
        await settle(app, pilot)
        assert app.screen is details and details.user == "brucking"
        await pilot.press("e")
        await settle(app, pilot)
        assert isinstance(app.screen, SetOptionModal)
        await pilot.press(*"gh", "enter", *'"github.com"', "enter")
        await settle(app, pilot)

    _, fake = drive(template, {**routes, "search": hm_template}, host="19BK")
    assert [v[:3] for v in fake.verbs()[:2]] == [["search", "--kind", "opt"]] + [
        ["search", "--kind", "hm"]
    ]
    assert fake.verbs()[2:] == [
        ["status"],
        [
            "set",
            "programs.ssh.matchBlocks.gh.hostname",
            '"github.com"',
            "--home",
            "brucking",
            "--dry-run",
        ],
    ]


def test_details_show_appstream_as_text():
    """The TUI shows a package's `appstream` as lines in the info pane (no images)."""
    info = load("info_cowsay")
    info["results"][0]["appstream"] = load("info_prismlauncher")["results"][0]["appstream"]

    async def script(app, pilot, fake):
        await open_details(app, pilot)
        text = app.screen.query_one("#info", Fields).text
        assert "app Prism Launcher" in text
        assert "summary Discover, manage, and play Minecraft instances" in text
        assert "category Game, ActionGame, AdventureGame" in text
        assert "shots 8 screenshot(s) (the GUI shows them)" in text
        assert "appstream search.nixos.org · an application" in text
        assert "Prism Launcher" in screen_text(app)

    drive(script, routes={**COWSAY, "info": info})
