"""apply on another host (apply/remote.py, apply/hosts.py), offline.

The local side is test_apply's `Machine` (workspace, authoritative clone, fake nix) plus a
bare hub. The other host ("other", reached as `otherbox`) is a directory: its own home
with a workspace cloned from the hub, its root-owned clone (`retc`), a profile and a
`current-system` link. The fake `ssh` runs the generated script for real with `sh`, as
the other host's user (HOME there), with a `sudo` that just runs the command (or
refuses) and a `nixos-rebuild` that makes a generation from the authoritative HEAD. So
the script's quoting, its checks and its exit statuses are what is tested.
"""
# ruff: noqa: F811 -- `m` is test_apply's fixture, imported; the tests take it by name

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest
from test_apply import HOST, Machine, git, m, run  # noqa: F401

from nixin import cli
from nixin.apply import hosts, proc, remote
from nixin.configrepo import NixinError

OTHER = "other"

SUDO = """#!/bin/sh
if [ -e "{flags}/sudo-fails" ]; then
  echo "sudo: 3 incorrect password attempts" >&2
  exit 1
fi
exec "$@"
"""

REBUILD = """#!/bin/sh
echo "nixos-rebuild $*" >> "{flags}/rebuild.log"
if [ -e "{flags}/rebuild-fails" ]; then
  echo "error: builder for '/nix/store/x.drv' failed with exit code 1" >&2
  exit 1
fi
flake="$3"
auth="${{flake%#*}}"
rev=$(git -c safe.directory='*' -C "$auth" rev-parse --short=12 HEAD)
top="{store}/$rev-nixos-system-{host}"
mkdir -p "$top"
cur=$(readlink -f "{profiles}/system")
if [ "$cur" != "$top" ]; then
  n=$(ls "{profiles}" | grep -c -- '-link$')
  n=$((n + 1))
  ln -s "$top" "{profiles}/system-$n-link"
  ln -sfn "system-$n-link" "{profiles}/system"
fi
if [ "$1" = switch ]; then ln -sfn "$top" "{current}"; fi
echo "Done. The new configuration is $top"
"""


class Other:
    """The other host, and the runner: ssh to it, everything else to the Machine."""

    def __init__(self, m: Machine, tmp: Path, workspace: str = "~/nixos") -> None:
        self.m = m
        self.home = tmp / "rhome"
        self.etc = tmp / "retc"
        self.flags = tmp / "rflags"
        self.store = tmp / "rstore"
        self.profiles = tmp / "rprofiles"
        self.current = tmp / "rcurrent"
        self.bin = tmp / "rbin"
        for d in (self.home, self.flags, self.store, self.profiles, self.bin):
            d.mkdir()
        self.ws = self.home / workspace.removeprefix("~/")
        self.ssh_calls: list[list[str]] = []
        self.unreachable = False
        self.drop = False  # the connection drops once the switch (step 3/3) has started
        # the hub, with the workspace's history; the workspace tracks it
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(m.hub)], check=True)
        git(m.ws, "remote", "add", "origin", str(m.hub))
        git(m.ws, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(m.hub), str(self.ws)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.ws), str(self.etc)], check=True)
        # generation 1 of the other host
        top = self.store / "initial-nixos-system-other"
        top.mkdir()
        (self.profiles / "system-1-link").symlink_to(top)
        (self.profiles / "system").symlink_to("system-1-link")
        self.current.symlink_to(top)
        fmt = {
            "flags": self.flags,
            "store": self.store,
            "profiles": self.profiles,
            "current": self.current,
            "host": OTHER,
        }
        for name, text in (("sudo", SUDO), ("nixos-rebuild", REBUILD)):
            (self.bin / name).write_text(text.format(**fmt))
            (self.bin / name).chmod(0o755)

    def flag(self, name: str, on: bool = True) -> None:
        f = self.flags / name
        f.touch() if on else f.unlink(missing_ok=True)

    def head(self, repo: Path) -> str:
        return git(repo, "rev-parse", "HEAD")

    def generation(self) -> int:
        return int(os.readlink(self.profiles / "system").split("-")[1])

    def __call__(self, argv: list[str], **kw) -> proc.Proc:
        if argv[0] != "ssh":
            return self.m(argv, **kw)
        self.ssh_calls.append(list(argv))
        assert argv[1:3] == ["-t", "otherbox"] and len(argv) == 4, argv
        assert kw.get("tty") is True and kw.get("stream") == "all", kw
        if self.unreachable:
            return proc.Proc(argv, 255, "", "ssh: connect to host otherbox port 22: No route\n")
        if self.drop:
            log = "".join(f"[nixin] {i}/3 step {i}\n" for i in (1, 2, 3))
            return proc.Proc(argv, 255, "", log + "Connection to otherbox closed by remote host.\n")
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}"}
        p = subprocess.run(
            ["sh", "-c", argv[3]],  # what the login shell on the other host does with it
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return proc.Proc(argv, p.returncode, "", p.stdout)


def hosts_toml(tmp: Path, text: str) -> Path:
    p = tmp / "xdg" / "nixin" / "hosts.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def o(m, tmp_path, monkeypatch):
    other = Other(m, tmp_path)
    hosts_toml(tmp_path, f'[{OTHER}]\nssh = "otherbox"\nauthoritative = "{other.etc}"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(proc, "RUN", other)
    monkeypatch.setattr(remote, "CURRENT", str(other.current))
    monkeypatch.setattr(remote, "PROFILE", str(other.profiles / "system"))
    monkeypatch.setattr(remote, "has_tty", lambda: True)
    return other


def change(m: Machine, text: str = "x") -> None:
    """A tracked edit in the workspace (what the writer would leave for the other host)."""
    (m.ws / "hosts" / HOST / "default.nix").write_text(
        f"{{ ... }}: {{ imports = [ ]; }} # {text}\n"
    )


def human(capsys, m: Machine, *argv: str) -> dict:
    """apply in human mode (a real one refuses --json): the document, with its message."""
    ns = cli._parser().parse_args(["--host", OTHER, *argv, "--config", str(m.ws)])
    ns.json = False
    doc = cli.document(ns)
    capsys.readouterr()
    return doc


def pushes(m: Machine) -> int:
    return sum(1 for c in m.calls if c[:1] == ["git"] and "push" in c)


# --- the verb ---------------------------------------------------------------------------


def test_dry_run_prints_every_command_and_runs_nothing(m, o, capsys):
    change(m)
    head = o.head(m.ws)
    d = run(capsys, m, "apply", "--dry-run", host=OTHER)
    assert d["ok"] and d["dry_run"] and d["needs_tty"]
    assert d["remote"] == {
        "host": OTHER,
        "ssh": "otherbox",
        "workspace": "~/nixos",
        "authoritative": str(o.etc),
        "config": str(Path(os.environ["XDG_CONFIG_HOME"]) / "nixin" / "hosts.toml"),
    }
    steps = [(c["step"], c["where"], c["privileged"]) for c in d["commands"]]
    assert steps == [
        ("commit", "local", False),
        ("commit", "local", False),
        ("push", "local", False),
        ("ssh", "local", False),
        ("workspace-pull", OTHER, False),
        ("authoritative-pull", OTHER, True),
        ("switch", OTHER, True),
    ]
    by = {c["step"]: c["command"] for c in d["commands"]}
    assert by["push"] == f"git -C {m.ws} push --porcelain origin HEAD:refs/heads/main"
    assert by["workspace-pull"] == "git -C ~/nixos pull --ff-only"
    assert by["authoritative-pull"] == f"sudo git -C {o.etc} pull --ff-only ~/nixos main"
    assert by["switch"] == f"sudo nixos-rebuild switch --flake {o.etc}#{OTHER}"
    ssh = next(c for c in d["commands"] if c["step"] == "ssh")["argv"]
    assert ssh[:3] == ["ssh", "-t", "otherbox"] and ssh[3].startswith("sh -c ")
    assert remote.NEW_COMMIT in d["script"]  # the commit does not exist yet
    assert d["commit"]["files"] == [f"hosts/{HOST}/default.nix"]
    assert d["push"]["commits"] == [] and d["push"]["tracking"] == head
    assert d["privilege"]["switch"] == {
        "backend": "ssh+sudo",
        "ready": False,
        "why": d["privilege"]["switch"]["why"],
    }
    assert any("builds its own system" in w for w in d["warnings"])
    # nothing ran: no ssh, no push or fetch, no commit, no build
    assert o.ssh_calls == [] and pushes(m) == 0
    assert not any(c[:1] == ["git"] and "fetch" in c for c in m.calls)
    nix = [c for c in m.calls if c[:1] == ["nix"]]
    assert all("builtins.attrNames" in c for c in nix)  # the host names only: no build
    assert o.head(m.ws) == head and git(m.ws, "status", "--porcelain")
    # clean tree: the script names HEAD itself
    git(m.ws, "checkout", "-q", "--", ".")
    d = run(capsys, m, "apply", "--dry-run", host=OTHER)
    assert d["commit"] is None and head in d["script"] and remote.NEW_COMMIT not in d["script"]
    text = run(capsys, m, "apply", "--dry-run", host=OTHER, json_=False)
    for line in (
        "  [user]    ssh -t otherbox sh -c SCRIPT",
        "  [other]   git -C ~/nixos pull --ff-only",
        f"  [other root] sudo git -C {o.etc} pull --ff-only ~/nixos main",
        f"  [other root] sudo nixos-rebuild switch --flake {o.etc}#{OTHER}",
        "script (sh -c on other):",
        f"    sudo nixos-rebuild switch --flake '{o.etc}#{OTHER}' || exit 25",
    ):
        assert line in text, (line, text)


def test_apply_round_trip(m, o, capsys):
    change(m, "one")
    d = human(capsys, m, "apply", "-m", "other: one")
    assert d["ok"], d
    head = o.head(m.ws)
    assert d["committed"] == head and d["commit_message"] == "other: one"
    assert git(m.ws, "log", "-1", "--format=%s") == "other: one"
    assert o.head(m.hub) == o.head(o.ws) == o.head(o.etc) == head  # pushed, pulled twice
    assert d["pushed"]["remote"] == "origin" and not d["pushed"]["up_to_date"]
    assert [c["subject"] for c in d["pushed"]["commits"]] == ["other: one"]
    assert d["authoritative_head"] == head and d["mode"] == "switch" and d["switched"]
    assert o.generation() == 2 and d["generation"] == 2 and d["changed"]
    assert (
        d["out"] == os.path.realpath(o.current) == str(o.store / f"{head[:12]}-nixos-system-other")
    )
    assert d["previous"]["store_path"] == str(o.store / "initial-nixos-system-other")
    assert d["previous"]["generation"] == 1 and d["running"]["generation"] == 2
    assert d["privileged_commands"] == [
        f"sudo git -C {o.etc} pull --ff-only ~/nixos main",
        f"sudo nixos-rebuild switch --flake {o.etc}#{OTHER}",
    ]
    assert (
        o.flags / "rebuild.log"
    ).read_text() == f"nixos-rebuild switch --flake {o.etc}#{OTHER}\n"
    assert "Done. The new configuration is" in d["switch_log"]
    assert d["diff"] is None and d["boot"] is None and d["history"] is None
    assert not (m.ws / "installer" / "history.jsonl").exists()  # no line for another host
    assert len(o.ssh_calls) == 1 and m.toplevel_evals() == 0  # nothing built here
    assert d["message"].startswith(f"apply {OTHER} (over ssh otherbox): switched to generation 2")
    assert "pushed    origin main: 1 commit(s)" in d["message"]
    # again, nothing new: the push is up to date, the other host stays where it is
    d = human(capsys, m, "apply")
    assert d["ok"] and d["committed"] is None and d["pushed"]["up_to_date"]
    assert (
        not d["changed"] and o.generation() == 2 and "(unchanged: it already was)" in d["message"]
    )
    # --no-switch: nixos-rebuild boot, the profile moves, the running system does not
    change(m, "two")
    d = human(capsys, m, "apply", "--no-switch")
    assert d["ok"] and d["mode"] == "boot" and not d["switched"] and d["generation"] == 3
    assert d["out"] == os.path.realpath(o.profiles / "system") != os.path.realpath(o.current)
    assert "nixos-rebuild boot --flake" in (o.flags / "rebuild.log").read_text()


def test_a_workspace_path_with_a_space(m, tmp_path, monkeypatch, capsys):
    other = Other(m, tmp_path, workspace="~/my nixos")
    hosts_toml(
        tmp_path,
        f'[{OTHER}]\nssh = "otherbox"\nworkspace = "~/my nixos"\nauthoritative = "{other.etc}"\n',
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(proc, "RUN", other)
    monkeypatch.setattr(remote, "CURRENT", str(other.current))
    monkeypatch.setattr(remote, "PROFILE", str(other.profiles / "system"))
    monkeypatch.setattr(remote, "has_tty", lambda: True)
    change(m)
    d = human(capsys, m, "apply")
    assert d["ok"], d
    assert other.head(other.etc) == other.head(m.ws) and other.generation() == 2
    assert (
        d["privileged_commands"][0] == f"sudo git -C {other.etc} pull --ff-only ~/'my nixos' main"
    )


def test_refusals_before_anything_runs(m, o, capsys, monkeypatch):
    change(m)
    head = o.head(m.ws)
    d = run(capsys, m, "apply", host=OTHER)  # --json: no terminal for the remote sudo
    assert d["error"]["code"] == "needs-tty" and "--dry-run" in d["error"]["message"]
    monkeypatch.setattr(remote, "has_tty", lambda: False)  # a UI, or no terminal
    d = human(capsys, m, "apply")
    assert d["error"]["code"] == "needs-tty"
    assert o.head(m.ws) == head and pushes(m) == 0 and o.ssh_calls == []
    monkeypatch.setattr(remote, "has_tty", lambda: True)
    git(m.ws, "remote", "rename", "origin", "hub")
    git(m.ws, "branch", "--unset-upstream")
    d = human(capsys, m, "apply")
    assert d["error"]["code"] == "push-failed" and "no remote 'origin'" in d["error"]["message"]
    assert o.head(m.ws) == head  # checked before the commit
    d = run(capsys, m, "apply", "--dry-run", host=OTHER)
    assert d["error"]["code"] == "push-failed"


def test_push_rejected(m, o, capsys, tmp_path):
    side = tmp_path / "side"
    subprocess.run(["git", "clone", "-q", str(m.hub), str(side)], check=True)
    (side / "README").write_text("hi\n")
    git(side, "add", "README")
    git(side, "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-qm", "readme")
    git(side, "push", "-q", "origin", "main")
    change(m)
    d = human(capsys, m, "apply")
    err = d["error"]
    assert err["code"] == "push-failed" and err["rejected"] is True
    assert "sync --pull" in err["message"] and err["committed"] == o.head(m.ws)
    assert o.ssh_calls == []


def test_remote_failures(m, o, capsys):
    change(m, "a")
    o.unreachable = True
    d = human(capsys, m, "apply")
    err = d["error"]
    assert err["code"] == "ssh-failed" and "No route" in err["message"]
    assert err["committed"] == err["pushed"] == o.head(m.ws) == o.head(m.hub)
    assert err["remote_steps_started"] == [] and err["privileged_commands"] == []
    o.unreachable = False
    # the session was up and dropped during the switch: not "is it reachable?"
    o.drop = True
    err = human(capsys, m, "apply")["error"]
    assert err["code"] == "ssh-failed" and err["remote_steps_started"] == ["1/3", "2/3", "3/3"]
    assert "dropped" in err["message"] and "after step 3/3" in err["message"]
    assert f"pushed {o.head(m.ws)[:7]}" in err["message"] and "reachable" not in err["message"]
    o.drop = False
    # the other host's workspace is on another branch
    git(o.ws, "checkout", "-q", "-b", "elsewhere")
    d = human(capsys, m, "apply")
    err = d["error"]
    assert (err["code"], err["step"]) == ("git-failed", "workspace-pull")
    assert "is not on branch main" in err["message"] and err["committed"] is None
    git(o.ws, "checkout", "-q", "main")
    # root's pull fails (three wrong passwords)
    o.flag("sudo-fails")
    d = human(capsys, m, "apply")
    err = d["error"]
    assert (err["code"], err["step"]) == ("git-failed", "authoritative-pull")
    assert "incorrect password" in err["message"]
    assert err["remote_steps_started"] == ["1/3", "2/3"]
    assert err["privileged_commands"] == [f"sudo git -C {o.etc} pull --ff-only ~/nixos main"]
    assert o.head(o.ws) == o.head(m.ws) != o.head(o.etc)
    o.flag("sudo-fails", False)
    # nixos-rebuild fails: the trees are there, the system is not
    o.flag("rebuild-fails")
    d = human(capsys, m, "apply")
    err = d["error"]
    assert err["code"] == "switch-failed" and "builder for" in err["message"]
    assert len(err["privileged_commands"]) == 2 and o.head(o.etc) == o.head(m.ws)
    assert o.generation() == 1
    o.flag("rebuild-fails", False)
    d = human(capsys, m, "apply")
    assert d["ok"] and o.generation() == 2


def test_the_other_workspace_cannot_fast_forward(m, o, capsys):
    (o.ws / "local.nix").write_text("{ }\n")
    git(o.ws, "add", "local.nix")
    git(o.ws, "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-qm", "local")
    change(m)
    d = human(capsys, m, "apply")
    err = d["error"]
    assert (err["code"], err["step"]) == ("git-failed", "workspace-pull")
    assert "pull --ff-only failed on other" in err["message"]
    assert err["remote_steps_started"] == ["1/3"]


HIST = "installer/history.jsonl"


def _history_line(repo: Path, out: str) -> None:
    with (repo / HIST).open("a") as fh:
        fh.write(json.dumps({"out": out}) + "\n")


def test_this_machines_history_line_is_not_committed_or_pushed(m, o, capsys):
    (m.ws / "installer").mkdir()
    _history_line(m.ws, "/nix/store/a")
    git(m.ws, "add", HIST)
    git(m.ws, "commit", "-qm", "history")
    _history_line(m.ws, "/nix/store/b")  # the line of this machine's last local apply
    d = run(capsys, m, "apply", "--dry-run", host=OTHER)
    assert d["commit"] is None  # a history-only tree has nothing to commit for another host
    change(m)
    d = human(capsys, m, "apply")
    assert (
        d["ok"]
        and git(m.ws, "show", "--name-only", "--format=", "HEAD") == f"hosts/{HOST}/default.nix"
    )
    assert git(m.ws, "status", "--porcelain") == f"M {HIST}"  # still pending, for later


def test_the_other_hosts_own_history_line_gets_a_hint(m, o, capsys):
    (m.ws / "installer").mkdir()
    _history_line(m.ws, "/nix/store/a")
    git(m.ws, "add", HIST)
    git(m.ws, "commit", "-qm", "history")
    git(m.ws, "push", "-q", "origin", "main")
    git(o.ws, "pull", "-q", "--ff-only")
    _history_line(o.ws, "/nix/store/b")  # a local apply on the other host, not committed
    _history_line(m.ws, "/nix/store/c")
    git(m.ws, "commit", "-qam", "nixin: mbp13: +x")  # a local apply here took its line along
    d = human(capsys, m, "apply")
    err = d["error"]
    assert err["code"] == "git-failed" and "there has uncommitted lines" in err["message"]
    assert f"git -C ~/nixos stash push -- {HIST}" in err["message"]


# --- hosts.toml -------------------------------------------------------------------------


def test_hosts_defaults_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert hosts.config_path() == tmp_path / "xdg" / "nixin" / "hosts.toml"
    r = hosts.remote("19BK")  # no file
    assert (r.ssh, r.workspace, r.authoritative, r.source) == (
        "19BK",
        "~/nixos",
        "/etc/nixos",
        None,
    )
    p = hosts_toml(
        tmp_path,
        '[mbp13]\nssh = "mbp13"\nworkspace = "/home/master/nixos"\n\n'
        '[19BK]\nssh = "brucking@19bk"\nport = 22\n',
    )
    r = hosts.remote("mbp13")
    assert (r.ssh, r.workspace, r.source) == ("mbp13", "/home/master/nixos", str(p))
    r = hosts.remote("19BK")  # a bare key that is all digits and letters is fine in TOML
    assert r.ssh == "brucking@19bk" and r.workspace == "~/nixos"
    assert r.warnings and "unknown key(s) in [19BK]: port" in r.warnings[0]
    r = hosts.remote("nas")  # not in the file: defaults, the file named
    assert (r.ssh, r.source) == ("nas", str(p))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")  # ignored, as for the cache
    assert hosts.config_path() == Path.home() / ".config" / "nixin" / "hosts.toml"


@pytest.mark.parametrize(
    "text, what",
    [
        ("[h\n", "not valid TOML"),
        ("h = 1\n", "must be a table"),
        ("[h]\nssh = 3\n", "h.ssh must be a non-empty string"),
        ("[h]\nssh = ''\n", "h.ssh must be a non-empty string"),
        ("[h]\nssh = '-oProxyCommand=x'\n", "not an ssh destination"),
        ("[h]\nssh = 'a b'\n", "not an ssh destination"),
        ("[h]\nworkspace = 'nixos'\n", "give an absolute path"),
        ("[h]\nworkspace = '~Bad/nixos'\n", "give an absolute path"),
        ("[h]\nauthoritative = 'etc/nixos'\n", "give an absolute path"),
    ],
)
def test_hosts_errors(tmp_path, text, what):
    p = hosts_toml(tmp_path, text)
    with pytest.raises(NixinError) as e:
        hosts.remote("h", p)
    assert e.value.code == "invalid-hosts-config" and what in e.value.message


def test_invalid_hosts_config_is_an_error_document(m, capsys, tmp_path, monkeypatch):
    hosts_toml(tmp_path, "[other\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    d = run(capsys, m, "apply", "--dry-run", host=OTHER)
    assert d["error"]["code"] == "invalid-hosts-config"


# --- the pieces -------------------------------------------------------------------------


def test_rpath_keeps_the_tilde_bare():
    assert remote.rpath("~/nixos") == "~/nixos"
    assert remote.rpath("~/my nixos") == "~/'my nixos'"
    assert remote.rpath("~brucking/nixos") == "~brucking/nixos"
    assert remote.rpath("~") == "~"
    assert remote.rpath("/home/a b/nixos") == "'/home/a b/nixos'"


def test_script_has_no_backslashes():
    """zsh, bash and fish all read the quoted script the same only without backslashes."""
    r = hosts.Remote("19BK", "19BK", "~/nixos")
    text = remote.script(r, "main", "a" * 40, "switch")
    assert "\\" not in text and "sudo nixos-rebuild switch --flake '/etc/nixos#19BK'" in text
    assert remote.ssh_argv(r, text)[3] == "sh -c " + __import__("shlex").quote(text)


def test_parse_state_and_reached():
    log = (
        "[nixin] before current=/nix/store/a profile=system-7-link profile_target=/nix/store/a\n"
        "[nixin] 1/3 git -C ~/nixos pull --ff-only\nAlready up to date.\n"
        "[nixin] 2/3 sudo …\n"
        "[nixin] after current=/nix/store/a profile=system-8-link profile_target=/nix/store/b\n"
    )
    assert remote.parse_state(log, "before") == {
        "store_path": "/nix/store/a",
        "generation": 7,
        "profile_generation": 7,
        "profile": "/nix/store/a",
    }
    after = remote.parse_state(log, "after")
    assert after["generation"] is None and after["profile_generation"] == 8  # boot: not running
    assert remote.reached(log) == ["1/3", "2/3"]
    assert remote.parse_state("[nixin] before current= profile= profile_target=\n", "before") == {
        "store_path": None,
        "generation": None,
        "profile_generation": None,
        "profile": None,
    }


def test_tty_runner_tees_prompts_without_a_newline(capfd):
    p = proc.run_process(["sh", "-c", "printf 'one\\r\\n[sudo] password: '; printf ok"], tty=True)
    out, err = capfd.readouterr()
    assert p.ok and out == "" and err == "one\r\n[sudo] password: ok"
    assert p.err == "one\n[sudo] password: ok"
    assert proc.run_process(["no-such-tool-x"], tty=True).missing


def test_has_tty_is_false_off_the_main_thread():
    seen = []
    t = threading.Thread(target=lambda: seen.append(remote.has_tty()))
    t.start()
    t.join()
    assert seen == [False]


def test_json_dry_run_is_one_document(m, o, capsys):
    rc = cli.main(["--json", "--host", OTHER, "apply", "--dry-run", "--config", str(m.ws)])
    out, err = capsys.readouterr()
    assert rc == 0 and err == "" and json.loads(out)["dry_run"]
