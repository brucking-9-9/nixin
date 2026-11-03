"""rollback: back to an earlier system generation, and the config with it.

Two halves, both through apply's backends (`sudo -n`; `needs-sudo` with the commands
when it cannot get root; `--dry-run` prints them):

system   `nix-env --profile /nix/var/nix/profiles/system --switch-generation GEN`, then
         GEN's own `bin/switch-to-configuration switch` (what `nixos-rebuild switch
         --rollback` does, for any generation). Verified: /run/current-system is GEN's
         store path and the profile points at GEN.
config   which commit builds GEN (history.jsonl, else plan's commit cache over the
         workspace's ancestry: `history.config_commit`). If there is one, the workspace
         gets ONE new commit whose tree is that commit's again (installer/history.jsonl
         aside): a revert of everything applied since, never a reset. Root pulls it into
         the authoritative tree, so the authoritative tree builds what runs again and the
         next `apply` does not switch back. Without such a commit the config is left as it
         is, and the result says so: the next apply would switch back to the config's
         system.

The revert commit is made first (in the workspace, as you), then the root steps: the
system, then the pull. GEN defaults to the generation before the running one. A line
with mode "rollback" is appended to installer/history.jsonl (committed by the next
commit nixin makes, as apply's lines are).
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from ..configrepo import NixinError, local_hostname
from . import (
    ApplyError,
    _auth_doc,
    _context,
    _diverged,
    _lock,
    _relation,
    build,
    history,
    privilege,
    proc,
    render,
    run_privileged,
    safety,
    system,
)
from .commitmsg import SUBJECT_MAX
from .system import HISTORY


def rollback(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        with _lock():
            return _rollback(ns)
    except NixinError as e:
        return e.doc()


def _wanted(ns: argparse.Namespace) -> int | None:
    args = getattr(ns, "args", None) or []
    if len(args) > 1:
        raise NixinError("usage", f"rollback takes one generation number (got {' '.join(args)})")
    if not args:
        return None
    raw = args[0].removeprefix("system-").removesuffix("-link")
    if not raw.isdigit():
        raise NixinError("usage", f"GEN is a generation number (nixin history), not {args[0]!r}")
    return int(raw)


def _pick(want: int | None, gens: list[tuple[int, str]], current: int | None) -> int:
    have = [n for n, _ in gens]
    span = f"{have[0]}–{have[-1]}" if have else "none"
    if want is not None:
        if want not in have:
            raise NixinError(
                "unknown-generation",
                f"there is no system generation {want} (on disk: {span}; nixin history)",
            )
        return want
    if current is None:
        raise NixinError(
            "unknown-generation",
            "the running system is not one of the profile's generations, so there is no "
            f"'previous' one; name it: nixin rollback GEN (on disk: {span}; nixin history)",
        )
    older = [n for n in have if n < current]
    if not older:
        raise NixinError(
            "unknown-generation", f"generation {current} runs and there is none before it"
        )
    return older[-1]


def _subject(gen: int, reverted: list[dict[str, str]]) -> str:
    if len(reverted) == 1:
        subject = f'Revert "{reverted[0]["subject"]}"'
    else:
        subject = f"nixin: rollback to generation {gen}: revert {len(reverted)} commits"
    return subject if len(subject) <= SUBJECT_MAX else subject[: SUBJECT_MAX - 1] + "…"


def _message(gen: int, from_gen: int | None, target: str, reverted: list[dict[str, str]]) -> str:
    was = from_gen if from_gen is not None else "?"
    body = [
        f"nixin rollback: generation {was} -> {gen}. The tree is {target[:12]}'s again",
        f"(the commit generation {gen} was built from; {HISTORY} aside).",
        "",
        "This reverts:",
        *(f"  {c['commit'][:12]} {c['subject']}" for c in reverted),
    ]
    return _subject(gen, reverted) + "\n\n" + "\n".join(body) + "\n"


def _rollback(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    want = _wanted(ns)
    ctx = _context(ns)
    if not ctx.local:
        raise NixinError(
            "host-not-local",
            f"rollback switches this machine ({local_hostname()}); {ctx.host!r} is another "
            f"host. Run nixin rollback on {ctx.host}",
        )
    gens = system.generations()
    before = system.running()
    gen = _pick(want, gens, before["generation"])
    out = dict(gens)[gen]
    system_needed = not (before["store_path"] == out and before["profile_generation"] == gen)

    # --- the config half: which commit builds `out`, and what it takes to get there
    found = history.config_commit(ctx.git, system.read_history(ctx.path), ctx.host, gen, out)
    st = ctx.git.status()
    head = ctx.git.head()
    auth = _auth_doc(ctx)
    target = found["commit"]
    revert = pull = False
    reverted: list[dict[str, str]] = []
    if target:
        if not auth["exists"]:
            raise NixinError(
                "authoritative-missing",
                f"{ctx.auth_path} is not a git checkout; rollback pulls its revert there "
                "(or pass --authoritative DIR)",
            )
        if auth["dirty"]:
            raise ApplyError(
                "authoritative-dirty",
                f"{ctx.auth_path} has uncommitted changes ({', '.join(auth['dirty'][:5])}); "
                "nixin only fast-forwards it",
                files=auth["dirty"],
            )
        rel = _relation(ctx, head, auth["head"])
        if rel["state"] not in ("same", "ahead"):
            raise _diverged(ctx, rel, auth["head"] or "?")
        real = [f for f in st.dirty if f != HISTORY]
        if real:
            raise ApplyError(
                "workspace-dirty",
                f"the workspace has uncommitted changes ({', '.join(real[:5])}); the revert "
                "commit would sweep them in. Apply them (nixin apply) or discard them "
                f"(git -C {ctx.path} restore --staged --worktree -- FILE), then roll back",
                files=real,
                commits=[],
            )
        revert = not ctx.git.same_tree(target, "HEAD", HISTORY)
        if rel["state"] == "ahead" and revert:
            pending = rel["pending"] or []
            raise ApplyError(
                "workspace-dirty",
                f"the workspace has {len(pending)} commit(s) the running system never had "
                "(not applied yet: "
                + "; ".join(f"{c['commit'][:7]} {c['subject']}" for c in pending[:3])
                + "); a revert would drop them. Apply them first, or move them off "
                f"{ctx.branch}, then roll back",
                files=[],
                commits=pending,
            )
        pull = revert or rel["state"] == "ahead"
        if revert:
            reverted = ctx.git.log(f"{target}..HEAD", limit=200)
        cfg_state = "reverted" if pull else "in-sync"
    else:
        cfg_state = "untouched"
    if not system_needed and not pull:
        raise NixinError(
            "nothing-to-apply",
            f"generation {gen} already runs and is the boot default"
            + ("" if target else "; the config has no commit for it (left as it is)"),
        )

    boot = safety.compare(before["store_path"], out, generation=gen) if system_needed else None
    if boot:
        ctx.warnings += boot["warnings"]
    if boot and boot["boot_space"]["state"] == "full":
        raise ApplyError(
            "boot-space", safety.full_error_message(boot), boot_space=boot["boot_space"]
        )
    message = _message(gen, before["generation"], target, reverted) if revert else None
    new_history = (ctx.path / HISTORY).is_file() and not ctx.git.tracked(HISTORY)
    add_history = [HISTORY] if new_history else []
    steps: list[privilege.Step] = []
    if system_needed:
        steps += [privilege.profile_step(system.PROFILE, gen), privilege.activate_step(out)]
    if pull:
        steps.append(privilege.pull_step(ctx.auth_path, ctx.path, ctx.branch))
    bk = privilege.backends(steps, ctx.auth_path, ctx.local)
    config: dict[str, Any] = {
        "state": cfg_state,
        "why": None if target else found["why"],
        "commit": target,
        "source": found.get("source"),
        "revert_commit": None,
        "reverted": reverted,
    }
    if not target:
        config["candidates"] = found["candidates"]
        ctx.warnings.append(_untouched(found, gen, ctx))
    if ns.dry_run:
        return _dry_run(ctx, gen, out, before, steps, bk, config, message, add_history, boot, t0)

    # --- the revert commit (as you), then the root steps
    progress: dict[str, Any] = {"committed": None, "generation": gen}
    t_commit = time.monotonic()
    if revert:
        ctx.git.restore(target, HISTORY)
        head = ctx.git.commit(message, add_history)
        progress["committed"] = config["revert_commit"] = head
    ran: list[str] = []
    t_system = time.monotonic()
    switch_log = ""
    if system_needed:
        p = run_privileged(ctx, bk, steps[0], steps, ran, progress)
        if not p.ok:
            raise ApplyError(
                "switch-failed",
                f"moving the system profile to generation {gen} failed:\n{p.tail(12)}",
                privileged_commands=ran,
                **progress,
            )
        p = run_privileged(ctx, bk, steps[1], steps[1:], ran, progress)
        switch_log = p.tail(20)
        now = system.realpath(system.CURRENT)
        if not p.ok:
            where = "already runs it" if now == out else f"is {now}"
            raise ApplyError(
                "switch-failed",
                f"switch-to-configuration of generation {gen} failed (/run/current-system "
                f"{where}):\n{p.tail(20)}\nWhen it is fixed: nixin rollback {gen}",
                privileged_commands=ran,
                **progress,
            )
        if now != out or system.profile_generation() != gen:
            raise ApplyError(
                "verify-failed",
                f"after the rollback /run/current-system is {now} and the profile is at "
                f"generation {system.profile_generation()}; expected {out} (generation {gen})",
                privileged_commands=ran,
                **progress,
            )
    t_pull = time.monotonic()
    auth_head = auth["head"]
    if pull:
        p = run_privileged(ctx, bk, steps[-1], steps[-1:], ran, progress)
        if not p.ok:
            raise ApplyError(
                "git-failed",
                f"generation {gen} runs, but pulling the revert into {ctx.auth_path} failed:"
                f"\n{p.tail(12)}\nThe next nixin apply would switch back; retry: nixin "
                f"rollback {gen}",
                privileged_commands=ran,
                **progress,
            )
        auth_head = ctx.auth.head()
        if auth_head != head:
            raise ApplyError(
                "git-failed",
                f"after the pull {ctx.auth_path} is at {auth_head}, expected {head}",
                privileged_commands=ran,
                **progress,
            )
    t_done = time.monotonic()
    config["authoritative_head"] = auth_head
    if target:  # the authoritative HEAD builds `out` now: answer apply/status from cache
        ctx.cache.put_commit(ctx.host, auth_head, {"out": out, "drv": None})
    after = system.running()
    subject = _subject(gen, reverted) if revert else f"nixin: rollback to generation {gen}"
    line = {
        "ts": system.now(),
        "host": ctx.host,
        "out": out,
        "generation": gen,
        "commit": auth_head if target else None,
        "message": subject,
        "mode": "rollback",
        "from_generation": before["generation"],
        "config": cfg_state,
    }
    try:
        system.append_history(ctx.path, line)
    except OSError as e:
        ctx.warnings.append(f"could not append to {HISTORY}: {e}")
    doc = {
        "ok": True,
        "generation": gen,
        "from_generation": before["generation"],
        "out": out,
        "switched": system_needed,
        "previous": before,
        "running": after,
        "config": config,
        "privileged_commands": ran,
        "backends": {s.name: bk[s.name].name for s in steps},
        "switch_log": switch_log,
        "boot": boot,
        "reboot_needed": boot["reboot_needed"] if boot else False,
        "diff": build.diff(before["store_path"], out) if system_needed else None,
        "history": line,
        "timings": {
            "prepare": round(t_commit - t0, 2),
            "commit": round(t_system - t_commit, 2),
            "switch": round(t_pull - t_system, 2),
            "pull": round(t_done - t_pull, 2),
            "total": round(time.monotonic() - t0, 2),
        },
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.rollback(doc, ctx.host)
    return doc


def _untouched(found: dict[str, Any], gen: int, ctx) -> str:
    if found["why"] == "not-in-workspace":
        what = (
            f"{HISTORY} names commit(s) for generation {gen} "
            f"({', '.join(c[:7] for c in found['candidates'][:3])}), but none is in the "
            f"workspace's history of {ctx.branch}"
        )
    else:
        what = (
            f"nixin knows no commit that builds generation {gen} (not in {HISTORY}, not in "
            "plan's cache)"
        )
    return (
        f"{what}: the system rolls back, the config stays as it is. The next nixin apply "
        "would switch back to the config's system; change the workspace first"
    )


def _dry_run(ctx, gen, out, before, steps, bk, config, message, add_history, boot, t0):
    commands: list[dict[str, Any]] = []
    g = ["git", "-C", str(ctx.path)]
    if message:
        target = config["commit"]
        commands.append(
            {
                "step": "revert",
                "privileged": False,
                "argv": [
                    *g,
                    "restore",
                    f"--source={target}",
                    "--staged",
                    "--worktree",
                    "--",
                    ".",
                    f":(exclude){HISTORY}",
                ],
            }
        )
        commands.append({"step": "revert", "privileged": False, "argv": [*g, "add", "-u"]})
        if add_history:
            commands.append(
                {"step": "revert", "privileged": False, "argv": [*g, "add", "--", *add_history]}
            )
        commands.append(
            {
                "step": "revert",
                "privileged": False,
                "argv": [*g, "commit", "-m", message.splitlines()[0]],
            }
        )
    for s in steps:
        commands.append(
            {
                "step": s.name,
                "privileged": True,
                "argv": bk[s.name].argv(s),
                "backend": bk[s.name].name,
                "by_hand": bk[s.name].display(s),
            }
        )
    for c in commands:
        c["command"] = proc.shown(c["argv"])
    ready = {s.name: bk[s.name].probe() for s in steps}
    doc = {
        "ok": True,
        "dry_run": True,
        "generation": gen,
        "from_generation": before["generation"],
        "out": out,
        "switched": any(s.name == "activate" for s in steps),
        "commands": commands,
        "commit": {"message": message} if message else None,
        "config": config,
        "running": before,
        "privilege": {
            k: {"backend": bk[k].name, "ready": v is None, "why": v} for k, v in ready.items()
        },
        "boot": boot,
        "reboot_needed": boot["reboot_needed"] if boot else False,
        "diff": build.diff(before["store_path"], out) if boot else None,
        "duration_s": round(time.monotonic() - t0, 2),
        "warnings": ctx.warnings,
    }
    if ctx.human:
        doc["message"] = render.rollback_dry_run(doc, ctx.host)
    return doc
