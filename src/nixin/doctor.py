"""`nixin doctor [--full]`: one read-only health check of this machine.

Each check is `{name, ok, detail, fix}`: `ok` true (fine), false (broken or will break a
switch), null (could not tell, or informational: never a failure); `fix` is the one
command to run, or null. Nothing here writes, prompts or evaluates nix: files are
stat'ed or parsed, git and systemctl only read. Without --full it stays local (well
under 2 s); --full also asks the hub (`git ls-remote`) and the hosts in hosts.toml
(`ssh -o BatchMode=yes true`), each with a short timeout, in parallel.

Every outside command goes through `Ctx.run` so the tests replace it (no systemctl, git
remote or ssh in the sandbox).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .configrepo import DEFAULT_CONFIG, NixinError

VERB = "doctor"
HELP = "read-only health check: option index, timers, trees, hosts, secrets, /boot"

TIMERS = ("nixin-outdated", "nixin-status")
SUMMARY_MAX_AGE = 2 * 86400  # the outdated timer runs daily; two missed days is stale
GIT_TIMEOUT = 5.0
SSH_TIMEOUT = 5  # ConnectTimeout; the process gets a few seconds more
MAX_SSH = 8

# Options whose value is a path the module reads at run time and Nix does not create.
_SECRET_KEY = re.compile(
    r"\b(\w*(?:environmentFile|EnvironmentFile|passwordFile|PasswordFile|keyFile|KeyFile"
    r"|secretFile|SecretFile|credentialsFile|CredentialsFile)|sshKey|identityPaths)\b"
    r"\s*=\s*\[?\s*\"([^\"]+)\""
)
_ENV_FILE = re.compile(r"\"(/etc/[\w.@-]+\.env)\"")
# Made at activation by Nix itself (agenix, sops-nix, the store): not hand-installed.
_PROVIDED = ("/nix/store/", "/run/agenix", "/run/secrets", "/dev/", "/proc/", "/sys/")
_REL = re.compile(r"(?<![\w./-])(\.\.?/[\w./+-]*)")

Runner = Callable[[list[str], float], tuple[int, str, str]]


def _run(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """(exit code, stdout, stderr); 127 when the program is missing, 124 on timeout."""
    env = dict(os.environ, LC_ALL="C", GIT_TERMINAL_PROMPT="0", SYSTEMD_PAGER="")
    try:
        p = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g} s"
    return p.returncode, p.stdout, p.stderr


@dataclass
class Ctx:
    host: str
    workspace: str
    authoritative: str
    state_dir: Path
    hosts_file: Path
    full: bool = False
    run: Runner = _run
    now: float = field(default_factory=time.time)
    boot: str = "/boot"
    home: str = field(default_factory=lambda: os.path.expanduser("~"))


def check(name: str, ok: bool | None, detail: str, fix: str | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60} min"
    if s < 2 * 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


# --- option indexes -----------------------------------------------------------------------


def check_indexes(ctx: Ctx) -> list[dict[str, Any]]:
    """Each index file is there, belongs to the running generation (system) or the active
    Home Manager one, describes this host, and was built from the authoritative lock's
    nixpkgs (a lock moved since the last switch leaves it behind)."""
    from . import optindex
    from .configrepo import parse_lock

    lock_rev = None
    try:
        lock = json.loads((Path(ctx.authoritative) / "flake.lock").read_text())
        li = parse_lock(lock)
        lock_rev = li.rev if li else None
    except (OSError, ValueError):
        pass
    out = []
    for scope in optindex.SCOPES:
        name = f"index.{scope}"
        path = optindex.path_for(scope)
        module = "nixosModules.default" if scope == "nixos" else "homeModules.default"
        if optindex.disabled():
            out.append(check(name, None, f"{path}: NIXIN_NO_INDEX is set"))
            continue
        idx = optindex.load(scope)
        if idx is None:
            if os.path.lexists(path):
                out.append(check(name, False, f"{path}: not a readable nixin-options/1 file"))
            else:
                out.append(check(name, None, f"{path}: absent ({module} is not in use)"))
            continue
        gen = idx.generation
        rev = (idx.header.get("nixpkgs") or {}).get("revision")
        where = f"generation {gen.get('number')}" if gen.get("number") is not None else "?"
        if scope == "nixos" and not idx.applies(ctx.host):
            out.append(check(name, False, f"{path}: built for {idx.host}, not {ctx.host}"))
        elif gen.get("fresh") is False:
            out.append(
                check(
                    name,
                    False,
                    f"{path}: from {where}, not the running one",
                    "nixin apply" if scope == "nixos" else "home-manager switch",
                )
            )
        elif lock_rev and rev and rev != lock_rev:
            out.append(
                check(
                    name,
                    False,
                    f"{path}: nixpkgs {rev[:7]}, the lock in {ctx.authoritative} has "
                    f"{lock_rev[:7]} (not switched since the lock moved)",
                    "nixin apply",
                )
            )
        else:
            out.append(check(name, True, f"{path}: {where}, {idx.header.get('count')} options"))
    return out


# --- the timers and their state files -----------------------------------------------------


def _show_blocks(text: str) -> dict[str, dict[str, str]]:
    """`systemctl show U1 U2 -p …` output: blank-line separated blocks, keyed by Id."""
    units: dict[str, dict[str, str]] = {}
    for block in text.split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if props.get("Id"):
            units[props["Id"]] = props
    return units


def _ts(v: str | None) -> str | None:
    return None if not v or v in ("n/a", "0") else v


def check_timers(ctx: Ctx) -> list[dict[str, Any]]:
    names = [f"{t}.{k}" for t in TIMERS for k in ("timer", "service")]
    props = "Id,LoadState,UnitFileState,ActiveState,Result,ExecMainStatus,ExecMainExitTimestamp,LastTriggerUSec"
    rc, so, se = ctx.run(["systemctl", "--user", "show", *names, "-p", props], 5.0)
    if rc != 0:
        msg = (se.strip().splitlines() or ["no output"])[-1]
        return [check(f"timer.{t}", None, f"systemctl --user show: {msg}") for t in TIMERS]
    units = _show_blocks(so)
    out = []
    for t in TIMERS:
        tm, sv = units.get(f"{t}.timer", {}), units.get(f"{t}.service", {})
        name = f"timer.{t}"
        if tm.get("LoadState") != "loaded":
            out.append(check(name, None, "not installed (the module's timer is off)"))
            continue
        last = _ts(sv.get("ExecMainExitTimestamp")) or _ts(tm.get("LastTriggerUSec"))
        when = f"last run {last}" if last else "not run yet"
        if (
            tm.get("UnitFileState") not in ("enabled", "linked", "static", "generated")
            or tm.get("ActiveState") != "active"
        ):
            out.append(
                check(
                    name,
                    False,
                    f"timer {tm.get('UnitFileState')}/{tm.get('ActiveState')}, {when}",
                    f"systemctl --user enable --now {t}.timer",
                )
            )
        elif sv.get("Result", "success") != "success" or sv.get("ExecMainStatus", "0") != "0":
            out.append(
                check(
                    name,
                    False,
                    f"{when}: {sv.get('Result')} (exit {sv.get('ExecMainStatus')})",
                    f"journalctl --user -u {t}.service -n 50",
                )
            )
        else:
            out.append(check(name, True, f"enabled, {when}: success"))
    return out


def check_state(ctx: Ctx) -> list[dict[str, Any]]:
    d = ctx.state_dir
    try:
        files = sorted(p for p in d.iterdir() if p.suffix == ".json")
    except OSError:
        return [check("state", None, f"{d}: absent (no nixin-state run yet)")]
    bad = []
    summary = None
    for p in files:
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            bad.append(p.name)
            continue
        if p.name == "summary.json":
            summary = doc
    out = []
    if bad:
        out.append(
            check(
                "state.files",
                False,
                f"{d}: {', '.join(bad)} do not parse",
                f"rm {' '.join(str(d / b) for b in bad)}",
            )
        )
    else:
        out.append(check("state.files", True, f"{d}: {len(files)} files parse"))
    if not isinstance(summary, dict):
        if "summary.json" not in bad:
            out.append(check("state.summary", None, "no summary.json yet"))
        return out
    at = summary.get("at")
    if not isinstance(at, (int, float)):
        try:
            at = (d / "summary.json").stat().st_mtime
        except OSError:
            at = None
    errors = summary.get("errors") or {}
    age = ctx.now - at if at else None
    if errors:
        which = ", ".join(f"{k}: {(v or {}).get('code')}" for k, v in sorted(errors.items()))
        unit = "nixin-status" if set(errors) == {"status"} else "nixin-outdated"
        out.append(
            check(
                "state.summary",
                False,
                f"last run failed ({which})",
                f"journalctl --user -u {unit}.service -n 50",
            )
        )
    elif age is not None and age > SUMMARY_MAX_AGE:
        out.append(
            check(
                "state.summary",
                False,
                f"summary.json is {_age(age)} old",
                "systemctl --user start nixin-outdated.service",
            )
        )
    else:
        n = (summary.get("outdated") or {}).get("count")
        p = (summary.get("pending") or {}).get("count")
        when = f"{_age(age)} old" if age is not None else "age unknown"
        out.append(check("state.summary", True, f"{when}; outdated {n}, pending {p}"))
    return out


# --- the trees: workspace, authoritative, hub ---------------------------------------------


def _git(ctx: Ctx, tree: str, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    rc, so, se = ctx.run(
        ["git", "-c", f"safe.directory={tree}", "-C", tree, *args], timeout
    )  # /etc/nixos is root's: git refuses to read it as another user without this
    return rc, (so if rc == 0 else se).strip()


def _lock_inputs(tree: str) -> dict[str, str] | None:
    try:
        nodes = json.loads((Path(tree) / "flake.lock").read_text())["nodes"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {
        k: str((v.get("locked") or {}).get("rev") or (v.get("locked") or {}).get("narHash"))
        for k, v in nodes.items()
        if isinstance(v, dict) and v.get("locked")
    }


def check_trees(ctx: Ctx) -> list[dict[str, Any]]:
    ws, auth = ctx.workspace, ctx.authoritative
    out = []
    rc, ws_head = _git(ctx, ws, "rev-parse", "HEAD")
    if rc != 0:
        return [
            check("tree.workspace", None, f"{ws}: not a git checkout", f"git clone {auth} {ws}")
        ]
    rc, auth_head = _git(ctx, auth, "rev-parse", "HEAD")
    auth_head = auth_head if rc == 0 else None
    rc, dirty = _git(ctx, ws, "status", "--porcelain")
    files = [ln for ln in dirty.splitlines() if ln.strip()] if rc == 0 else []
    if files:
        out.append(
            check(
                "tree.dirty",
                None,
                f"{ws}: {len(files)} uncommitted file(s)",
                f"nixin plan --config {ws}",
            )
        )
    else:
        out.append(check("tree.dirty", True, f"{ws}: clean"))
    if auth_head is None:
        out.append(check("tree.authoritative", False, f"{auth}: not a git checkout"))
    elif auth_head == ws_head:
        out.append(check("tree.authoritative", True, f"{auth} = workspace @ {ws_head[:7]}"))
    else:
        rc, _ = _git(ctx, ws, "merge-base", "--is-ancestor", auth_head, ws_head)
        if rc == 0:
            rc2, n = _git(ctx, ws, "rev-list", "--count", f"{auth_head}..{ws_head}")
            out.append(
                check(
                    "tree.authoritative",
                    None,
                    f"workspace is {n if rc2 == 0 else 'some'} commit(s) ahead of {auth} "
                    f"({auth_head[:7]})",
                    f"nixin apply --config {ws}",
                )
            )
        elif rc == 1:
            out.append(
                check(
                    "tree.authoritative",
                    False,
                    f"{auth} @ {auth_head[:7]} is not in the workspace's history "
                    f"({ws_head[:7]}): apply cannot fast-forward it",
                    f"git -C {ws} pull {auth}",
                )
            )
        else:  # the object is not in the workspace: it never fetched that commit
            out.append(
                check(
                    "tree.authoritative",
                    None,
                    f"{auth} @ {auth_head[:7]}, workspace @ {ws_head[:7]} (not compared)",
                    f"git -C {ws} fetch {auth}",
                )
            )
    a, b = _lock_inputs(ws), _lock_inputs(auth)
    if a is None or b is None:
        out.append(check("tree.lock", None, "a flake.lock is missing or does not parse"))
    else:
        moved = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if moved:
            out.append(
                check(
                    "tree.lock",
                    None,
                    f"workspace lock differs from {auth}: {', '.join(moved)}",
                    f"nixin apply --config {ws}",
                )
            )
        else:
            out.append(check("tree.lock", True, f"same flake.lock as {auth}"))
    out.append(_hub(ctx, ws, ws_head))
    return out


def _hub(ctx: Ctx, ws: str, ws_head: str) -> dict[str, Any]:
    if not ctx.full:
        return check("tree.hub", None, "not asked (--full runs git ls-remote origin)")
    rc, up = _git(ctx, ws, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc != 0 or "/" not in up:
        return check("tree.hub", None, "the workspace branch tracks no remote")
    remote, branch = up.split("/", 1)
    rc, so, _ = ctx.run(
        [
            "git",
            "-c",
            f"core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout={SSH_TIMEOUT}",
            "-C",
            ws,
            "ls-remote",
            remote,
            f"refs/heads/{branch}",
        ],
        GIT_TIMEOUT + SSH_TIMEOUT,
    )
    if rc != 0 or not so.split():
        return check("tree.hub", None, f"{up}: unknown (offline or unreachable)")
    hub = so.split()[0]
    if hub == ws_head:
        return check("tree.hub", True, f"{up} = workspace @ {hub[:7]}")
    rc, _ = _git(ctx, ws, "merge-base", "--is-ancestor", hub, ws_head)
    if rc == 0:
        return check(
            "tree.hub", None, f"workspace is ahead of {up} ({hub[:7]})", "nixin sync --push"
        )
    if rc != 1:  # the hub's commit was never fetched: how far apart is unknown
        return check("tree.hub", None, f"{up} @ {hub[:7]}: not fetched", f"git -C {ws} fetch")
    _, behind = _git(ctx, ws, "rev-list", "--count", f"{ws_head}..{hub}")
    _, ahead = _git(ctx, ws, "rev-list", "--count", f"{hub}..{ws_head}")
    if ahead.isdigit() and int(ahead) > 0:
        return check(
            "tree.hub",
            False,
            f"diverged from {up} @ {hub[:7]}: {behind} behind, {ahead} ahead",
            f"git -C {ws} pull --rebase",
        )
    return check(
        "tree.hub", False, f"{behind} commit(s) behind {up} @ {hub[:7]}", "nixin sync --pull"
    )


# --- hosts.toml ---------------------------------------------------------------------------


def check_hosts(ctx: Ctx) -> list[dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    from .apply import hosts

    try:
        data = hosts.load(ctx.hosts_file)
        remotes = [hosts.remote(h, ctx.hosts_file) for h in sorted(data) if h != ctx.host]
    except NixinError as e:
        return [check("hosts", False, e.message, f"$EDITOR {ctx.hosts_file}")]
    if not remotes:
        return []
    if not ctx.full:
        return [check(f"host.{r.host}", None, f"ssh {r.ssh}: not asked (--full)") for r in remotes]

    def probe(r: hosts.Remote) -> dict[str, Any]:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT}", r.ssh, "true"]
        rc, _so, se = ctx.run(argv, SSH_TIMEOUT + 3)
        if rc == 0:
            return check(f"host.{r.host}", True, f"ssh {r.ssh}: reachable")
        last = (se.strip().splitlines() or [f"exit {rc}"])[-1]
        return check(
            f"host.{r.host}", False, f"ssh {r.ssh}: {last}", f"ssh -o BatchMode=yes {r.ssh} true"
        )

    with ThreadPoolExecutor(max_workers=min(MAX_SSH, len(remotes))) as pool:
        return list(pool.map(probe, remotes))


# --- hand-installed secrets ---------------------------------------------------------------


def _tracked_nix(ctx: Ctx) -> list[str]:
    rc, so = _git(ctx, ctx.workspace, "ls-files", "-z", "--", "*.nix")
    if rc == 0:
        return sorted(f for f in so.split("\0") if f)
    root = Path(ctx.workspace)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.nix") if ".git" not in p.parts)


def host_files(root: Path, tracked: list[str], host: str) -> list[str]:
    """The tracked .nix files nixosConfigurations.<host> reaches, eval-free: the `./…`
    paths in its block of flake.nix, then every `./…` / `../…` path those files name
    (a directory means its default.nix). All of them when flake.nix has no such block."""
    known = set(tracked)
    try:
        flake = (root / "flake.nix").read_text()
    except OSError:
        return tracked
    starts = [m.start() for m in re.finditer(r"nixosConfigurations\.", flake)]
    mine = re.search(rf"nixosConfigurations\.(\"{re.escape(host)}\"|{re.escape(host)}\b)", flake)
    if mine is None:
        return tracked
    end = next((s for s in starts if s > mine.start()), len(flake))
    seen: set[str] = set()

    def resolve(base: str, rel: str) -> str | None:
        p = os.path.normpath(os.path.join(os.path.dirname(base), rel))
        for cand in (p, os.path.join(p, "default.nix")):
            if cand in known:
                return cand
        return None

    todo = [
        r
        for m in _REL.finditer(flake[mine.start() : end])
        if (r := resolve("flake.nix", m.group(1)))
    ]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        try:
            text = (root / f).read_text()
        except OSError:
            continue
        todo += [r for m in _REL.finditer(text) if (r := resolve(f, m.group(1)))]
    return sorted(seen) or tracked


def _expand(path: str, home: str) -> str | None:
    for var in ("${config.home.homeDirectory}", "$HOME"):
        path = path.replace(var, home)
    path = path.replace("${config.xdg.configHome}", os.path.join(home, ".config"))
    if path.startswith("~/"):
        path = os.path.join(home, path[2:])
    if "${" in path or not path.startswith("/"):
        return None  # computed: cannot know it without an eval
    return path


def check_secrets(ctx: Ctx) -> list[dict[str, Any]]:
    root = Path(ctx.workspace)
    found: dict[str, str] = {}  # path -> the first file:line naming it
    for f in host_files(root, _tracked_nix(ctx), ctx.host):
        try:
            lines = (root / f).read_text().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
            for m in list(_SECRET_KEY.finditer(code)) + list(_ENV_FILE.finditer(code)):
                p = _expand(m.group(m.lastindex or 1), ctx.home)
                if p and not p.startswith(_PROVIDED):
                    found.setdefault(p, f"{f}:{i}")
    out = []
    for p, where in sorted(found.items()):
        try:
            os.stat(p)  # existence only; a root-only file is fine
        except FileNotFoundError:
            out.append(check(f"secret {p}", False, f"missing (named at {where})", f"see {where}"))
        except PermissionError:
            out.append(check(f"secret {p}", None, f"cannot look as {os.getuid()} ({where})"))
        except OSError as e:
            out.append(check(f"secret {p}", False, f"{e.strerror} ({where})", f"see {where}"))
        else:
            out.append(check(f"secret {p}", True, f"present ({where})"))
    if not out:
        out.append(check("secrets", True, "the config names no hand-installed files"))
    return out


# --- /boot --------------------------------------------------------------------------------


def check_boot(ctx: Ctx) -> list[dict[str, Any]]:
    from .apply import safety, system

    running = system.realpath(system.CURRENT)
    limit = safety.boot_window(running) if running else None
    if limit is None:  # /boot itself is root's 0700: the builder says it is systemd-boot
        return [check("boot", None, "not systemd-boot (no configurationLimit in the builder)")]
    gens = system.generations()
    biggest, which = 0, None
    for n, path in gens:
        size = sum(safety._size(p) for p in safety.boot_files(path))
        if size > biggest:
            biggest, which = size, n
    free = safety.disk_free(ctx.boot)
    out = []
    mib = 1 << 20
    if free is None:
        out.append(check("boot.space", None, f"{ctx.boot}: statvfs failed"))
    elif biggest and free[0] < biggest:
        out.append(
            check(
                "boot.space",
                False,
                f"{free[0] // mib} MiB free, generation {which}'s kernel+initrd need "
                f"{biggest // mib} MiB: a kernel change that size would not fit",
                None,  # gc cannot help: the builder keeps only `limit` on /boot already
            )
        )
    else:
        out.append(
            check(
                "boot.space",
                True,
                f"{free[0] // mib} MiB free of {free[1] // mib}, biggest kernel+initrd "
                f"{biggest // mib} MiB (generation {which})",
            )
        )
    if limit:
        ok = len(gens) <= limit
        out.append(
            check(
                "boot.generations",
                True if ok else None,
                f"{len(gens)} generations, configurationLimit {limit}",
                None if ok else f"nixin gc --keep {limit}",
            )
        )
    return out


# --- failed units -------------------------------------------------------------------------


def check_failed(ctx: Ctx) -> list[dict[str, Any]]:
    out = []
    for name, extra in (("units.system", []), ("units.user", ["--user"])):
        argv = ["systemctl", *extra, "--failed", "--plain", "--no-legend", "--no-pager"]
        rc, so, se = ctx.run(argv, 5.0)
        if rc != 0:
            out.append(check(name, None, (se.strip().splitlines() or ["systemctl failed"])[-1]))
            continue
        units = [ln.split()[0] for ln in so.splitlines() if ln.split()]
        if units:
            cmd = "journalctl" + (" --user" if extra else "") + f" -u {units[0]} -n 50"
            out.append(check(name, None, f"failed: {', '.join(units)}", cmd))
        else:
            out.append(check(name, True, "no failed units"))
    return out


CHECKS: tuple[Callable[[Ctx], list[dict[str, Any]]], ...] = (
    check_indexes,
    check_timers,
    check_state,
    check_trees,
    check_hosts,
    check_secrets,
    check_boot,
    check_failed,
)


def doctor(ctx: Ctx) -> dict[str, Any]:
    """Every check, the groups side by side (each is a few subprocesses or file reads)."""
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.monotonic()

    def one(fn: Callable[[Ctx], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        try:
            return fn(ctx)
        except Exception as e:  # noqa: BLE001 -- one broken probe must not hide the rest
            group = fn.__name__.removeprefix("check_")
            return [check(group, None, f"the check failed: {type(e).__name__}: {e}")]

    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        checks = [c for group in pool.map(one, CHECKS) for c in group]
    failed = [c["name"] for c in checks if c["ok"] is False]
    return {
        "ok": True,
        "healthy": not failed,
        "failed": failed,
        "unknown": sum(1 for c in checks if c["ok"] is None),
        "full": ctx.full,
        "workspace": ctx.workspace,
        "authoritative": ctx.authoritative,
        "checks": checks,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }


def render(d: dict[str, Any], host: str) -> str:
    mark = {True: "ok ", False: "BAD", None: " - "}
    width = max((len(c["name"]) for c in d["checks"]), default=0)
    lines = [
        f"nixin doctor · {host} · {len(d['checks'])} checks, {len(d['failed'])} failed, "
        + f"{d['unknown']} unknown/info ({d['elapsed_ms']} ms)"
    ]
    for c in d["checks"]:
        lines.append(f"  {mark[c['ok']]} {c['name']:<{width}}  {c['detail']}")
        if c["fix"] and c["ok"] is not True:
            lines.append(f"      {'':<{width}} fix: {c['fix']}")
    if not d["full"]:
        lines.append("(--full also asks the hub and the hosts in hosts.toml)")
    return "\n".join(lines)


# --- the verb -----------------------------------------------------------------------------


def _workspace(config: str | None) -> str:
    if config:
        return os.path.abspath(os.path.expanduser(config))
    from . import state

    return state.workspace()


def _state_dir() -> Path:
    d = os.environ.get("NIXIN_STATE_DIR")  # as state.state_dir(), which also mkdirs
    if not d:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        d = os.path.join(base, "nixin")
    return Path(d)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(VERB, help=HELP)
    p.add_argument(  # not `config`: cli.document would then read it to pick a host
        "--config",
        dest="workspace",
        metavar="DIR",
        help="the workspace (default: $NIXIN_CONFIG, else ~/nixos, else /etc/nixos)",
    )
    p.add_argument(
        "--authoritative", help="the root-owned tree (default: $NIXIN_AUTHORITATIVE or /etc/nixos)"
    )
    p.add_argument("--full", action="store_true", help="also ask the hub and the hosts (network)")
    p.set_defaults(run=run)


def run(ns: argparse.Namespace) -> dict[str, Any]:
    from .apply import hosts

    if ns.host != socket.gethostname():
        return NixinError(
            "host-not-local",
            f"doctor looks at this machine ({socket.gethostname()}); run it on {ns.host}",
        ).doc()
    ctx = Ctx(
        host=ns.host,
        workspace=_workspace(ns.workspace or ns.config_before_verb),
        authoritative=ns.authoritative or os.environ.get("NIXIN_AUTHORITATIVE") or DEFAULT_CONFIG,
        state_dir=_state_dir(),
        hosts_file=hosts.config_path(),
        full=ns.full,
    )
    doc = doctor(ctx)
    if not ns.json:
        doc["message"] = render(doc, ctx.host)
    return doc
