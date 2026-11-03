"""`nixin doctor`, offline: every outside command goes through a fake runner (no
systemctl, git remote or ssh in the sandbox); files are tmp files."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nixin import cli, doctor, optindex
from nixin.apply import safety, system

REV_A, REV_B = "a" * 40, "b" * 40


class Runner:
    """Answers argv by the first rule whose words all appear in it, in order of adding."""

    def __init__(self) -> None:
        self.rules: list[tuple[tuple[str, ...], tuple[int, str, str]]] = []
        self.calls: list[list[str]] = []

    def on(self, *words: str, rc: int = 0, out: str = "", err: str = "") -> Runner:
        self.rules.append((words, (rc, out, err)))
        return self

    def __call__(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        self.calls.append(argv)
        for words, answer in self.rules:
            if all(w in argv for w in words):
                return answer
        return 1, "", "no rule"


@pytest.fixture
def ctx(tmp_path):
    ws, auth = tmp_path / "nixos", tmp_path / "etc-nixos"
    ws.mkdir()
    auth.mkdir()
    return doctor.Ctx(
        host="mbp13",
        workspace=str(ws),
        authoritative=str(auth),
        state_dir=tmp_path / "state",
        hosts_file=tmp_path / "hosts.toml",
        run=Runner(),
        boot=str(tmp_path / "boot"),
        home=str(tmp_path / "home"),
    )


def by_name(rows):
    return {r["name"]: r for r in rows}


def lock(rev: str, **extra: str) -> str:
    nodes = {
        "root": {"inputs": {"nixpkgs": "nixpkgs", **{k: k for k in extra}}},
        "nixpkgs": {"locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": rev}},
    }
    for k, v in extra.items():
        nodes[k] = {"locked": {"type": "github", "owner": "x", "repo": k, "rev": v}}
    return json.dumps({"nodes": nodes, "root": "root", "version": 7})


# --- option indexes -----------------------------------------------------------------------


def fake_index(*, host="mbp13", fresh=True, rev=REV_A, number=128):
    return SimpleNamespace(
        host=host,
        header={"nixpkgs": {"revision": rev}, "count": 28042},
        generation={"number": number, "fresh": fresh},
        applies=lambda h, user=None: h == host,
    )


@pytest.mark.parametrize(
    "idx, ok, words",
    [
        (fake_index(), True, "generation 128, 28042 options"),
        (fake_index(fresh=False, number=120), False, "from generation 120, not the running"),
        (fake_index(rev=REV_B), False, "not switched since the lock moved"),
        (fake_index(host="19BK"), False, "built for 19BK, not mbp13"),
    ],
)
def test_index_against_the_running_generation_and_the_lock(ctx, monkeypatch, idx, ok, words):
    Path(ctx.authoritative, "flake.lock").write_text(lock(REV_A))
    monkeypatch.delenv("NIXIN_NO_INDEX", raising=False)
    monkeypatch.setattr(optindex, "load", lambda scope: idx if scope == "nixos" else None)
    monkeypatch.setenv("NIXIN_INDEX_HOME", str(Path(ctx.home) / "absent.json"))
    rows = by_name(doctor.check_indexes(ctx))
    assert rows["index.nixos"]["ok"] is ok and words in rows["index.nixos"]["detail"]
    assert rows["index.nixos"]["fix"] == (None if ok or idx.host != "mbp13" else "nixin apply")
    assert rows["index.home"]["ok"] is None and "absent" in rows["index.home"]["detail"]


def test_index_file_there_but_unreadable_is_a_failure(ctx, monkeypatch, tmp_path):
    bad = tmp_path / "options-nixos.json"
    bad.write_text("not json")
    monkeypatch.delenv("NIXIN_NO_INDEX", raising=False)
    monkeypatch.setenv("NIXIN_INDEX_NIXOS", str(bad))
    monkeypatch.setenv("NIXIN_INDEX_HOME", str(tmp_path / "none.json"))
    rows = by_name(doctor.check_indexes(ctx))
    assert rows["index.nixos"]["ok"] is False


# --- timers -------------------------------------------------------------------------------


def show(**units: dict[str, str]) -> str:
    return "\n\n".join(
        "\n".join([f"Id={uid}", *(f"{k}={v}" for k, v in props.items())])
        for uid, props in units.items()
    )


TIMER_OK = {"LoadState": "loaded", "UnitFileState": "enabled", "ActiveState": "active"}
RUN_OK = {
    "LoadState": "loaded",
    "Result": "success",
    "ExecMainStatus": "0",
    "ExecMainExitTimestamp": "Tue 2026-11-03 09:00:55 PST",
}


def test_timers_enabled_failed_disabled_and_missing(ctx):
    out = show(
        **{
            "nixin-outdated.timer": TIMER_OK,
            "nixin-outdated.service": RUN_OK,
            "nixin-status.timer": dict(TIMER_OK, UnitFileState="disabled", ActiveState="inactive"),
            "nixin-status.service": RUN_OK,
        }
    )
    ctx.run.on("systemctl", "--user", "show", out=out)
    rows = by_name(doctor.check_timers(ctx))
    assert rows["timer.nixin-outdated"]["ok"] is True
    assert "last run Tue 2026-11-03 09:00:55 PST" in rows["timer.nixin-outdated"]["detail"]
    assert rows["timer.nixin-status"]["ok"] is False
    assert rows["timer.nixin-status"]["fix"] == "systemctl --user enable --now nixin-status.timer"

    ctx.run.rules.clear()
    failed = dict(RUN_OK, Result="exit-code", ExecMainStatus="1")
    out = show(
        **{
            "nixin-outdated.timer": TIMER_OK,
            "nixin-outdated.service": failed,
            "nixin-status.timer": {"LoadState": "not-found"},
            "nixin-status.service": {"LoadState": "not-found"},
        }
    )
    ctx.run.on("systemctl", out=out)
    rows = by_name(doctor.check_timers(ctx))
    assert rows["timer.nixin-outdated"]["ok"] is False
    assert rows["timer.nixin-outdated"]["fix"].startswith("journalctl --user -u nixin-outdated")
    assert rows["timer.nixin-status"]["ok"] is None


def test_timers_without_a_user_manager_are_unknown(ctx):
    ctx.run.on("systemctl", rc=1, err="Failed to connect to bus: No medium found")
    rows = doctor.check_timers(ctx)
    assert [r["ok"] for r in rows] == [None, None] and "connect to bus" in rows[0]["detail"]


# --- state files --------------------------------------------------------------------------


def test_state_files_parse_and_summary_age(ctx):
    assert doctor.check_state(ctx)[0]["ok"] is None  # no directory yet
    ctx.state_dir.mkdir()
    summary = {"at": ctx.now - 600, "outdated": {"count": 57}, "pending": {"count": 0}}
    (ctx.state_dir / "summary.json").write_text(json.dumps(summary))
    (ctx.state_dir / "status.json").write_text("{}")
    rows = by_name(doctor.check_state(ctx))
    assert rows["state.files"]["ok"] is True and "2 files parse" in rows["state.files"]["detail"]
    assert rows["state.summary"]["ok"] is True
    assert rows["state.summary"]["detail"] == "10 min old; outdated 57, pending 0"

    (ctx.state_dir / "status.json").write_text("{trunc")
    summary["at"] = ctx.now - 3 * 86400
    (ctx.state_dir / "summary.json").write_text(json.dumps(summary))
    rows = by_name(doctor.check_state(ctx))
    assert rows["state.files"]["ok"] is False and "status.json" in rows["state.files"]["detail"]
    assert rows["state.summary"]["ok"] is False and "3 days old" in rows["state.summary"]["detail"]

    summary.update(at=ctx.now, errors={"lock": {"code": "network", "message": "x", "at": 1}})
    (ctx.state_dir / "summary.json").write_text(json.dumps(summary))
    row = by_name(doctor.check_state(ctx))["state.summary"]
    assert row["ok"] is False and "lock: network" in row["detail"]
    assert row["fix"] == "journalctl --user -u nixin-outdated.service -n 50"


# --- trees --------------------------------------------------------------------------------


def trees(ctx, ws_head=REV_A, auth_head=REV_A, dirty="", ancestor=0, hub=None, **lock_revs):
    r = ctx.run
    r.on(ctx.workspace, "rev-parse", "HEAD", out=ws_head + "\n")
    r.on(ctx.authoritative, "rev-parse", "HEAD", out=auth_head + "\n")
    r.on(ctx.workspace, "status", "--porcelain", out=dirty)
    r.on("merge-base", "--is-ancestor", rc=ancestor)
    r.on("rev-list", "--count", out="2\n")
    r.on("@{u}", out="origin/main\n")
    if hub is not None:
        r.on("ls-remote", "origin", "refs/heads/main", out=f"{hub}\trefs/heads/main\n")
    else:
        r.on("ls-remote", rc=128, err="fatal: Could not resolve host: github.com")
    Path(ctx.workspace, "flake.lock").write_text(lock(REV_A, **lock_revs))
    Path(ctx.authoritative, "flake.lock").write_text(lock(REV_A))
    return by_name(doctor.check_trees(ctx))


def test_trees_in_step(ctx):
    rows = trees(ctx)
    assert rows["tree.dirty"]["ok"] is True
    assert rows["tree.authoritative"]["ok"] is True
    assert rows["tree.lock"]["ok"] is True
    assert rows["tree.hub"]["ok"] is None and "--full" in rows["tree.hub"]["detail"]
    assert not any("ls-remote" in c for c in ctx.run.calls)  # no network without --full
    # /etc/nixos is root's: git needs safe.directory to read it as the user
    assert any(f"safe.directory={ctx.authoritative}" in c for c in ctx.run.calls)


def test_trees_ahead_dirty_and_lock_drift_are_info(ctx):
    rows = trees(ctx, auth_head=REV_B, dirty=" M flake.nix\n?? new.nix\n", home_manager=REV_B)
    assert rows["tree.dirty"]["ok"] is None and "2 uncommitted" in rows["tree.dirty"]["detail"]
    a = rows["tree.authoritative"]
    assert (
        a["ok"] is None
        and "2 commit(s) ahead" in a["detail"]
        and a["fix"].startswith("nixin apply")
    )
    assert rows["tree.lock"]["ok"] is None and "home_manager" in rows["tree.lock"]["detail"]


def test_trees_diverged_authoritative_is_a_failure(ctx):
    rows = trees(ctx, auth_head=REV_B, ancestor=1)
    assert rows["tree.authoritative"]["ok"] is False
    assert "cannot fast-forward" in rows["tree.authoritative"]["detail"]


def test_hub_with_full_equal_offline_and_behind(ctx):
    ctx.full = True
    assert trees(ctx, hub=REV_A)["tree.hub"]["ok"] is True
    ctx.run.rules.clear()
    row = trees(ctx)["tree.hub"]  # ls-remote fails: offline
    assert row["ok"] is None and "unknown" in row["detail"]
    ctx.run.rules.clear()
    row = trees(ctx, hub=REV_B, ancestor=1)["tree.hub"]
    assert row["ok"] is False and row["fix"] == "nixin sync --pull"
    ls = next(c for c in ctx.run.calls if "ls-remote" in c)
    assert any("BatchMode=yes" in a for a in ls)  # never a password prompt


def test_workspace_not_a_checkout(ctx):
    ctx.run.on("rev-parse", rc=128, err="fatal: not a git repository")
    [row] = doctor.check_trees(ctx)
    assert row["name"] == "tree.workspace" and row["ok"] is None


# --- hosts --------------------------------------------------------------------------------


def test_hosts_reachable_in_parallel_only_with_full(ctx):
    assert doctor.check_hosts(ctx) == []  # no hosts.toml: nothing to ask
    ctx.hosts_file.write_text('[mbp13]\n\n["19BK"]\nssh = "brucking@19bk"\n\n[pi]\n')
    rows = by_name(doctor.check_hosts(ctx))
    assert set(rows) == {"host.19BK", "host.pi"}  # not this machine
    assert all(r["ok"] is None for r in rows.values()) and ctx.run.calls == []

    ctx.full = True
    ctx.run.on("ssh", "brucking@19bk", rc=255, err="ssh: connect to host 19bk: timed out")
    ctx.run.on("ssh", "pi", "true")
    rows = by_name(doctor.check_hosts(ctx))
    assert rows["host.pi"]["ok"] is True
    assert rows["host.19BK"]["ok"] is False and "timed out" in rows["host.19BK"]["detail"]
    for c in ctx.run.calls:
        assert c[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def test_hosts_toml_that_does_not_parse(ctx):
    ctx.hosts_file.write_text("[19BK\n")
    [row] = doctor.check_hosts(ctx)
    assert row["ok"] is False and "not valid TOML" in row["detail"]


# --- secrets ------------------------------------------------------------------------------

FLAKE = """{
  outputs = { nixpkgs, ... }: {
    nixosConfigurations."19BK" = nixpkgs.lib.nixosSystem {
      modules = [ ./configuration.nix ./beszel-agent.nix ];
    };
    nixosConfigurations.mbp13 = nixpkgs.lib.nixosSystem {
      modules = [ ./hosts/mbp13 ];
    };
  };
}
"""


def config(ctx, tmp_path) -> dict[str, Path]:
    ws = Path(ctx.workspace)
    present = tmp_path / "etc" / "mbp13-builder"
    present.parent.mkdir()
    present.write_text("key")
    files = {
        "flake.nix": FLAKE,
        "configuration.nix": "{ ... }: { }\n",
        "beszel-agent.nix": '{ services.beszel.agent.environmentFile = "/etc/beszel-agent.env"; }\n',
        "hosts/mbp13/default.nix": "{ imports = [ ./services ../../shared.nix ]; }\n",
        "hosts/mbp13/services/default.nix": (
            "{ config, ... }: {\n"
            '  # environmentFile = "/etc/commented-out.env";\n'
            '  services.x.environmentFile = "${config.home.homeDirectory}/.config/beszel/agent.env";\n'
            "  age.secrets.y.file = ./y.age;\n"
            '  services.y.passwordFile = "/run/agenix/y";\n'
            f'  nix.buildMachines = [ {{ sshKey = "{present}"; }} ];\n'
            "}\n"
        ),
        "shared.nix": '{ sops.age.keyFile = "/var/lib/sops/key.txt"; }\n',
        "unused.nix": '{ x.credentialsFile = "/etc/unused"; }\n',
    }
    for rel, text in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return {"present": present}


def test_secrets_of_this_host_only_with_file_and_line(ctx, tmp_path):
    paths = config(ctx, tmp_path)
    rows = by_name(doctor.check_secrets(ctx))  # git ls-files fails: walks the tree
    home_env = f"{ctx.home}/.config/beszel/agent.env"
    assert set(rows) == {
        f"secret {home_env}",
        f"secret {paths['present']}",
        "secret /var/lib/sops/key.txt",
    }  # not 19BK's beszel env, not a comment, not agenix's /run path, not an unimported file
    miss = rows[f"secret {home_env}"]
    assert miss["ok"] is False and "hosts/mbp13/services/default.nix:3" in miss["detail"]
    assert rows[f"secret {paths['present']}"]["ok"] is True
    assert "shared.nix:1" in rows["secret /var/lib/sops/key.txt"]["detail"]

    ctx.host = "19BK"
    rows = by_name(doctor.check_secrets(ctx))
    assert set(rows) == {"secret /etc/beszel-agent.env"}


def test_secret_in_a_directory_the_user_cannot_read_is_unknown(ctx, tmp_path, monkeypatch):
    config(ctx, tmp_path)
    real = doctor.os.stat

    def stat(p, *a, **k):
        if str(p).startswith("/var/lib/sops"):
            raise PermissionError(13, "Permission denied")
        return real(p, *a, **k)

    monkeypatch.setattr(doctor.os, "stat", stat)
    rows = by_name(doctor.check_secrets(ctx))
    assert rows["secret /var/lib/sops/key.txt"]["ok"] is None


def test_host_files_follow_imports_from_the_flake_block(ctx, tmp_path):
    config(ctx, tmp_path)
    root = Path(ctx.workspace)
    tracked = sorted(str(p.relative_to(root)) for p in root.rglob("*.nix"))
    assert doctor.host_files(root, tracked, "mbp13") == [
        "hosts/mbp13/default.nix",
        "hosts/mbp13/services/default.nix",
        "shared.nix",
    ]
    assert doctor.host_files(root, tracked, "nope") == tracked  # no block: everything


# --- /boot --------------------------------------------------------------------------------


@pytest.fixture
def boot(monkeypatch, tmp_path):
    gens = [(n, str(tmp_path / f"sys-{n}")) for n in (126, 127, 128)]
    sizes = {f"{p}/kernel": 20 << 20 for _n, p in gens} | {
        f"{p}/initrd": 30 << 20 for _n, p in gens
    }
    monkeypatch.setattr(system, "realpath", lambda p: gens[-1][1])
    monkeypatch.setattr(system, "generations", lambda: gens)
    monkeypatch.setattr(
        safety,
        "boot_files",
        lambda out: {f"{out}/kernel": ["default"], f"{out}/initrd": ["default"]},
    )
    monkeypatch.setattr(safety, "_size", lambda p: sizes.get(p, 0))
    state = SimpleNamespace(limit=2, free=(80 << 20, 200 << 20))
    monkeypatch.setattr(safety, "boot_window", lambda out: state.limit)
    monkeypatch.setattr(safety, "disk_free", lambda p: state.free)
    return state


def test_boot_space_and_generations(ctx, boot):
    rows = by_name(doctor.check_boot(ctx))
    assert rows["boot.space"]["ok"] is True and "50 MiB" in rows["boot.space"]["detail"]
    g = rows["boot.generations"]
    assert g["ok"] is None and g["fix"] == "nixin gc --keep 2" and "3 generations" in g["detail"]

    boot.free, boot.limit = (40 << 20, 200 << 20), 5
    rows = by_name(doctor.check_boot(ctx))
    assert rows["boot.space"]["ok"] is False and "would not fit" in rows["boot.space"]["detail"]
    assert rows["boot.generations"]["ok"] is True


def test_boot_not_systemd_boot(ctx, boot):
    boot.limit = None
    [row] = doctor.check_boot(ctx)
    assert row["name"] == "boot" and row["ok"] is None


# --- failed units -------------------------------------------------------------------------


def test_failed_units_are_information(ctx):
    ctx.run.on("--user", "--failed")
    ctx.run.on("--failed", out="beszel-agent.service loaded failed failed Beszel Agent\n")
    rows = by_name(doctor.check_failed(ctx))
    assert rows["units.system"]["ok"] is None
    assert rows["units.system"]["detail"] == "failed: beszel-agent.service"
    assert rows["units.system"]["fix"] == "journalctl -u beszel-agent.service -n 50"
    assert rows["units.user"]["ok"] is True


# --- the document and the verb --------------------------------------------------------------


def test_one_broken_check_does_not_hide_the_others(ctx, monkeypatch):
    def boom(c):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        doctor,
        "CHECKS",
        (
            boom,
            lambda c: [doctor.check("a", True, "fine")],
            lambda c: [doctor.check("b", False, "x", "do y")],
        ),
    )
    d = doctor.doctor(ctx)
    assert d["ok"] is True and d["healthy"] is False and d["failed"] == ["b"]
    assert d["checks"][0] == {
        "name": "boom",
        "ok": None,
        "detail": "the check failed: RuntimeError: boom",
        "fix": None,
    }
    text = doctor.render(d, "mbp13")
    assert "BAD b" in text and "fix: do y" in text and "1 failed" in text


def test_cli_json_envelope_and_host_not_local(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(doctor, "CHECKS", (lambda c: [doctor.check("a", True, "fine")],))
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "mbp13")
    monkeypatch.setattr(doctor.socket, "gethostname", lambda: "mbp13")
    monkeypatch.setattr("nixin.configrepo.local_hostname", lambda: "mbp13")
    monkeypatch.setenv("NIXIN_STATE_DIR", str(tmp_path / "state"))
    t0 = time.monotonic()
    assert cli.main(["--json", "doctor", "--config", str(tmp_path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert time.monotonic() - t0 < 2
    assert doc["verb"] == "doctor" and doc["host"] == "mbp13" and doc["ok"] is True
    assert doc["workspace"] == str(tmp_path) and doc["checks"] == [doctor.check("a", True, "fine")]
    assert not (tmp_path / "state").exists()  # read-only: the state dir is not created

    assert cli.main(["--json", "doctor", "--host", "19BK"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "host-not-local"
