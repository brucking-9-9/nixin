"""Writer layer, offline: a tiny config under tests/fixtures/writer-config copied to tmp_path,
fake tools (parse = the writer's own scanner, fmt = identity) and no nix.

Live tests (they clone $NIXIN_CONFIG and run nix) are skipped unless NIXIN_LIVE=1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nixin import writer
from nixin.cli import main
from nixin.configrepo import NixinError
from nixin.writer import edits
from nixin.writer.nixtext import NixFile, NixSyntaxError, lex, parse_attrpath
from nixin.writer.state import Tools, render
from nixin.writer.workspace import HEADER, Workspace

FIXTURE = Path(__file__).parent / "fixtures" / "writer-config"


class FakeTools:
    """parse: the writer's own bracket/binding scanner; fmt: identity."""

    def __init__(self) -> None:
        self.parsed: list[str] = []

    def parse(self, text: str) -> str | None:
        self.parsed.append(text)
        try:
            nf = NixFile(text)
            nf.module_shapes()
        except NixSyntaxError as e:
            return str(e)
        return None

    def fmt(self, text: str) -> tuple[str, str | None]:
        return text, None


class FakeNix:
    """Answers the writer's eval checks from a canned package set."""

    def __init__(self, installed: list[str], pkgs: dict[str, str], fail: str | None = None):
        self.installed, self.pkgs, self.fail = installed, pkgs, fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        apply = args[args.index("--apply") + 1] if "--apply" in args else ""
        if self.fail:
            raise NixinError("eval-failed", self.fail)
        if "hasAttrByPath" in apply:
            wanted = _lists(apply)
            return json.dumps([".".join(a) in self.pkgs for a in wanted])
        if "installed" in apply:
            wanted = _lists(apply)
            return json.dumps(
                {
                    "installed": self.installed,
                    "wanted": [self.pkgs.get(".".join(a)) for a in wanted],
                }
            )
        if "__nixinUnset" in apply:
            return "true"
        raise AssertionError(f"unexpected nix call {args}")


def _lists(apply: str) -> list[list[str]]:
    """The attribute paths of the trailing `[ [ "a" "b" ] … ]` argument."""
    tail = apply[apply.rindex("[ [") :]
    return [re.findall(r'"([^"]*)"', g) for g in re.findall(r'\[((?: "[^"]*")+) \]', tail)]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "config"
    shutil.copytree(FIXTURE, root)
    monkeypatch.setattr(writer, "TOOLS", FakeTools())
    monkeypatch.setattr(writer, "NIX", FakeNix([], {}, fail="nix must not run in this test"))
    return root


def run(root: Path, *argv: str, host: str = "mbp13", nix: bool = False) -> tuple[int, dict]:
    import io
    from contextlib import redirect_stdout

    args = ["--json", "--host", host, *argv, "--config", str(root)]
    if not nix:
        args.append("--no-eval")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(args)
    return rc, json.loads(buf.getvalue())


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


# --- nixtext ------------------------------------------------------------------------


def test_lexer_strings_paths_and_comments():
    src = """{ a = "x ${"y}"} z"; b = ''q ''${no} ${ {c=1;}.c } ''' ''; p = ./a/${x}.nix;
    u = https://example.org/x; d = 1/2; e = a // b; /* c } */ f = <nixpkgs/lib>; }"""
    toks, comments = lex(src)
    kinds = {t.text: t.kind for t in toks}
    assert kinds['"x ${"y}"} z"'] == "str"
    assert kinds["./a/${x}.nix"] == "path"
    assert kinds["https://example.org/x"] == "uri"
    assert kinds["1/2"] == "path"  # as in Nix
    assert kinds["//"] == "//"
    assert kinds["<nixpkgs/lib>"] == "path"
    assert [c.text for c in comments] == ["/* c } */"]
    NixFile(src)  # brackets match


def test_walk_follows_wrappers_and_full_form_modules():
    nf = NixFile((FIXTURE / "services.nix").read_text())
    defs, special = nf.module_defs()
    paths = {d.path: d for d in defs}
    assert ("services", "tailscale", "enable") in paths
    assert ("environment", "systemPackages") in paths
    assert paths[("environment", "systemPackages")].conditional  # under mkIf
    assert not any(p[0] in ("options", "config") for p in paths)
    assert special == []


def test_walk_skips_strings_and_let_bindings():
    nf = NixFile((FIXTURE / "hosts/mbp13/default.nix").read_text())
    defs, special = nf.module_defs()
    paths = [d.path for d in defs]
    assert ("greeting",) not in paths  # let binding, not config
    assert paths.count(("services", "openssh", "enable")) == 1  # not the one in the '' string
    assert ("services", "openssh", "settings", "PermitRootLogin") in paths
    items = [
        nf.compact(e.lo, e.hi)
        for d in defs
        if d.path == ("environment", "systemPackages")
        for _, e in nf.list_items(d)
    ]
    assert items == ["git", "kitty", "btop", "sl"]  # through with, ++ and lib.optionals
    imports = [nf.elem_path(e) for d in special for _, e in nf.list_items(d)]
    assert imports == ["./hardware.nix", "./desktop.nix"]


def test_parse_attrpath():
    assert parse_attrpath('environment.etc."a.conf".text') == [
        "environment",
        "etc",
        "a.conf",
        "text",
    ]
    assert parse_attrpath("services.openssh.enable") == ["services", "openssh", "enable"]
    for bad in ("a..b", "a.", "foo bar", "a.${b}", "1.2"):
        with pytest.raises(ValueError):
            parse_attrpath(bad)


# --- edits --------------------------------------------------------------------------


def test_removal_owned_line_trailing_comment_and_shared_line():
    t = "[\n  a\n  kitty # terminal\n  b\n]\n"
    s = t.index("kitty")
    assert edits.apply(t, [edits.removal(t, s, s + 5)]) == "[\n  a\n  b\n]\n"
    t = "[ a kitty b ]"
    s = t.index("kitty")
    assert edits.apply(t, [edits.removal(t, s, s + 5)]) == "[ a b ]"
    t = "{\n  # keep me\n  x = 1;\n\n  y = 2;\n}\n"
    s, e = t.index("x ="), t.index("1;") + 2
    assert edits.apply(t, [edits.removal(t, s, e)]) == "{\n  # keep me\n\n  y = 2;\n}\n"


def test_import_insert_and_exact_restore_of_a_one_line_list():
    text = "{ ... }:\n{\n  imports = [ ./modules ];\n}\n"
    nf = NixFile(text)
    _, special = nf.module_defs()
    sh = nf.shapes(special[0].binding.val_lo, special[0].binding.val_hi)[0]
    e, original = edits.insert_into_list(nf, sh, "../x.nix # gen")
    new = edits.apply(text, [e])
    assert new == "{ ... }:\n{\n  imports = [\n    ./modules\n    ../x.nix # gen\n  ];\n}\n"
    assert original == "[ ./modules ]"
    nf2 = NixFile(new)
    _, special = nf2.module_defs()
    sh2 = nf2.shapes(special[0].binding.val_lo, special[0].binding.val_hi)[0]
    e2, restored = edits.remove_from_list(nf2, sh2, 1, original)
    assert restored and edits.apply(new, [e2]) == text


# --- workspace ----------------------------------------------------------------------


def test_hosts_closure_and_hm_users(cfg):
    ws = Workspace.open(str(cfg))
    hosts = ws.flake_hosts()
    assert sorted(hosts) == ["19BK", "bare", "mbp13"]
    files = [m.file for m in ws.closure(hosts["19BK"]) if not m.inline]
    assert files == ["configuration.nix", "ssh.nix", "services.nix"]
    users = ws.hm_users(ws.closure(hosts["19BK"]))
    assert [m.file for m in users["alice"]] == ["home/home.nix"]
    hm = [m.file for m in ws.closure(users["alice"])]
    assert hm == [
        "home/home.nix",
        "home/modules/default.nix",
        "home/modules/kitty.nix",
        "home/modules/packages.nix",
    ]
    assert ws.hm_users(ws.closure(hosts["mbp13"])) == {}


def test_option_decls_exact_covering_below(cfg):
    ws = Workspace.open(str(cfg))
    mods = ws.closure(ws.flake_hosts()["19BK"])
    exact, _, _ = ws.option_decls(mods, ("services", "openssh", "enable"))
    assert [(f.file, f.line) for f in exact] == [("ssh.nix", 5)]
    exact, _, below = ws.option_decls(mods, ("services", "tailscale", "enable"))
    assert exact[0].wrappers == ("mkForce",)
    assert ws.text("services.nix")[slice(*exact[0].value_span)] == "true"
    _, _, below = ws.option_decls(mods, ("services", "openssh"))
    assert {f.option for f in below} >= {"services.openssh.enable", "services.openssh.openFirewall"}


# --- verbs: the acceptance list, offline ----------------------------------------------


def test_add_creates_module_and_import_then_rm_reverts(cfg):
    before = snapshot(cfg)
    rc, d = run(cfg, "add", "cowsay")
    assert rc == 0 and d["ok"], d
    kinds = {c["file"]: c["kind"] for c in d["changes"]}
    assert kinds == {
        "hosts/mbp13/default.nix": "modified",
        "installer/mbp13.nix": "created",
        "installer/state.json": "created",
    }
    assert d["imports_added"] == [
        {"file": "hosts/mbp13/default.nix", "import": "../../installer/mbp13.nix"}
    ]
    gen = (cfg / "installer/mbp13.nix").read_text()
    assert gen.startswith(HEADER) and "pkgs.cowsay" in gen
    assert (
        "../../installer/mbp13.nix # generated by nixin"
        in (cfg / "hosts/mbp13/default.nix").read_text()
    )
    state = json.loads((cfg / "installer/state.json").read_text())
    assert state["nixos"]["mbp13"]["packages"] == ["cowsay"]
    assert all(p["ok"] for p in d["checks"]["parse"])
    assert d["warnings"] == [] and d["imports_removed"] == []  # always present

    rc, d = run(cfg, "add", "cowsay", "git")  # idempotent
    assert rc == 0 and d["changes"] == []
    assert {a["name"]: a["action"] for a in d["actions"]} == {
        "cowsay": "already-added",
        "git": "already-declared",
    }

    rc, d = run(cfg, "rm", "cowsay")
    assert rc == 0, d
    assert {c["kind"] for c in d["changes"]} == {"modified", "deleted"}
    assert snapshot(cfg) == before


def test_add_home_expands_one_line_imports_and_rm_restores(cfg):
    before = snapshot(cfg)
    rc, d = run(cfg, "add", "--home", "alice", "cowsay", host="19BK")
    assert rc == 0, d
    assert d["scope"] == "home" and d["user"] == "alice"
    home = (cfg / "home/home.nix").read_text()
    assert "    ./modules\n    ../installer/alice-home.nix # generated by nixin" in home
    assert "home.packages" in (cfg / "installer/alice-home.nix").read_text()
    rc, d = run(cfg, "rm", "--home", "alice", "cowsay", host="19BK")
    assert rc == 0 and d["imports_removed"][0]["restored"]
    assert snapshot(cfg) == before


def test_set_renders_nested_attrpath_and_unset_removes_it(cfg):
    before = snapshot(cfg)
    rc, d = run(cfg, "set", "services.openssh.settings.X11Forwarding", "true", host="19BK")
    assert rc == 0, d
    gen = (cfg / "installer/19BK.nix").read_text()
    assert "  services.openssh.settings.X11Forwarding = true;\n" in gen
    assert '"services.openssh' not in gen  # an attrpath, not a quoted flat key
    assert "./installer/19BK.nix # generated" in (cfg / "configuration.nix").read_text()
    rc, d = run(cfg, "unset", "services.openssh.settings.X11Forwarding", host="19BK")
    assert rc == 0, d
    assert snapshot(cfg) == before


def test_set_quoted_component_and_multiline_value(cfg):
    rc, d = run(cfg, "set", 'environment.etc."a.conf".text', "''\n  x\n''", host="19BK")
    assert rc == 0, d
    gen = (cfg / "installer/19BK.nix").read_text()
    assert 'environment.etc."a.conf".text =' in gen
    state = json.loads((cfg / "installer/state.json").read_text())
    assert state["nixos"]["19BK"]["options"] == {'environment.etc."a.conf".text': "''\n  x\n''"}


def test_rm_hand_written_element_edits_in_place(cfg):
    rc, d = run(cfg, "rm", "kitty")
    assert rc == 0, d
    [c] = d["changes"]
    assert c["file"] == "hosts/mbp13/default.nix" and c["kind"] == "modified"
    minus = [ln for ln in c["diff"].splitlines() if ln.startswith("-") and not ln.startswith("---")]
    plus = [ln for ln in c["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert minus == ["-      kitty # terminal"] and plus == []
    assert not (cfg / "installer").exists()


def test_set_existing_definition_edits_in_place_and_keeps_mkforce(cfg):
    rc, d = run(cfg, "set", "services.openssh.enable", "false", host="19BK")
    assert rc == 0, d
    assert [c["file"] for c in d["changes"]] == ["ssh.nix"]
    assert "    enable = false;\n" in (cfg / "ssh.nix").read_text()
    assert d["actions"][0]["in_place"] and d["actions"][0]["old"] == "true"
    rc, d = run(cfg, "set", "services.tailscale.enable", "false", host="19BK")
    assert rc == 0, d
    assert "services.tailscale.enable = lib.mkForce false;" in (cfg / "services.nix").read_text()
    assert any("mkForce" in w for w in d["warnings"])
    rc, d = run(cfg, "set", "services.openssh.enable", "false", host="19BK")
    assert d["changes"] == [] and d["actions"][0]["action"] == "unchanged"


def test_unset_hand_written_binding(cfg):
    rc, d = run(cfg, "unset", "services.openssh.openFirewall", host="19BK")
    assert rc == 0, d
    text = (cfg / "ssh.nix").read_text()
    assert "openFirewall" not in text and "\n\n\n" not in text
    assert "# --- OpenSSH ---" in text


def test_ambiguous_then_where(cfg):
    before = snapshot(cfg)
    rc, d = run(cfg, "rm", "btop")
    assert rc == 1 and d["error"]["code"] == "ambiguous"
    assert [(c["file"], c["line"]) for c in d["error"]["candidates"]] == [
        ("hosts/mbp13/default.nix", 17),
        ("hosts/mbp13/desktop.nix", 4),
    ]
    assert snapshot(cfg) == before
    rc, d = run(cfg, "rm", "btop", "--where", "hosts/mbp13/desktop.nix:4")
    assert rc == 0, d
    assert [c["file"] for c in d["changes"]] == ["hosts/mbp13/desktop.nix"]
    rc, d = run(cfg, "rm", "git", "--where", "nope.nix:1")
    assert rc == 1 and d["error"]["code"] == "not-found"


def test_not_found_points_at_programs_enable(cfg):
    rc, d = run(cfg, "rm", "--home", "alice", "kitty", host="19BK")
    assert rc == 1 and d["error"]["code"] == "not-found"
    assert "programs.kitty.enable is set at home/modules/kitty.nix:4" in d["error"]["message"]
    rc, d = run(cfg, "rm", "fd", host="19BK")
    assert "use --home alice" in d["error"]["message"]
    rc, d = run(cfg, "unset", "services.nope.enable")
    assert d["error"]["code"] == "not-found"


def test_errors_write_nothing(cfg):
    before = snapshot(cfg)
    rc, d = run(cfg, "set", "services.openssh.enable", "{ a = 1;")
    assert d["error"]["code"] == "invalid-nix"
    rc, d = run(cfg, "add", "foo bar")
    assert d["error"]["code"] == "invalid-nix"
    rc, d = run(cfg, "add", "cowsay", host="bare")
    assert d["error"]["code"] == "import-site-missing"
    assert d["error"]["line"] == "imports = [ ../installer/bare.nix ];"
    rc, d = run(cfg, "add", "cowsay", host="nope")
    assert d["error"]["code"] == "unknown-host"
    rc, d = run(cfg, "add", "--home", "alice", "cowsay")
    assert d["error"]["code"] == "unknown-user" and "19BK" in d["error"]["message"]
    rc, d = run(cfg, "add", "--dry-run", "cowsay")
    assert rc == 0 and d["dry_run"] and len(d["changes"]) == 3
    assert snapshot(cfg) == before


def test_not_a_workspace(cfg, tmp_path):
    _, d = run(Path("/etc/nixos"), "add", "cowsay")
    assert d["error"]["code"] == "not-a-workspace"
    ro = tmp_path / "ro"
    shutil.copytree(FIXTURE, ro)
    ro.chmod(0o555)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("running as root")
        _, d = run(ro, "add", "cowsay")
        assert d["error"]["code"] == "not-a-workspace"
    finally:
        ro.chmod(0o755)


def test_read_only_module_inside_a_workspace_is_refused_before_writing(cfg):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    before = snapshot(cfg)
    (cfg / "hosts/mbp13/default.nix").chmod(0o444)
    rc, d = run(cfg, "add", "cowsay")
    assert rc == 1 and d["error"]["code"] == "not-a-workspace"
    assert snapshot(cfg) == before


def test_hand_edited_generated_file_is_rerendered_with_a_warning(cfg):
    run(cfg, "add", "cowsay")
    p = cfg / "installer/mbp13.nix"
    p.write_text(p.read_text().replace("pkgs.cowsay", "pkgs.cowsay pkgs.sl"))
    rc, d = run(cfg, "add", "hello")
    assert rc == 0 and any("edited by hand" in w for w in d["warnings"])
    assert "pkgs.sl" not in p.read_text() and "pkgs.hello" in p.read_text()


def test_eval_checks_with_a_fake_nix(cfg, monkeypatch):
    nix = FakeNix(["git-2.51", "cowsay-3.8.4"], {"cowsay": "cowsay-3.8.4", "git": "git-2.51"})
    monkeypatch.setattr(writer, "NIX", nix)
    rc, d = run(cfg, "add", "cowsay", nix=True)
    assert rc == 0, d
    ev = d["checks"]["eval"]
    assert ev["ok"] and ev["present"] == {"cowsay": "cowsay-3.8.4"}
    assert ev["attr"] == "nixosConfigurations.mbp13.config.environment.systemPackages"
    rc, d = run(cfg, "add", "nosuch", nix=True)
    assert d["error"]["code"] == "unknown-package"
    monkeypatch.setattr(writer, "NIX", FakeNix([], {}, fail="Scripted initrd has been removed."))
    rc, d = run(cfg, "rm", "kitty", nix=True)
    assert rc == 0 and d["ok"]  # the text change happened and is reported
    assert d["checks"]["eval"]["code"] == "eval-failed"
    assert any("Scripted initrd" in w for w in d["warnings"])


def test_human_output_is_the_diff_and_one_summary_line(cfg, capsys):
    rc = main(["--host", "mbp13", "add", "cowsay", "--config", str(cfg), "--no-eval"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "--- a/hosts/mbp13/default.nix"
    assert out[-1].startswith("added cowsay to mbp13 — ")


def test_render_through_real_nixfmt_is_stable():
    if shutil.which("nixfmt") is None:
        pytest.skip("nixfmt not on PATH")
    entry = {"packages": ["cowsay", "python3Packages.requests"], "options": {"a.b": "true"}}
    text, why = Tools().fmt(render("nixos", entry))
    assert why is None
    assert text == Tools().fmt(text)[0]
    assert "    pkgs.cowsay\n    pkgs.python3Packages.requests\n" in text
    assert "  a.b = true;\n" in text


# --- live -----------------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("NIXIN_LIVE") != "1", reason="set NIXIN_LIVE=1 to run nix")


@live
def test_live_add_rm_on_a_clone(tmp_path, monkeypatch):
    src = os.environ.get("NIXIN_CONFIG") or os.path.expanduser("~/nixos")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", src, str(clone)], check=True)
    host = os.environ.get("NIXIN_LIVE_HOST", "19BK")
    rc, d = run(clone, "add", "cowsay", host=host, nix=True)
    assert rc == 0, d
    assert d["checks"]["eval"]["ok"], d["checks"]
    rc, d = run(clone, "rm", "cowsay", host=host, nix=True)
    assert rc == 0 and d["checks"]["eval"]["absent"] == ["cowsay"]
    status = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
