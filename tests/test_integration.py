"""The merged CLI as one tool, offline: the install flow a UI drives, through main().

discovery answers from a fake HTTP transport (conftest) and a fake `nix` for --nixpkgs;
truth and the writer share one fake nix, `World`, that "evaluates" the writer fixture
config by reading its installer/state.json, so what `add` writes is what the next `where`
sees. No nix, no network, no git (the fixture copy has no .git).

Every document goes through `envelope()`: the invariants docs/CONTRACT.md promises for
every verb under --json.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import fixture

from nixin import writer
from nixin.apply import proc as apply_proc
from nixin.cli import VERBS, main
from nixin.configrepo import ConfigRepo
from nixin.discovery import local
from nixin.writer.nixtext import NixFile, NixSyntaxError

ROOT = Path(__file__).parent.parent
CONFIG = Path(__file__).parent / "fixtures" / "writer-config"
REF = "github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
NIXPKGS_STORE = "/nix/store/6nwzgfvznah5rg192x0hnl5ykchdgi59-source"
HM_STORE = "/nix/store/9k8yg0sk8ka6krz4qp58n7b6gc9malqk-source"
X11 = "services.openssh.settings.X11Forwarding"

# docs/CONTRACT.md, "Error codes" (test_contract_lists_every_code keeps the two in step)
ERROR_CODES = {
    "usage",
    "internal",
    "not-implemented",
    "network",
    "bad-channel",
    "not-found",
    "nix-missing",
    "nix-failed",
    "nix-timeout",
    "config-not-found",
    "not-a-flake",
    "unknown-host",
    "eval-failed",
    "eval-timeout",
    "not-a-workspace",
    "unknown-user",
    "unknown-package",
    "invalid-nix",
    "type-mismatch",
    "ambiguous",
    "import-site-missing",
    "file-conflict",
    "invalid-state",
    "needs-sudo",
    "build-failed",
    "nothing-to-apply",
    "host-not-local",
    "authoritative-missing",
    "authoritative-dirty",
    "authoritative-diverged",
    "switch-failed",
    "verify-failed",
    "git-failed",
    "diverged",
    "busy",
}
HTTP_CODE = re.compile(r"http-[1-5][0-9]{2}")

# what each host "has" before nixin touches it (the fixture's hand-written lists)
BASE = {
    ("nixos", "19BK"): ["git", "ripgrep", "jq"],
    ("nixos", "mbp13"): ["git", "kitty", "btop", "firefox"],
    ("nixos", "bare"): [],
    ("home", "alice"): ["ripgrep", "fd"],
}
BASE_FILE = {
    ("nixos", "19BK"): "configuration.nix",
    ("nixos", "mbp13"): "hosts/mbp13/default.nix",
    ("home", "alice"): "home/modules/packages.nix",
}
HM_USERS = {"19BK": ["alice"], "mbp13": [], "bare": []}
PKGS = {"cowsay", "hello", "git", "ripgrep", "jq", "kitty", "btop", "firefox", "fd", "sl"}


# --- the envelope -----------------------------------------------------------------------


def envelope(out: str, err: str, rc: int, verb: str | None) -> dict:
    """One JSON object on stdout and nothing else, nothing on stderr, the shared keys, an
    exit code that matches `ok` / the error code, a documented error code."""
    assert err == "", f"stderr under --json: {err!r}"
    doc, end = json.JSONDecoder().raw_decode(out)
    assert out[end:].strip() == "" and out.endswith("\n"), "more than one document on stdout"
    assert isinstance(doc, dict) and {"ok", "verb", "host"} <= set(doc)
    assert doc["verb"] == verb and isinstance(doc["host"], str) and doc["host"]
    assert "message" not in doc, "human text leaked into the --json document"
    if doc["ok"] is True:
        assert rc == 0 and "error" not in doc
    else:
        assert doc["ok"] is False
        code = doc["error"]["code"]
        assert code in ERROR_CODES or HTTP_CODE.fullmatch(code), code
        assert isinstance(doc["error"]["message"], str) and doc["error"]["message"]
        assert rc == (2 if code == "usage" else 1)
    return doc


def first_verb(argv: list[str]) -> str | None:
    return next((a for a in argv if a in VERBS), None)


# --- fakes ------------------------------------------------------------------------------


class FakeTools:
    """parse: the writer's own scanner; fmt: identity (as in test_writer)."""

    def parse(self, text: str) -> str | None:
        try:
            NixFile(text).module_shapes()
        except NixSyntaxError as e:
            return str(e)
        return None

    def fmt(self, text: str) -> tuple[str, str | None]:
        return text, None


def _strings(s: str) -> list[str]:
    return re.findall(r'"([^"]*)"', s)


def _attr_lists(tail: str) -> list[list[str]]:
    """The `[ [ "a" "b" ] … ]` argument the writer's checks end with."""
    tail = tail[tail.rindex("[ [") :]
    return [_strings(g) for g in re.findall(r"\[((?: \"[^\"]*\")+) \]", tail)]


def _nix_value(raw: str):
    return {"true": True, "false": False}.get(raw.strip(), raw.strip())


class World:
    """The nix calls truth and the writer make, answered from the fixture tree on disk."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[list[str]] = []

    # the tree as nix would see it
    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(self.root)).encode() + b"\0" + p.read_bytes())
        return h.hexdigest()

    def store(self) -> str:
        return f"/nix/store/{self.fingerprint()[:32]}-source"

    def state(self) -> dict:
        p = self.root / "installer" / "state.json"
        return json.loads(p.read_text()) if p.exists() else {"nixos": {}, "home": {}}

    def added(self, kind: str, name: str) -> dict:
        return self.state().get(kind, {}).get(name, {"packages": [], "options": {}})

    def gen_file(self, kind: str, name: str) -> str:
        return f"installer/{name}.nix" if kind == "nixos" else f"installer/{name}-home.nix"

    def installed(self, kind: str, name: str) -> list[str]:
        return BASE.get((kind, name), []) + self.added(kind, name)["packages"]

    # answers
    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        joined = " ".join(args)
        if args[:2] == ["flake", "metadata"]:
            return json.dumps(
                {"path": self.store(), "fingerprint": self.fingerprint(), "lastModified": 0}
            )
        if args[:2] == ["flake", "archive"]:
            return json.dumps(
                {
                    "path": self.store(),
                    "inputs": {
                        "nixpkgs": {"path": NIXPKGS_STORE, "inputs": {}},
                        "home-manager": {"path": HM_STORE, "inputs": {}},
                    },
                }
            )
        if joined.endswith("#nixosConfigurations --apply builtins.attrNames"):
            return json.dumps(sorted(HM_USERS))  # the hosts
        m = re.search(r'#nixosConfigurations\.(?:"([^"]+)"|([A-Za-z0-9_-]+))', joined)
        assert m, f"unexpected nix call: {args}"
        host = m.group(1) or m.group(2)
        if "system.build.toplevel" in joined:
            return f"/nix/store/0000-nixos-system-{host}.drv"
        apply = args[args.index("--apply") + 1]
        if "programsFromOptions" in apply:  # truth/packages.nix
            return json.dumps(self.packages(host))
        if "nixin-option" in apply:  # truth/options.nix; its ARGS attrset is the tail
            spec = apply[apply.rindex("{ prefix = ") :]
            prefix = _strings(re.search(r"prefix = \[([^\]]*)\]", spec).group(1))
            hm = re.search(r'hm = (?:null|"([^"]*)")', spec).group(1)
            return json.dumps(self.options(host, hm, prefix))
        if "hasAttrByPath" in apply:  # writer: do the attributes exist in pkgs?
            return json.dumps([".".join(a) in PKGS for a in _attr_lists(apply)])
        if "installed = map" in apply:  # writer: the package-list check after a write
            m = re.search(r"home-manager\.users\.(\w+)\.home\.packages", apply)
            kind, name = ("home", m.group(1)) if m else ("nixos", host)
            return json.dumps(
                {
                    "installed": [f"{p}-1.0" for p in self.installed(kind, name)],
                    "wanted": [f"{'.'.join(a)}-1.0" for a in _attr_lists(apply)],
                }
            )
        if "__nixinUnset" in apply:  # writer: the option-value check after a write
            m = re.search(r"home-manager\.users\.(\w+)", apply)
            kind, name = ("home", m.group(1)) if m else ("nixos", host)
            path = ".".join(_strings(apply[apply.index("attrByPath") :].split("]", 1)[0]))
            return json.dumps(self.value(kind, name, path))
        raise AssertionError(f"unexpected nix call: {args}")

    def packages(self, host: str) -> dict:
        def pkgs(kind: str, name: str) -> list[dict]:
            return [
                {"name": f"{p}-1.0", "pname": p, "version": "1.0"}
                for p in self.installed(kind, name)
            ]

        return {
            "system": {"packages": pkgs("nixos", host), "programs": []},
            "home": {
                u: {"packages": pkgs("home", u), "programs": ["kitty"]} for u in HM_USERS[host]
            },
        }

    def value(self, kind: str, name: str, path: str):
        raw = self.added(kind, name)["options"].get(path)
        return False if raw is None else _nix_value(raw)

    def options(self, host: str, hm: str | None, prefix: list[str]) -> list[dict]:
        kind, name = ("home", hm) if hm else ("nixos", host)
        dotted = ".".join(prefix)
        store = self.store()
        list_opt = "home.packages" if hm else "environment.systemPackages"
        if dotted == list_opt:
            base = [f"<derivation {p}-1.0>" for p in BASE.get((kind, name), [])]
            gen = [f"<derivation {p}-1.0>" for p in self.added(kind, name)["packages"]]
            defs = self._defs(kind, name, base, gen)
            return [
                {
                    "name": list_opt,
                    "type": "list of package",
                    "isDefined": True,
                    "declarations": [f"{NIXPKGS_STORE}/nixos/modules/config/system-path.nix"],
                    "definitions": [d["file"] for d in defs],
                    "definitionValues": defs,
                    "value": base + gen,
                }
            ]
        if not hm and dotted.startswith("services.openssh.settings"):
            mine = self.added(kind, name)["options"]
            settings = {"PermitRootLogin": "no", "X11Forwarding": self.value(kind, name, X11)}
            decl = f"{NIXPKGS_STORE}/nixos/modules/services/networking/ssh/sshd.nix"
            defs = [{"file": decl, "value": {"X11Forwarding": False}}]
            if host == "mbp13":
                defs.append({"file": f"{store}/hosts/mbp13/default.nix", "value": {}})
            if X11 in mine:
                defs.append(
                    {
                        "file": f"{store}/{self.gen_file(kind, name)}",
                        "value": {"X11Forwarding": _nix_value(mine[X11])},
                    }
                )
            sub = {}
            if dotted == X11:  # options.nix follows the path into the submodule
                sub = {
                    "sub": {
                        "option": X11,
                        "description": "Whether to allow X11 connections to be forwarded.",
                        "typeInfo": {
                            "name": "nullOr",
                            "description": "null or boolean",
                            "nested": {"elemType": {"name": "bool", "description": "boolean"}},
                        },
                        "default": {"value": False},
                    }
                }
            return [
                {
                    **sub,
                    "name": "services.openssh.settings",
                    "type": "open submodule of attribute set of (atom)",
                    "description": "Configuration for `sshd_config(5)`.",
                    "isDefined": True,
                    "highestPrio": 100,
                    "declarations": [decl],
                    "declarationPositions": [{"file": decl, "line": 475, "column": 7}],
                    "definitions": [d["file"] for d in defs],
                    "definitionValues": defs,
                    "value": settings,
                }
            ]
        if hm and dotted == "programs.bat.enable":
            decl = f"{HM_STORE}/modules/programs/bat.nix"
            defs = [{"file": decl, "value": False}]
            if dotted in self.added(kind, name)["options"]:
                defs.append(
                    {
                        "file": f"{store}/{self.gen_file(kind, name)}",
                        "value": self.value(kind, name, dotted),
                    }
                )
            return [
                {
                    "name": dotted,
                    "type": "boolean",
                    "description": "Whether to enable bat.",
                    "isDefined": True,
                    "highestPrio": 100 if len(defs) > 1 else 1500,
                    "declarations": [decl],
                    "definitions": [d["file"] for d in defs],
                    "definitionValues": defs,
                    "value": self.value(kind, name, dotted),
                }
            ]
        return []

    def _defs(self, kind: str, name: str, base: list, gen: list) -> list[dict]:
        store, out = self.store(), []
        if base and (kind, name) in BASE_FILE:
            out.append({"file": f"{store}/{BASE_FILE[kind, name]}", "value": base})
        if gen:
            out.append({"file": f"{store}/{self.gen_file(kind, name)}", "value": gen})
        return out


class Done:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def fake_subprocess(argv: list[str], **kw) -> Done:
    """discovery's local.py is the only thing that may start a process here."""
    assert argv[0] == "nix" and argv[1:3] == local.NIX_FLAGS, f"unexpected process {argv}"
    assert kw.get("stdin") is subprocess.DEVNULL
    cmd = argv[3:]
    if cmd[:3] == ["flake", "metadata", "--json"]:
        return Done(json.dumps(fixture("local/flake_metadata.json")))
    if cmd[:2] == ["eval", "--json"] and cmd[2].endswith(".cowsay"):
        return Done(
            json.dumps(
                {
                    "broken": False,
                    "description": "Program which generates ASCII pictures of a cow",
                    "homepage": "https://cowsay.diamonds",
                    "license": ["GPL-3.0-only"],
                    "mainProgram": "cowsay",
                    "outputs": ["out", "man"],
                    "platforms": ["x86_64-linux", "aarch64-linux"],
                    "pname": "cowsay",
                    "position": f"{NIXPKGS_STORE}/pkgs/by-name/co/cowsay/package.nix:58",
                    "unfree": False,
                    "version": "3.8.4",
                }
            )
        )
    raise AssertionError(f"unexpected nix call {cmd}")


ES_INDEX = "nixos-52-unstable-1c89083e451e891152365b799595cf7790a43325"


def es_hits(*sources: dict) -> dict:
    return {
        "hits": {
            "total": {"relation": "eq", "value": len(sources)},
            "hits": [{"_index": ES_INDEX, "_source": s} for s in sources],
        }
    }


COWSAY_ES = {
    "package_attr_name": "cowsay",
    "package_description": "Program which generates ASCII pictures of a cow",
    "package_homepage": ["https://cowsay.diamonds"],
    "package_license_set": ["GPL-3.0-only"],
    "package_mainProgram": "cowsay",
    "package_outputs": ["man", "out"],
    "package_platforms": ["x86_64-linux"],
    "package_pname": "cowsay",
    "package_position": "pkgs/by-name/co/cowsay/package.nix:58",
    "package_programs": ["cowsay", "cowthink"],
    "package_pversion": "3.8.4",
}
X11_ES = {
    "option_default": "false",
    "option_description": "<rendered-html><p>Whether to allow X11 connections.</p></rendered-html>",
    "option_example": None,
    "option_name": X11,
    "option_source": "nixos/modules/services/networking/ssh/sshd.nix",
    "option_type": "boolean",
}


@pytest.fixture
def world(tmp_path, monkeypatch, transport, cache_dir):
    root = tmp_path / "config"
    shutil.copytree(CONFIG, root)
    w = World(root)
    original_locate = ConfigRepo.locate.__func__
    monkeypatch.setattr(
        ConfigRepo,
        "locate",
        classmethod(lambda cls, explicit=None, nix=None: original_locate(cls, explicit, nix=w)),
    )
    monkeypatch.setattr(writer, "NIX", w)
    monkeypatch.setattr(writer, "TOOLS", FakeTools())
    monkeypatch.setattr(subprocess, "run", fake_subprocess)
    monkeypatch.setattr(local, "current_system", lambda: "x86_64-linux")
    monkeypatch.setattr(
        "nixin.truth.running_system",
        lambda: {"system": None, "version": "26.11.20261023.3ec6363", "nixpkgs_rev": "3ec6363"},
    )
    monkeypatch.setattr("nixin.truth.local_hostname", lambda: "mbp13")
    monkeypatch.delenv("NIXIN_CONFIG", raising=False)
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(tmp_path / "no-authoritative"))  # status
    # apply's git/nix/sudo seam: the fixture copy is no git checkout
    monkeypatch.setattr(
        apply_proc,
        "RUN",
        lambda argv, **kw: apply_proc.Proc(argv, 128, "", "fatal: not a git repository"),
    )

    has = lambda body, s: s.lower() in json.dumps(body).lower()
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    transport.add("POST", r"/_search$", es_hits(COWSAY_ES), when=lambda b: has(b, "cowsay"))
    transport.add("POST", r"/_search$", es_hits(X11_ES), when=lambda b: has(b, "X11Forwarding"))
    transport.add("POST", r"/_search$", fixture_file="es/empty.json")
    transport.add("GET", r"/data/options-master\.json$", fixture_file="hm/options-master.json")
    return w


def nixin(capsys, world: World, *argv: str, host: str = "mbp13") -> dict:
    """`nixin --json --host H ARGV --config <fixture copy>` through main(), envelope checked."""
    full = ["--json", "--host", host, *argv]
    if first_verb(full) in (
        "status",
        "where",
        "list",
        "add",
        "rm",
        "set",
        "unset",
        "plan",
        "apply",
        "sync",
        "update",
    ):
        full += ["--config", str(world.root)]
    rc = main(full)
    out, err = capsys.readouterr()
    return envelope(out, err, rc, first_verb(full))


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# --- the flows --------------------------------------------------------------------------


def test_install_flow(world, capsys):
    before = snapshot(world.root)
    doc = nixin(capsys, world, "search", "cowsay")
    assert doc["ok"] and doc["results"][0]["name"] == "cowsay" and doc["kind"] == "pkg"

    doc = nixin(capsys, world, "info", "cowsay", "--nixpkgs", REF)
    (rec,) = doc["results"]
    assert rec["source"] == "local-nix" and rec["rev"] == REF.rsplit("/", 1)[1]
    assert doc["sources"]["search.nixos.org"]["error"]["code"] == "bad-channel"  # bare rev

    doc = nixin(capsys, world, "status")
    assert doc["ok"] and doc["eval"]["ok"] and doc["packages"]["system"] == 4
    assert doc["config"]["host_is_local"] is True and doc["hm_users"] == []

    doc = nixin(capsys, world, "where", "cowsay")
    assert doc["ok"] and doc["installed"] == [] and doc["declared"] == []

    doc = nixin(capsys, world, "add", "cowsay", "--dry-run")
    assert doc["dry_run"] and {c["file"]: c["kind"] for c in doc["changes"]} == {
        "hosts/mbp13/default.nix": "modified",
        "installer/mbp13.nix": "created",
        "installer/state.json": "created",
    }
    assert snapshot(world.root) == before and doc["checks"]["eval"] is None

    doc = nixin(capsys, world, "add", "cowsay")
    assert doc["actions"] == [{"name": "cowsay", "action": "added", "file": "installer/mbp13.nix"}]
    assert doc["checks"]["eval"]["ok"] and doc["checks"]["eval"]["present"] == {
        "cowsay": "cowsay-1.0"
    }

    doc = nixin(capsys, world, "where", "cowsay")
    (inst,) = doc["installed"]
    assert inst["pname"] == "cowsay" and inst["scope"] == "system"
    (decl,) = doc["declared"]
    assert decl["file"] == "installer/mbp13.nix" and decl["source"] == "config"
    assert decl["lines"][0]["text"] == "pkgs.cowsay"

    doc = nixin(capsys, world, "add", "cowsay")
    assert doc["actions"][0]["action"] == "already-added" and doc["changes"] == []

    doc = nixin(capsys, world, "rm", "cowsay")
    assert doc["checks"]["eval"]["absent"] == ["cowsay"]
    assert snapshot(world.root) == before  # add then rm leaves the tree as it was


def test_list_flow(world, capsys):
    """`list` on the fixture tree: what each host has, and the lines that list it (the
    writer's scanner), a nixin-generated module included."""
    doc = nixin(capsys, world, "list")
    assert [(p["scope"], p["pname"]) for p in doc["packages"]] == [
        ("system", "btop"),
        ("system", "firefox"),
        ("system", "git"),
        ("system", "kitty"),
    ]
    assert doc["count"] == 4 and doc["users"] == [] and doc["warnings"] == []
    by = {
        p["pname"]: [(d["file"], d["line"], d["text"]) for d in p["declared"]]
        for p in doc["packages"]
    }
    assert by["kitty"] == [("hosts/mbp13/default.nix", 16, "kitty")]
    assert by["btop"] == [  # `rm btop` is ambiguous for the same reason
        ("hosts/mbp13/default.nix", 17, "btop"),
        ("hosts/mbp13/desktop.nix", 4, "pkgs.btop"),
    ]

    nixin(capsys, world, "add", "cowsay")
    doc = nixin(capsys, world, "list", "--filter", "COW")
    ((row,),) = [doc["packages"]]
    assert (row["name"], row["scope"]) == ("cowsay-1.0", "system")
    assert [(d["file"], d["text"]) for d in row["declared"]] == [
        ("installer/mbp13.nix", "pkgs.cowsay")
    ]
    nixin(capsys, world, "rm", "cowsay")

    doc = nixin(capsys, world, "list", host="19BK")
    assert [(p["scope"], p["user"], p["pname"]) for p in doc["packages"]] == [
        ("home", "alice", "fd"),
        ("system", None, "git"),
        ("system", None, "jq"),
        ("system", None, "ripgrep"),
        ("home", "alice", "ripgrep"),
    ]
    doc = nixin(capsys, world, "list", "--home", "alice", "--programs", host="19BK")
    assert [p["declared"][0]["file"] for p in doc["packages"]] == ["home/modules/packages.nix"] * 2
    assert doc["programs"] == [
        {"scope": "home", "user": "alice", "name": "kitty", "option": "programs.kitty.enable"}
    ]
    assert nixin(capsys, world, "list", "--home", "alice")["error"]["code"] == "unknown-user"


def test_option_flow(world, capsys):
    before = snapshot(world.root)
    doc = nixin(capsys, world, "search", "--kind", "opt", X11)
    assert doc["results"][0]["name"] == X11 and doc["results"][0]["kind"] == "opt"

    doc = nixin(capsys, world, "where", X11)
    (scope,) = doc["scopes"]
    assert scope["mode"] == "sub" and scope["options"][0]["value"] is False

    doc = nixin(capsys, world, "set", X11)  # no value: what it is, nothing written
    assert (doc["type"], doc["default"], doc["value"]) == ("null or boolean", "false", False)
    assert doc["target"] == {"file": "installer/mbp13.nix", "line": None, "in_place": False}
    assert snapshot(world.root) == before

    doc = nixin(capsys, world, "set", X11, "yes")
    assert doc["error"]["code"] == "type-mismatch" and doc["error"]["expected"] == "null or boolean"
    assert snapshot(world.root) == before

    doc = nixin(capsys, world, "set", X11, "true")
    assert doc["option"] == X11 and doc["value"] == "true"  # the Nix text as given
    assert doc["checks"]["type"] == {
        "ok": True,
        "option": "services.openssh.settings",
        "expected": "null or boolean",
        "note": "fits",
    }
    assert doc["checks"]["eval"] == {
        "ok": True,
        "attr": f"nixosConfigurations.mbp13.config.{X11}",
        "value": True,
        "note": f"{X11} = true",
    }

    doc = nixin(capsys, world, "where", X11)
    opt = doc["scopes"][0]["options"][0]
    assert opt["value"] is True
    gen = [d for d in opt["definitions"] if d["file"] == "installer/mbp13.nix"]
    assert gen and gen[0]["line_heuristic"] and gen[0]["value"] is True

    doc = nixin(capsys, world, "set", X11, "true")
    assert doc["actions"][0]["action"] == "unchanged" and doc["changes"] == []

    doc = nixin(capsys, world, "unset", X11)
    assert doc["checks"]["eval"]["value"] is False
    assert snapshot(world.root) == before

    doc = nixin(capsys, world, "unset", X11)
    assert doc["error"]["code"] == "not-found" and doc["error"]["hints"] == []


def test_home_manager_flow(world, capsys):
    before = snapshot(world.root)
    doc = nixin(capsys, world, "search", "--kind", "hm", "programs.kitty")
    assert doc["source"] == "home-manager-options" and doc["count"] >= 1

    doc = nixin(capsys, world, "add", "--home", "alice", "cowsay")  # alice lives on 19BK
    assert doc["error"]["code"] == "unknown-user" and "--host" in doc["error"]["message"]

    doc = nixin(capsys, world, "add", "--home", "alice", "cowsay", host="19BK")
    assert doc["scope"] == "home" and doc["user"] == "alice"
    assert doc["imports_added"] == [
        {"file": "home/home.nix", "import": "../installer/alice-home.nix"}
    ]

    doc = nixin(capsys, world, "where", "cowsay", host="19BK")
    (inst,) = doc["installed"]
    assert (inst["scope"], inst["user"]) == ("home", "alice")
    assert doc["declared"][0]["file"] == "installer/alice-home.nix"

    doc = nixin(capsys, world, "rm", "--home", "alice", "cowsay", host="19BK")
    assert doc["imports_removed"][0]["restored"] is True  # the one-line imports list is back
    assert snapshot(world.root) == before

    doc = nixin(capsys, world, "set", "--home", "alice", "programs.bat.enable", "true", host="19BK")
    assert doc["checks"]["eval"]["value"] is True
    doc = nixin(capsys, world, "where", "programs.bat.enable", host="19BK")
    (scope,) = doc["scopes"]
    assert (scope["scope"], scope["user"], scope["mode"]) == ("home", "alice", "exact")
    assert scope["options"][0]["value"] is True
    doc = nixin(capsys, world, "unset", "--home", "alice", "programs.bat.enable", host="19BK")
    assert snapshot(world.root) == before


def test_flow_without_json_is_human_text(world, capsys):
    """The same verbs without --json: text on stdout, nothing on stderr; a failure is one
    `nixin VERB: …` line on stderr and nothing on stdout."""
    cfg = ["--config", str(world.root)]
    for argv in (
        ["search", "cowsay"],
        ["info", "cowsay", "--nixpkgs", REF],
        ["status", *cfg],
        ["where", "cowsay", *cfg],
        ["add", "cowsay", *cfg],
        ["where", "cowsay", *cfg],
        ["rm", "cowsay", *cfg],
    ):
        assert main(["--host", "mbp13", *argv]) == 0, argv
        out, err = capsys.readouterr()
        assert out.strip() and err == "", argv
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
    assert main(["--host", "mbp13", "rm", "cowsay", *cfg]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("nixin rm: cowsay is not declared")


# --- every verb, every error path: the envelope holds ------------------------------------

SWEEP = [
    # discovery
    ["search", "cowsay"],
    ["search", "--kind", "all", "cowsay"],
    ["search", "cowsay", "--channel", "99.99"],  # bad-channel
    ["search", "cowsay", "--nixpkgs", REF, "--kind", "opt"],  # bad-channel (bare rev)
    ["search", ""],  # usage (the verb's own check)
    ["search"],  # usage (argparse)
    ["search", "x", "--kind", "package"],  # usage (choices)
    ["info", "--kind", "pkg", "no-such-thing"],  # not-found
    ["info", "--kind", "opt", X11],
    ["versions", "cowsay"],  # network: nixhub has no rule
    ["search", "--kind", "fn", "concatStringsSep"],  # network: noogle has no rule
    # truth
    ["status"],
    ["status", "--full"],
    ["status", "--host", "nope"],  # unknown-host, --host after the verb
    ["where", "cowsay"],
    ["where", X11],
    ["where", "services.nosuch.option"],  # not-found
    ["where"],  # usage
    ["where", "kitty", "git"],  # usage: one NAME
    ["status", "extra"],  # usage
    ["where", "x", "--kind", "pkg"],  # usage (truth says package|option)
    ["list"],
    ["list", "--programs", "--filter", "git"],
    ["list", "--home", "bob"],  # unknown-user
    ["list", "extra"],  # usage
    ["list", "--scope", "nope"],  # usage (choices)
    # writer
    ["add", "cowsay", "--dry-run"],
    ["add", "no-such-pkg"],  # unknown-package
    ["add"],  # usage
    ["rm", "btop"],  # ambiguous
    ["rm", "btop", "--where", "hosts/mbp13/desktop.nix:4", "--dry-run"],
    ["rm", "no-such-pkg"],  # not-found
    ["rm", "x", "--where", "nonsense"],  # usage
    ["set", X11, "{ "],  # invalid-nix
    ["set", X11, '"yes"'],  # type-mismatch
    ["set", "services.nosuch.enable", "true"],  # not-found (no such option)
    ["set", X11],  # what it is (no value)
    ["set"],  # usage
    ["unset", X11],  # not-found
    ["add", "--home", "bob", "cowsay"],  # unknown-user
    # apply (the fixture copy is no git checkout: not-a-workspace)
    ["plan"],
    ["apply"],
    ["apply", "--dry-run"],
    ["sync", "--push"],
    ["plan", "extra"],  # usage
    # update (the fixture copy has no flake.lock: not-found)
    ["update", "--dry-run"],
    ["update"],
    ["update", "extra"],  # usage
    # no verb / unknown verb
    [],
    ["frobnicate"],
]


@pytest.mark.parametrize("argv", SWEEP, ids=lambda a: " ".join(a) or "<no verb>")
def test_envelope_holds_everywhere(world, capsys, argv):
    nixin(capsys, world, *argv)


@pytest.mark.parametrize(
    "argv, code",
    [
        (["status", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["where", "x", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["list", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["add", "x", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["add", "x", "--config", "/etc/nixos"], "not-a-workspace"),
        (["plan", "--config", "/etc/nixos"], "not-a-workspace"),
        (["apply", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["update", "--config", "/etc/nixos"], "not-a-workspace"),
        (["update", "--config", "/nonexistent/nixin"], "config-not-found"),
        (["frobnicate"], "usage"),
    ],
)
def test_error_codes(world, capsys, argv, code):
    rc = main(["--json", *argv])
    out, err = capsys.readouterr()
    doc = envelope(out, err, rc, first_verb(argv))
    assert doc["error"]["code"] == code


def test_writer_refuses_the_authoritative_config_by_default(world, capsys):
    rc = main(["--json", "add", "cowsay"])  # no --config, no $NIXIN_CONFIG: /etc/nixos
    out, err = capsys.readouterr()
    doc = envelope(out, err, rc, "add")
    assert doc["error"]["code"] == "not-a-workspace"


def test_contract_lists_every_code():
    """docs/CONTRACT.md's error table and ERROR_CODES name the same codes."""
    text = (ROOT / "docs" / "CONTRACT.md").read_text()
    section = text.split("## Error codes", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\| `([a-z0-9<>-]+)` \|", section, re.MULTILINE))
    assert documented - {"http-<status>"} == ERROR_CODES
    assert "http-<status>" in documented


def test_contract_covers_every_verb():
    text = (ROOT / "docs" / "CONTRACT.md").read_text()
    for verb in VERBS:
        assert f"### `{verb}`" in text, verb


def test_no_error_code_escapes_the_contract():
    """Every code the source raises is in ERROR_CODES (catches a new code nobody documented)."""
    src = "\n".join(p.read_text() for p in (ROOT / "src" / "nixin").rglob("*.py"))
    raised = set(
        re.findall(
            r'(?:NixinError|WriterError|DiscoveryError|ApplyError)\(\s*"([a-z][a-z0-9-]*)"',
            src,
            re.MULTILINE,
        )
    )
    raised |= set(re.findall(r'"code": "([a-z][a-z0-9-]*)"', src))
    raised |= set(re.findall(r'code="([a-z][a-z0-9-]*)"', src))
    assert raised and raised <= ERROR_CODES, raised - ERROR_CODES


# --- the --host default ------------------------------------------------------------------


def _attr_names_calls(world: World) -> int:
    return sum(" ".join(c).endswith("--apply builtins.attrNames") for c in world.calls)


def test_host_default_is_this_machine_when_the_config_has_it(world, capsys, monkeypatch):
    monkeypatch.setattr("nixin.configrepo.local_hostname", lambda: "mbp13")
    rc = main(["--json", "where", X11, "--config", str(world.root)])
    doc = envelope(*capsys.readouterr(), rc, "where")
    assert doc["ok"] and doc["host"] == "mbp13"
    # flake.nix names mbp13, so the default costs no eval of the host names (truth's own
    # host check is the one attrNames call)
    assert _attr_names_calls(world) == 1


def test_host_default_never_picks_one_of_several(world, capsys, monkeypatch):
    monkeypatch.setattr("nixin.configrepo.local_hostname", lambda: "laptop")
    for argv in (["where", X11], ["set", X11, "true"], ["list"]):
        rc = main(["--json", *argv, "--config", str(world.root)])
        doc = envelope(*capsys.readouterr(), rc, argv[0])
        assert doc["host"] == "laptop" and doc["error"]["code"] == "unknown-host", argv
        msg = doc["error"]["message"]
        assert "19BK, bare, mbp13" in msg and "--host" in msg, msg


def test_host_default_is_the_only_host(world, capsys, monkeypatch):
    flake = world.root / "flake.nix"
    text = flake.read_text()
    start = text.index('      nixosConfigurations."19BK"')
    end = text.index("      nixosConfigurations.mbp13")
    text = text[:start] + text[end:]
    flake.write_text(text.replace("      nixosConfigurations.bare = ", "      bare = "))
    monkeypatch.setattr("nixin.configrepo.local_hostname", lambda: "laptop")
    monkeypatch.setattr(sys.modules[__name__], "HM_USERS", {"mbp13": []})  # nix's attrNames
    rc = main(["--json", "set", X11, "true", "--config", str(world.root)])
    doc = envelope(*capsys.readouterr(), rc, "set")
    assert doc["ok"] and doc["host"] == "mbp13", doc
    assert "installer/mbp13.nix" in [c["file"] for c in doc["changes"]]
    # an explicit --host is never second-guessed
    rc = main(["--json", "--host", "laptop", "where", X11, "--config", str(world.root)])
    doc = envelope(*capsys.readouterr(), rc, "where")
    assert doc["error"]["code"] == "unknown-host"
    assert "'laptop' is not in nixosConfigurations (mbp13)" in doc["error"]["message"]


def test_host_default_without_a_config_is_the_hostname(world, capsys, monkeypatch):
    monkeypatch.setattr("nixin.configrepo.local_hostname", lambda: "laptop")
    rc = main(["--json", "search", "cowsay"])  # discovery takes no --config: nothing read
    doc = envelope(*capsys.readouterr(), rc, "search")
    assert doc["host"] == "laptop" and world.calls == []
    rc = main(["--json", "where", X11, "--config", "/nonexistent/nixin"])
    doc = envelope(*capsys.readouterr(), rc, "where")
    assert doc["host"] == "laptop" and doc["error"]["code"] == "config-not-found"
