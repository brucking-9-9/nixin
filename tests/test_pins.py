"""pins layer, offline: tests/fixtures/pins-config (the writer fixture plus kernels and a
flake.lock: nixpkgs is node `nixpkgs_2` at 3ec6363) copied to tmp_path, fake tools and a
fake nix that answers the pin evals from a canned package set. The generated module is
checked byte for byte against tests/fixtures/pins/19BK.nix through the real nixfmt.

The live test (NIXIN_LIVE=1) clones $NIXIN_CONFIG and pins the kernel on 19BK for real:
the kernel's drvPath must not change, `pins` must call the pin stale, `unpin` must leave
the clone clean.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from conftest import live
from test_writer import FakeTools

from nixin import configrepo, pins, writer
from nixin.cli import main
from nixin.configrepo import ConfigRepo, NixinError
from nixin.pins import checks as chk
from nixin.writer.state import Tools, render

FIXTURE = Path(__file__).parent / "fixtures" / "pins-config"
EXPECTED = Path(__file__).parent / "fixtures" / "pins" / "19BK.nix"
LOCK_REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
LOCK_NAR = "sha256-rZLrzrC6i+jnTAlvPZHogKDulY7WWDEv9hJeHieO7Zw="
NEW_REV = "0bb2eedc20e4739f29e8045034f2828ad7054f8f"
OLD_REV = "1d060086d60cb76a40cf37109f979d88a5bd6396"
STORE = "/nix/store/0000000000000000000000000000000-source"
TODAY = "2026-11-05"

PKGS = {
    "linux_7_2": {"version": "7.2.14", "pname": "linux", "out": "/nix/store/k-linux-7.2.14"},
    "linuxPackages_7_2": {"version": "7.2.14", "pname": "linux", "out": None},
    "firefox": {
        "version": "159.0",
        "pname": "firefox",
        "out": "/nix/store/f-firefox-159.0",
        "inputs": ["libglvnd", "gtk3"],
    },
    "ripgrep": {"version": "14.1.1", "pname": "ripgrep", "out": "/nix/store/r-ripgrep-14.1.1"},
    "openssl": {"version": "3.5.0", "pname": "openssl", "out": "/nix/store/o-openssl-3.5.0"},
}


class FakeNix:
    """The pin evals: `__nixinPinCheck` (versions, the kernel, the modules), the pinned
    tree's versions and glibc (`--expr`), `flake prefetch`, `flake update`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[list[str]] = []
        self.pkgs = {k: dict(v) for k, v in PKGS.items()}
        self.kernel = "7.2.14"
        self.glibc = "2.44"
        self.pinned_glibc = "2.44"
        self.modules: list[dict] = []
        self.specialisations: dict = {}
        self.pinned_versions: dict[str, dict | None] = {}
        self.fail: str | None = None

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        if self.fail:
            raise NixinError("eval-failed", self.fail)
        if args[:2] == ["flake", "metadata"]:
            return json.dumps({"path": STORE, "fingerprint": f"fp-{len(self.calls)}"})
        if args[:2] == ["flake", "prefetch"]:
            rev = args[3].rsplit("/", 1)[-1]
            return json.dumps({"hash": f"sha256-{rev[:8]}", "locked": {"lastModified": 1793726008}})
        if args[:2] == ["flake", "update"]:
            lock_file = self.path / "flake.lock"
            lock = json.loads(lock_file.read_text())
            lock["nodes"]["nixpkgs_2"]["locked"].update(rev=NEW_REV, narHash="sha256-new")
            lock_file.write_text(json.dumps(lock, indent=2))
            return ""  # the host's kernel stays 7.2.14: that is what the hold is for
        if "--expr" in args:
            expr = args[args.index("--expr") + 1]
            if "glibc.version" in expr:
                return self.pinned_glibc
            attrs = json.loads(
                json.loads(expr[expr.index("builtins.fromJSON") + 18 :].split(")")[0])
            )
            return json.dumps([self.pinned_versions.get(".".join(a)) for a in attrs])
        apply = args[args.index("--apply") + 1] if "--apply" in args else ""
        if "__nixinPinCheck" in apply:
            wanted = re.search(r"attrs = map info (\[.*?\]);", apply).group(1)
            names = [".".join(re.findall(r'"([^"]*)"', part)) for part in wanted[1:-1].split("] [")]
            names = [n for n in names if n]
            doc = {
                "attrs": [self.pkgs.get(n) for n in names],
                "glibc": self.glibc,
                "useGlobalPkgs": True,
                "kernel": self.kernel,
            }
            if "specialisations" in apply:
                doc.update(
                    {
                        "modules": self.modules,
                        "nvidia": None,
                        "specialisations": self.specialisations,
                    }
                )
            return json.dumps(doc)
        raise AssertionError(f"unexpected nix call {args}")


class FakeStore:
    def __init__(self) -> None:
        self.referrers: dict[str, list[str]] = {}
        self.closure: list[str] = []

    def __call__(self, args: list[str]) -> list[str] | None:
        if args[:2] == ["-q", "--referrers-closure"]:
            return self.referrers.get(args[2])
        if args[:1] == ["-qR"]:
            return self.closure
        raise AssertionError(args)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "config"
    shutil.copytree(FIXTURE, root)
    nix = FakeNix(root)
    monkeypatch.setattr(writer, "TOOLS", FakeTools())
    monkeypatch.setattr(writer, "NIX", nix)
    # every verb (pin through the writer, pins read-only, update) gets the fake nix
    monkeypatch.setattr(
        configrepo.ConfigRepo,
        "locate",
        classmethod(lambda cls, explicit=None, nix=None, _n=nix: ConfigRepo(root, nix=_n)),
    )
    monkeypatch.setattr(chk, "STORE", FakeStore())
    monkeypatch.setattr(pins, "_today", lambda: TODAY)
    monkeypatch.setattr(chk, "RUNNING_SYSTEM", str(root))  # exists: dependents() runs
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return root


def run(root: Path, *argv: str, host: str = "19BK", json_: bool = True) -> tuple[int, dict | str]:
    args = [*(["--json"] if json_ else []), "--host", host, *argv, "--config", str(root)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(args)
    return rc, (json.loads(buf.getvalue()) if json_ else buf.getvalue())


def state(root: Path) -> dict:
    return json.loads((root / "installer" / "state.json").read_text())


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


# --- pin kernel on 19BK --------------------------------------------------------------------------


def test_pin_kernel_holds_the_attr_the_config_names(cfg):
    rc, doc = run(cfg, "pin", "kernel", "--why", "T2 kernel build takes 2-3 h")
    assert rc == 0 and doc["ok"] and doc["verb"] == "pin"
    assert doc["pin"] == "kernel"
    (p,) = doc["pins"]
    assert p["attrs"] == ["linux_7_2", "linuxPackages_7_2"] and p["mode"] == "kernel"
    assert p["rev"] == LOCK_REV and p["short_rev"] == "3ec6363" and p["date"] == "2026-10-23"
    assert p["version"] == "7.2.14" and p["pname"] == "linux" and p["since"] == TODAY
    assert p["stale"] and "flake.lock is at 3ec6363" in p["stale_why"]
    st = state(cfg)["nixos"]["19BK"]["pins"]["kernel"]
    assert st["nar_hash"] == LOCK_NAR and st["owner"] == "nixos" and st["repo"] == "nixpkgs"
    assert st["decl"] == "configuration.nix:15"
    gen = (cfg / "installer" / "19BK.nix").read_text()
    assert f'url = "https://github.com/nixos/nixpkgs/archive/{LOCK_REV}.tar.gz";' in gen
    assert f'sha256 = "{LOCK_NAR}";' in gen
    assert "linux_7_2 = pinned.linux_7_2;" in gen
    assert "linuxPackages_7_2 = final.linuxPackagesFor final.linux_7_2;" in gen
    assert "config = config.nixpkgs.config; overlays = [ ];" in gen
    assert "# never the module's `pkgs` here" in gen
    assert "T2 kernel build takes 2-3 h. `nixin unpin kernel`" in gen
    assert "./installer/19BK.nix # generated by nixin" in (cfg / "configuration.nix").read_text()
    assert [c["file"] for c in doc["changes"]] == [
        "configuration.nix",
        "installer/19BK.nix",
        "installer/state.json",
    ]
    ids = [c["id"] for c in doc["hold_checks"]]
    assert ids == ["eval-cost", "kernel-toolchain", "pin-stale"]
    assert doc["checks"]["eval"]["ok"] and doc["checks"]["eval"]["kernel"] == "7.2.14"
    assert doc["checks"]["eval"]["note"].startswith("linux_7_2 7.2.14")
    assert [a["action"] for a in doc["actions"]] == ["pinned"]


def test_pin_firefox_joins_the_same_overlay_and_unpin_restores_the_tree(cfg):
    before = tree(cfg)
    assert run(cfg, "pin", "kernel")[0] == 0
    rc, doc = run(cfg, "pin", "firefox", "--why", "160 breaks the kiosk profile")
    assert rc == 0 and doc["pins"][0]["attrs"] == ["firefox"] and doc["pins"][0]["mode"] == "attr"
    gen = (cfg / "installer" / "19BK.nix").read_text()
    assert gen.count("builtins.fetchTarball") == 1  # one revision, one pinned tree
    assert "firefox = pinned.firefox;" in gen and "linux_7_2 = pinned.linux_7_2;" in gen
    assert sorted(state(cfg)["nixos"]["19BK"]["pins"]) == ["firefox", "kernel"]
    rc, doc = run(cfg, "unpin", "kernel")
    assert rc == 0 and [u["name"] for u in doc["unpinned"]] == ["kernel"]
    assert "linux_7_2" not in (cfg / "installer" / "19BK.nix").read_text()
    assert doc["checks"]["eval"]["ok"] and doc["checks"]["eval"]["note"].startswith("now linux_7_2")
    rc, doc = run(cfg, "unpin", "--all")
    assert rc == 0 and [u["name"] for u in doc["unpinned"]] == ["firefox"]
    assert not (cfg / "installer").exists()
    assert tree(cfg) == before  # byte for byte, import line included


def test_pin_is_idempotent_and_unpin_unknown_is_not_found(cfg):
    assert run(cfg, "pin", "kernel")[0] == 0
    rc, doc = run(cfg, "pin", "kernel")
    assert rc == 0 and doc["actions"] == [
        {"name": "kernel", "action": "already-pinned", "file": "installer/19BK.nix"}
    ]
    assert doc["changes"] == []
    rc, doc = run(cfg, "unpin", "nope")
    assert (
        rc == 1
        and doc["error"]["code"] == "not-found"
        and "pins: kernel" in doc["error"]["message"]
    )


def test_dry_run_writes_nothing(cfg):
    before = tree(cfg)
    rc, doc = run(cfg, "pin", "kernel", "--dry-run")
    assert rc == 0 and doc["dry_run"] and len(doc["changes"]) == 3
    assert tree(cfg) == before


def test_pin_with_an_older_rev_prefetches_and_reads_the_pinned_tree(cfg):
    writer.NIX.pinned_versions = {"firefox": {"version": "158.0.1", "pname": "firefox"}}
    rc, doc = run(cfg, "pin", "firefox", "--rev", OLD_REV)
    assert rc == 0, doc
    p = doc["pins"][0]
    assert p["rev"] == OLD_REV and p["version"] == "158.0.1" and p["date"] == "2026-11-03"
    assert not p["stale"]
    assert state(cfg)["nixos"]["19BK"]["pins"]["firefox"]["nar_hash"] == f"sha256-{OLD_REV[:8]}"
    assert ["flake", "prefetch", "--json", f"github:nixos/nixpkgs/{OLD_REV}"] in writer.NIX.calls
    # glibc differs → gl-skew (firefox links libglvnd/gtk3), from the post-write eval
    ids = {c["id"]: c for c in doc["hold_checks"]}
    assert "pin-stale" not in ids
    writer.NIX.pinned_glibc = "2.42"
    rc, doc = run(cfg, "pins", "--check")
    ids = {c["id"]: c for c in doc["checks"]}
    assert ids["gl-skew"]["level"] == "warn" and "glibc 2.42" in ids["gl-skew"]["text"]
    assert ids["gl-skew"]["data"]["inputs"] == ["libglvnd", "gtk3"]


# --- mbp13: the package-set alias, --whole-set, kernel-set-mismatch ------------------------------


def test_pin_kernel_whole_set_on_mbp13(cfg):
    rc, doc = run(cfg, "pin", "kernel", "--whole-set", host="mbp13")
    assert rc == 0, doc
    assert doc["pins"][0]["mode"] == "kernel-set"
    gen = (cfg / "installer" / "mbp13.nix").read_text()
    assert "linux_7_2 = pinned.linux_7_2;" in gen
    assert "linuxPackages_7_2 = pinned.linuxPackages_7_2;" in gen
    assert "linuxPackagesFor" not in gen
    ids = {c["id"]: c for c in doc["hold_checks"]}
    assert "kernel-toolchain" not in ids  # modules come from the pin too
    m = ids["kernel-set-mismatch"]
    assert m["level"] == "warn" and m["data"] == {
        "pin": "kernel",
        "file": "hosts/mbp13/desktop.nix",
        "line": 8,
    }
    assert "pkgs.linuxPackages_6_12, not config.boot.kernelPackages" in m["text"]


def test_kernel_modules_dropped_blocks(cfg):
    writer.NIX.modules = [{"name": "nvidia-x11", "ok": False}, {"name": "broadcom-sta", "ok": True}]
    rc, doc = run(cfg, "pin", "kernel", host="mbp13")
    assert rc == 0  # the write stays; the check says what is wrong
    ids = {c["id"]: c for c in doc["hold_checks"]}
    b = ids["kernel-modules-dropped"]
    assert b["level"] == "block" and "nvidia-x11 from the new nixpkgs does not build" in b["text"]
    assert "nixin pin kernel --whole-set" in b["text"]
    assert doc["checks"]["eval"]["ok"] is False and doc["checks"]["eval"]["code"] == "pin-blocked"
    assert any(
        w.startswith("pin-blocked: kernel held at 7.2.14 but nvidia-x11") for w in doc["warnings"]
    )


# --- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, code, words",
    [
        (["pin", "python3Packages.requests"], "pin-unsupported", "lives in a package set"),
        (["pin", "kdePackages.ark"], "pin-unsupported", "lives in a package set"),
        (["pin", "foo.bar"], "pin-unsupported", "nested attribute"),
        (["pin", "nope"], "unknown-package", "not in the pkgs of 19BK: nope"),
        (["pin"], "usage", "pin NAME"),
        (["pin", "firefox", "--whole-set"], "usage", "--whole-set is for"),
        (["pin", "a", "b"], "usage", "pin NAME"),
        (["unpin"], "usage", "unpin NAME"),
        (["pins", "x"], "usage", "takes no arguments"),
    ],
)
def test_refusals_write_nothing(cfg, argv, code, words):
    before = tree(cfg)
    rc, doc = run(cfg, *argv)
    assert rc == (2 if code == "usage" else 1) and doc["error"]["code"] == code
    assert words in doc["error"]["message"]
    assert tree(cfg) == before


def test_kernel_without_a_declaration_is_unsupported(cfg):
    rc, doc = run(cfg, "pin", "kernel", host="bare")
    assert rc == 1 and doc["error"]["code"] == "pin-unsupported"
    assert "does not define boot.kernelPackages" in doc["error"]["message"]


def test_kernel_from_another_input_is_unsupported(cfg):
    f = cfg / "configuration.nix"
    f.write_text(
        f.read_text().replace(
            'pkgs.linuxPackagesFor (\n      pkgs.linux_7_2.override {\n        pname = "linux-t2";\n        kernelPatches = [ ];\n      }\n    )',
            "inputs.nixos-hardware.legacyPackages.linuxPackages_t2",
        )
    )
    rc, doc = run(cfg, "pin", "kernel")
    assert rc == 1 and doc["error"]["code"] == "pin-unsupported"
    assert "another flake input" in doc["error"]["message"]


def test_kernel_attr_that_is_not_the_running_kernel_is_unsupported(cfg):
    writer.NIX.kernel = "6.12.40"
    rc, doc = run(cfg, "pin", "kernel")
    assert rc == 1 and doc["error"]["code"] == "pin-unsupported"
    assert "kernel is 6.12.40 but pkgs.linux_7_2 is 7.2.14" in doc["error"]["message"]


def test_not_a_workspace(cfg, capsys):
    rc = main(["--json", "--host", "19BK", "pin", "kernel", "--config", "/etc/nixos"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["error"]["code"] == "not-a-workspace"


# --- pins -------------------------------------------------------------------------------------------


def test_pins_lists_and_flags_stale(cfg, capsys):
    rc, doc = run(cfg, "pins")
    assert rc == 0 and doc["pins"] == [] and doc["count"] == 0 and doc["checked"] is False
    assert run(cfg, "pin", "kernel", "--why", "slow build")[0] == 0
    assert run(cfg, "pin", "ripgrep")[0] == 0
    rc, doc = run(cfg, "pins")
    assert rc == 0 and doc["count"] == 2 and doc["stale"] == 2
    assert doc["lock"]["short_rev"] == "3ec6363" and doc["lock"]["node"] == "nixpkgs_2"
    k, r = doc["pins"]
    assert (k["name"], r["name"]) == ("kernel", "ripgrep")
    assert k["drop"] == "nixin unpin kernel" and k["why"] == "slow build"
    evals = sum(c[:1] == ["eval"] for c in writer.NIX.calls)
    rc, text = run(cfg, "pins", json_=False)
    assert sum(c[:1] == ["eval"] for c in writer.NIX.calls) == evals  # no eval without --check
    lines = text.splitlines()
    assert lines[0] == "19BK: 2 pins, 2 stale (flake.lock: nixpkgs 3ec6363)"
    assert lines[1].split() == ["pin", "attrs", "held", "at", "nixpkgs"]
    assert "kernel" in lines[2] and "(stale: flake.lock is at 3ec6363" in lines[2]
    assert lines[-1] == "drop one: nixin unpin NAME; all: nixin unpin --all"


def test_pins_check_after_the_lock_moved(cfg):
    assert run(cfg, "pin", "kernel")[0] == 0
    assert run(cfg, "pin", "openssl")[0] == 0
    # the lock moves on (as `nixin update` would): the pins are no longer stale
    lock_file = cfg / "flake.lock"
    lock = json.loads(lock_file.read_text())
    lock["nodes"]["nixpkgs_2"]["locked"].update(rev=NEW_REV, narHash="sha256-new")
    lock_file.write_text(json.dumps(lock))
    store = chk.STORE
    store.referrers["/nix/store/o-openssl-3.5.0"] = [
        "/nix/store/o-openssl-3.5.0",
        "/nix/store/a-curl-8.1",
        "/nix/store/b-python3-3.13",
        "/nix/store/c-git-2.50",
        "/nix/store/d-nodejs-22",
        "/nix/store/e-rustc-1.90",
        "/nix/store/z-not-installed-1.0",
    ]
    store.closure = [
        p for p in store.referrers["/nix/store/o-openssl-3.5.0"] if "not-installed" not in p
    ]
    rc, doc = run(cfg, "pins", "--check")
    assert rc == 0 and doc["checked"] and doc["stale"] == 0
    assert doc["pins"][0]["now"] == {"linux_7_2": "7.2.14", "linuxPackages_7_2": "7.2.14"}
    ids = {c["id"]: c for c in doc["checks"]}
    assert "pin-stale" not in ids
    d = ids["rebuilds-dependents"]
    assert d["level"] == "warn" and d["data"]["count"] == 5
    assert d["text"].startswith("5 installed packages depend on openssl and will rebuild")
    assert "curl-8.1, python3-3.13" in d["text"]
    assert ids["eval-cost"]["data"]["revisions"] == [LOCK_REV]
    # a version the overlay does not manage to hold is a block
    writer.NIX.pkgs["openssl"]["version"] = "3.6.0"
    rc, doc = run(cfg, "pins", "--check")
    b = {c["id"]: c for c in doc["checks"]}["pin-not-holding"]
    assert b["level"] == "block" and "should hold openssl at 3.5.0" in b["text"]


def test_pins_on_a_read_only_tree(cfg):
    """`pins` reads any config (no workspace needed), as outdated does."""
    assert run(cfg, "pin", "kernel")[0] == 0
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    cfg.chmod(0o555)
    try:
        rc, doc = run(cfg, "pins")
        rc2, doc2 = run(cfg, "pin", "ripgrep")
    finally:
        cfg.chmod(0o755)
    assert rc == 0 and doc["count"] == 1
    assert rc2 == 1 and doc2["error"]["code"] == "not-a-workspace"


# --- update --hold -----------------------------------------------------------------------------------


def test_update_hold_pins_first_then_moves_the_lock_then_checks(cfg):
    rc, doc = run(cfg, "update", "--hold", "kernel")
    assert rc == 0, doc
    assert doc["verb"] == "update" and doc["changed"] and doc["to"]["rev"] == NEW_REV
    (h,) = doc["holds"]
    assert h["name"] == "kernel" and h["rev"] == LOCK_REV and h["stale"]  # at pin time
    pinned = state(cfg)["nixos"]["19BK"]["pins"]["kernel"]
    assert pinned["why"] == f"held by `nixin update --hold` on {TODAY}"
    ids = [c["id"] for c in doc["checks"]]
    assert "pin-stale" not in ids and "kernel-toolchain" in ids and "eval-cost" in ids
    assert "warnings" not in doc
    # order: the pin's eval before `flake update`
    calls = writer.NIX.calls
    first_pin = next(i for i, c in enumerate(calls) if "__nixinPinCheck" in " ".join(c))
    upd = next(i for i, c in enumerate(calls) if c[:2] == ["flake", "update"])
    assert first_pin < upd


def test_update_hold_refuses_before_moving_the_lock(cfg):
    before = tree(cfg)
    rc, doc = run(cfg, "update", "--hold", "python3Packages.requests")
    assert rc == 1 and doc["error"]["code"] == "pin-unsupported"
    assert tree(cfg) == before and all(c[:2] != ["flake", "update"] for c in writer.NIX.calls)


# --- the renderer ------------------------------------------------------------------------------------


ENTRY = {
    "packages": ["cowsay"],
    "options": {"services.tailscale.enable": "true"},
    "pins": {
        "kernel": {
            "attrs": ["linux_7_2", "linuxPackages_7_2"],
            "mode": "kernel",
            "rev": NEW_REV,
            "nar_hash": "sha256-fiPJ88izTVucjeo2EQ3jk4aWmLg03I1D4vxnX2QjEgE=",
            "date": "2026-11-01",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "version": "7.2.14",
            "pname": "linux",
            "why": "T2 kernel build takes 2-3 h",
            "since": TODAY,
        },
        "firefox": {
            "attrs": ["firefox"],
            "mode": "attr",
            "rev": NEW_REV,
            "nar_hash": "sha256-fiPJ88izTVucjeo2EQ3jk4aWmLg03I1D4vxnX2QjEgE=",
            "date": "2026-11-01",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "version": "159.0",
            "pname": "firefox",
            "why": "160 breaks the kiosk profile",
            "since": TODAY,
        },
        "ripgrep": {
            "attrs": ["ripgrep"],
            "mode": "attr",
            "rev": OLD_REV,
            "nar_hash": "sha256-jQp1wKvbwWdLBASTXkSi/VYmDJ5AbBi1WvcMt9HWJC8=",
            "date": "2026-11-03",
            "owner": "NixOS",
            "repo": "nixpkgs",
            "version": "14.1.1",
            "pname": "ripgrep",
            "why": None,
            "since": TODAY,
        },
    },
}


def test_render_matches_the_fixture_byte_for_byte_through_real_nixfmt():
    if shutil.which("nixfmt") is None:
        pytest.skip("nixfmt not on PATH")
    text, why = Tools().fmt(render("nixos", ENTRY))
    assert why is None
    assert text == Tools().fmt(text)[0]  # stable
    if os.environ.get("NIXIN_WRITE_FIXTURES") == "1":  # regenerate after a renderer change
        EXPECTED.write_text(text)
    assert text == EXPECTED.read_text()


def test_render_without_pins_is_unchanged_and_home_ignores_pins():
    text = render("nixos", {"packages": ["cowsay"], "options": {}})
    assert "overlays" not in text and "let" not in text
    text = render("home", {**ENTRY, "packages": []})
    assert "overlays" not in text


def test_render_groups_pins_by_revision_oldest_first():
    text = render("nixos", ENTRY)
    assert text.index("nixpkgs-0bb2eed = import") < text.index("nixpkgs-1d06008 = import")
    assert text.count("final: prev:") == 2
    assert "archive/1d060086d60cb76a40cf37109f979d88a5bd6396.tar.gz" in text


# --- the eval expressions parse (the fakes never parse Nix) -------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        chk.check_apply(["linux_7_2", "linuxPackages_7_2", "firefox"], kernel=True),
        chk.check_apply(["firefox"], kernel=False),
        chk.pinned_glibc_apply("https://x/a.tar.gz", "sha256-x"),
        pins._pinned_expr("https://x/a.tar.gz", "sha256-x", ["firefox", "python3Packages.x"]),
    ],
    ids=["check-kernel", "check-attr", "pinned-glibc", "pinned-versions"],
)
def test_eval_expressions_parse_with_the_real_nix(expr, tmp_path, monkeypatch):
    if shutil.which("nix-instantiate") is None:
        pytest.skip("nix-instantiate not on PATH")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NIX_REMOTE", "dummy://")
    proc = subprocess.run(
        ["nix-instantiate", "--parse", "-E", f"with {{ }}; {expr}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# --- judge: the pieces the fakes cannot reach ------------------------------------------------------------


def test_judge_glibc_skew_without_gl_is_info_and_specialisations_count():
    pins_ = {
        "ripgrep": {**ENTRY["pins"]["ripgrep"]},
        "kernel": {**ENTRY["pins"]["kernel"]},
    }
    got = {
        "attrs": [
            {"version": "7.2.14", "pname": "linux", "out": None, "inputs": []},
            {"version": "7.2.14", "pname": "linux", "out": None, "inputs": []},
            {"version": "14.1.1", "pname": "ripgrep", "out": None, "inputs": ["pcre2"]},
        ],
        "glibc": "2.44",
        "kernel": "7.2.14",
        "modules": [],
        "nvidia": {"name": "nvidia-x11-470", "ok": True},
        "specialisations": {
            "gaming": {"kernel": "7.2.14", "modules": [{"name": "v4l2loopback", "ok": False}]},
            "lts": {
                "kernel": "6.12.40",
                "modules": [{"name": "nvidia-x11", "ok": False}],
            },  # its own kernel
        },
    }
    items = chk.judge(pins_, got=got, lock_rev=NEW_REV, pinned_glibc={OLD_REV: "2.42"}, set_refs=[])
    ids = {c["id"]: c for c in items}
    assert (
        ids["glibc-skew"]["level"] == "info"
        and "fine for a leaf package" in ids["glibc-skew"]["text"]
    )
    assert "gl-skew" not in ids
    assert ids["kernel-modules-dropped"]["data"]["modules"] == [
        {"name": "v4l2loopback (gaming)", "ok": False}
    ]
    assert ids["pin-stale"]["data"]["pin"] == "kernel"  # kernel pinned at the lock's rev
    assert [c["level"] for c in items] == sorted((c["level"] for c in items), key=chk.level_order)


def test_read_pins_and_summary_without_a_workspace(tmp_path):
    assert pins.read_pins(tmp_path, "19BK") == {}
    (tmp_path / "installer").mkdir()
    (tmp_path / "installer" / "state.json").write_text(
        json.dumps({"version": 1, "nixos": {"19BK": {"pins": {"kernel": ENTRY["pins"]["kernel"]}}}})
    )
    (s,) = pins.summary(tmp_path, "19BK", NEW_REV)
    assert s["name"] == "kernel" and s["stale"] and s["drop"] == "nixin unpin kernel"
    assert pins.stale_warnings([s]) == [
        f'pin "kernel" holds nothing: flake.lock is at {NEW_REV[:7]}, the revision it pins (nixin unpin kernel)'
    ]
    assert pins.summary(tmp_path, "19BK", OLD_REV)[0]["stale"] is False
    (tmp_path / "installer" / "state.json").write_text("{ not json")
    assert pins.read_pins(tmp_path, "19BK") == {}


# --- live: a clone of $NIXIN_CONFIG, never the config itself ----------------------------------------------


@live
def test_live_pin_kernel_keeps_the_kernel_drv_and_unpin_restores(tmp_path, monkeypatch):
    src = os.path.expanduser(os.environ.get("NIXIN_CONFIG") or "~/nixos")
    host = os.environ.get("NIXIN_LIVE_HOST", "19BK")
    if not (Path(src) / ".git").exists() or shutil.which("git") is None:
        pytest.skip("NIXIN_CONFIG (a git checkout) is needed")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", src, str(clone)], check=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def drv() -> str:
        return subprocess.run(
            [
                "nix",
                "eval",
                "--raw",
                f'{clone}#nixosConfigurations."{host}".config.boot.kernelPackages.kernel.drvPath',
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    before = drv()
    rc, doc = run(clone, "pin", "kernel", "--why", "live test", host=host)
    assert rc == 0, doc
    assert doc["checks"]["eval"]["ok"], doc["checks"]
    assert drv() == before  # the hold at the lock's own rev changes nothing
    rc, doc = run(clone, "pins", host=host)
    assert rc == 0 and doc["pins"][0]["stale"]
    rc, doc = run(clone, "unpin", "kernel", host=host)
    assert rc == 0 and doc["checks"]["eval"]["ok"]
    status = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""
    assert drv() == before
