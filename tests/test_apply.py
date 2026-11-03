"""The apply layer offline: plan, apply, sync and status's pending block.

`Machine` is a whole host in a temp dir: a real git workspace and a real git clone as the
authoritative tree, a bare repo as the hub, and fakes for everything else behind
`proc.RUN`: nix (flake metadata, the hosts eval, the toplevel drvPath eval, build,
diff-closures), sudo (strips `-n`, or refuses), nixos-rebuild (builds the authoritative
tree the same fake way and moves the profile links), nix-env --switch-generation and
switch-to-configuration (rollback), systemctl. The toplevel's out path is a hash of the
tracked tree minus installer/history.jsonl, so "same content, same out path" holds as
with real nix, and root's build of the pulled commit has to land on the out path the
workspace build produced.

Each toplevel is a directory like a real one, as far as nixin reads it: `kernel`,
`initrd` and `kernel-modules` links into the fake store (the kernel release comes from a
tracked `KERNEL` file, default 7.2.12; a specialisation `nvidia` runs `SPEC_KERNEL`,
default 6.12.114), `kernel-params`, `nixos-version`, `boot.json`, and a
`bin/switch-to-configuration` that names an installer script with the systemd-boot
builder's `CONFIGURATION_LIMIT` (`self.boot_limit`). /boot's free space is
`self.boot_free`.

Needs git (package.nix has it in nativeCheckInputs); skipped without it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import FIXTURES

from nixin import apply as ap
from nixin.apply import build, closure, commitmsg, privilege, proc, safety, system
from nixin.apply.git import Git
from nixin.cli import main
from nixin.configrepo import ConfigRepo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

HOST = "testhost"
HISTORY = "installer/history.jsonl"
STATE = {"version": 1, "nixos": {HOST: {"packages": ["cowsay"], "options": {}}}, "home": {}}


def git(path: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True)
    return p.stdout.strip()


class Machine:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.ws = tmp / "ws"
        self.auth = tmp / "etc-nixos"
        self.hub = tmp / "hub.git"
        self.store = tmp / "store"
        self.store.mkdir()
        self.calls: list[list[str]] = []
        self.sudo_ok = True
        self.rebuild_rc = 0
        self.rebuild_salt = ""  # root's build lands elsewhere (verify-failed)
        self.fail_build = False
        self.activate_rc = 0
        self.boot_limit: int | None = 2
        self.boot_free = 10**9
        self.closures: dict[str, set[str]] = {}
        self.meta: dict[str, dict[str, str]] = {}
        # the workspace: a flake with one host
        (self.ws / "hosts" / HOST).mkdir(parents=True)
        (self.ws / "flake.nix").write_text("{ outputs = _: { }; }\n")
        (self.ws / "hosts" / HOST / "default.nix").write_text("{ ... }: { imports = [ ]; }\n")
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.name", "Test Person")
        git(self.ws, "config", "user.email", "test@example.org")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "initial")
        subprocess.run(["git", "clone", "-q", str(self.ws), str(self.auth)], check=True)
        git(self.auth, "config", "user.name", "Root")
        git(self.auth, "config", "user.email", "root@example.org")
        # the system profile, at generation 1 = the initial tree
        self.profiles = tmp / "profiles"
        self.profiles.mkdir()
        self.current = tmp / "current-system"
        self.booted = tmp / "booted-system"
        out = self.realise(self.out_of(self.auth))
        self.set_generation(out, switch=True)
        self.booted.symlink_to(out)

    # --- the fake nix -----------------------------------------------------------------

    def tracked(self, repo: Path) -> dict[str, bytes]:
        files = git(repo, "ls-files", "-z").split("\0")
        out = {}
        for f in files:
            p = repo / f
            if f and p.is_file():
                out[f] = p.read_bytes()
        return out

    def _hash(self, files: dict[str, bytes], salt: str = "") -> str:
        h = hashlib.sha256(salt.encode())
        for name in sorted(files):
            h.update(name.encode() + b"\0" + files[name] + b"\0")
        return h.hexdigest()[:32]

    def source_hash(self, repo: Path) -> str:
        return self._hash(self.tracked(repo))

    def out_of(self, repo: Path, salt: str = "") -> str:
        files = {k: v for k, v in self.tracked(repo).items() if k != HISTORY}
        h = self._hash(files, salt)
        state = json.loads(files.get("installer/state.json", b"{}") or b"{}")
        pkgs = set(((state.get("nixos") or {}).get(HOST) or {}).get("packages") or [])
        out = str(self.store / f"{h}-nixos-system-{HOST}")
        self.closures[out] = pkgs
        text = {k: v.decode().strip() for k, v in files.items() if k in ("KERNEL", "SPEC_KERNEL")}
        self.meta[out] = {
            "kernel": text.get("KERNEL", "7.2.12"),
            "spec_kernel": text.get("SPEC_KERNEL", "6.12.114"),
        }
        return out

    def _store_file(self, name: str, rel: str, size: int) -> Path:
        h = hashlib.sha256(name.encode()).hexdigest()[:32]
        f = self.store / f"{h}-{name}" / rel
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * size)
        return f

    def kernel_files(self, ver: str) -> tuple[Path, Path]:
        """The fake kernel (1000 B per major version digit) and initrd (3000 B) of a release."""
        k = self._store_file(f"linux-{ver}", "bzImage", 1000 * int(ver.split(".")[0]))
        i = self._store_file(f"initrd-linux-{ver}", "initrd", 3000)
        return k, i

    def _boot_entry(self, top: Path, ver: str) -> None:
        k, i = self.kernel_files(ver)
        (top / "kernel").symlink_to(k)
        (top / "initrd").symlink_to(i)
        mods = self.store / (hashlib.sha256(ver.encode()).hexdigest()[:32] + f"-linux-{ver}-mod")
        (mods / "lib" / "modules" / ver).mkdir(parents=True, exist_ok=True)
        (top / "kernel-modules").symlink_to(mods)
        (top / "kernel-params").write_text("loglevel=4")
        (top / "nixos-version").write_text("26.11.20261023.3ec6363")
        spec = {"kernel": str(k), "initrd": str(i), "init": f"{top}/init", "toplevel": str(top)}
        (top / "boot.json").write_text(json.dumps({"org.nixos.bootspec.v1": spec}))

    def realise(self, out: str) -> str:
        top = Path(out)
        if top.exists():
            return out
        meta = self.meta[out]
        top.mkdir(parents=True)
        self._boot_entry(top, meta["kernel"])
        spec = top / "specialisation" / "nvidia"
        spec.mkdir(parents=True)
        self._boot_entry(spec, meta["spec_kernel"])
        limit = "" if self.boot_limit is None else f'CONFIGURATION_LIMIT = int("{self.boot_limit}")'
        h = hashlib.sha256(limit.encode()).hexdigest()[:32]
        installer = self.store / f"{h}-install-systemd-boot.sh"
        installer.write_text(f"#!/bin/sh\n# systemd-boot builder\n{limit}\n")
        (top / "bin").mkdir()
        (top / "bin" / "switch-to-configuration").write_text(f"exec {installer} $@\n")
        return out

    def count(self, *prefix: str) -> int:
        return sum(1 for c in self.calls if c[: len(prefix)] == list(prefix))

    def toplevel_evals(self) -> int:
        return sum(1 for c in self.calls if c[:3] == ["nix", "eval", "--raw"])

    def nix(self, argv: list[str]) -> proc.Proc:
        args = [a for a in argv[1:] if a not in ("--option", "warn-dirty", "false")]
        if args[:2] == ["flake", "metadata"]:
            repo = Path(args[-1])
            meta = {"path": f"/nix/store/{self.source_hash(repo)}-source", "lastModified": 0}
            meta["fingerprint"] = "fp-" + self.source_hash(repo) + git(repo, "rev-parse", "HEAD")
            return proc.Proc(argv, 0, json.dumps(meta), "")
        if args[:2] == ["eval", "--json"] and "builtins.attrNames" in args:
            return proc.Proc(argv, 0, json.dumps([HOST, "other"]), "")
        if args[:2] == ["eval", "--raw"] and args[2].endswith(".drvPath"):
            repo = Path(args[2].split("#", 1)[0])
            if (repo / "BROKEN").exists():
                return proc.Proc(argv, 1, "", "error: undefined variable 'oops'\n       at /x:1:1")
            out = self.out_of(repo)
            return proc.Proc(argv, 0, out.replace("-nixos-system", "-nixos-sys") + ".drv", "")
        if args[:1] == ["build"]:
            drv = args[-1].removesuffix("^out")
            if self.fail_build:
                err = (
                    "these 2 derivations will be built:\n"
                    f"error: builder for '{drv}' failed with exit code 2;\n"
                    "       last 2 log lines:\n       > make: *** [all] Error 2\n"
                    "error: 1 dependencies of derivation 'x.drv' failed to build\n"
                )
                return proc.Proc(argv, 1, "", err)
            out = drv.removesuffix(".drv").replace("-nixos-sys", "-nixos-system")
            return proc.Proc(argv, 0, self.realise(out) + "\n", "building…\n")
        if args[:2] == ["store", "diff-closures"]:
            old, new = self.closures.get(args[2], set()), self.closures.get(args[3], set())
            lines = [f"{p}: ∅ → 3.8.4, \x1b[31;1m68.2 KiB\x1b[0m" for p in sorted(new - old)]
            lines += [f"{p}: 3.8.4 → ∅, \x1b[32;1m-68.2 KiB\x1b[0m" for p in sorted(old - new)]
            return proc.Proc(argv, 0, "".join(ln + "\n" for ln in lines), "")
        raise AssertionError(f"unexpected nix call {argv}")

    # --- the system profile -----------------------------------------------------------

    def generation(self) -> int:
        return int(os.readlink(self.profiles / "system").split("-")[1])

    def set_generation(self, out: str, *, switch: bool) -> int:
        links = [p for p in self.profiles.iterdir() if p.name.endswith("-link")]
        n = len(links) + 1
        (self.profiles / f"system-{n}-link").symlink_to(out)
        tmp = self.profiles / "system.tmp"
        tmp.symlink_to(f"system-{n}-link")
        os.replace(tmp, self.profiles / "system")
        if switch:
            tmp = self.tmp / "current.tmp"
            tmp.symlink_to(out)
            os.replace(tmp, self.current)
        return n

    def switch_generation(self, argv: list[str]) -> proc.Proc:
        assert argv[1:3] == ["--profile", str(self.profiles / "system")], argv
        n = int(argv[4])
        if not (self.profiles / f"system-{n}-link").exists():
            return proc.Proc(argv, 1, "", f"error: generation {n} does not exist\n")
        tmp = self.profiles / "system.tmp"
        tmp.symlink_to(f"system-{n}-link")
        os.replace(tmp, self.profiles / "system")
        return proc.Proc(argv, 0, "", f"switching profile from version ? to {n}\n")

    def activate(self, argv: list[str]) -> proc.Proc:
        out = str(Path(argv[0]).parent.parent)
        assert argv[1] == "switch" and os.path.isdir(out)
        tmp = self.tmp / "current.tmp"
        tmp.symlink_to(out)
        os.replace(tmp, self.current)
        if self.activate_rc:
            return proc.Proc(argv, self.activate_rc, "", "warning: the following units failed\n")
        return proc.Proc(argv, 0, "", "activating the configuration...\n")

    def rebuild(self, argv: list[str]) -> proc.Proc:
        mode, flake = argv[1], argv[3]
        repo, host = flake.split("#")
        assert host == HOST
        if self.rebuild_rc:
            return proc.Proc(argv, self.rebuild_rc, "", "error: activation script failed\n")
        out = self.realise(self.out_of(Path(repo), self.rebuild_salt))
        self.set_generation(out, switch=mode == "switch")
        return proc.Proc(argv, 0, f"{out}\n", "Done. The new configuration is\n")

    # --- the runner -------------------------------------------------------------------

    def __call__(self, argv: list[str], **kw) -> proc.Proc:
        self.calls.append(list(argv))
        if argv[0] == "git":
            return proc.run_process(argv)
        if argv[0] == "sudo":
            if not self.sudo_ok:
                return proc.Proc(argv, 1, "", "sudo: a password is required\n")
            assert argv[1] == "-n", argv
            return self(argv[2:], **kw)
        if argv[0] == "nix":
            return self.nix(argv)
        if argv[0] == "nixos-rebuild":
            return self.rebuild(argv)
        if argv[0] == "nix-env":
            return self.switch_generation(argv)
        if argv[0].endswith("/bin/switch-to-configuration"):
            return self.activate(argv)
        if argv[0] == "systemctl":
            return proc.Proc(argv, 3, "inactive\n", "")
        if argv == ["true"]:  # sudo -n true, --dry-run's probe
            return proc.Proc(argv, 0, "", "")
        raise AssertionError(f"unexpected command {argv}")

    # --- what the writer would do -----------------------------------------------------

    def add_cowsay(self) -> None:
        inst = self.ws / "installer"
        inst.mkdir(exist_ok=True)
        (inst / "state.json").write_text(json.dumps(STATE, indent=2) + "\n")
        (inst / f"{HOST}.nix").write_text("# generated by nixin\n{ pkgs, ... }: { }\n")
        mod = self.ws / "hosts" / HOST / "default.nix"
        mod.write_text("{ ... }: { imports = [ ../../installer/testhost.nix ]; }\n")
        git(self.ws, "add", "--intent-to-add", "installer/state.json", f"installer/{HOST}.nix")

    def rm_cowsay(self) -> None:
        (self.ws / "installer" / "state.json").unlink()
        (self.ws / "installer" / f"{HOST}.nix").unlink()
        mod = self.ws / "hosts" / HOST / "default.nix"
        mod.write_text("{ ... }: { imports = [ ]; }\n")


@pytest.fixture
def m(tmp_path, monkeypatch, cache_dir):
    machine = Machine(tmp_path)
    monkeypatch.setattr(proc, "RUN", machine)
    monkeypatch.setattr(system, "CURRENT", str(machine.current))
    monkeypatch.setattr(system, "BOOTED", str(machine.booted))
    monkeypatch.setattr(system, "PROFILE", str(machine.profiles / "system"))
    monkeypatch.setattr(safety, "STORE", str(machine.store))
    monkeypatch.setattr(safety, "disk_free", lambda path: (machine.boot_free, 10**10))
    monkeypatch.setattr(ap, "local_hostname", lambda: HOST)
    monkeypatch.setattr(privilege, "helper_installed", lambda: False)  # no nixos-switch
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(machine.auth))
    monkeypatch.delenv("NIXIN_CONFIG", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # no signing hooks etc. from ~
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return machine


def run(capsys, m: Machine, *argv: str, json_: bool = True, host: str = HOST):
    """main() under --json (the document, envelope checked) or not (stdout; exit 0 asserted)."""
    full = (["--json"] if json_ else []) + ["--host", host, *argv, "--config", str(m.ws)]
    rc = main(full)
    out, err = capsys.readouterr()
    if not json_:
        assert rc == 0, err
        return out
    assert err == ""
    doc = json.loads(out)
    assert doc["verb"] == argv[0] and doc["host"] == host
    assert "message" not in doc
    assert rc == (0 if doc["ok"] else 2 if doc["error"]["code"] == "usage" else 1)
    return doc


# --- plan ---------------------------------------------------------------------------------


def test_plan_of_the_running_tree_is_up_to_date_and_cached(m, capsys):
    d = run(capsys, m, "plan")
    assert d["ok"]
    assert d["out"] == os.path.realpath(m.current) and d["up_to_date"] is True
    assert d["pending_commits"] == [] and d["authoritative"]["relation"] == "same"
    assert d["diff"] == {"added": [], "removed": [], "changed": [], "size_delta": 0}
    assert d["running"]["generation"] == 1 and d["cached"] is False
    assert d["workspace"]["branch"] == "main" and d["workspace"]["dirty"] == []
    builds = m.count("nix", "build")
    d2 = run(capsys, m, "plan")
    assert d2["cached"] is True and d2["out"] == d["out"]
    assert m.count("nix", "build") == builds  # no second build


def test_plan_shows_a_pending_change(m, capsys):
    m.add_cowsay()
    d = run(capsys, m, "plan")
    assert d["ok"] and d["up_to_date"] is False
    assert d["out"] != os.path.realpath(m.current)
    assert [a["name"] for a in d["diff"]["added"]] == ["cowsay"]
    assert d["diff"]["added"][0] == {"name": "cowsay", "version": "3.8.4", "size": 69837}
    assert set(d["workspace"]["dirty"]) == {
        "installer/state.json",
        f"installer/{HOST}.nix",
        f"hosts/{HOST}/default.nix",
    }
    assert d["timings"]["eval_s"] >= 0 and "build_s" in d["timings"]


def test_plan_human_output(m, capsys):
    m.add_cowsay()
    text = run(capsys, m, "plan", json_=False)
    assert f"plan {HOST}:" in text and "+ " in text and "cowsay 3.8.4" in text
    assert "next: nixin apply" in text


def test_plan_errors(m, capsys):
    (m.ws / "BROKEN").write_text("x")
    git(m.ws, "add", "BROKEN")
    d = run(capsys, m, "plan")
    assert d["error"]["code"] == "eval-failed" and "undefined variable" in d["error"]["message"]
    (m.ws / "BROKEN").unlink()
    git(m.ws, "rm", "-q", "--cached", "BROKEN")
    m.fail_build = True
    m.add_cowsay()
    d = run(capsys, m, "plan")
    assert d["error"]["code"] == "build-failed"
    assert d["error"]["message"].startswith("error: builder for")
    d = run(capsys, m, "plan", host="nope")
    assert d["error"]["code"] == "unknown-host"


def test_plan_refuses_non_workspaces(m, capsys, tmp_path):
    assert main(["--json", "plan", "--config", "/etc/nixos"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not-a-workspace"
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "flake.nix").write_text("{ }")
    assert main(["--json", "plan", "--config", str(plain)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not-a-workspace"
    git(m.ws, "checkout", "-q", "--detach")
    d = run(capsys, m, "plan")
    assert d["error"]["code"] == "not-a-workspace" and "detached" in d["error"]["message"]
    d = run(capsys, m, "plan", "extra")
    assert d["error"]["code"] == "usage"


# --- apply --------------------------------------------------------------------------------


def test_add_plan_apply_rm_apply(m, capsys):
    gen0 = os.path.realpath(m.current)
    m.add_cowsay()
    planned = run(capsys, m, "plan")
    builds = m.count("nix", "build")
    d = run(capsys, m, "apply")
    assert d["ok"], d
    # committed with the workspace's identity and a generated message
    assert d["committed"] == git(m.ws, "rev-parse", "HEAD")
    assert git(m.ws, "log", "-1", "--format=%an <%ae>") == "Test Person <test@example.org>"
    assert git(m.ws, "log", "-1", "--format=%s") == f"nixin: {HOST}: +cowsay"
    assert d["commit_message"] == f"nixin: {HOST}: +cowsay"
    # no rebuild: plan's entry (source hash) survives the commit
    assert m.count("nix", "build") == builds and d["cached"] is True
    # root pulled it and switched; the running system is what plan built
    assert git(m.auth, "rev-parse", "HEAD") == d["committed"] == d["authoritative_head"]
    assert os.path.realpath(m.current) == d["out"] == planned["out"]
    assert d["generation"] == 2 == m.generation() and d["switched"] is True
    assert d["privileged_commands"] == [
        f"sudo -n git -C {m.auth} pull --ff-only {m.ws} main",
        f"sudo -n nixos-rebuild switch --flake {m.auth}#{HOST}",
    ]
    assert d["backends"] == {"pull": "sudo", "switch": "sudo"}
    assert d["switch_log"].endswith(f"Done. The new configuration is\n{d['out']}")
    assert [a["name"] for a in d["diff"]["added"]] == ["cowsay"]
    # one history line, not committed yet
    lines = (m.ws / HISTORY).read_text().splitlines()
    assert len(lines) == 1
    h = json.loads(lines[0])
    assert {k: h[k] for k in ("host", "out", "generation", "commit", "message", "mode")} == {
        "host": HOST,
        "out": d["out"],
        "generation": 2,
        "commit": d["committed"],
        "message": f"nixin: {HOST}: +cowsay",
        "mode": "switch",
    }
    assert HISTORY in Git(m.ws).status().untracked

    # nothing changed: refused without evaluating anything
    evals = m.toplevel_evals()
    d2 = run(capsys, m, "apply")
    assert d2["error"]["code"] == "nothing-to-apply"
    assert m.toplevel_evals() == evals

    # rm, plan, then apply with -m: the history line goes in with it, and plan's build
    # (made while history.jsonl was still untracked) is reused
    m.rm_cowsay()
    run(capsys, m, "plan")
    builds, evals = m.count("nix", "build"), m.toplevel_evals()
    d3 = run(capsys, m, "apply", "-m", "test: no cowsay")
    assert d3["ok"], d3
    assert d3["cached"] is True
    assert (m.count("nix", "build"), m.toplevel_evals()) == (builds, evals)
    assert git(m.ws, "log", "-1", "--format=%s") == "test: no cowsay"
    files = git(m.ws, "show", "--name-status", "--format=", "HEAD").splitlines()
    assert sorted(files) == sorted(
        [
            f"M\thosts/{HOST}/default.nix",
            f"A\t{HISTORY}",
            f"D\tinstaller/{HOST}.nix",
            "D\tinstaller/state.json",
        ]
    )
    assert d3["out"] == gen0 and d3["generation"] == 3  # same tree content, same out path
    assert [r["name"] for r in d3["diff"]["removed"]] == ["cowsay"]
    assert len((m.ws / HISTORY).read_text().splitlines()) == 2

    # history.jsonl is tracked and dirty now; still nothing to apply, still no eval
    assert Git(m.ws).status().dirty == [HISTORY]
    evals = m.toplevel_evals()
    d4 = run(capsys, m, "apply")
    assert d4["error"]["code"] == "nothing-to-apply"
    assert m.toplevel_evals() == evals
    p = run(capsys, m, "plan")
    assert p["up_to_date"] is True and p["cached"] is True


def test_history_in_a_new_installer_dir_is_committed_next_time(m, capsys):
    """A hand edit, no installer/ yet: git shows the new dir, not the history file."""
    (m.ws / "flake.nix").write_text("{ outputs = _: { edited = true; }; }\n")
    d = run(capsys, m, "apply")
    assert d["ok"] and d["commit_message"] == "nixin: update flake.nix"
    assert Git(m.ws).status().untracked == ["installer/"]
    (m.ws / "flake.nix").write_text("{ outputs = _: { edited = 2; }; }\n")
    d = run(capsys, m, "apply")
    assert d["ok"]
    assert git(m.ws, "show", "--name-only", "--format=", "HEAD").split() == ["flake.nix", HISTORY]


def test_apply_dry_run_runs_nothing(m, capsys):
    m.add_cowsay()
    head = git(m.ws, "rev-parse", "HEAD")
    d = run(capsys, m, "apply", "--dry-run")
    assert d["ok"] and d["dry_run"] is True
    steps = [c["step"] for c in d["commands"]]
    assert steps == ["commit", "commit", "build", "pull", "switch"]
    assert d["commands"][-1]["command"] == f"sudo -n nixos-rebuild switch --flake {m.auth}#{HOST}"
    assert d["commands"][-1]["by_hand"] == f"sudo nixos-rebuild switch --flake {m.auth}#{HOST}"
    assert d["commit"]["message"].startswith(f"nixin: {HOST}: +cowsay\n")
    assert d["out"] is None and d["privilege"]["pull"] == {
        "backend": "sudo",
        "ready": True,
        "why": None,
    }
    assert git(m.ws, "rev-parse", "HEAD") == head
    assert m.count("nix", "build") == 0 and m.count("nixos-rebuild") == 0
    assert not any("pull" in c for c in m.calls) and not any("commit" in c for c in m.calls)
    text = run(capsys, m, "apply", "--dry-run", json_=False)
    assert "[root]" in text and f"sudo -n git -C {m.auth} pull" in text
    assert "nothing was run" in text


def test_needs_sudo_names_the_commands_and_a_retry_finishes(m, capsys):
    m.add_cowsay()
    m.sudo_ok = False
    d = run(capsys, m, "apply")
    e = d["error"]
    assert e["code"] == "needs-sudo"
    assert e["commands"] == [
        f"sudo git -C {m.auth} pull --ff-only {m.ws} main",
        f"sudo nixos-rebuild switch --flake {m.auth}#{HOST}",
    ]
    assert all(c in e["message"] for c in e["commands"])
    assert e["committed"] == git(m.ws, "rev-parse", "HEAD") and e["out"]
    assert git(m.auth, "rev-parse", "HEAD") != e["committed"]  # root did nothing
    m.sudo_ok = True
    builds = m.count("nix", "build")
    d = run(capsys, m, "apply")
    assert d["ok"] and d["committed"] is None and d["out"] == e["out"]
    assert m.count("nix", "build") == builds


def test_apply_refusals(m, capsys):
    d = run(capsys, m, "apply", host="other")  # another host: over ssh, never under --json
    assert d["error"]["code"] == "needs-tty"
    # a commit made in the authoritative tree itself
    (m.auth / "flake.nix").write_text("{ outputs = _: { x = 1; }; }\n")
    git(m.auth, "commit", "-qam", "edited as root")
    m.add_cowsay()
    d = run(capsys, m, "apply")
    assert d["error"]["code"] == "authoritative-diverged"
    assert "fetch" in d["error"]["message"] and d["error"]["state"] == "unknown"
    git(m.auth, "reset", "-q", "--hard", "HEAD~1")
    # uncommitted edits there
    (m.auth / "flake.nix").write_text("{ outputs = _: { y = 1; }; }\n")
    d = run(capsys, m, "apply")
    assert d["error"]["code"] == "authoritative-dirty" and d["error"]["files"] == ["flake.nix"]
    git(m.auth, "checkout", "-q", "--", ".")
    shutil.rmtree(m.auth)
    d = run(capsys, m, "apply")
    assert d["error"]["code"] == "authoritative-missing"


def test_apply_when_the_workspace_is_behind(m, capsys):
    m.add_cowsay()
    git(m.ws, "add", "-A")
    git(m.ws, "commit", "-qm", "cowsay")
    d = run(capsys, m, "apply")
    assert d["ok"]
    git(m.ws, "reset", "-q", "--hard", "HEAD~1")  # workspace now behind the authoritative tree
    d = run(capsys, m, "apply")
    assert d["error"]["code"] == "authoritative-diverged" and d["error"]["state"] == "behind"


def test_switch_failure_and_verify_failure(m, capsys):
    m.add_cowsay()
    m.rebuild_rc = 1
    d = run(capsys, m, "apply")
    e = d["error"]
    assert e["code"] == "switch-failed" and "activation script failed" in e["message"]
    assert e["authoritative_head"] == git(m.ws, "rev-parse", "HEAD")
    assert len(e["privileged_commands"]) == 2
    assert m.generation() == 1
    m.rebuild_rc = 0
    m.rebuild_salt = "impure"
    d = run(capsys, m, "apply")  # nothing new in git, but running != out: re-switch
    assert d["error"]["code"] == "verify-failed"
    m.rebuild_salt = ""
    d = run(capsys, m, "apply")
    assert d["ok"] and d["committed"] is None


def test_no_switch_is_nixos_rebuild_boot(m, capsys):
    before = os.path.realpath(m.current)
    m.add_cowsay()
    d = run(capsys, m, "apply", "--no-switch")
    assert d["ok"] and d["switched"] is False and d["mode"] == "boot"
    assert d["privileged_commands"][-1].endswith(f"nixos-rebuild boot --flake {m.auth}#{HOST}")
    assert os.path.realpath(m.current) == before
    assert os.path.realpath(m.profiles / "system") == d["out"] and d["generation"] == 2


def test_apply_human_output(m, capsys):
    m.add_cowsay()
    text = run(capsys, m, "apply", json_=False)
    assert f"apply {HOST}: switched to generation 2" in text
    assert "[root]" in text and f"sudo -n git -C {m.auth} pull" in text


def test_apply_busy(m, capsys, cache_dir):
    import fcntl

    lock = cache_dir / "apply" / "apply.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        d = run(capsys, m, "apply")
    assert d["error"]["code"] == "busy"


# --- sync ---------------------------------------------------------------------------------


def test_sync_against_a_local_bare_remote(m, capsys, tmp_path):
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(m.hub)], check=True)
    git(m.ws, "remote", "add", "test", str(m.hub))
    d = run(capsys, m, "sync", "--remote", "test")
    assert d["ok"] and d["remote"] == {"name": "test", "url": str(m.hub), "head": None}
    assert d["workspace"]["ahead"] is None
    d = run(capsys, m, "sync", "--remote", "test", "--push")
    head = git(m.ws, "rev-parse", "HEAD")
    assert d["done"] == ["fetch", "push"] and d["remote"]["head"] == head
    assert (d["workspace"]["ahead"], d["workspace"]["behind"]) == (0, 0)
    assert d["authoritative"] == {
        "path": str(m.auth),
        "head": head,
        "relation": "same",
        "behind_workspace": 0,
    }
    # someone else pushes to the hub
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(m.hub), str(other)], check=True)
    (other / "README").write_text("hi\n")
    git(other, "add", "README")
    git(other, "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-qm", "readme")
    git(other, "push", "-q", "origin", "main")
    d = run(capsys, m, "sync", "--remote", "test")
    assert (d["workspace"]["ahead"], d["workspace"]["behind"]) == (0, 1)
    d = run(capsys, m, "sync", "--remote", "test", "--pull")
    assert d["done"] == ["fetch", "pull"] and d["workspace"]["behind"] == 0
    assert d["authoritative"]["behind_workspace"] == 1
    # diverge: one commit on each side
    (m.ws / "a").write_text("a")
    git(m.ws, "add", "a")
    git(m.ws, "commit", "-qm", "a")
    (other / "b").write_text("b")
    git(other, "add", "b")
    git(other, "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-qm", "b")
    git(other, "pull", "-q", "--rebase", "origin", "main")
    git(other, "push", "-q", "origin", "main")
    d = run(capsys, m, "sync", "--remote", "test", "--pull")
    assert d["error"]["code"] == "diverged"
    d = run(capsys, m, "sync", "--remote", "test", "--push")
    assert d["error"]["code"] == "diverged"
    d = run(capsys, m, "sync", "--remote", "nope")
    assert d["error"]["code"] == "usage"
    text = run(capsys, m, "sync", "--remote", "test", json_=False)
    assert "1 ahead, 1 behind test/main" in text


# --- status's pending block ---------------------------------------------------------------


def test_pending(m, capsys):
    repo = ConfigRepo(m.ws)
    p = ap.pending(repo, HOST)
    assert p["workspace"] == str(m.ws) and p["commits"] == [] and p["dirty"] == []
    assert p["running_matches"] is None  # nothing in plan's cache yet
    m.add_cowsay()
    git(m.ws, "add", "-A")
    git(m.ws, "commit", "-qm", "cowsay")
    (m.ws / "flake.nix").write_text("{ outputs = _: { z = 1; }; }\n")
    p = ap.pending(repo, HOST)
    assert [c["subject"] for c in p["commits"]] == ["cowsay"] and p["dirty"] == ["flake.nix"]
    git(m.ws, "checkout", "-q", "--", "flake.nix")
    d = run(capsys, m, "apply")
    p = ap.pending(repo, HOST)
    assert p["commits"] == [] and p["built"] == d["out"] and p["running_matches"] is True
    assert ap.pending(ConfigRepo(m.auth), HOST)["workspace"] is None


def test_annotate_status_adds_a_line(m):
    doc = {"ok": True, "message": "mbp13 evaluates\n"}
    ap.annotate_status(doc, ConfigRepo(m.ws), HOST)
    assert doc["pending"]["commits"] == [] and doc["message"].endswith("commits none")
    bad = {"ok": False}
    ap.annotate_status(bad, ConfigRepo(m.ws), HOST)
    assert "pending" not in bad


# --- the privilege backends ---------------------------------------------------------------


class FakeHelperRun:
    """proc.RUN for the nixos-switch backend: the helper writes its files and returns."""

    def __init__(self, d: Path, rc: int = 0, busy: bool = False, write: bool = True) -> None:
        self.d, self.rc, self.busy, self.write = d, rc, busy, write
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[0] == "systemctl":
            return proc.Proc(argv, 0 if self.busy else 3, "", "")
        assert argv == ["nixos-switch"]
        if self.write:
            self.d.mkdir(parents=True, exist_ok=True)
            if self.rc == 0:
                (self.d / "log").write_text("activating the configuration...\n")
            else:
                (self.d / "auth").write_text("sudo: 3 incorrect password attempts\n")
            (self.d / "status").write_text(f"{self.rc}\n")
        return proc.Proc(argv, 0, "", "")


def _helper(d: Path, **kw) -> privilege.NixosSwitch:
    t = [0.0]

    def sleep(s):
        t[0] += s

    return privilege.NixosSwitch(state_dir=d, sleep=sleep, clock=lambda: t[0], **kw)


def test_nixos_switch_backend(tmp_path, monkeypatch):
    step = privilege.switch_step(Path("/etc/nixos"), HOST)
    monkeypatch.setattr(proc, "RUN", FakeHelperRun(tmp_path / "ok"))
    b = _helper(tmp_path / "ok")
    assert b.blocked() is None and b.display(step) == "nixos-switch"
    p = b.run(step, stream=False)
    assert p.ok and "activating" in p.err and p.argv == ["nixos-switch"]
    # authentication failed: the log stays empty, the reason is in `auth`
    monkeypatch.setattr(proc, "RUN", FakeHelperRun(tmp_path / "denied", rc=1))
    p = _helper(tmp_path / "denied").run(step, stream=False)
    assert p.rc == 1 and "incorrect password" in p.err
    # no status file ever: give up after `wait`
    monkeypatch.setattr(proc, "RUN", FakeHelperRun(tmp_path / "silent", write=False))
    p = _helper(tmp_path / "silent", wait=10.0).run(step, stream=False)
    assert p.rc == 124 and "no result" in p.err
    # a run is already live: the helper would do nothing, so nixin does not start it
    monkeypatch.setattr(proc, "RUN", FakeHelperRun(tmp_path / "busy", busy=True))
    assert "already in progress" in _helper(tmp_path / "busy").blocked()


def test_backend_selection(monkeypatch):
    sw = privilege.switch_step(Path("/etc/nixos"), HOST)
    boot = privilege.switch_step(Path("/etc/nixos"), HOST, "boot")
    pull = privilege.pull_step(Path("/etc/nixos"), Path("/home/u/nixos"), "main")
    monkeypatch.setattr(privilege, "helper_installed", lambda: True)
    got = privilege.backends([pull, sw], Path("/etc/nixos"), True)
    assert (got["pull"].name, got["switch"].name) == ("sudo", "nixos-switch")
    assert privilege.backends([boot], Path("/etc/nixos"), True)["switch"].name == "sudo"
    assert privilege.backends([sw], Path("/etc/nixos"), False)["switch"].name == "sudo"
    assert privilege.backends([sw], Path("/srv/nixos"), True)["switch"].name == "sudo"
    monkeypatch.setattr(privilege, "helper_installed", lambda: False)
    assert privilege.backends([sw], Path("/etc/nixos"), True)["switch"].name == "sudo"


def test_apply_through_the_helper(m, capsys, monkeypatch, tmp_path):
    """With nixos-switch selected, apply waits for its status file, then verifies."""
    helper_dir = tmp_path / "ns"

    def runner(argv, **kw):
        if argv == ["nixos-switch"]:
            m.calls.append(argv)
            p = m.rebuild(["nixos-rebuild", "switch", "--flake", f"{m.auth}#{HOST}"])
            helper_dir.mkdir(exist_ok=True)
            (helper_dir / "log").write_text(p.err)
            (helper_dir / "status").write_text(f"{p.rc}\n")
            return proc.Proc(argv, 0, "", "")
        return m(argv, **kw)

    monkeypatch.setattr(proc, "RUN", runner)
    monkeypatch.setattr(
        privilege,
        "backends",
        lambda steps, auth, local: {
            "pull": privilege.Sudo(),
            "switch": privilege.NixosSwitch(state_dir=helper_dir, sleep=lambda s: None),
        },
    )
    m.add_cowsay()
    d = run(capsys, m, "apply")
    assert d["ok"] and d["backends"] == {"pull": "sudo", "switch": "nixos-switch"}
    assert d["privileged_commands"][-1] == "nixos-switch"
    assert os.path.realpath(m.current) == d["out"]


def test_sudo_backend_display():
    s = privilege.pull_step(Path("/etc/nixos"), Path("/home/u/my nixos"), "main")
    b = privilege.Sudo()
    assert b.argv(s)[:2] == ["sudo", "-n"]
    assert b.display(s) == "sudo git -C /etc/nixos pull --ff-only '/home/u/my nixos' main"
    assert privilege.needs_password(proc.Proc([], 1, "", "sudo: a password is required"))
    assert not privilege.needs_password(proc.Proc([], 1, "", "fatal: not a git repository"))


# --- pieces -------------------------------------------------------------------------------


def test_diff_closures_parse_real_output():
    text = (FIXTURES / "apply" / "diff-closures-107-109.txt").read_text()
    d = closure.parse(text)
    assert "unparsed" not in d
    assert len(d["added"]) + len(d["removed"]) + len(d["changed"]) == len(text.splitlines())
    rows = {r["name"]: r for r in [*d["added"], *d["removed"], *d["changed"]]}
    assert rows["aalib"] == {"name": "aalib", "version": "1.4rc5", "size": -212992}
    assert rows["akonadi"] == {
        "name": "akonadi",
        "from": None,
        "to": None,
        "size_delta": 273408,
    }
    assert rows["attica"] == {
        "name": "attica",
        "from": "6.30.0",
        "to": "6.31.0",
        "size_delta": None,
    }
    assert rows["breeze"]["from"] == "6.7.5, 6.7.5-qt5" and rows["breeze"]["to"] == (
        "6.8.1, 6.8.1-qt5"
    )
    assert rows["breeze-icons"]["size_delta"] == round(-12.7 * 1024**2)
    added = d["added"][0]
    assert added["name"] == "libstrangle" and added["version"].startswith("0.1.1")


def test_diff_closures_edge_cases():
    assert closure.parse_line("foo: ε → 1.0") == {
        "kind": "changed",
        "name": "foo",
        "from": "",
        "to": "1.0",
        "size_delta": None,
    }
    assert closure.parse_line("garbage") is None
    assert closure.parse("\n")["size_delta"] == 0
    assert closure.human_size(69837) == "+68.2 KiB" and closure.human_size(-5) == "-5 B"
    assert closure.size_bytes("1.5 GiB") == round(1.5 * 1024**3)


def test_commit_messages():
    old = json.dumps({"version": 1, "nixos": {"h": {"packages": ["a"], "options": {"x": "1"}}}})
    new = json.dumps(
        {
            "version": 1,
            "nixos": {"h": {"packages": ["b"], "options": {"x": "2", "y": "true"}}},
            "home": {"u": {"packages": ["bat"], "options": {}}},
        }
    )
    msg = commitmsg.message(old, new, ["installer/state.json", "installer/h.nix"])
    subject, _, body = msg.partition("\n\n")
    assert subject == "nixin: h: +b -a set x = 2 set y = true; u (home): +bat"
    assert "- h: +b" in body and "  installer/h.nix" in body
    assert commitmsg.message(None, None, ["hosts/h/default.nix"]).startswith(
        "nixin: update hosts/h/default.nix\n"
    )
    assert commitmsg.message(None, None, [HISTORY]).startswith("nixin: apply history\n")
    many = [f"m{i}.nix" for i in range(5)]
    assert commitmsg.message(None, None, many).startswith(
        "nixin: update m0.nix, m1.nix, m2.nix (+2"
    )
    long = json.dumps({"nixos": {"h": {"packages": [f"pkg{i}" for i in range(30)]}}})
    assert len(commitmsg.message(None, long, []).splitlines()[0]) == commitmsg.SUBJECT_MAX


def test_git_status_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a").write_text("a\n" * 20)
    (r / "b").write_text("b")
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    git(r, "mv", "a", "c")
    (r / "b").write_text("bb")
    (r / "new").write_text("n")
    git(r, "add", "-N", "new")
    (r / "junk dir").mkdir()
    (r / "junk dir" / "f").write_text("j")
    st = Git(r).status()
    assert st.dirty == ["b", "c", "new"] and st.untracked == ["junk dir/"]


def test_plan_cache_drops_collected_paths(tmp_path):
    c = build.PlanCache(tmp_path)
    out = tmp_path / "store" / "abc-system"
    out.mkdir(parents=True)
    c.put_source("h", "src1", {"out": str(out)})
    assert c.by_source("h", "src1")["out"] == str(out)
    out.rmdir()
    assert c.by_source("h", "src1") is None
    assert c.by_source("h", None) is None and c.by_commit("h", "nope") is None


def test_system_generations(tmp_path, monkeypatch):
    prof = tmp_path / "profiles"
    prof.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for n, t in ((7, a), (8, b), (9, a)):
        (prof / f"system-{n}-link").symlink_to(t)
    (prof / "system").symlink_to("system-9-link")
    (tmp_path / "cur").symlink_to(a)
    monkeypatch.setattr(system, "PROFILE", str(prof / "system"))
    monkeypatch.setattr(system, "CURRENT", str(tmp_path / "cur"))
    r = system.running()
    assert r["generation"] == 9 and r["profile_generation"] == 9 and r["store_path"] == str(a)
    assert system.generation_of(str(b)) == 8 and system.generation_of("/nope") is None


# --- plan/apply: what the new system does to boot -----------------------------------------


def set_kernel(m: Machine, ver: str, name: str = "KERNEL") -> None:
    (m.ws / name).write_text(ver + "\n")
    git(m.ws, "add", "--intent-to-add", name)


def test_plan_same_kernel_needs_no_reboot(m, capsys):
    m.add_cowsay()
    d = run(capsys, m, "plan")
    b = d["boot"]
    assert d["reboot_needed"] is False and b["reasons"] == [] and b["pending_reboot"] is False
    assert b["kernel"] == {
        "changed": False,
        "from": "7.2.12",
        "to": "7.2.12",
        "from_path": b["kernel"]["from_path"],
        "to_path": b["kernel"]["from_path"],
    }
    assert b["boot_space"]["needed"] == 0 and b["boot_space"]["state"] == "ok"
    assert b["boot_space"]["limit"] == 2 and b["boot_space"]["window"] == [1]
    assert [s["state"] for s in b["specialisations"]] == ["same"]
    assert d["warnings"] == []
    text = run(capsys, m, "plan", json_=False)
    assert "reboot" not in text and "/boot" not in text


def test_plan_new_kernel_needs_a_reboot_and_room_on_boot(m, capsys):
    set_kernel(m, "7.3.0")
    m.boot_free = 20000
    d = run(capsys, m, "plan")
    b = d["boot"]
    assert d["reboot_needed"] is True and b["reasons"] == ["kernel", "initrd", "modules"]
    assert (b["kernel"]["from"], b["kernel"]["to"]) == ("7.2.12", "7.3.0")
    s = b["boot_space"]
    k, i = m.kernel_files("7.3.0")
    assert [f["path"] for f in s["files"]] == sorted([str(k), str(i)])
    assert s["needed"] == 7000 + 3000 and s["free"] == 20000 and s["freed"] == 0
    assert s["state"] == "ok" and s["ratio"] == 0.5
    assert any(
        w.startswith("needs a reboot to take effect: kernel 7.2.12 → 7.3.0") for w in d["warnings"]
    )
    text = run(capsys, m, "plan", json_=False)
    assert "reboot    needs a reboot: kernel 7.2.12 → 7.3.0, initrd, kernel-modules" in text
    assert "/boot     +0.0 MiB of 0.0 MiB free (50 %)" in text
    assert "warning   needs a reboot" not in text  # shown once, as the reboot line
    m.boot_free = 12000  # 10000 of 12000: over 80 %
    d = run(capsys, m, "plan")
    assert d["boot"]["boot_space"]["state"] == "warn"
    assert any("/boot is tight" in w for w in d["warnings"])
    m.boot_free = 9000
    d = run(capsys, m, "plan")
    assert d["ok"] and d["boot"]["boot_space"]["state"] == "full"  # plan reports, apply refuses
    assert any("/boot is too small" in w for w in d["warnings"])


def test_boot_space_counts_what_the_bootloader_deletes_first(m, capsys):
    """limit 2: gen 1 (7.2.12) and gen 2 (7.3.0) on /boot; a gen 3 on 8.0.0 keeps 2 and 3,
    so gen 1's files go first and count as room."""
    set_kernel(m, "7.3.0")
    assert run(capsys, m, "apply")["ok"]
    set_kernel(m, "8.0.0")
    m.boot_free = 3000
    d = run(capsys, m, "plan")
    s = d["boot"]["boot_space"]
    assert s["window"] == [1, 2]
    assert s["needed"] == 8000 + 3000 and s["freed"] == 7000 + 3000  # 7.2.12's pair
    assert s["state"] == "warn" and s["ratio"] == round(11000 / 13000, 3)
    m.boot_limit = 0  # no limit: nothing is deleted
    set_kernel(m, "8.0.1")
    d = run(capsys, m, "plan")
    assert d["boot"]["boot_space"]["freed"] == 0 and d["boot"]["boot_space"]["state"] == "full"


def test_apply_refuses_when_the_kernels_do_not_fit(m, capsys):
    set_kernel(m, "7.3.0")
    m.boot_free = 5000
    run(capsys, m, "plan")
    head = git(m.ws, "rev-parse", "HEAD")
    d = run(capsys, m, "apply")
    e = d["error"]
    assert e["code"] == "boot-space" and e["boot_space"]["needed"] == 10000
    assert "configurationLimit" in e["message"] and "linux-7.3.0" in e["message"]
    # plan's cache knew the out path: refused before the commit, nothing ran as root
    assert git(m.ws, "rev-parse", "HEAD") == head and m.count("sudo") == 0
    # without plan's cache it commits and builds first, then refuses before the pull
    (m.ws / "flake.nix").write_text("{ outputs = _: { other = 1; }; }\n")
    d = run(capsys, m, "apply")
    e = d["error"]
    assert e["code"] == "boot-space" and e["committed"] == git(m.ws, "rev-parse", "HEAD")
    assert m.count("sudo") == 0 and m.generation() == 1
    m.boot_free = 10**9
    d = run(capsys, m, "apply")
    assert d["ok"] and d["reboot_needed"] is True and d["boot"]["reasons"][0] == "kernel"


def test_apply_dry_run_reports_the_boot_check(m, capsys):
    set_kernel(m, "7.3.0")
    m.boot_free = 5000
    run(capsys, m, "plan")
    d = run(capsys, m, "apply", "--dry-run")
    assert d["ok"] and d["reboot_needed"] is True
    assert d["boot"]["boot_space"]["state"] == "full"
    text = run(capsys, m, "apply", "--dry-run", json_=False)
    assert "(does not fit: refused)" in text


def test_specialisation_kernel_and_pending_reboot(m, capsys):
    set_kernel(m, "6.12.115", "SPEC_KERNEL")
    d = run(capsys, m, "plan")
    b = d["boot"]
    assert d["reboot_needed"] is False  # the default entry is unchanged
    assert b["specialisations"] == [
        {
            "name": "nvidia",
            "state": "changed",
            "changed": ["kernel", "initrd", "modules"],
            "kernel": {"from": "6.12.114", "to": "6.12.115"},
        }
    ]
    assert b["boot_space"]["needed"] == 6000 + 3000
    assert b["bootloader"]["changed"] is False  # boot.json covers the default entry only here
    text = run(capsys, m, "plan", json_=False)
    assert "specialisation nvidia: kernel 6.12.114 → 6.12.115" in text
    # running something that was not booted
    set_kernel(m, "7.3.0")
    assert run(capsys, m, "apply")["ok"]
    (m.ws / "flake.nix").write_text("{ outputs = _: { z = 1; }; }\n")
    d = run(capsys, m, "plan")
    assert d["boot"]["pending_reboot"] is True and d["reboot_needed"] is False
    assert "pending already" in run(capsys, m, "plan", json_=False)


def test_plan_for_another_host_has_no_boot_check(m, capsys):
    d = run(capsys, m, "plan", host="other")
    assert d["boot"] is None and d["reboot_needed"] is None


def test_boot_window_parsing(tmp_path, monkeypatch):
    store = tmp_path / "nix" / "store"
    monkeypatch.setattr(safety, "STORE", str(store))
    h = "a" * 32
    builder = store / f"{h}-systemd-boot" / "bin" / "systemd-boot"
    builder.parent.mkdir(parents=True)
    builder.write_text('#!/usr/bin/python\nCONFIGURATION_LIMIT = int("5")\n')
    wrapper = store / f"{'b' * 32}-install-systemd-boot.sh"
    wrapper.write_text(f'#!/bin/sh\n{store}/{h}-systemd-boot/bin/systemd-boot "$@"\n')
    top = tmp_path / "top"
    (top / "bin").mkdir(parents=True)
    stc = top / "bin" / "switch-to-configuration"
    stc.write_bytes(b"\x7fELF\x00junk\x00" + str(wrapper).encode() + b"\x00more")
    assert safety.boot_window(str(top)) == 5
    builder.write_text("configurationLimit = 0\n")  # the older builder; 0 is "no limit"
    assert safety.boot_window(str(top)) == 0
    builder.write_text("no limit here\n")
    assert safety.boot_window(str(top)) is None
    assert safety.boot_window(str(tmp_path / "nope")) is None


# --- history ------------------------------------------------------------------------------


def test_history_joins_generations_with_the_apply_lines(m, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "200")
    gen1 = os.path.realpath(m.current)
    m.add_cowsay()
    a = run(capsys, m, "apply", "-m", "test: cowsay")
    m.rm_cowsay()
    b = run(capsys, m, "apply")
    d = run(capsys, m, "history")
    assert d["ok"] and d["total"] == 3 and (d["current"], d["booted"], d["default"]) == (3, 1, 3)
    rows = d["generations"]
    assert [r["generation"] for r in rows] == [3, 2, 1]
    r3, r2, r1 = rows
    assert r3["current"] and r3["default"] and not r3["booted"] and r1["booted"]
    assert r3["store_path"] == gen1 == r1["store_path"]  # rm brought the same tree back
    assert (r3["commit"], r3["message"], r3["mode"], r3["match"]) == (
        b["committed"],
        f"nixin: {HOST}: -cowsay",
        "switch",
        "generation",
    )
    assert r2["commit"] == a["committed"] and r2["message"] == "test: cowsay"
    assert r2["applied_host"] == HOST and len(r2["entries"]) == 1
    assert r1["match"] == "out" and r1["match_generation"] == 3 and r1["entries"] == []
    assert r1["kernel"] == "7.2.12" and r1["nixos_version"] == "26.11.20261023.3ec6363"
    assert r1["date"].endswith("Z")
    assert d["history"] == {"path": str(m.ws / HISTORY), "entries": 2, "unmatched": 0}
    d = run(capsys, m, "history", "--limit", "1")
    assert [r["generation"] for r in d["generations"]] == [3] and d["total"] == 3
    text = run(capsys, m, "history", json_=False)
    lines = text.splitlines()
    assert lines[0] == f"history {HOST}: 3 generations"
    assert lines[2].startswith("*") and f"{b['committed'][:7]}  nixin: {HOST}: -cowsay" in lines[2]
    assert lines[4].startswith(" b ") and lines[4].endswith(f"nixin: {HOST}: -cowsay (= gen 3)")
    assert run(capsys, m, "history", "--limit", "-1")["error"]["code"] == "usage"
    assert run(capsys, m, "history", "x")["error"]["code"] == "usage"
    assert run(capsys, m, "history", host="other")["error"]["code"] == "host-not-local"


def test_history_from_the_authoritative_tree_warns(m, capsys):
    rc = main(["--json", "--host", HOST, "history", "--config", str(m.auth)])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and d["generations"][0]["commit"] is None
    assert any("lags" in w for w in d["warnings"]) and any(
        "no installer" in w for w in d["warnings"]
    )


# --- rollback -----------------------------------------------------------------------------


def test_rollback_round_trip(m, capsys):
    """plan on the clean tree (caches its commit), apply cowsay, roll back: the system is
    generation 1 again and the config is the initial tree again, by a revert commit."""
    first = git(m.ws, "rev-parse", "HEAD")
    gen1 = run(capsys, m, "plan")["out"]
    m.add_cowsay()
    a = run(capsys, m, "apply", "-m", "test: cowsay")
    assert a["ok"] and m.generation() == 2
    d = run(capsys, m, "rollback")
    assert d["ok"], d
    assert (d["generation"], d["from_generation"], d["out"], d["switched"]) == (1, 2, gen1, True)
    assert os.path.realpath(m.current) == gen1 and m.generation() == 1
    c = d["config"]
    assert c["state"] == "reverted" and c["commit"] == first and c["source"] == "plan-cache"
    assert [x["subject"] for x in c["reverted"]] == ["test: cowsay"]
    head = git(m.ws, "rev-parse", "HEAD")
    assert c["revert_commit"] == head == c["authoritative_head"] == git(m.auth, "rev-parse", "HEAD")
    assert git(m.ws, "log", "-1", "--format=%s") == 'Revert "test: cowsay"'
    assert git(m.ws, "log", "-1", "--format=%an") == "Test Person"
    assert "This reverts:" in git(m.ws, "log", "-1", "--format=%b")
    # the tree is the first commit's again, history.jsonl aside (it went in with the apply line)
    assert git(m.ws, "diff", "--stat", first, "HEAD", "--", ".", f":(exclude){HISTORY}") == ""
    assert not (m.ws / "installer" / "state.json").exists()
    assert len(git(m.ws, "show", f"HEAD:{HISTORY}").splitlines()) == 1
    assert Git(m.ws).status().dirty == [HISTORY]
    assert d["privileged_commands"] == [
        f"sudo -n nix-env --profile {m.profiles / 'system'} --switch-generation 1",
        f"sudo -n {gen1}/bin/switch-to-configuration switch",
        f"sudo -n git -C {m.auth} pull --ff-only {m.ws} main",
    ]
    assert d["backends"] == {"profile": "sudo", "activate": "sudo", "pull": "sudo"}
    assert [r["name"] for r in d["diff"]["removed"]] == ["cowsay"]
    line = json.loads((m.ws / HISTORY).read_text().splitlines()[-1])
    assert {k: line[k] for k in ("generation", "out", "commit", "mode", "from_generation")} == {
        "generation": 1,
        "out": gen1,
        "commit": head,
        "mode": "rollback",
        "from_generation": 2,
    }
    assert line["config"] == "reverted" and line["message"] == 'Revert "test: cowsay"'
    # authoritative == running: apply has nothing to do, and status agrees, without nix
    evals = m.toplevel_evals()
    assert run(capsys, m, "apply")["error"]["code"] == "nothing-to-apply"
    assert m.toplevel_evals() == evals
    assert ap.pending(ConfigRepo(m.ws), HOST)["running_matches"] is True
    # history shows both
    h = run(capsys, m, "history")
    r2, r1 = h["generations"]
    assert (r2["commit"], r2["mode"]) == (a["committed"], "switch")
    assert (r1["commit"], r1["mode"], r1["current"]) == (head, "rollback", True)
    # again: generation 1 runs and the config builds it
    assert run(capsys, m, "rollback", "1")["error"]["code"] == "nothing-to-apply"


def test_rollback_and_forward_by_history_lines(m, capsys):
    m.add_cowsay()
    a = run(capsys, m, "apply", "-m", "test: cowsay")
    m.rm_cowsay()
    run(capsys, m, "apply", "-m", "test: no cowsay")
    d = run(capsys, m, "rollback")  # 3 -> 2: cowsay back
    assert d["ok"] and d["generation"] == 2
    c = d["config"]
    assert (c["commit"], c["source"], c["state"]) == (a["committed"], "history", "reverted")
    assert git(m.ws, "log", "-1", "--format=%s") == 'Revert "test: no cowsay"'
    assert (m.ws / "installer" / "state.json").exists()
    assert os.path.realpath(m.current) == a["out"]
    d = run(capsys, m, "rollback", "3")  # and forward again: the -cowsay commit's tree
    assert d["ok"] and d["generation"] == 3 and m.generation() == 3
    assert [x["subject"] for x in d["config"]["reverted"]] == ['Revert "test: no cowsay"']
    assert not (m.ws / "installer" / "state.json").exists()
    assert git(m.ws, "rev-parse", "HEAD") == git(m.auth, "rev-parse", "HEAD")


def test_rollback_without_a_commit_leaves_the_config(m, capsys):
    m.add_cowsay()
    run(capsys, m, "apply")
    head = git(m.ws, "rev-parse", "HEAD")
    d = run(capsys, m, "rollback")
    assert d["ok"] and m.generation() == 1
    c = d["config"]
    assert (c["state"], c["why"], c["commit"], c["revert_commit"]) == (
        "untouched",
        "no-mapping",
        None,
        None,
    )
    assert git(m.ws, "rev-parse", "HEAD") == head == git(m.auth, "rev-parse", "HEAD")
    assert any("the config stays as it is" in w for w in d["warnings"])
    assert len(d["privileged_commands"]) == 2  # no pull
    line = json.loads((m.ws / HISTORY).read_text().splitlines()[-1])
    assert line["commit"] is None and line["config"] == "untouched"
    text = run(capsys, m, "rollback", "2", json_=False)  # back to 2: its apply line names HEAD
    assert "in sync: the authoritative tree already builds it" in text
    assert "generation 1 → 2" in text


def test_rollback_dry_run_runs_nothing(m, capsys):
    run(capsys, m, "plan")
    m.add_cowsay()
    run(capsys, m, "apply")
    head, calls = git(m.ws, "rev-parse", "HEAD"), len(m.calls)
    d = run(capsys, m, "rollback", "--dry-run")
    assert d["ok"] and d["dry_run"] and (d["generation"], d["from_generation"]) == (1, 2)
    steps = [(c["step"], c["privileged"]) for c in d["commands"]]
    assert steps == [
        ("revert", False),  # restore
        ("revert", False),  # add -u
        ("revert", False),  # add history.jsonl, untracked until now
        ("revert", False),  # commit
        ("profile", True),
        ("activate", True),
        ("pull", True),
    ]
    assert d["commands"][4]["by_hand"].startswith("sudo nix-env --profile")
    assert d["commit"]["message"].startswith('Revert "nixin: testhost: +cowsay"')
    assert d["config"]["state"] == "reverted" and d["privilege"]["activate"]["ready"] is True
    assert git(m.ws, "rev-parse", "HEAD") == head and m.generation() == 2
    assert not any(
        c[0] in ("nix-env", "sudo") for c in m.calls[calls:] if c != ["sudo", "-n", "true"]
    )
    text = run(capsys, m, "rollback", "--dry-run", json_=False)
    assert "[user]" in text and "[root]" in text and "nothing was run" in text


def test_rollback_needs_sudo_then_finishes(m, capsys):
    run(capsys, m, "plan")
    m.add_cowsay()
    run(capsys, m, "apply")
    gen1 = os.path.realpath(m.profiles / "system-1-link")
    m.sudo_ok = False
    e = run(capsys, m, "rollback")["error"]
    assert e["code"] == "needs-sudo"
    assert e["commands"] == [
        f"sudo nix-env --profile {m.profiles / 'system'} --switch-generation 1",
        f"sudo {gen1}/bin/switch-to-configuration switch",
        f"sudo git -C {m.auth} pull --ff-only {m.ws} main",
    ]
    assert e["committed"] == git(m.ws, "rev-parse", "HEAD") and m.generation() == 2
    assert "nixin rollback again" in e["message"]
    m.sudo_ok = True
    d = run(capsys, m, "rollback", "1")  # the revert exists: no second commit, just root's part
    assert d["ok"] and d["config"]["revert_commit"] is None and d["config"]["state"] == "reverted"
    assert git(m.auth, "rev-parse", "HEAD") == e["committed"] and m.generation() == 1


def test_rollback_switch_failure_then_config_only(m, capsys):
    run(capsys, m, "plan")
    m.add_cowsay()
    run(capsys, m, "apply")
    m.activate_rc = 4
    e = run(capsys, m, "rollback")["error"]
    assert e["code"] == "switch-failed" and "nixin rollback 1" in e["message"]
    assert "already runs it" in e["message"]
    m.activate_rc = 0
    d = run(capsys, m, "rollback", "1")  # the system is there: only the pull is left
    assert d["ok"] and d["switched"] is False and len(d["privileged_commands"]) == 1
    assert git(m.auth, "rev-parse", "HEAD") == git(m.ws, "rev-parse", "HEAD")


def test_rollback_refusals(m, capsys):
    assert run(capsys, m, "rollback")["error"]["code"] == "unknown-generation"  # only gen 1
    assert run(capsys, m, "rollback", "99")["error"]["code"] == "unknown-generation"
    assert run(capsys, m, "rollback", "abc")["error"]["code"] == "usage"
    assert run(capsys, m, "rollback", "1", "2")["error"]["code"] == "usage"
    assert run(capsys, m, "rollback", "1")["error"]["code"] == "nothing-to-apply"
    assert run(capsys, m, "rollback", "1", host="other")["error"]["code"] == "host-not-local"
    run(capsys, m, "plan")
    m.add_cowsay()
    run(capsys, m, "apply")
    (m.ws / "flake.nix").write_text("{ outputs = _: { wip = 1; }; }\n")
    e = run(capsys, m, "rollback")["error"]
    assert e["code"] == "workspace-dirty" and e["files"] == ["flake.nix"]
    git(m.ws, "commit", "-qam", "not applied")
    e = run(capsys, m, "rollback")["error"]
    assert e["code"] == "workspace-dirty" and [c["subject"] for c in e["commits"]] == [
        "not applied"
    ]
    git(m.ws, "reset", "-q", "--hard", "HEAD~1")
    (m.auth / "flake.nix").write_text("{ outputs = _: { root = 1; }; }\n")
    assert run(capsys, m, "rollback")["error"]["code"] == "authoritative-dirty"
    git(m.auth, "checkout", "-q", "--", ".")
    assert m.generation() == 2 and m.count("nix-env") == 0


def test_rollback_outside_the_boot_window_warns(m, capsys):
    """configurationLimit 1 (the target's own builder decides): gen 2 has no entry once
    gen 3 exists, so a reboot after rolling back to it would start gen 3."""
    m.boot_limit = 1
    set_kernel(m, "7.3.0")
    run(capsys, m, "apply")
    set_kernel(m, "7.3.1")
    run(capsys, m, "apply")
    d = run(capsys, m, "rollback")
    assert d["ok"] and d["generation"] == 2
    assert d["boot"]["boot_entry"] is False and d["reboot_needed"] is True
    assert d["boot"]["boot_space"]["window"] == [3]
    assert any("gets no boot entry" in w for w in d["warnings"])
