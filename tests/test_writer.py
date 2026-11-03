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
import sys
from pathlib import Path

import pytest

from nixin import writer
from nixin.cli import main
from nixin.configrepo import NixinError
from nixin.writer import edits, typecheck
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
    """Answers the writer's eval checks from a canned package set; with `options`, also
    truth's option queries (flake metadata/archive, options.nix by prefix). `fail` fails
    every call, `fail_check` only the checks after a write."""

    def __init__(
        self,
        installed: list[str],
        pkgs: dict[str, str],
        fail: str | None = None,
        *,
        options: dict[str, dict] | None = None,
        fail_check: str | None = None,
        bad_value: str | None = None,
    ):
        self.installed, self.pkgs, self.fail = installed, pkgs, fail
        self.options, self.fail_check, self.bad_value = options or {}, fail_check, bad_value
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        apply = args[args.index("--apply") + 1] if "--apply" in args else ""
        if self.fail:
            raise NixinError("eval-failed", self.fail)
        if args[:2] == ["flake", "metadata"]:  # a new fingerprint each time: no cache hits
            return json.dumps({"path": STORE, "fingerprint": f"fp-{len(self.calls)}"})
        if args[:2] == ["flake", "archive"]:
            return json.dumps({"path": STORE, "inputs": {"nixpkgs": {"path": NIXPKGS}}})
        if "nixin-option" in apply:  # truth's options.nix: entries by prefix
            spec = apply[apply.rindex("{ prefix = ") :]
            prefix = re.findall(r'"([^"]*)"', re.search(r"prefix = \[([^\]]*)\]", spec).group(1))
            return json.dumps(_option_entries(self.options, prefix))
        if self.fail_check and ("installed" in apply or "__nixinUnset" in apply):
            raise NixinError("eval-failed", self.fail_check)
        if self.bad_value and "__nixinUnset" in apply:  # the option's value throws
            return json.dumps({"__nixinError": True})
        if self.bad_value and "builtins.deepSeq" in apply:  # … and why, without tryEval
            raise NixinError("eval-failed", self.bad_value)
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


STORE = "/nix/store/0000000000000000000000000000000-source"
NIXPKGS = "/nix/store/6nwzgfvznah5rg192x0hnl5ykchdgi59-source"
SSHD = f"{NIXPKGS}/nixos/modules/services/networking/ssh/sshd.nix"
BOOL = {"name": "bool", "description": "boolean"}
U16 = {
    "name": "unsignedInt16",
    "description": "16 bit unsigned integer; between 0 and 65535 (both inclusive)",
}
PERMIT = {
    "name": "nullOr",
    "description": 'null or one of "yes", "no"',
    "nested": {
        "elemType": {"name": "enum", "description": 'one of "yes", "no"', "values": ["yes", "no"]}
    },
}
# options.nix entries (docs mode) for the mbp13 fixture host
OPTIONS = {
    "services.openssh.enable": {
        "type": "boolean",
        "typeInfo": BOOL,
        "default": {"value": False},
        "description": "Whether to enable the OpenSSH secure shell daemon. ",
        "declarations": [SSHD],
        "definitions": [f"{STORE}/hosts/mbp13/default.nix"],
        "definitionValues": [{"file": f"{STORE}/hosts/mbp13/default.nix", "value": True}],
        "isDefined": True,
        "value": True,
    },
    "services.openssh.settings": {
        "type": "open submodule of attribute set of (atom)",
        "typeInfo": {
            "name": "submodule",
            "description": "open submodule of attribute set of (atom)",
        },
        "default": {"value": {}},
        "declarations": [SSHD],
        "definitions": [f"{STORE}/hosts/mbp13/default.nix"],
        "isDefined": True,
        "value": {"PermitRootLogin": "no"},
        "sub:PermitRootLogin": {
            "option": "services.openssh.settings.PermitRootLogin",
            "description": "Whether the root user can login using ssh. ",
            "typeInfo": PERMIT,
            "default": {"value": "prohibit-password"},
        },
    },
    "networking.firewall.allowedTCPPorts": {
        "type": "list of 16 bit unsigned integer",
        "typeInfo": {"name": "listOf", "description": "list of port", "nested": {"elemType": U16}},
        "default": {"value": []},
        "example": {"value": [22, 80]},
        "declarations": [f"{NIXPKGS}/nixos/modules/services/networking/firewall.nix"],
        "definitions": [],
        "isDefined": True,
        "value": [],
    },
    "networking.hostName": {
        "type": "string",
        "typeInfo": {"name": "str", "description": "string"},
        "default": {"value": "nixos"},
        "declarations": [f"{NIXPKGS}/nixos/modules/tasks/network-interfaces.nix"],
        "definitions": [],
        "isDefined": True,
        "value": "nixos",
    },
}


def _option_entries(options: dict[str, dict], prefix: list[str]) -> list[dict]:
    """What options.nix answers for a prefix: the options under it, or the option above it
    (with `sub` for the rest of the path when the test's table has one)."""
    out = []
    for name, spec in options.items():
        parts = name.split(".")
        if parts[: len(prefix)] != prefix and prefix[: len(parts)] != parts:
            continue
        entry = {"name": name, **{k: v for k, v in spec.items() if not k.startswith("sub:")}}
        rest = prefix[len(parts) :]
        if rest:
            entry["sub"] = spec.get("sub:" + ".".join(rest), {"opaque": "submodule"})
        out.append(entry)
    return out


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
    before = (cfg / "hosts/mbp13/default.nix").read_text()
    rc, d = run(cfg, "rm", "kitty", nix=True)
    # the eval after the write failed: the verb fails, the change stays and is listed
    assert rc == 1 and d["ok"] is False and d["error"]["code"] == "eval-failed"
    assert d["checks"]["eval"]["code"] == "eval-failed"
    assert [c["file"] for c in d["changes"]] == ["hosts/mbp13/default.nix"]
    assert (cfg / "hosts/mbp13/default.nix").read_text() != before
    assert "Scripted initrd" in d["error"]["message"] and "--no-check" in d["error"]["message"]
    assert d["error"]["undo"] == [f"git -C {cfg} checkout -- hosts/mbp13/default.nix"]
    rc, d = run(cfg, "add", "jq", "hello", nix=True)  # the pre-check fails too: a warning
    assert d["error"]["code"] == "eval-failed" and d["error"]["undo"] == [
        f"nixin --host mbp13 rm jq hello --config {cfg}"
    ]
    assert any("could not check the package names" in w for w in d["warnings"])
    assert "pkgs.jq" in (cfg / "installer/mbp13.nix").read_text()
    nix_calls = len(writer.NIX.calls)
    rc, d = run(cfg, "rm", "jq", "hello", "--no-check", nix=True)  # --no-check: no eval at all
    assert rc == 0 and d["ok"] and d["checks"]["eval"] is None
    assert len(writer.NIX.calls) == nix_calls


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


def test_tools_time_out_instead_of_hanging(tmp_path, monkeypatch):
    from nixin.writer import state

    for name in ("nix-instantiate", "nixfmt"):
        exe = tmp_path / name
        exe.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(state, "TOOL_TIMEOUT", 0.2)
    assert "did not finish" in Tools().parse("{ }")
    text, why = Tools().fmt("{ }")
    assert text == "{ }" and "did not finish" in why


# --- the value check, with the real parser -----------------------------------------------


@pytest.fixture
def storeless_nix(tmp_path, monkeypatch):
    """nix-instantiate with no store (`dummy://`): parse and pure evals work, also in the
    build sandbox (nix is a check input)."""
    if shutil.which("nix-instantiate") is None:
        pytest.skip("nix-instantiate not on PATH")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NIX_REMOTE", "dummy://")


@pytest.fixture
def real_parse(cfg, storeless_nix, monkeypatch):
    """The real `nix-instantiate --parse`: it resolves names while parsing, which the fake
    scanner does not, and that is what the value check has to get right."""
    monkeypatch.setattr(writer, "TOOLS", Tools())
    return cfg


@pytest.mark.parametrize(
    ("option", "argv"),
    [
        ("boot.kernelPackages", ["pkgs.linuxPackages_7_2"]),
        ("x", ["lib.mkForce", "true"]),
        ("y", ["config.networking.hostName"]),
    ],
)
def test_set_value_may_name_module_arguments(real_parse, option, argv):
    rc, d = run(real_parse, "set", option, *argv)
    assert rc == 0, d
    value = " ".join(argv)
    assert d["value"] == value
    assert f"\n  {option} = {value};\n" in (real_parse / "installer/mbp13.nix").read_text()
    state = json.loads((real_parse / "installer/state.json").read_text())
    assert state["nixos"]["mbp13"]["options"] == {option: value}
    assert all(p["ok"] for p in d["checks"]["parse"]), d["checks"]


def test_set_malformed_value_is_invalid_nix(real_parse):
    before = snapshot(real_parse)
    rc, d = run(real_parse, "set", "z", "pkgs.foo [")
    assert rc == 1 and d["error"]["code"] == "invalid-nix", d
    msg = d["error"]["message"]
    assert "unexpected end of file" in msg and "1| pkgs.foo [" in msg  # the user's text
    assert "checks.eval" in msg
    assert snapshot(real_parse) == before


def test_set_names_are_checked_in_the_file_they_land_in(real_parse):
    """The value check lets every free name through; the parse of the edited file then knows
    the real scope: ssh.nix is `{ ... }:` (no lib), the generated module binds config, lib
    and pkgs only."""
    before = snapshot(real_parse)
    rc, d = run(real_parse, "set", "services.openssh.enable", "lib.mkForce false", host="19BK")
    assert rc == 1 and d["error"]["code"] == "invalid-nix", d
    assert d["error"]["file"] == "ssh.nix"
    assert "undefined variable 'lib'" in d["error"]["message"]
    rc, d = run(real_parse, "set", "z", "inputs.self.rev")
    assert rc == 1 and d["error"]["file"] == "installer/mbp13.nix", d
    assert "undefined variable 'inputs'" in d["error"]["message"]
    assert snapshot(real_parse) == before


def test_option_check_shows_package_sets_and_functions_as_json(storeless_nix):
    """`nix eval --json` of boot.kernelPackages failed ("cannot convert a function to JSON")."""
    value = (
        '{ f = x: x; drv = { type = "derivation"; name = "hello-2.12"; }; n = 1; '
        'kernel = { callPackage = throw "not forced"; }; p = /no/such/file; '
        "l = builtins.genList (i: i) 61; deep.a.b.c.d.e = 1; }"
    )
    out = subprocess.run(
        [
            "nix-instantiate",
            "--eval",
            "--strict",
            "--json",
            "--expr",
            f"({writer.SHOW_VALUE}) {value}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    got = json.loads(out)
    assert got["f"] == "<function>" and got["drv"] == "<derivation hello-2.12>"
    assert got["kernel"] == "<package set>" and got["p"] == "/no/such/file" and got["n"] == 1
    assert got["l"][:2] == [0, 1] and got["l"][60:] == ["<… 1 more>"]
    assert got["deep"]["a"]["b"]["c"]["d"] == "<…>"


# --- the type check before `set` ---------------------------------------------------------

STR = {"name": "str", "description": "string"}
ENUM = {"name": "enum", "description": 'one of "fast", "slow", 1', "values": ["fast", "slow", 1]}


def listof(t: dict) -> dict:
    return {
        "name": "listOf",
        "description": f"list of {t['description']}",
        "nested": {"elemType": t},
    }


def wrap(name: str, **nested: dict) -> dict:
    return {"name": name, "description": name, "nested": nested}


TYPE_CASES = [
    # (type, value, None when it fits / is not judged, else a piece of the reason)
    (BOOL, "true", None),
    (BOOL, "lib.mkForce false", None),
    (BOOL, "lib.mkOverride 50 true", None),
    (BOOL, "mkIf config.x.enable true", None),
    (BOOL, "a == b", None),
    (BOOL, "!x", None),
    (BOOL, "config.services.x.enable", None),  # an expression: the eval judges it
    (BOOL, "if x then true else false", None),
    (BOOL, "yes", "yes is a bare word (Nix reads it as a variable), not boolean"),
    (BOOL, '"yes"', '"yes" is a string, not boolean'),
    (BOOL, "1", "1 is an integer, not boolean"),
    (BOOL, "null", "null is null, not boolean"),
    (BOOL, "lib.mkDefault [ true ]", "is a list, not boolean"),
    (BOOL, "x: true", "is a function, not boolean"),
    (BOOL, "with lib; yes", None),  # under `with` a bare name may be bound
    (BOOL, "let yes = true; in yes", None),
    ({"name": "int", "description": "signed integer"}, "-3", None),
    ({"name": "int", "description": "signed integer"}, "1.5", "1.5 is a float"),
    (U16, "22", None),
    (U16, "70000", "70000 is out of range (0…65535)"),
    (U16, "-1", "-1 is out of range (0…65535)"),
    ({"name": "positiveInt", "description": "positive integer, meaning >0"}, "0", "at least 1"),
    (
        {"name": "intBetween", "description": "integer between 1 and 9 (both inclusive)"},
        "10",
        "(1…9)",
    ),
    ({"name": "float", "description": "floating point number"}, "1", "1 is an integer"),
    ({"name": "float", "description": "floating point number"}, "1.0", None),
    ({"name": "number", "description": "number"}, "1", None),
    (STR, '"box"', None),
    (STR, "''\n  multi\n''", None),
    (STR, '"${config.networking.domain}"', None),
    (STR, "https://example.org", None),  # a URI literal is a string
    (STR, "box", "box is a bare word, not string: quote it for Nix, '\"box\"'"),
    (STR, "./file", "./file is a path, not string"),
    (STR, '"a" + "b"', None),
    ({"name": "nonEmptyStr", "description": "non-empty string"}, '""', '"" is empty'),
    (
        {
            "name": "singleLineStr",
            "description": "(optionally newline-terminated) single-line string",
        },
        '"a\nb"',
        "more than one line",
    ),
    (
        {"name": 'strMatching "[a-z]+"', "description": "string matching the pattern [a-z]+"},
        "3",
        "is an integer",
    ),
    (
        {"name": "separatedString", "description": 'strings concatenated with "\\n"'},
        "[ ]",
        "is a list",
    ),
    (ENUM, '"fast"', None),
    (ENUM, "1", None),
    (ENUM, '"medium"', '"medium" is not one of "fast", "slow", 1'),
    (ENUM, "true", "true is a boolean, not one of"),
    (ENUM, "medium", 'medium is a bare word, not one of "fast", "slow", 1: quote it'),
    (ENUM, '"${x}"', None),
    (listof(U16), "[ 22 80 ]", None),
    (listof(U16), '[ 22 "80" ]', 'element 2: "80" is a string, not 16 bit unsigned integer'),
    (listof(U16), "[ 22 ] ++ config.x.ports", None),
    (listof(U16), "[ 22 ] ++ [ 99999 ]", "element 2: 99999 is out of range"),
    (listof(U16), "22", "22 is an integer, not list of"),
    (listof(U16), "lib.mkAfter [ 8080 ]", None),
    (listof(STR), "with pkgs; [ hello ]", None),
    (listof({"name": "package", "description": "package"}), "[ pkgs.hello ]", None),
    (listof({"name": "package", "description": "package"}), "[ hello ]", None),  # not judged
    (
        wrap("attrsOf", elemType=STR),
        '{ a = "x"; b.c = "y"; }',
        "b: { b = … } is an attribute set, not string",
    ),
    (wrap("attrsOf", elemType=STR), '{ a = "x"; } // config.y', None),
    (wrap("attrsOf", elemType=STR), "{ a = 1; }", "a: 1 is an integer, not string"),
    (wrap("attrsOf", elemType=STR), "[ ]", "is a list, not attrsOf"),
    (wrap("nullOr", elemType=STR), "null", None),
    (wrap("nullOr", elemType=STR), "true", "true is a boolean, not string"),
    (wrap("either", left=BOOL, right=STR), '"x"', None),
    (wrap("either", left=BOOL, right=STR), "1", "1 is an integer, not either"),
    (wrap("either", left=BOOL, right={"name": "package", "description": "package"}), "1", None),
    (wrap("coercedTo", coercedType=STR, finalType=listof(STR)), '"a"', None),
    (wrap("coercedTo", coercedType=STR, finalType=listof(STR)), "1", "1 is an integer"),
    (wrap("unique", elemType=BOOL), '"x"', "is a string, not boolean"),
    ({"name": "attrs", "description": "attribute set"}, "{ }", None),
    ({"name": "attrs", "description": "attribute set"}, "1", "1 is an integer"),
    # not judged: package, path, submodule, function, anything, a type nixin does not know
    ({"name": "package", "description": "package"}, '"hello"', None),
    ({"name": "path", "description": "path"}, "1", None),
    ({"name": "submodule", "description": "submodule"}, "true", None),
    ({"name": "functionTo", "description": "function that evaluates to a(n) str"}, "1", None),
    ({"name": "anything", "description": "anything"}, "1", None),
    ({"name": "hm.types.dagOf", "description": "DAG of string"}, "1", None),
    (None, "1", None),
]


@pytest.mark.parametrize(("t", "value", "why"), TYPE_CASES, ids=lambda x: str(x)[:40])
def test_type_check_per_type(t, value, why):
    got = typecheck.check(t, typecheck.literal(value))
    if why is None:
        assert got is None, got
    else:
        assert got is not None and why in got, got


def _options_nix(monkeypatch, **kw) -> FakeNix:
    nix = FakeNix([], {"cowsay": "cowsay-3.8.4"}, options=OPTIONS, **kw)
    monkeypatch.setattr(writer, "NIX", nix)
    return nix


def test_set_type_mismatch_writes_nothing_and_says_what_fits(cfg, monkeypatch, cache_dir):
    _options_nix(monkeypatch)
    before = snapshot(cfg)
    rc, d = run(cfg, "set", "services.openssh.enable", "yes", nix=True)
    assert rc == 1 and d["error"]["code"] == "type-mismatch", d
    err = d["error"]
    assert err["expected"] == "boolean" and err["type_info"] == BOOL
    assert err["default"] == "false" and err["example"] is None
    assert err["option"] == "services.openssh.enable" and err["value"] == "yes"
    assert "yes is a bare word" in err["message"] and "--force" in err["message"]
    assert "default false" in err["message"]
    rc, d = run(cfg, "set", "networking.firewall.allowedTCPPorts", '[ 22 "80" ]', nix=True)
    assert d["error"]["code"] == "type-mismatch" and d["error"]["example"] == "[ 22 80 ]"
    assert 'element 2: "80" is a string' in d["error"]["message"]
    rc, d = run(cfg, "set", "services.openssh.settings.PermitRootLogin", '"maybe"', nix=True)
    assert d["error"]["code"] == "type-mismatch", d
    assert d["error"]["default"] == '"prohibit-password"'
    assert '"maybe" is not one of "yes", "no"' in d["error"]["message"]
    rc, d = run(cfg, "set", "networking.hostName", "box", nix=True)
    assert "quote it for Nix, '\"box\"'" in d["error"]["message"]
    assert snapshot(cfg) == before


def test_set_value_that_fits_is_written_with_checks_type(cfg, monkeypatch, cache_dir):
    _options_nix(monkeypatch)
    rc, d = run(cfg, "set", "services.openssh.settings.PermitRootLogin", '"yes"', nix=True)
    assert rc == 0 and d["ok"], d
    assert d["checks"]["type"] == {
        "ok": True,
        "option": "services.openssh.settings",
        "expected": 'null or one of "yes", "no"',
        "note": "fits",
    }
    assert 'settings.PermitRootLogin = "yes";' in (cfg / "hosts/mbp13/default.nix").read_text()
    rc, d = run(cfg, "set", "networking.firewall.allowedTCPPorts", "config.x.ports", nix=True)
    assert rc == 0 and d["checks"]["type"]["note"] == "an expression the eval judges"
    rc, d = run(cfg, "set", "services.openssh.settings.Ciphers", '[ "x" ]', nix=True)
    assert rc == 0 and d["checks"]["type"]["ok"] is None  # the freeform part: type unknown


def test_set_force_and_unknown_options(cfg, monkeypatch, cache_dir):
    nix = _options_nix(monkeypatch)
    rc, d = run(cfg, "set", "services.nosuch.enable", "true", nix=True)
    assert rc == 1 and d["error"]["code"] == "not-found", d
    assert "not an option of mbp13" in d["error"]["message"] and "--force" in d["error"]["message"]
    rc, d = run(cfg, "set", "services.nosuch.enable", "true", "--force", nix=True)
    assert rc == 0 and d["checks"]["type"] == {"ok": None, "note": "not checked (--force)"}
    calls = len(nix.calls)
    rc, d = run(cfg, "set", "services.openssh.enable", "yes", "--force", nix=True)
    assert rc == 0 and "enable = yes;" in (cfg / "hosts/mbp13/default.nix").read_text()
    assert not any("nixin-option" in " ".join(c) for c in nix.calls[calls:])  # no type query
    rc, d = run(cfg, "set", "services.openssh", "true", nix=True)  # a group of options
    assert d["error"]["code"] == "type-mismatch"
    assert d["error"]["expected"] == "an attribute set (of the 2 options under it)"
    calls = len(nix.calls)
    rc, d = run(cfg, "set", "services.openssh.enable", "maybe", "--no-check", nix=True)
    assert rc == 0 and d["checks"]["type"] is None and len(nix.calls) == calls


def test_set_without_a_value_describes_the_option(cfg, monkeypatch, cache_dir, capsys):
    _options_nix(monkeypatch)
    before = snapshot(cfg)
    rc, d = run(cfg, "set", "services.openssh.enable", nix=True)
    assert rc == 0 and d["ok"], d
    assert d["option"] == "services.openssh.enable" and d["mode"] == "exact"
    assert (d["type"], d["default"], d["example"], d["value"]) == ("boolean", "false", None, True)
    assert d["type_info"] == BOOL and d["scope"] == "nixos" and d["user"] is None
    assert d["definitions"][0]["file"] == "hosts/mbp13/default.nix"
    assert d["declarations"][0]["source"] == "nixpkgs"
    assert d["target"] == {"file": "hosts/mbp13/default.nix", "line": 22, "in_place": True}
    rc, d = run(cfg, "set", "networking.hostName", nix=True)
    assert d["target"] == {"file": "installer/mbp13.nix", "line": None, "in_place": False}
    assert d["definitions"] == [] and d["value"] == "nixos"
    rc, d = run(cfg, "set", "services.openssh.settings.PermitRootLogin", nix=True)
    assert d["mode"] == "sub" and d["type"] == 'null or one of "yes", "no"'
    assert d["real_option"] == "services.openssh.settings" and d["value"] == "no"
    rc, d = run(cfg, "set", "services.nosuch", nix=True)
    assert d["error"]["code"] == "not-found" and "--force" not in d["error"]["message"]
    assert snapshot(cfg) == before

    args = ["--host", "mbp13", "set", "services.openssh.enable", "--config", str(cfg)]
    assert main(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "services.openssh.enable = true  [nixos mbp13]"
    assert "  type      boolean" in out and "  default   false" in out
    assert "  set in    hosts/mbp13/default.nix:22" in out
    assert out[-1] == "  writes    hosts/mbp13/default.nix:22 (in place)"


def test_eval_failure_after_a_write_says_how_to_undo(cfg, monkeypatch, cache_dir):
    _options_nix(monkeypatch, fail_check="error: boom")
    rc, d = run(cfg, "set", "networking.hostName", '"box"', nix=True)
    assert rc == 1 and d["error"]["code"] == "eval-failed", d
    assert d["error"]["undo"] == [f"nixin --host mbp13 unset networking.hostName --config {cfg}"]
    assert 'networking.hostName = "box";' in (cfg / "installer/mbp13.nix").read_text()
    assert {c["file"] for c in d["changes"]} == {
        "installer/mbp13.nix",
        "installer/state.json",
        "hosts/mbp13/default.nix",
    }
    msg = d["error"]["message"]
    assert msg.startswith('set networking.hostName = "box" for mbp13 in installer/mbp13.nix — ')
    assert "the change is kept, but the eval after the write failed: error: boom" in msg
    rc, d = run(cfg, "set", "networking.hostName", '"other"', nix=True)
    assert d["error"]["undo"] == [
        f"nixin --host mbp13 set networking.hostName '\"box\"' --config {cfg}"
    ]
    rc, d = run(cfg, "unset", "networking.hostName", nix=True)
    assert d["error"]["undo"] == [
        f"nixin --host mbp13 set networking.hostName '\"other\"' --config {cfg}"
    ]
    rc, d = run(cfg, "set", "services.openssh.enable", "false", nix=True)
    where = "--where hosts/mbp13/default.nix:22"
    assert d["error"]["undo"] == [
        f"nixin --host mbp13 set services.openssh.enable true {where} --config {cfg}"
    ]
    rc, d = run(cfg, "unset", "services.openssh.enable", nix=True)
    assert d["error"]["undo"] == [f"git -C {cfg} checkout -- hosts/mbp13/default.nix"]
    rc, d = run(cfg, "rm", "--home", "alice", "fd", host="19BK", nix=True)
    assert d["error"]["undo"] == [f"git -C {cfg} checkout -- home/modules/packages.nix"]
    rc, d = run(cfg, "add", "--home", "alice", "cowsay", host="19BK", nix=True)
    assert d["error"]["undo"] == [f"nixin --host 19BK rm cowsay --home alice --config {cfg}"]
    rc, d = run(cfg, "rm", "--home", "alice", "cowsay", host="19BK", nix=True)
    assert d["error"]["undo"] == [f"nixin --host 19BK add cowsay --home alice --config {cfg}"]


def test_a_value_that_does_not_evaluate_says_why(cfg, monkeypatch, cache_dir):
    why = "error: A definition for option `services.openssh.enable' is not of type `boolean'."
    _options_nix(monkeypatch, bad_value=why)
    rc, d = run(cfg, "set", "services.openssh.enable", '"yes"', "--force", nix=True)
    assert rc == 1 and d["error"]["code"] == "eval-failed"
    assert d["checks"]["eval"]["message"] == f"services.openssh.enable does not evaluate:\n{why}"
    assert why in d["error"]["message"] and d["error"]["undo"]


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
