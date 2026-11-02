"""The apply layer offline: plan, apply, sync and status's pending block.

`Machine` is a whole host in a temp dir: a real git workspace and a real git clone as the
authoritative tree, a bare repo as the hub, and fakes for everything else behind
`proc.RUN`: nix (flake metadata, the hosts eval, the toplevel drvPath eval, build,
diff-closures), sudo (strips `-n`, or refuses), nixos-rebuild (builds the authoritative
tree the same fake way and moves the profile links), systemctl. The toplevel's out path
is a hash of the tracked tree minus installer/history.jsonl, so "same content, same out
path" holds as with real nix, and root's build of the pulled commit has to land on the
out path the workspace build produced.

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
from nixin.apply import build, closure, commitmsg, privilege, proc, system
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
        self.closures: dict[str, set[str]] = {}
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
        out = self.realise(self.out_of(self.auth))
        self.set_generation(out, switch=True)

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
        return out

    def realise(self, out: str) -> str:
        os.makedirs(out, exist_ok=True)
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

    def rebuild(self, argv: list[str]) -> proc.Proc:
        mode, flake = argv[1], argv[3]
        repo, host = flake.split("#")
        assert host == HOST
        if self.rebuild_rc:
            return proc.Proc(argv, self.rebuild_rc, "", "error: activation script failed\n")
        out = self.realise(self.out_of(Path(repo), self.rebuild_salt))
        self.set_generation(out, switch=mode == "switch")
        return proc.Proc(argv, 0, "", f"Done. The new configuration is {out}\n")

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
    monkeypatch.setattr(system, "PROFILE", str(machine.profiles / "system"))
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
    assert "Done. The new configuration is" in d["switch_log"]
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
    d = run(capsys, m, "apply", host="other")
    assert d["error"]["code"] == "host-not-local"
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
