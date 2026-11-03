"""generations and gc offline, on apply's `Machine` (test_apply.py) with the store side faked.

`GcMachine` adds what gc asks nix and root: `nix path-info --closure-size`, `nix-store
--gc --print-roots / --print-dead`, `nix-store -qR` and `-q --size`, and as root
`nix-env --delete-generations`, the boot default's `switch-to-configuration boot` and
`nix-collect-garbage`. A system's closure is itself, a shared `base` path, its kernel
files and one path per name in `self.closures[out]`; every store path weighs 100 B
except `base` (5000 B) and the per-system paths (`self.weight`). The store and /boot
report free space that grows when gc frees something.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from test_apply import HOST, Machine, run

from nixin import apply as ap
from nixin.apply import generations, privilege, proc, safety, system

DAY = 86400.0


class GcMachine(Machine):
    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp)
        self.weight: dict[str, int] = {}
        self.dead: list[str] = []
        self.store_free = 10**11
        self.extra_roots: list[str] = []
        self.fail: str | None = None  # "delete" | "bootloader" | "collect" | "path-info"
        self.base = str(self.store / ("b" * 32 + "-base"))

    def own(self, out: str) -> list[str]:
        return [str(self.store / ("p" * 32 + f"-{name}")) for name in sorted(self.closures[out])]

    def requisites(self, p: str) -> set[str]:
        if p.startswith(str(self.store / ("p" * 32))) or p in (self.base, *self.dead):
            return {p}
        if not os.path.isdir(p):
            raise KeyError(p)
        top = Path(p)
        files = {os.path.realpath(top / n) for n in ("kernel", "initrd", "kernel-modules")}
        return {p, self.base, *files, *(self.own(p) if p in self.closures else [])}

    def size(self, p: str) -> int:
        if p == self.base:
            return 5000
        return self.weight.get(p, 100)

    def store_cmd(self, argv: list[str]) -> proc.Proc:
        args = argv[1:]
        if args == ["--gc", "--print-roots"]:
            base = self.profiles
            lines = [
                f'"{base / name}" -> {os.path.realpath(base / name)}'
                for name in sorted(os.listdir(base))
                if name.endswith("-link")
            ]
            lines += [f'"{self.current}" -> {os.path.realpath(self.current)}']
            lines += [f'"{self.booted}" -> {os.path.realpath(self.booted)}']
            lines += [f'"{{censored}}" -> {p}' for p in self.extra_roots]
            return proc.Proc(argv, 0, "\n".join(lines) + "\n", "")
        if args == ["--gc", "--print-dead"]:
            return proc.Proc(argv, 0, "".join(p + "\n" for p in self.dead), "finding roots\n")
        if args[:2] == ["--query", "--requisites"]:
            out: set[str] = set()
            for p in args[2:]:
                try:
                    out |= self.requisites(p)
                except KeyError:
                    return proc.Proc(argv, 1, "", f"error: path '{p}' is not valid\n")
            return proc.Proc(argv, 0, "".join(p + "\n" for p in sorted(out)), "")
        if args[:2] == ["--query", "--size"]:
            return proc.Proc(argv, 0, "".join(f"{self.size(p)}\n" for p in args[2:]), "")
        raise AssertionError(f"unexpected nix-store call {argv}")

    def delete(self, argv: list[str]) -> proc.Proc:
        assert argv[1:4] == ["--profile", str(self.profiles / "system"), "--delete-generations"]
        if self.fail == "delete":
            return proc.Proc(argv, 1, "", "error: cannot lock the profile\n")
        lines = []
        for n in map(int, argv[4:]):
            if n == self.generation():
                return proc.Proc(argv, 1, "", f"error: cannot delete current generation {n}\n")
            (self.profiles / f"system-{n}-link").unlink()
            lines.append(f"removing profile version {n}\n")
        return proc.Proc(argv, 0, "", "".join(lines))

    def collect(self, argv: list[str]) -> proc.Proc:
        assert argv == ["nix-collect-garbage"]
        if self.fail == "collect":
            return proc.Proc(argv, 1, "", "error: getting status of '/nix/store/x': gone\n")
        live: set[str] = set()
        for name in os.listdir(self.profiles):
            if name.endswith("-link"):
                live |= self.requisites(os.path.realpath(self.profiles / name))
        for link in (self.current, self.booted):
            live |= self.requisites(os.path.realpath(link))
        gone = {p for out in self.closures for p in self.own(out)} - live
        gone |= set(self.dead)
        freed = sum(self.size(p) for p in gone)
        self.store_free += freed
        self.dead = []
        text = "".join(f"deleting '{p}'\n" for p in sorted(gone))
        summary = f"{len(gone)} store paths deleted, {freed / 1024**2:.2f} MiB freed\n"
        return proc.Proc(argv, 0, summary, text)

    def __call__(self, argv: list[str], **kw) -> proc.Proc:
        if (
            argv[0] == "sudo"
            and self.sudo_ok
            and argv[2:3] in (["nix-env"],)
            and ("--delete-generations" in argv)
        ):
            self.calls.append(list(argv))
            return self.delete(argv[2:])
        if argv[0] == "sudo" and self.sudo_ok and argv[2:] == ["nix-collect-garbage"]:
            self.calls.append(list(argv))
            return self.collect(argv[2:])
        if argv[0] == "sudo" and self.sudo_ok and argv[-1] == "boot":
            self.calls.append(list(argv))
            assert argv[2] == f"{self.profiles / 'system'}/bin/switch-to-configuration"
            if self.fail == "bootloader":
                return proc.Proc(argv, 1, "", "error: installing the boot loader failed\n")
            self.boot_free += 4000
            return proc.Proc(argv, 0, "", "updating systemd-boot\n")
        if argv[0] == "nix-store":
            self.calls.append(list(argv))
            return self.store_cmd(argv)
        if argv[:3] == ["nix", "path-info", "--closure-size"]:
            self.calls.append(list(argv))
            if self.fail == "path-info":
                return proc.Proc(argv, 1, "", "error: cannot connect to the daemon\n")
            lines = [
                f"{p}\t{sum(self.size(q) for q in self.requisites(p))}" for p in sorted(argv[3:])
            ]
            return proc.Proc(argv, 0, "\n".join(lines) + "\n", "")
        return super().__call__(argv, **kw)

    def add_generations(self, n: int, *, step: float = DAY) -> list[str]:
        """n more generations, each its own system with its own package (`pkg<N>`); the
        newest runs and is the profile's. Every generation is one `step` older than the
        next one (generation 1, the booted one, is the oldest)."""
        outs = []
        for i in range(n):
            gen = len(self.links()) + 1
            out = self.out_of(self.ws, salt=f"gen{gen}")
            self.closures[out] = {f"pkg{gen}"}
            self.realise(out)
            self.set_generation(out, switch=i == n - 1)
            outs.append(out)
        nums = self.links()
        now = time.time()
        for age, k in enumerate(reversed(nums)):
            t = now - age * step - 60
            os.utime(self.profiles / f"system-{k}-link", (t, t), follow_symlinks=False)
        return outs

    def links(self) -> list[int]:
        return sorted(
            int(p.split("-")[1]) for p in os.listdir(self.profiles) if p.endswith("-link")
        )


@pytest.fixture
def g(tmp_path, monkeypatch, cache_dir):
    machine = GcMachine(tmp_path)
    monkeypatch.setattr(proc, "RUN", machine)
    monkeypatch.setattr(system, "CURRENT", str(machine.current))
    monkeypatch.setattr(system, "BOOTED", str(machine.booted))
    monkeypatch.setattr(system, "PROFILE", str(machine.profiles / "system"))
    monkeypatch.setattr(safety, "STORE", str(machine.store))
    monkeypatch.setattr(safety, "BOOT", str(tmp_path / "boot"))

    def disk_free(path: str):
        if path == safety.BOOT:
            return machine.boot_free, 2 * 10**8
        return machine.store_free, 10**12

    monkeypatch.setattr(safety, "disk_free", disk_free)
    monkeypatch.setattr(ap, "local_hostname", lambda: HOST)
    monkeypatch.setattr(privilege, "helper_installed", lambda: False)
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(machine.auth))
    monkeypatch.delenv("NIXIN_CONFIG", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("NIXIN_WIDTH", "200")
    return machine


def gc_run(capsys, *argv: str, json_: bool = True, host: str = HOST):
    """main() for gc (it takes no --config); the document, or the human text."""
    from nixin.cli import main

    rc = main([*(["--json"] if json_ else []), "--host", host, "gc", *argv])
    out, err = capsys.readouterr()
    if not json_:
        assert rc == 0, err
        return out
    assert err == ""
    doc = json.loads(out)
    assert doc["verb"] == "gc" and doc["host"] == host and "message" not in doc
    assert rc == (0 if doc["ok"] else 2 if doc["error"]["code"] == "usage" else 1)
    return doc


def root_calls(m: GcMachine) -> list[list[str]]:
    return [c for c in m.calls if c[0] == "sudo" and c[2:] != ["true"]]


# --- the policy ---------------------------------------------------------------------------


def policy(keep=None, older=None) -> generations.Policy:
    return generations.Policy(keep, older)


def test_policy(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(generations, "_mtime", lambda n: now - (10 - n) * DAY)
    nums = list(range(1, 11))
    guard = {"current": 9, "booted": 2, "default": 10}

    def dropped(p):
        return [n for n, why in generations.decide(nums, p, guard, now).items() if not why]

    assert dropped(policy(keep=3)) == [1, 3, 4, 5, 6, 7]
    why = generations.decide(nums, policy(keep=3), guard, now)
    assert why[9] == ["current", "newest"] and why[2] == ["booted"] and why[8] == ["newest"]
    # younger than 3.5 days: 7..10; 6 ran at the cutoff (nix's rule)
    assert dropped(policy(older=3.5)) == [1, 3, 4, 5]
    assert generations.decide(nums, policy(older=3.5), guard, now)[6] == ["cutoff"]
    # both: kept when either keeps it
    assert dropped(policy(keep=6, older=3.5)) == [1, 3, 4]
    assert dropped(policy(keep=1, older=0)) == [1, 3, 4, 5, 6, 7, 8]
    assert dropped(policy()) == [] and generations.decide(nums, policy(), guard)[5] == ["no-policy"]


def test_span_and_sizes():
    assert generations.span([1, 2, 3, 5, 7, 8]) == "1–3, 5, 7–8"
    assert generations.size_text(3 * 1024**3) == "3.0 GiB" and generations.size_text(None) == "?"


# --- generations ----------------------------------------------------------------------------


def test_generations_lists_sizes_boot_entries_and_what_gc_drops(g, capsys):
    outs = g.add_generations(5)  # gens 1 (booted) .. 6 (current)
    d = run(capsys, g, "generations")
    assert d["ok"] and d["total"] == 6
    assert (d["current"], d["booted"], d["default"]) == (6, 1, 6)
    assert d["policy"] == {"keep": 3, "older_than_days": None}
    assert d["drop"] == [2, 3]  # 4..6 newest, 1 booted
    rows = {r["generation"]: r for r in d["generations"]}
    assert rows[6]["kept_by"] == ["current", "default", "newest"] and not rows[6]["drop"]
    assert rows[1]["kept_by"] == ["booted"] and rows[2]["drop"]
    # closure: the system dir (100) + base (5000) + 3 kernel paths (300) + its package (100)
    assert rows[6]["closure_size"] == 5500 and rows[6]["store_path"] == outs[-1]
    assert d["boot"] == {"source": "configurationLimit", "limit": 2, "generations": [5, 6]}
    assert [r["boot_entry"] for r in d["generations"]] == [True, True, False, False, False, False]
    assert d["sizes"]["measured"] == 6 and d["sizes"]["error"] is None
    assert d["history"]["entries"] == 0 and d["warnings"] == []
    # sizes are cached by store path: the second run asks nix nothing
    before = g.count("nix", "path-info")
    d = run(capsys, g, "generations", "--keep", "1")
    assert g.count("nix", "path-info") == before and d["sizes"]["cached"] == 6
    assert d["drop"] == [2, 3, 4, 5]
    d = run(capsys, g, "generations", "--older-than", "2.5")  # 4..6 younger; 3 at the cutoff
    assert d["drop"] == [2] and d["policy"] == {"keep": None, "older_than_days": 2.5}


def test_generations_human_output_and_history(g, capsys):
    g.add_cowsay()
    applied = run(capsys, g, "apply", "-m", "test: cowsay")
    g.add_generations(3)
    text = run(capsys, g, "generations", json_=False)
    lines = text.splitlines()
    assert lines[0] == f"generations {HOST}: 5; nixin gc --keep 3 would delete 1 (2)"
    assert lines[1].split() == ["gen", "date", "kernel", "closure", "gc", "commit", "message"]
    row2 = next(ln for ln in lines if ln[4:].split()[0:1] == ["2"])
    assert "drop" in row2 and f"{applied['committed'][:7]}  test: cowsay" in row2
    assert lines[2].startswith("*") and "e" in lines[2][:4]
    assert "boot entries: the newest 2 (configurationLimit" in text
    assert "closure sizes overlap" in text


def test_generations_reads_the_boot_entries_when_it_can(g, capsys, tmp_path):
    g.add_generations(3)
    entries = tmp_path / "boot" / "loader" / "entries"
    entries.mkdir(parents=True)
    for name in (
        "nixos-generation-3.conf",
        "nixos-generation-3-specialisation-nvidia.conf",
        "nixos-generation-4.conf",
        "other.conf",
    ):
        (entries / name).write_text("title NixOS\n")
    d = run(capsys, g, "generations")
    assert d["boot"] == {"source": "entries", "limit": 2, "generations": [3, 4]}
    assert {r["generation"] for r in d["generations"] if r["boot_entry"]} == {3, 4}


def test_generations_without_sizes_or_a_bootloader(g, capsys):
    g.fail = "path-info"
    g.boot_limit = None
    g.add_generations(1)
    d = run(capsys, g, "generations")
    assert d["ok"] and all(r["closure_size"] is None for r in d["generations"])
    assert d["boot"]["source"] is None and d["generations"][0]["boot_entry"] is None
    assert any("closure sizes unknown" in w for w in d["warnings"])


def test_generations_errors(g, capsys):
    assert run(capsys, g, "generations", host="other")["error"]["code"] == "host-not-local"
    assert run(capsys, g, "generations", "--keep", "0")["error"]["code"] == "usage"
    assert run(capsys, g, "generations", "x")["error"]["code"] == "usage"


# --- gc --dry-run ---------------------------------------------------------------------------


def test_gc_dry_run_lists_estimates_and_runs_nothing(g, capsys):
    outs = g.add_generations(5)  # 1 booted, 2..6; --keep 2 drops 2, 3, 4
    g.weight = {p: 10_000 * (i + 2) for i, out in enumerate(outs) for p in g.own(out)}
    g.dead = [str(g.store / ("d" * 32 + "-old-source"))]
    g.weight[g.dead[0]] = 777
    g.extra_roots = [g.own(outs[1])[0]]  # gen 3's package is in use by a process
    d = gc_run(capsys, "--keep", "2", "--dry-run")
    assert d["ok"] and d["dry_run"] and root_calls(g) == []
    assert [r["generation"] for r in d["delete"]] == [2, 3, 4]
    assert d["keep"] == [1, 5, 6] and d["protected"] == {"current": 6, "booted": 1, "default": 6}
    assert d["delete"][0]["closure_size"] == 5400 + 20_000
    profile = str(g.profiles / "system")
    assert [c["by_hand"] for c in d["commands"]] == [
        f"sudo nix-env --profile {profile} --delete-generations 2 3 4",
        f"sudo {profile}/bin/switch-to-configuration boot",
        "sudo nix-collect-garbage",
    ]
    assert [c["argv"][:2] for c in d["commands"]] == [["sudo", "-n"]] * 3
    e = d["estimate"]
    # gens 2-4's system paths, gen 2's and gen 4's package (gen 3's is still a root)
    assert e["generations"] == {"paths": 5, "bytes": 300 + 20_000 + 40_000}
    assert e["dead"] == {"paths": 1, "bytes": 777}
    assert e["total"] == {"paths": 6, "bytes": 61_077}
    assert d["store"]["free_before"] == 10**11 and d["store"]["free_after"] is None
    assert d["boot"]["entries_before"] == [5, 6] and d["boot"]["entries_source"] == (
        "configurationLimit"
    )
    assert d["privilege"] == {"backend": "sudo", "ready": True, "why": None}
    text = gc_run(capsys, "--keep", "2", "--dry-run", json_=False)
    assert text.startswith(f"gc {HOST} --dry-run: would delete 3 generation(s) (2–4), keep 3")
    assert "frees     about 59.6 KiB" in text and "garbage already (1 paths)" in text


def test_gc_estimate_skips_a_root_that_went_away(g, capsys):
    g.add_generations(3)
    g.extra_roots = [str(g.store / ("z" * 32 + "-gone"))]  # nix-store -qR: not valid
    d = gc_run(capsys, "--keep", "1", "--dry-run")
    assert d["estimate"]["generations"]["paths"] == 4  # gens 2 and 3: system + package


def test_gc_dry_run_without_a_policy_only_collects(g, capsys):
    g.add_generations(2)
    d = gc_run(capsys, "--dry-run")
    assert d["delete"] == [] and [c["step"] for c in d["commands"]] == ["collect"]
    assert d["estimate"]["generations"] == {"paths": 0, "bytes": 0}
    assert any("neither --keep nor --older-than" in w for w in d["warnings"])


# --- gc -------------------------------------------------------------------------------------


def test_gc_deletes_rewrites_the_boot_menu_and_collects(g, capsys):
    outs = g.add_generations(5)
    g.weight = {p: 2 * 1024**2 for out in outs for p in g.own(out)}
    d = gc_run(capsys, "--keep", "2")
    assert d["ok"] and not d["dry_run"]
    assert d["deleted"] == [2, 3, 4] and g.links() == [1, 5, 6]
    assert d["generations_left"] == 3
    steps = [c[2] for c in root_calls(g)]
    boot = f"{g.profiles / 'system'}/bin/switch-to-configuration"
    assert steps == ["nix-env", boot, "nix-collect-garbage"]
    assert d["privileged_commands"][0].startswith("sudo -n nix-env --profile ")
    assert d["backends"] == {"delete": "sudo", "bootloader": "sudo", "collect": "sudo"}
    text = "3 store paths deleted, 6.00 MiB freed"
    assert d["freed"] == {"paths": 3, "bytes": 6 * 1024**2, "text": text}
    assert d["estimate"]["generations"]["bytes"] == 6 * 1024**2 + 300
    assert d["estimate"]["dead"] is None  # only --dry-run scans for garbage
    assert d["store"]["freed"] == 6 * 1024**2 and d["boot"]["freed"] == 4000
    assert d["boot"]["entries_before"] == [5, 6] == d["boot"]["entries_after"]
    assert set(d["timings"]) == {"prepare", "delete", "bootloader", "collect", "total"}
    # the running, booted and default generations are still there; history still reads
    h = run(capsys, g, "history")
    assert [r["generation"] for r in h["generations"]] == [6, 5, 1]


def test_gc_human_output(g, capsys):
    g.add_generations(3)
    text = gc_run(capsys, "--keep", "1", json_=False)
    lines = text.splitlines()
    assert lines[0] == f"gc {HOST}: deleted 2 generation(s) (2–3), 2 left (1, 4)"
    assert any(ln.startswith("freed ") and "(nix-collect-garbage)" in ln for ln in lines)
    assert any(ln.startswith("took ") for ln in lines)


def test_gc_needs_sudo_then_a_retry_finishes(g, capsys):
    g.add_generations(3)
    g.sudo_ok = False
    d = gc_run(capsys, "--keep", "1")
    e = d["error"]
    assert e["code"] == "needs-sudo" and g.links() == [1, 2, 3, 4]
    profile = g.profiles / "system"
    assert e["commands"] == [
        f"sudo nix-env --profile {profile} --delete-generations 2 3",
        f"sudo {profile}/bin/switch-to-configuration boot",
        "sudo nix-collect-garbage",
    ]
    assert "nixin gc again" in e["message"] and e["deleted"] == [] and e["done"] == []
    g.sudo_ok = True
    d = gc_run(capsys, "--keep", "1")
    assert d["ok"] and d["deleted"] == [2, 3] and g.links() == [1, 4]


@pytest.mark.parametrize("step", ["delete", "bootloader", "collect"])
def test_gc_step_failures(g, capsys, step):
    g.add_generations(3)
    g.fail = step
    e = gc_run(capsys, "--keep", "1")["error"]
    assert e["code"] == "gc-failed" and e["step"] == step
    assert e["deleted"] == ([] if step == "delete" else [2, 3])
    assert len(e["privileged_commands"]) == ["delete", "bootloader", "collect"].index(step) + 1


def test_gc_refusals(g, capsys):
    assert gc_run(capsys, "--keep", "1", host="other")["error"]["code"] == "host-not-local"
    assert gc_run(capsys, "--keep", "0")["error"]["code"] == "usage"
    assert gc_run(capsys, "--older-than", "-1")["error"]["code"] == "usage"
    assert gc_run(capsys, "x")["error"]["code"] == "usage"
    assert root_calls(g) == []


def test_gc_never_deletes_the_running_or_booted_generation(g, capsys):
    """The booted generation is the oldest and the running one is not the newest (a
    rollback): --keep 1 keeps both anyway."""
    g.add_generations(4)  # 1 booted .. 5 running
    run(capsys, g, "rollback", "3")
    assert system.running()["generation"] == 3
    d = gc_run(capsys, "--keep", "1")
    assert d["ok"] and d["deleted"] == [2, 4] and g.links() == [1, 3, 5]
    assert d["protected"] == {"current": 3, "booted": 1, "default": 3}
