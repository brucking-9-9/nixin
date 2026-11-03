"""`nixin try`, offline: recorded probes, a fake nix (probe, build, path-info) and a fake store.

Fixtures (tests/fixtures/try/, recorded on mbp13 on 2026-11-02 against ~/nixos's lock,
nixpkgs 3ec6363):

    probe-cowsay.json            probe.nix on cowsay (mainProgram cowsay; out + man)
    probe-gnome-calculator.json  on gnome-calculator (a GUI app)
    probe-steam.json             on steam: unfree, so out_paths is null
    probe-missing.json           on `cowsy`: found = false; the names next to it trimmed to
                                 the ones with "cow" or starting co/gnome-c/hello (555 of 28514)
    org.gnome.Calculator.desktop gnome-calculator's desktop entry (translations dropped)

The fake store maps each recorded out path to a directory under tmp_path and fills bin/
and share/applications/ the way the real outputs have them. Live tests (NIXIN_LIVE=1) run
against $NIXIN_CONFIG and build what they try (cowsay, hello: small, usually cached).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES, fixture, live

from nixin import cli
from nixin.apply.proc import Proc
from nixin.tryout import build, launch, nixpkgs, render, runner

REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
REF = f"github:nixos/nixpkgs/{REV}"
SYSTEM = "x86_64-linux"
FETCH_STDERR = """these 3 paths will be fetched (2.3 MiB download, 10.6 MiB unpacked):
  /nix/store/0wzx59q463byhgj9y56jklygdbgdcfph-gnome-calculator-50.0
  /nix/store/m3h8qssh09bj36a26miq8rmikz79365z-libgee-0.20.8
  /nix/store/5v4famff6shizzpddr68jblxr2p972r9-libmpc-1.4.1
copying path '/nix/store/m3h8qssh09bj36a26miq8rmikz79365z-libgee-0.20.8' from 'https://cache.nixos.org'...
copying path '/nix/store/5v4famff6shizzpddr68jblxr2p972r9-libmpc-1.4.1' from 'https://cache.nixos.org'...
copying path '/nix/store/0wzx59q463byhgj9y56jklygdbgdcfph-gnome-calculator-50.0' from 'https://cache.nixos.org'...
"""
BUILD_FAILED_STDERR = """this derivation will be built:
  /nix/store/aaaa-broken-thing-1.0.drv
building '/nix/store/aaaa-broken-thing-1.0.drv'...
error: builder for '/nix/store/aaaa-broken-thing-1.0.drv' failed with exit code 2;
       last 2 log lines:
       > make: *** [Makefile:12: all] Error 1
       For full logs, run:
         nix log /nix/store/aaaa-broken-thing-1.0.drv
"""
NETWORK_STDERR = """error:
       … while fetching the input 'github:nixos/nixpkgs/nixos-unstable'

       error: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-unstable': Could not resolve host: api.github.com (6)
"""
ALIAS_STDERR = """error:
       … while calling the 'throw' builtin
         at /nix/store/6nwzgfvznah5rg192x0hnl5ykchdgi59-source/pkgs/top-level/aliases.nix:1012:3:

       error: 'oldthing' has been renamed to/replaced by 'newthing'
"""

# what the recorded outputs have in bin/ and share/applications/
CONTENTS = {
    "cowsay-3.8.4": {"bin": ["cowsay", "cowthink"]},
    "gnome-calculator-50.0": {
        "bin": ["gcalccmd", "gnome-calculator"],
        "desktop": {
            "org.gnome.Calculator.desktop": (
                FIXTURES / "try" / "org.gnome.Calculator.desktop"
            ).read_text()
        },
    },
    "steam-1.0.0.87": {
        "bin": ["steam"],
        "desktop": {
            "steam.desktop": "[Desktop Entry]\nName=Steam\nExec=steam %U\nTerminal=false\n"
        },
    },
    "multi-1.0": {"bin": ["alpha", "beta"]},
    "viewer-2.0": {
        "bin": ["viewer", "viewer-helper"],
        "desktop": {
            "viewer.desktop": "[Desktop Entry]\nName=Viewer\nExec=env GDK_BACKEND=x11 viewer %U\n"
        },
    },
    "zlib-1.3.2": {"bin": []},
}


def probe_of(name: str) -> dict:
    return copy.deepcopy(fixture(f"try/probe-{name}.json"))


def synthetic(pname: str, version: str, *, main: str | None = None) -> dict:
    """A probe answer for a package the fixtures do not have."""
    d = probe_of("cowsay")
    d.update(
        name=f"{pname}-{version}",
        pname=pname,
        version=version,
        main_program=main,
        description=None,
        outputs=["out"],
        out_paths={"out": f"/nix/store/{'x' * 32}-{pname}-{version}"},
    )
    return d


class FakeNix:
    """The nix calls `try` makes. Probe answers by attribute path; `nix build` "realises"
    the outputs in the fake store (unless `build_fails`); path-info sums a fixed closure."""

    def __init__(self, store: Path) -> None:
        self.store = store
        self.calls: list[tuple[list[str], str]] = []
        self.probes: dict[str, dict | str] = {
            "cowsay": probe_of("cowsay"),
            "gnome-calculator": probe_of("gnome-calculator"),
            "steam": probe_of("steam"),
            "python3Packages": {"found": True, "derivation": False, "names": ["requests"]},
            "multi": synthetic("multi", "1.0"),
            "viewer": synthetic("viewer", "2.0"),
            "zlib": synthetic("zlib", "1.3.2"),
        }
        self.build_stderr: dict[str, str] = {"gnome-calculator": FETCH_STDERR}
        self.build_fails: dict[str, str] = {}
        self.present: set[str] = set()  # attrs whose outputs are in the store before the build

    def local(self, path: str) -> str:
        return str(self.store / os.path.basename(path))

    def outputs(self, attr: str) -> dict[str, str]:
        info = self.probes[attr]
        assert isinstance(info, dict) and info.get("out_paths"), attr
        return {o: self.local(p) for o, p in info["out_paths"].items()}

    def realise(self, attr: str) -> None:
        for out, path in self.outputs(attr).items():
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            if out != "out":
                continue
            name = p.name.split("-", 1)[1]
            spec = CONTENTS.get(name, {})
            (p / "bin").mkdir(exist_ok=True)
            for prog in spec.get("bin", []):
                f = p / "bin" / prog
                f.write_text("#!/bin/sh\n")
                f.chmod(0o755)
            for fname, text in spec.get("desktop", {}).items():
                d = p / "share" / "applications"
                d.mkdir(parents=True, exist_ok=True)
                (d / fname).write_text(text)

    def __call__(self, argv: list[str], *, stream: str = "capture", timeout=None) -> Proc:
        self.calls.append((argv, stream))
        assert argv[0] == "nix", argv
        verb = argv[1]
        if verb == "eval":
            m = re.search(r"path = \[ (.*?) \]; \}$", argv[5])
            assert m, argv[5]
            attr = ".".join(re.findall(r'"([^"]*)"', m.group(1)))
            ans = self.probes.get(attr)
            if isinstance(ans, str):
                return Proc(argv, 1, "", ans)
            if ans is None:
                ans = probe_of("missing")
            ans = copy.deepcopy(ans)
            if ans.get("out_paths"):
                ans["out_paths"] = {o: self.local(p) for o, p in ans["out_paths"].items()}
                if attr in self.present:
                    self.realise(attr)
            return Proc(argv, 0, json.dumps(ans), "")
        if verb == "build":
            attr = argv[4].split("#", 1)[1]
            if attr in self.build_fails:
                return Proc(argv, 1, "", self.build_fails[attr])
            self.realise(attr)
            drv = f"/nix/store/{'d' * 32}-{attr}.drv"
            out = json.dumps([{"drvPath": drv, "outputs": self.outputs(attr)}])
            return Proc(
                argv, 0, out, "" if stream == "inherit" else self.build_stderr.get(attr, "")
            )
        if verb == "path-info":
            lines = [f"{p}\t{1 << 20}" for p in argv[4:]] + [
                f"/nix/store/{'g' * 32}-glibc\t{30 << 20}"
            ]
            return Proc(argv, 0, "\n".join(lines) + "\n", "")
        raise AssertionError(f"unexpected nix call {argv}")

    def verbs(self) -> list[str]:
        return [a[1] for a, _ in self.calls]


@pytest.fixture
def config(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    (d / "flake.nix").write_text("{ outputs = _: { }; }\n")
    shutil.copy(FIXTURES / "flake.lock.json", d / "flake.lock")
    return d


@pytest.fixture
def nix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeNix:
    fake = FakeNix(tmp_path / "store")
    monkeypatch.setattr(runner, "RUN", fake)
    monkeypatch.setattr("nixin.tryout.current_system", lambda: SYSTEM)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in ("NIXPKGS_ALLOW_UNFREE", "NIXPKGS_ALLOW_BROKEN", "NIXPKGS_ALLOW_INSECURE"):
        monkeypatch.delenv(var, raising=False)
    return fake


@pytest.fixture
def execs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str], dict]]:
    """launch.handoff's exec, recorded instead of replacing the test process."""
    seen: list[tuple[str, list[str], dict]] = []
    monkeypatch.setattr(launch, "EXEC", lambda f, argv, env: seen.append((f, argv, env)))
    return seen


def run_json(capsys, *argv: str) -> tuple[int, dict]:
    rc = cli.main(["--json", *argv])
    out, err = capsys.readouterr()
    assert err == "", "--json keeps stderr empty"
    return rc, json.loads(out)


# --- the --json document ---------------------------------------------------------------------


def test_json_builds_and_reports(nix, config, execs, capsys):
    rc, doc = run_json(capsys, "try", "cowsay", "--config", str(config), "--", "moo")
    assert rc == 0 and doc["ok"] and doc["verb"] == "try"
    assert doc["attr"] == "cowsay" and doc["version"] == "3.8.4"
    assert doc["nixpkgs"]["flakeref"] == REF and doc["nixpkgs"]["from"] == "lock"
    assert doc["nixpkgs"]["input"] == "nixpkgs" and doc["nixpkgs"]["short_rev"] == "3ec6363"
    assert doc["programs"] == ["cowsay", "cowthink"] and doc["program"] == "cowsay"
    assert doc["command"] == f"nix run {REF}#cowsay -- moo"
    assert doc["argv"] == ["nix", "run", f"{REF}#cowsay", "--", "moo"]
    assert doc["shell_command"] == f"nix shell {REF}#cowsay"
    assert doc["exec"] == [
        str(nix.store / "4lbicqd70qlfdw2l5wbq23c9qj1m5nww-cowsay-3.8.4" / "bin" / "cowsay"),
        "moo",
    ]
    assert doc["out"].endswith("-cowsay-3.8.4") and set(doc["outputs"]) == {"out", "man"}
    assert doc["size"] == (2 << 20) + (30 << 20)  # the two outputs + the fake glibc
    assert doc["cached"] is False and doc["build_only"] is True and doc["gui"] is False
    assert doc["fetched"] == {"paths": 0, "download": 0, "unpacked": 0, "built": 0}
    assert set(doc["timings"]) == {"probe", "build", "size", "total"}
    assert execs == [], "--json never hands the terminal over"
    assert nix.verbs() == ["eval", "build", "path-info"]
    assert all(stream == "capture" for _, stream in nix.calls)


def test_json_cached_when_the_outputs_are_already_there(nix, config, capsys):
    nix.present.add("cowsay")
    _, doc = run_json(capsys, "try", "cowsay", "--config", str(config))
    assert doc["cached"] is True
    assert doc["command"] == f"nix run {REF}#cowsay"


def test_probe_is_one_eval_on_legacy_packages(nix, config, capsys):
    run_json(capsys, "try", "python3Packages.requests", "--config", str(config))
    argv = nix.calls[0][0]
    assert argv[:4] == ["nix", "eval", "--json", f"{REF}#legacyPackages.{SYSTEM}"]
    assert argv[4] == "--apply" and argv[5].endswith('{ path = [ "python3Packages" "requests" ]; }')
    assert nixpkgs.probe_text().strip() in argv[5]


def test_gui_app(nix, config, capsys):
    _, doc = run_json(capsys, "try", "gnome-calculator", "--config", str(config))
    assert doc["gui"] is True and doc["program"] == "gnome-calculator"
    assert doc["desktop"] == [
        {
            "file": "org.gnome.Calculator.desktop",
            "name": "Calculator",
            "exec": "gnome-calculator",
            "program": "gnome-calculator",
            "terminal": False,
            "hidden": False,
        }
    ]
    assert doc["fetched"] == {
        "paths": 3,
        "download": round(2.3 * (1 << 20)),
        "unpacked": round(10.6 * (1 << 20)),
        "built": 0,
    }


def test_nixpkgs_rev_needs_no_config(nix, tmp_path, capsys):
    rc, doc = run_json(
        capsys, "try", "cowsay", "--nixpkgs", REV[:12], "--config", str(tmp_path / "nope")
    )
    assert rc == 0
    assert doc["nixpkgs"]["flakeref"] == f"github:nixos/nixpkgs/{REV[:12]}"
    assert doc["nixpkgs"]["from"] == "nixpkgs" and doc["nixpkgs"]["config"] is None
    _, doc = run_json(capsys, "try", "cowsay", "--nixpkgs", "nixpkgs")
    assert doc["command"] == "nix run nixpkgs#cowsay"


def test_missing_config_says_nixpkgs_works_too(nix, tmp_path, capsys):
    rc, doc = run_json(capsys, "try", "cowsay", "--config", str(tmp_path / "nope"))
    assert rc == 1 and doc["error"]["code"] == "config-not-found"
    assert "--nixpkgs" in doc["error"]["message"]


# --- picking the program ------------------------------------------------------------------------


def test_program_other_than_nix_runs_goes_through_nix_shell(nix, config, capsys):
    _, doc = run_json(
        capsys, "try", "cowsay", "--program", "cowthink", "--config", str(config), "--", "hmm"
    )
    assert doc["program"] == "cowthink"
    assert doc["argv"] == ["nix", "shell", f"{REF}#cowsay", "--command", "cowthink", "hmm"]
    assert doc["exec"][0].endswith("/bin/cowthink")


def test_unknown_program(nix, config, capsys):
    rc, doc = run_json(capsys, "try", "cowsay", "--program", "cowsing", "--config", str(config))
    assert rc == 1 and doc["error"]["code"] == "not-found"
    assert doc["error"]["programs"] == ["cowsay", "cowthink"]


def test_ambiguous_program(nix, config, capsys):
    rc, doc = run_json(capsys, "try", "multi", "--config", str(config))
    err = doc["error"]
    assert rc == 1 and err["code"] == "ambiguous-program"
    assert err["programs"] == ["alpha", "beta"] and "--program NAME" in err["message"]
    assert err["shell_command"] == f"nix shell {REF}#multi" and err["out"].endswith("multi-1.0")
    assert "cached" in err and "size" in err
    _, doc = run_json(capsys, "try", "multi", "--program", "beta", "--config", str(config))
    assert doc["ok"] and doc["argv"] == ["nix", "shell", f"{REF}#multi", "--command", "beta"]
    _, doc = run_json(capsys, "try", "multi", "--shell", "--config", str(config))
    assert doc["ok"] and doc["program"] is None and doc["command"] == f"nix shell {REF}#multi"


def test_graphical_entry_picks_the_program(nix, config, capsys):
    _, doc = run_json(capsys, "try", "viewer", "--config", str(config))
    assert doc["program"] == "viewer" and doc["gui"] is True
    assert doc["desktop"][0]["program"] == "viewer"  # past `env VAR=…`
    # nix run would look for bin/viewer (the pname) too, so it stays nix run
    assert doc["argv"] == ["nix", "run", f"{REF}#viewer"]


def test_no_program(nix, config, capsys):
    rc, doc = run_json(capsys, "try", "zlib", "--config", str(config))
    assert rc == 1 and doc["error"]["code"] == "no-program"
    assert doc["error"]["programs"] == [] and "--shell" in doc["error"]["message"]


# --- refusals and failures ----------------------------------------------------------------------


def test_not_found_suggests(nix, config, capsys):
    rc, doc = run_json(capsys, "try", "cowsy", "--config", str(config))
    err = doc["error"]
    assert rc == 1 and err["code"] == "not-found"
    assert err["suggestions"][0] == "cowsay" and "did you mean cowsay" in err["message"]
    assert nix.verbs() == ["eval"], "nothing is built"


def test_package_set_is_not_a_package(nix, config, capsys):
    _, doc = run_json(capsys, "try", "python3Packages", "--config", str(config))
    assert doc["error"]["code"] == "not-found" and "package set" in doc["error"]["message"]


def test_alias_that_throws_is_not_found(nix, config, capsys):
    nix.probes["oldthing"] = ALIAS_STDERR
    _, doc = run_json(capsys, "try", "oldthing", "--config", str(config))
    assert doc["error"]["code"] == "not-found"
    assert "renamed to/replaced by 'newthing'" in doc["error"]["message"]


def test_unfree_says_how(nix, config, capsys):
    rc, doc = run_json(capsys, "try", "steam", "--config", str(config))
    err = doc["error"]
    assert rc == 1 and err["code"] == "unfree"
    assert err["commands"] == [
        "NIXPKGS_ALLOW_UNFREE=1 nixin try steam --impure",
        f"NIXPKGS_ALLOW_UNFREE=1 nix run --impure {REF}#steam",
    ]
    assert "unfreeRedistributable" in err["message"] and "allowUnfree" in err["message"]
    assert nix.verbs() == ["eval"]


def test_unfree_reasons(nix, config, capsys, monkeypatch):
    monkeypatch.setenv("NIXPKGS_ALLOW_UNFREE", "1")
    _, doc = run_json(capsys, "try", "steam", "--config", str(config))
    assert "is set, but" in doc["error"]["message"] and "--impure" in doc["error"]["message"]
    monkeypatch.delenv("NIXPKGS_ALLOW_UNFREE")
    _, doc = run_json(capsys, "try", "steam", "--impure", "--config", str(config))
    assert "--impure is given, but NIXPKGS_ALLOW_UNFREE=1 is not set" in doc["error"]["message"]


def test_unfree_allowed_runs_impure(nix, config, capsys, monkeypatch):
    monkeypatch.setenv("NIXPKGS_ALLOW_UNFREE", "1")
    allowed = probe_of("steam")
    allowed["available"] = True
    allowed["out_paths"] = {"out": "/nix/store/" + "s" * 32 + "-steam-1.0.0.87"}
    nix.probes["steam"] = allowed
    _, doc = run_json(capsys, "try", "steam", "--impure", "--config", str(config))
    assert doc["ok"] and doc["unfree"] and doc["impure"]
    assert doc["command"] == f"NIXPKGS_ALLOW_UNFREE=1 nix run --impure {REF}#steam"
    assert doc["argv"] == ["nix", "run", "--impure", f"{REF}#steam"]
    assert nix.calls[0][0][-1] == "--impure" and "--impure" in nix.calls[1][0]


def test_broken_is_refused_with_its_variable(nix, config, capsys):
    broken = synthetic("crumbly", "0.1", main="crumbly")
    broken.update(broken=True, available=False, out_paths=None)
    nix.probes["crumbly"] = broken
    _, doc = run_json(capsys, "try", "crumbly", "--config", str(config))
    assert doc["error"]["code"] == "build-failed"
    assert doc["error"]["commands"][0] == "NIXPKGS_ALLOW_BROKEN=1 nixin try crumbly --impure"


def test_build_failed(nix, config, capsys):
    nix.build_fails["multi"] = BUILD_FAILED_STDERR
    rc, doc = run_json(capsys, "try", "multi", "--config", str(config))
    assert rc == 1 and doc["error"]["code"] == "build-failed"
    assert doc["error"]["message"].startswith("error: builder for")
    assert "nix log" in doc["error"]["message"]


def test_network(nix, config, capsys):
    nix.build_fails["multi"] = NETWORK_STDERR
    _, doc = run_json(capsys, "try", "multi", "--config", str(config))
    assert doc["error"]["code"] == "network"
    nix.probes["hello"] = NETWORK_STDERR
    _, doc = run_json(capsys, "try", "hello", "--nixpkgs", "github:nixos/nixpkgs/nixos-unstable")
    assert doc["error"]["code"] == "network"


@pytest.mark.parametrize(
    "argv",
    [
        ["try"],
        ["try", "bad..attr"],
        ["try", "cowsay", "--shell", "--", "moo"],
        ["try", "cowsay", "--shell", "--program", "cowsay"],
    ],
)
def test_usage(nix, config, capsys, argv):
    rc, doc = run_json(capsys, *argv, "--config", str(config))
    assert rc == 2 and doc["error"]["code"] == "usage" and doc["verb"] == "try"


def test_args_after_double_dash_are_the_programs(nix, config, capsys):
    """--json before and after the verb, and --json after `--` is an ARG."""
    _, doc = run_json(
        capsys, "try", "cowsay", "--json", "--config", str(config), "--", "--json", "-n"
    )
    assert doc["argv"][-3:] == ["--", "--json", "-n"]


# --- human mode: the handoff --------------------------------------------------------------------


def test_human_mode_hands_the_terminal_to_nix_run(nix, config, execs, capsys, monkeypatch):
    monkeypatch.setenv(
        "QT_PLUGIN_PATH",
        "/nix/store/qq-qtbase/lib/qt-6/plugins:/run/current-system/sw/lib/qt-6/plugins",
    )
    rc = cli.main(["try", "cowsay", "--config", str(config), "--", "moo"])
    _, err = capsys.readouterr()
    assert rc == 0  # only because EXEC is faked: really nixin is gone by now
    assert execs and os.path.basename(execs[0][0]) == "nix"
    assert execs[0][1] == ["nix", "run", f"{REF}#cowsay", "--", "moo"]
    assert execs[0][2]["QT_PLUGIN_PATH"] == "/run/current-system/sw/lib/qt-6/plugins"
    assert f"nixin try: nix run {REF}#cowsay -- moo" in err
    assert "fetching cowsay" in err  # not in the store yet
    assert "tee" in {s for _, s in nix.calls}, "stderr is not a terminal here: nix's lines are teed"


def test_human_mode_on_a_terminal_lets_nix_draw(nix, config, execs, capsys, monkeypatch):
    monkeypatch.setattr(runner, "stderr_is_terminal", lambda: True)
    cli.main(["try", "gnome-calculator", "--config", str(config)])
    capsys.readouterr()
    assert [s for a, s in nix.calls if a[1] == "build"] == ["inherit"]
    assert execs[0][1] == ["nix", "run", f"{REF}#gnome-calculator"]


def test_shell_mode(nix, config, execs, capsys):
    cli.main(["try", "cowsay", "--shell", "--config", str(config)])
    _, err = capsys.readouterr()
    assert execs[0][1] == ["nix", "shell", f"{REF}#cowsay"]
    assert "a shell with cowsay 3.8.4 on PATH" in err and "exit leaves it" in err


def test_build_only_human(nix, config, execs, capsys):
    nix.present.add("cowsay")
    rc = cli.main(["try", "cowsay", "--build-only", "--config", str(config)])
    out, _ = capsys.readouterr()
    assert rc == 0 and execs == []
    assert out.startswith("cowsay 3.8.4: Program which generates ASCII pictures")
    assert "already in the store" in out and "cowsay (runs), cowthink" in out
    assert f"run       nix run {REF}#cowsay" in out
    assert "garbage collection" in out


def test_human_error_goes_to_stderr(nix, config, execs, capsys):
    rc = cli.main(["try", "steam", "--config", str(config)])
    out, err = capsys.readouterr()
    assert rc == 1 and out == "" and execs == []
    assert err.startswith("nixin try: steam is unfree")


# --- pieces -------------------------------------------------------------------------------------


def test_fetched_parses_nix_summary():
    assert build.fetched(FETCH_STDERR)["paths"] == 3
    one = build.fetched("this path will be fetched (10.5 KiB download, 40.1 KiB unpacked):\n")
    assert one == {"paths": 1, "download": 10752, "unpacked": 41062, "built": 0}
    assert build.fetched(BUILD_FAILED_STDERR)["built"] == 1
    assert build.fetched("these 12 derivations will be built:\n")["built"] == 12


def test_desktop_parsing():
    e = build.parse_desktop(
        "[Desktop Entry]\nName=A\nName[de]=B\nExec=a %U\n[Desktop Action x]\nExec=b\n"
    )
    assert e == {"Name": "A", "Name[de]": "B", "Exec": "a %U"}
    assert build.exec_program("/nix/store/x-a/bin/app --flag %U") == "app"
    assert build.exec_program("env FOO=1 BAR=2 app %F") == "app"
    assert build.exec_program(None) is None


def test_suggest():
    names = ["cowsay", "xcowsay", "cow", "neo-cowsay", "cowpy", "coreutils"]
    assert nixpkgs.suggest("cowsy", names)[:2] == ["cowsay", "xcowsay"]
    assert nixpkgs.suggest("python3Packages.reqests", ["requests", "grequests"]) == [
        "python3Packages.requests",
        "python3Packages.grequests",
    ]


def test_child_env_drops_the_wrapper():
    env = {
        "PATH": "/nix/store/py-python3/bin:/nix/store/nf-nixfmt/bin:/run/wrappers/bin:/nix/store/later/bin",
        "QT_PLUGIN_PATH": "/nix/store/q-qtbase/lib/qt-6/plugins",
        "NIXPKGS_QT6_QML_IMPORT_PATH": "/nix/store/q-qtdecl/lib/qt-6/qml",
        "QML2_IMPORT_PATH": "/home/me/.nix-profile/lib/qt-6/qml",
        "PYTHONNOUSERSITE": "true",
        "HOME": "/home/me",
    }
    e = launch.child_env(env)
    assert e["PATH"] == "/run/wrappers/bin:/nix/store/later/bin"
    assert "QT_PLUGIN_PATH" not in e and "NIXPKGS_QT6_QML_IMPORT_PATH" not in e
    assert e["QML2_IMPORT_PATH"] == "/home/me/.nix-profile/lib/qt-6/qml"
    assert "PYTHONNOUSERSITE" not in e and e["HOME"] == "/home/me"


def test_terminal_choice(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for exe in ("xterm", "kitty"):
        (bin_ / exe).write_text("#!/bin/sh\n")
        (bin_ / exe).chmod(0o755)
    assert launch.terminal({"PATH": str(bin_)}) == ["kitty"]
    assert launch.terminal({"PATH": str(bin_), "TERMINAL": "foot"}) == ["foot"]
    assert launch.terminal({"PATH": str(bin_), "TERMINAL": "myterm --class x"}) == [
        "myterm",
        "--class",
        "x",
        "-e",
    ]
    assert launch.terminal({"PATH": str(tmp_path / "empty")}) is None
    argv = launch.in_terminal("nix run x#cowsay", hold=True, name="cowsay", env={"PATH": str(bin_)})
    assert argv[:3] == ["kitty", "sh", "-c"] and argv[-1] == "cowsay"
    assert argv[3].startswith("nix run x#cowsay\n") and "read -r _" in argv[3]
    assert launch.in_terminal("nix shell x#a", hold=False, env={"PATH": str(bin_)}) == [
        "kitty",
        "sh",
        "-c",
        "nix shell x#a",
    ]
    with pytest.raises(LookupError):
        launch.in_terminal("x", hold=False, env={"PATH": str(tmp_path / "empty")})


def test_detached_uses_its_own_session(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    seen = []
    monkeypatch.setattr(launch, "SPAWN", lambda argv, *, log: seen.append((argv, log)) or 4242)
    r = launch.detached(["/nix/store/x-calc/bin/calc"], name="gnome-calculator")
    assert r == {
        "pid": 4242,
        "argv": ["/nix/store/x-calc/bin/calc"],
        "log": str(tmp_path / "nixin" / "try" / "gnome-calculator.log"),
    }
    assert seen[0][1] == tmp_path / "nixin" / "try" / "gnome-calculator.log"


def test_spawn_really_detaches(tmp_path):
    """The real _spawn: a new session (setsid), output to the log."""
    log = tmp_path / "out.log"
    pid = launch._spawn(
        ["sh", "-c", 'echo "sid=$(ps -o sid= -p $$ 2>/dev/null || echo none)"'], log=log
    )
    os.waitpid(pid, 0)
    assert log.read_text().startswith("sid=")


def test_size_rendering():
    assert render.size(None) == "?"
    assert render.size(512) == "512 B"
    assert render.size(3 << 20) == "3.0 MiB"


# --- live ---------------------------------------------------------------------------------------


@live
def test_live_try_cowsay_json(capsys):
    rc, doc = run_json(capsys, "try", "cowsay", "--", "moo")
    assert rc == 0, doc
    assert doc["program"] == "cowsay" and "cowthink" in doc["programs"]
    assert doc["command"].startswith("nix run github:") and doc["command"].endswith(
        "#cowsay -- moo"
    )
    assert os.path.exists(doc["exec"][0])


@live
def test_live_not_found(capsys):
    _, doc = run_json(capsys, "try", "cowsy")
    assert doc["error"]["code"] == "not-found" and "cowsay" in doc["error"]["suggestions"]
