"""apply on another host: push the workspace to the hub, then one `ssh -t` session there.

`nixin apply --host H`, H not this machine (hosts.py says how to reach it):

    commit   the workspace's pending changes, exactly as a local apply does
    push     the workspace branch to the hub (the branch's remote, else origin):
             `git push <remote> HEAD:refs/heads/<branch>`; `push-failed`
    ssh -t   one session on H, so sudo asks for its password once at most, running a
             POSIX sh script (no backslashes: zsh, bash and fish pass it to sh unchanged):
                 git -C <its workspace> pull --ff-only
                 sudo git -C <its authoritative> pull --ff-only <its workspace> <branch>
                 sudo nixos-rebuild switch --flake <its authoritative>#H
             with checks between: H's workspace is on <branch>, and after each pull the
             tree is at the pushed commit, so what was pushed is what H switches to

Nothing is built here: H builds its own system, so there is no closure diff, boot check
or out-path verification (the script reports /run/current-system before and after).
H's `installer/history.jsonl` gets no line, and this machine's neither: that file is
joined with the local generations, which H's are not. The remote sudo may ask for a
password on this terminal, so a real run needs one: `needs-tty` under --json, from a
UI's worker thread, or without a terminal. `--dry-run` prints every command and the
script, and runs nothing: no network, no ssh.
"""

from __future__ import annotations

import re
import shlex
import sys
import threading
import time
from typing import Any

from ..configrepo import NixinError, local_hostname
from ..truth.render import row, sub
from ..writer.state import STATE_FILE
from . import ApplyError, commitmsg, hosts, proc
from .system import HISTORY

CURRENT = "/run/current-system"  # on H; module constants so the tests can move them
PROFILE = "/nix/var/nix/profiles/system"
MARK = "[nixin]"
NEW_COMMIT = "NEW_COMMIT"  # the dry run's stand-in for a commit not made yet
_GEN = re.compile(r"system-(\d+)-link$")

# exit status of the script -> (error code, step, what happened)
FAILS: dict[int, tuple[str, str, str]] = {
    20: ("git-failed", "workspace-pull", "{ws} on {host} is not on branch {branch}"),
    21: ("git-failed", "workspace-pull", "git -C {ws} pull --ff-only failed on {host}"),
    22: (
        "git-failed",
        "workspace-pull",
        (
            "after its pull {ws} on {host} is not at {sha}: its {branch} does not track the "
            "hub's {branch} (there: git -C {ws} branch -u <hub remote>/{branch})"
        ),
    ),
    23: ("git-failed", "authoritative-pull", "root's pull into {auth} on {host} failed"),
    24: ("git-failed", "authoritative-pull", "after root's pull {auth} on {host} is not at {sha}"),
    25: ("switch-failed", "switch", "nixos-rebuild {mode} failed on {host} ({auth} is at {sha})"),
}


def has_tty() -> bool:
    """A person at a terminal: the CLI's main thread with stdin and stderr on a tty (the
    UIs call verbs from worker threads and capture stderr)."""
    if threading.current_thread() is not threading.main_thread():
        return False
    return all(f is not None and getattr(f, "isatty", bool)() for f in (sys.stdin, sys.stderr))


def rpath(p: str) -> str:
    """A path for the remote shell: a leading ~ or ~user stays bare (it expands there),
    the rest is quoted."""
    if p.startswith("~"):
        head, sep, rest = p.partition("/")
        return head + (sep + shlex.quote(rest) if rest else sep)
    return shlex.quote(p)


def remote_steps(r: hosts.Remote, branch: str, mode: str) -> list[dict[str, Any]]:
    """The three commands H runs, as a person would type them there."""
    pull = ["git", "-C", r.workspace, "pull", "--ff-only"]
    auth_pull = ["git", "-C", r.authoritative, "pull", "--ff-only", r.workspace, branch]
    switch = ["nixos-rebuild", mode, "--flake", f"{r.authoritative}#{r.host}"]
    out = [
        {"step": "workspace-pull", "privileged": False, "argv": pull},
        {"step": "authoritative-pull", "privileged": True, "argv": ["sudo", *auth_pull]},
        {"step": "switch", "privileged": True, "argv": ["sudo", *switch]},
    ]
    for c in out:
        c["where"] = r.host
        c["command"] = " ".join(
            rpath(a) if a.startswith("~") else proc.shown([a]) for a in c["argv"]
        )
    return out


def script(r: hosts.Remote, branch: str, commit: str, mode: str) -> str:
    """The sh script the ssh session runs (see the module docstring)."""
    steps = remote_steps(r, branch, mode)
    q = shlex.quote
    b, sha, auth = q(branch), q(commit), q(r.authoritative)
    flake = q(f"{r.authoritative}#{r.host}")
    state = (
        f"current=$(readlink -f {q(CURRENT)}) profile=$(readlink {q(PROFILE)}) "
        f"profile_target=$(readlink -f {q(PROFILE)})"
    )

    def fail(code: int, *words: str) -> str:
        return "{ echo nixin: " + " ".join(words) + f" >&2; exit {code}; }}"

    return "\n".join(
        [
            f"ws={rpath(r.workspace)}",
            f'echo "{MARK} before {state}"',
            "echo " + q(f"{MARK} 1/3 {steps[0]['command']}"),
            f'[ "$(git -C "$ws" symbolic-ref --short -q HEAD)" = {b} ] || '
            + fail(20, '"$ws"', "is not on branch", b),
            'git -C "$ws" pull --ff-only || exit 21',
            f'[ "$(git -C "$ws" rev-parse HEAD)" = {sha} ] || '
            + fail(22, '"$ws"', "is not at", sha, "after its pull"),
            "echo " + q(f"{MARK} 2/3 {steps[1]['command']}"),
            f'sudo git -C {auth} pull --ff-only "$ws" {b} || exit 23',
            f"[ \"$(git -c safe.directory='*' -C {auth} rev-parse HEAD)\" = {sha} ] || "
            + fail(24, auth, "is not at", sha, "after the pull"),
            "echo " + q(f"{MARK} 3/3 {steps[2]['command']}"),
            f"sudo nixos-rebuild {q(mode)} --flake {flake} || exit 25",
            f'echo "{MARK} after {state}"',
        ]
    )


def ssh_argv(r: hosts.Remote, text: str) -> list[str]:
    """`ssh -t DEST 'sh -c …'`: one argument for the remote login shell to parse."""
    return ["ssh", "-t", r.ssh, "sh -c " + shlex.quote(text)]


def parse_state(log: str, which: str) -> dict[str, Any] | None:
    """The `[nixin] before|after current=… profile=… profile_target=…` line, as the
    `running` block of a local apply (store_path, generation, profile_generation, profile)."""
    for line in log.splitlines():
        if line.startswith(f"{MARK} {which} "):
            kv = dict(t.partition("=")[::2] for t in line.split()[2:] if "=" in t)
            m = _GEN.search(kv.get("profile", ""))
            gen = int(m.group(1)) if m else None
            current = kv.get("current") or None
            target = kv.get("profile_target") or None
            return {
                "store_path": current,
                "generation": gen if gen is not None and target == current else None,
                "profile_generation": gen,
                "profile": target,
            }
    return None


def reached(log: str) -> list[str]:
    """The script's steps that started (`1/3`, `2/3`, `3/3`)."""
    return re.findall(rf"^{re.escape(MARK)} (\d/3) ", log, re.MULTILINE)


# --- the verb ---------------------------------------------------------------------------


def apply(ctx: Any) -> dict[str, Any]:
    """`apply` for a host that is not this machine (ctx: apply's Ctx; ctx.local is False)."""
    t0 = time.monotonic()
    ns = ctx.ns
    r = hosts.remote(ctx.host)
    ctx.warnings.extend(r.warnings)
    mode = "boot" if ns.no_switch else "switch"
    if not ns.dry_run and (ns.json or not has_tty()):
        why = "under --json" if ns.json else "without a terminal (or from a UI)"
        raise NixinError(
            "needs-tty",
            f"apply on {ctx.host} runs sudo there over `ssh -t {r.ssh}`, which may ask for a "
            f"password on this terminal; it cannot run {why}. Run `nixin --host {ctx.host} "
            f"apply` in a terminal, or --dry-run to see the commands",
        )
    if getattr(ns, "authoritative", None):
        ctx.warnings.append(
            f"--authoritative is this machine's tree; on {ctx.host} it is {r.authoritative} "
            f"(hosts.toml: authoritative)"
        )
    st = ctx.git.status()
    head = ctx.git.head()
    # not this machine's installer/history.jsonl: its pending line waits for the next local
    # apply. Pushed, it would block H's `pull --ff-only` whenever H has a line of its own.
    to_commit = [f for f in st.dirty if f != HISTORY]
    message = ns.message or commitmsg.message(
        ctx.git.show("HEAD", STATE_FILE), _read(ctx.path / STATE_FILE), to_commit
    )
    name = ctx.git.upstream_remote() or "origin"
    remotes = ctx.git.remotes()
    if name not in remotes:
        raise NixinError(
            "push-failed",
            f"the workspace has no remote {name!r} to push {ctx.branch} to "
            f"(remotes: {', '.join(remotes) or 'none'}); {ctx.host} pulls from the hub",
        )
    hub = {"remote": name, "url": ctx.git.remote_url(name), "branch": ctx.branch}
    push = [
        "git",
        "-C",
        str(ctx.path),
        "push",
        "--porcelain",
        name,
        f"HEAD:refs/heads/{ctx.branch}",
    ]
    ctx.warnings.append(
        f"{ctx.host} builds its own system: no closure diff, boot check or out-path check here"
    )
    if ns.dry_run:
        return _dry_run(ctx, r, mode, st, head, to_commit, message, hub, push, t0)

    committed = None
    t_commit = time.monotonic()
    if to_commit:
        committed = head = ctx.git.commit(message, [], exclude=HISTORY)
    if head is None:
        raise NixinError("git-failed", f"{ctx.path} has no commit to push")
    progress: dict[str, Any] = {"committed": committed}
    t_push = time.monotonic()
    pushed = _push(ctx, hub, progress)
    progress["pushed"] = head
    t_remote = time.monotonic()
    text = script(r, ctx.branch, head, mode)
    argv = ssh_argv(r, text)
    if ctx.human:
        sys.stderr.write(f"{MARK} ssh -t {r.ssh}: {ctx.host} pulls {head[:7]} and switches\n")
        sys.stderr.flush()
    p = proc.run(argv, stream="all", tty=True)
    t_done = time.monotonic()
    log = p.err
    steps = remote_steps(r, ctx.branch, mode)
    started = reached(log)  # step i+1 of 3 started: its command ran (or runs: ssh dropped)
    progress.update(
        remote_steps_started=started,
        privileged_commands=[
            s["command"] for i, s in enumerate(steps) if s["privileged"] and f"{i + 1}/3" in started
        ],
    )
    if not p.ok:
        raise _failure(p, r, ctx, head, hub, mode, progress)
    before, after = parse_state(log, "before"), parse_state(log, "after")
    out = (after or {}).get("store_path" if mode == "switch" else "profile")
    subject = message.splitlines()[0] if message else ""
    doc = {
        "ok": True,
        "remote": r.doc(),
        "committed": committed,
        "commit_message": subject if committed else None,
        "pushed": pushed,
        "authoritative_head": head,
        "out": out,
        "generation": (after or {}).get("profile_generation"),
        "switched": mode == "switch",
        "mode": mode,
        "changed": (before or {}).get("profile") != (after or {}).get("profile"),
        "previous": before,
        "running": after,
        "privileged_commands": [s["command"] for s in steps if s["privileged"]],
        "backends": {"pull": "ssh+sudo", "switch": "ssh+sudo"},
        "ssh": {"argv": argv, "script": text},
        "switch_log": p.tail(20),
        "diff": None,
        "boot": None,
        "reboot_needed": None,
        "cached": None,
        "history": None,
        "timings": {
            "prepare": round(t_commit - t0, 2),
            "commit": round(t_push - t_commit, 2),
            "push": round(t_remote - t_push, 2),
            "remote": round(t_done - t_remote, 2),
            "total": round(time.monotonic() - t0, 2),
        },
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render(doc, ctx.host)
    return doc


def _push(ctx: Any, hub: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    """`git push --porcelain` of the branch; {remote, url, branch, from, to, commits[],
    up_to_date}, or `push-failed` (with `rejected`: the hub has commits we do not)."""
    p = ctx.git.run("push", "--porcelain", hub["remote"], f"HEAD:refs/heads/{ctx.branch}")
    flag, summary = "", ""
    for line in p.out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1].endswith(f":refs/heads/{ctx.branch}"):
            flag, summary = parts[0], parts[2]
    if not p.ok or flag == "!":
        rejected = flag == "!" or any(
            w in p.err for w in ("rejected", "non-fast-forward", "fetch first")
        )
        hint = (
            f": {hub['remote']} has commits the workspace does not (nixin sync --pull first)"
            if rejected
            else ""
        )
        raise _err(
            "push-failed",
            f"git push {hub['remote']} {ctx.branch} failed{hint}\n{p.tail(8)}",
            rejected=rejected,
            **progress,
        )
    old, _, new = summary.partition("..")
    commits = ctx.git.log(f"{old}..{new}") if flag == " " and new else []
    if flag == "*":  # a new branch on the hub: everything it now has
        commits = ctx.git.log("HEAD", limit=50)
    return {**hub, "flag": flag, "up_to_date": flag == "=", "commits": commits}


def _failure(p, r, ctx, head, hub, mode, progress) -> NixinError:
    log = p.err
    if p.missing:
        return _err("ssh-failed", "`ssh` is not on PATH", **progress)
    if p.rc in FAILS:
        code, step, what = FAILS[p.rc]
        text = what.format(
            ws=r.workspace,
            host=r.host,
            branch=ctx.branch,
            sha=head[:7],
            remote=hub["remote"],
            auth=r.authoritative,
            mode=mode,
        )
        if step == "authoritative-pull" and "fast-forward" in log:
            code = "authoritative-diverged"
        if p.rc == 21 and HISTORY in log and "overwritten" in log:
            ws = rpath(r.workspace)
            text += (
                f": {HISTORY} there has uncommitted lines ({r.host}'s own applies) and the "
                f"pushed commits change it too. On {r.host}: git -C {ws} stash push -- "
                f"{HISTORY}, apply again, then git -C {ws} stash pop there (keep both "
                "sides' lines if git asks)"
            )
        return _err(code, f"{text}:\n{p.tail(12)}", step=step, **progress)
    started = progress["remote_steps_started"]
    if p.rc == 255 and started:  # the session was up: the connection dropped mid-way
        return _err(
            "ssh-failed",
            f"the connection to {r.host} dropped (ssh exit 255) after step {started[-1]} had "
            f"started (pushed {head[:7]}): {r.host} may have pulled it, or be switching to it; "
            f"look there (nixos-rebuild list-generations, nixin status) before applying "
            f"again:\n{p.tail(6)}",
            **progress,
        )
    if p.rc == 255:
        return _err(
            "ssh-failed",
            f"ssh {r.ssh} failed (is {r.host} reachable as {r.ssh!r}? hosts.toml sets the "
            f"destination):\n{p.tail(6)}",
            **progress,
        )
    return _err(
        "ssh-failed",
        f"the session on {r.host} ended with exit status {p.rc} (pushed {head[:7]}; "
        f"steps started: {', '.join(progress['remote_steps_started']) or 'none'}):\n{p.tail(12)}",
        **progress,
    )


def _err(code: str, message: str, **extra: Any) -> NixinError:
    return ApplyError(code, message, **extra)


def _dry_run(ctx, r, mode, st, head, to_commit, message, hub, push, t0):
    commands: list[dict[str, Any]] = []
    g = ["git", "-C", str(ctx.path)]
    if to_commit:
        add = [*g, "add", "-u", "--", ".", f":(exclude){HISTORY}"]
        commands.append({"step": "commit", "argv": add})
        commands.append({"step": "commit", "argv": [*g, "commit", "-m", message.splitlines()[0]]})
    commands.append({"step": "push", "argv": push})
    text = script(r, ctx.branch, NEW_COMMIT if to_commit or not head else head, mode)
    commands.append({"step": "ssh", "argv": ssh_argv(r, text)})
    for c in commands:
        c.update(where="local", privileged=False, command=proc.shown(c["argv"]))
    commands += remote_steps(r, ctx.branch, mode)
    tracking = ctx.git.rev(f"refs/remotes/{hub['remote']}/{ctx.branch}")
    unpushed = ctx.git.log(f"{tracking}..HEAD") if tracking and head else None
    why = f"not probed: the dry run does not connect; sudo on {r.host} may ask on this terminal"
    doc = {
        "ok": True,
        "dry_run": True,
        "remote": r.doc(),
        "commands": commands,
        "script": text,
        "commit": {"files": to_commit, "message": message} if to_commit else None,
        "push": {**hub, "tracking": tracking, "commits": unpushed},
        "out": None,
        "cached": None,
        "workspace": {"path": str(ctx.path), "branch": ctx.branch, "head": head, **st.doc()},
        "authoritative": {"path": r.authoritative, "relation": "unknown"},
        "pending_commits": unpushed,
        "running": None,
        "privilege": {
            "pull": {"backend": "ssh+sudo", "ready": False, "why": why},
            "switch": {"backend": "ssh+sudo", "ready": False, "why": why},
        },
        "diff": None,
        "boot": None,
        "reboot_needed": None,
        "needs_tty": True,
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render_dry_run(doc, ctx.host)
    return doc


def _read(p) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


# --- human output -----------------------------------------------------------------------


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "?"


def render_dry_run(d: dict[str, Any], host: str) -> str:
    r = d["remote"]
    lines = [
        f"apply {host} --dry-run: {host} is not this machine ({local_hostname()}); over "
        + f"`ssh -t {r['ssh']}` it would run, in order (nothing was run)"
    ]
    for c in d["commands"]:
        if c["step"] == "ssh":
            lines.append(sub("[user]", f"ssh -t {r['ssh']} sh -c SCRIPT  (SCRIPT below)"))
        elif c["where"] == "local":
            lines.append(sub("[user]", c["command"]))
        else:
            lines.append(sub(f"[{c['where']}{' root' if c['privileged'] else ''}]", c["command"]))
    if d["commit"]:
        lines.append(row("commit", d["commit"]["message"].splitlines()[0]))
    p = d["push"]
    if p["commits"] is None:
        n = f"no {p['remote']}/{p['branch']} here yet (a new branch on the hub?)"
    else:
        n = f"{len(p['commits'])} commit(s) not on {p['remote']}/{p['branch']} (last fetch)"
    lines.append(row("push", f"{p['remote']} {p['url'] or '?'}: {n}"))
    lines.append(row("trees", f"on {host}: {r['workspace']}, {r['authoritative']} (root's)"))
    lines.append(
        row(
            "checks",
            f"{r['workspace']} is on {d['workspace']['branch']}; both end at the pushed commit",
        )
    )
    lines.append(row("sudo", f"on {host}; it may ask for a password on this terminal"))
    lines += [row("warning", w) for w in d["warnings"]]
    lines.append("script (sh -c on " + host + "):")
    lines += ["    " + ln for ln in d["script"].splitlines()]
    return "\n".join(lines)


def render(d: dict[str, Any], host: str) -> str:
    r, run = d["remote"], d["running"] or {}
    how = "switched to" if d["switched"] else "boot default is"
    gen = f"generation {d['generation']}" if d["generation"] is not None else "?"
    lines = [f"apply {host} (over ssh {r['ssh']}): {how} {gen} {d['out'] or '?'}"]
    if not d["changed"]:
        lines[0] += " (unchanged: it already was)"
    if d["committed"]:
        lines.append(row("committed", f"{_short(d['committed'])} {d['commit_message']}"))
    p = d["pushed"]
    what = "up to date" if p["up_to_date"] else f"{len(p['commits'])} commit(s)"
    lines.append(row("pushed", f"{p['remote']} {p['branch']}: {what}"))
    auth = f"{r['authoritative']} on {host} @ {_short(d['authoritative_head'])}"
    lines.append(row("authorit.", auth))
    for c in d["privileged_commands"]:
        lines.append(sub(f"[{host} root]", c))
    prev = d["previous"] or {}
    if prev.get("profile_generation") is not None and d["changed"]:
        was = f"generation {prev['profile_generation']} {prev.get('store_path') or '?'}"
        lines.append(row("previous", was))
    if run.get("store_path") and not d["switched"]:
        lines.append(row("running", f"{run['store_path']} (until the next boot)"))
    t = d["timings"]
    lines.append(row("took", f"{t['total']} s (push {t['push']} s, on {host} {t['remote']} s)"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)
