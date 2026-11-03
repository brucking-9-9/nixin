"""gc: delete old system generations, refresh the boot menu, collect the garbage.

    nixin gc [--keep N] [--older-than DAYS] [--dry-run]

Which generations go is generations.py's policy: the newest N, or those made in the
last DAYS days (and the one running then), stay; the running generation, the booted one
and the boot default always stay. Without --keep or --older-than no generation goes and
gc only collects what is garbage already.

The estimate (before anything runs as root) is what the store would free: the closures
of the generations that go, minus what the GC roots left after still reach
(`nix-store --gc --print-roots` without the links of those generations, then
`nix-store -qR`); with --dry-run also what is garbage already (`nix-store --gc
--print-dead`, the list `nix-collect-garbage --dry-run` prints; it scans the whole
store). Sized with `nix-store -q --size`. An upper bound: with auto-optimise-store a file
hard-linked into a live path frees nothing; and a little low, since the derivations nix
keeps for live outputs (keep-derivations) of the deleted systems come on top.

Then as root, through apply's backend (`sudo -n`; `needs-sudo` with the commands):

    delete      nix-env --profile /nix/var/nix/profiles/system --delete-generations N...
    bootloader  /nix/var/nix/profiles/system/bin/switch-to-configuration boot
    collect     nix-collect-garbage

The bootloader step is the boot default's own (through the profile link, so the default
entry stays what it was): the entries of the deleted generations go, and the kernels and
initrds only they used leave /boot. It runs before the collection, so no boot entry ever
points at a collected system. Plain nix-collect-garbage (no -d) leaves every other
profile alone. Reported: nix's own count of what it freed, and the free space of the
store and of /boot before and after.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..configrepo import NixinError
from ..truth.render import row, sub
from . import ApplyError, _lock, generations, privilege, proc, run_privileged, safety, system

CHUNK = 500  # store paths per nix-store call
_INVALID = re.compile(r"path '([^']+)' is not valid")
_FREED = re.compile(r"(\d+) store paths? deleted, ([\d.]+) (B|KiB|MiB|GiB|TiB) freed")
_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
NOTE = (
    "an estimate: files shared through auto-optimise-store hard links free nothing, and the "
    "derivations nix keeps for the deleted systems (keep-derivations) come on top"
)


def add_arguments(p: argparse.ArgumentParser) -> None:
    generations.add_arguments(p, preview=False)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="list what goes and estimate what it frees; run nothing as root",
    )


def gc(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        with _lock():
            return _gc(ns)
    except NixinError as e:
        return e.doc()


@dataclass
class _Caller:
    """What run_privileged reads of apply's Ctx: the command line, and whether to stream."""

    ns: argparse.Namespace
    human: bool


# --- the estimate -------------------------------------------------------------------------


def _nix_store(args: list[str], what: str) -> proc.Proc:
    p = proc.run(["nix-store", *args])
    if p.missing:
        raise NixinError("nix-missing", "nix-store is not on PATH")
    return proc.need(p, "nix-failed", what)


def _chunks(paths: list[str]):
    for i in range(0, len(paths), CHUNK):
        yield paths[i : i + CHUNK]


def _per_path(args: list[str], paths: list[str], what: str) -> list[str]:
    """`nix-store ARGS PATH...` over chunks; a path nix calls invalid (collected or removed
    meanwhile) is dropped and the chunk retried. The output lines."""
    out: list[str] = []
    for chunk in _chunks(sorted(set(paths))):
        chunk = list(chunk)
        while chunk:
            p = proc.run(["nix-store", *args, *chunk])
            if p.ok:
                out += [ln.strip() for ln in p.out.splitlines() if ln.strip()]
                break
            m = _INVALID.search(p.err)
            if p.missing or not m or m.group(1) not in chunk:
                _nix_store([*args, *chunk], what)  # raises with nix's message
            chunk.remove(m.group(1))
    return out


def _roots(drop: set[int]) -> list[str]:
    """The store paths of every GC root except the links of the generations in `drop`."""
    p = _nix_store(["--gc", "--print-roots"], "listing the GC roots")
    profiles = os.path.realpath(system.link(0).parent)
    names = {system.link(n).name for n in drop}
    out = []
    for line in p.out.splitlines():
        root, sep, path = line.rpartition(" -> ")
        if not sep or not path.strip().startswith("/"):
            continue
        root = root.strip().strip('"')
        if os.path.basename(root) in names and (
            os.path.realpath(os.path.dirname(root)) == profiles
        ):
            continue
        out.append(path.strip())
    return out


def _bytes(paths: set[str]) -> int:
    lines = _per_path(["--query", "--size"], sorted(paths), "sizing store paths")
    return sum(int(x) for x in lines if x.isdigit())


def _dead() -> set[str]:
    p = _nix_store(["--gc", "--print-dead"], "listing the garbage")
    return {ln.strip() for ln in p.out.splitlines() if ln.strip().startswith("/")}


def estimate(drop: list[dict[str, Any]], *, dead: bool) -> dict[str, Any]:
    """What deleting `drop` (generation rows) and collecting would free; `dead` adds what
    is garbage already (a whole-store scan, run alongside)."""
    t = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        dead_job = pool.submit(_dead) if dead else None
        only: set[str] = set()
        roots = None
        if drop:
            kept_roots = _roots({r["generation"] for r in drop})
            roots = len(kept_roots)
            live = set(_per_path(["--query", "--requisites"], kept_roots, "the roots' closure"))
            mine = _per_path(
                ["--query", "--requisites"],
                [r["store_path"] for r in drop],
                "the generations' closure",
            )
            only = set(mine) - live
        garbage = dead_job.result() if dead_job else None
    gens = {"paths": len(only), "bytes": _bytes(only) if only else 0}
    doc: dict[str, Any] = {"generations": gens, "dead": None, "roots": roots}
    total = dict(gens)
    if garbage is not None:
        rest = garbage - only
        doc["dead"] = {"paths": len(rest), "bytes": _bytes(rest) if rest else 0}
        total = {k: gens[k] + doc["dead"][k] for k in gens}
    doc.update({"total": total, "seconds": round(time.monotonic() - t, 2), "note": NOTE})
    return doc


# --- the verb -----------------------------------------------------------------------------


def _free(path: str) -> int | None:
    df = safety.disk_free(path)
    return df[0] if df else None


def _space(path: str) -> dict[str, Any]:
    """{path, total, free_before, free_after, freed}: the "after" half is filled by _after."""
    df = safety.disk_free(path)
    return {
        "path": path,
        "total": df[1] if df else None,
        "free_before": df[0] if df else None,
        "free_after": None,
        "freed": None,
    }


def _after(space: dict[str, Any]) -> None:
    space["free_after"] = _free(space["path"])
    if space["free_before"] is not None and space["free_after"] is not None:
        space["freed"] = space["free_after"] - space["free_before"]


def _row(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("generation", "date", "store_path", "kernel", "nixos_version", "commit", "message")
    return {k: r[k] for k in keys} | {"closure_size": None, "boot_entry": r["boot_entry"]}


def _freed(text: str) -> dict[str, Any] | None:
    m = _FREED.search(text)
    if not m:
        return None
    n = round(float(m.group(2)) * _UNITS[m.group(3)])
    return {"paths": int(m.group(1)), "bytes": n, "text": m.group(0)}


def _gc(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    if getattr(ns, "args", None):
        raise NixinError("usage", f"gc takes no arguments (got {' '.join(ns.args)!r})")
    from . import local_hostname  # the package's (tests replace it there)

    if ns.host != local_hostname():
        raise NixinError(
            "host-not-local",
            f"gc deletes this machine's ({local_hostname()}) generations; run nixin gc on "
            f"{ns.host}",
        )
    policy = generations.Policy.of(ns, default_keep=None)
    warnings: list[str] = []
    if not policy.given:
        warnings.append(
            "neither --keep nor --older-than: no generation is deleted, gc only collects "
            "what is garbage already"
        )
    j = generations.rows(policy, [], sizes=False)
    gens = list(reversed(j["rows"]))  # oldest first
    guard = j["guard"]
    drop = [r for r in gens if r["drop"]]
    if any(r["generation"] in guard.values() for r in drop):  # decide() never does this
        raise NixinError("internal", f"gc would delete a protected generation ({guard})")
    delete = [_row(r) for r in drop]
    if drop:
        size_of, _ = generations.closure_sizes([r["store_path"] for r in drop])
        for d in delete:
            d["closure_size"] = size_of.get(d["store_path"])
    numbers = [r["generation"] for r in drop]
    try:
        est = estimate(drop, dead=ns.dry_run)
    except NixinError as e:
        est = None
        warnings.append(f"no estimate: {e.message}")
    steps: list[privilege.Step] = []
    if numbers:
        steps.append(privilege.delete_step(system.PROFILE, numbers))
        steps.append(privilege.bootloader_step(system.PROFILE))
    steps.append(privilege.collect_step())
    bk: dict[str, privilege.Backend] = {s.name: privilege.Sudo() for s in steps}
    boot = _space(safety.BOOT)
    boot.update(
        {
            "entries_before": j["boot"]["generations"],
            "entries_after": None,
            "entries_source": j["boot"]["source"],
        }
    )
    store = _space(safety.STORE)
    doc: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(ns.dry_run),
        "policy": policy.doc(),
        "delete": delete,
        "keep": [r["generation"] for r in gens if not r["drop"]],
        "protected": guard,
        "estimate": est,
    }
    if ns.dry_run:
        commands = [
            {
                "step": s.name,
                "privileged": True,
                "argv": bk[s.name].argv(s),
                "command": proc.shown(bk[s.name].argv(s)),
                "backend": bk[s.name].name,
                "by_hand": bk[s.name].display(s),
            }
            for s in steps
        ]
        why = privilege.Sudo().probe()
        doc.update(
            {
                "commands": commands,
                "privilege": {"backend": "sudo", "ready": why is None, "why": why},
                "store": store,
                "boot": boot,
                "duration_s": round(time.monotonic() - t0, 2),
                "warnings": warnings,
            }
        )
        if not ns.json:
            doc["message"] = render_dry_run(doc, ns.host)
        return doc

    ran: list[str] = []
    progress: dict[str, Any] = {"deleted": [], "done": []}
    timings: dict[str, float] = {"prepare": round(time.monotonic() - t0, 2)}
    collect_out = ""
    for i, s in enumerate(steps):
        caller = _Caller(ns, human=not ns.json and s.name != "collect")  # no path-per-line
        t = time.monotonic()
        p = run_privileged(caller, bk, s, steps[i:], ran, progress)  # type: ignore[arg-type]
        timings[s.name] = round(time.monotonic() - t, 2)
        if s.name == "delete":
            progress["deleted"] = [n for n in numbers if not os.path.lexists(system.link(n))]
        if not p.ok:
            raise ApplyError(
                "gc-failed",
                f"{_what(s)} failed:\n{p.tail(12)}"
                + ("" if s.name == "collect" else "\nWhen it is fixed: nixin gc again"),
                step=s.name,
                privileged_commands=ran,
                **progress,
            )
        if s.name == "delete" and progress["deleted"] != numbers:
            left = sorted(set(numbers) - set(progress["deleted"]))
            raise ApplyError(
                "gc-failed",
                f"nix-env succeeded but generation(s) {generations.span(left)} are still "
                "there; nothing else ran",
                step=s.name,
                privileged_commands=ran,
                **progress,
            )
        if s.name == "collect":
            collect_out = p.out + "\n" + p.err
        progress["done"].append(s.name)
    timings["total"] = round(time.monotonic() - t0, 2)
    _after(store)
    _after(boot)
    left = [n for n, _ in system.generations()]
    boot["entries_after"] = generations.boot_entries(left)["generations"]
    freed = _freed(collect_out)
    if freed is None:
        warnings.append("nix-collect-garbage did not say what it freed")
    doc.update(
        {
            "deleted": progress["deleted"],
            "freed": freed,
            "store": store,
            "boot": boot,
            "generations_left": len(left),
            "privileged_commands": ran,
            "backends": {s.name: bk[s.name].name for s in steps},
            "timings": timings,
            "warnings": warnings,
        }
    )
    if not ns.json:
        doc["message"] = render(doc, ns.host)
    return doc


def _what(s: privilege.Step) -> str:
    return {
        "delete": "deleting the generations (nix-env --delete-generations)",
        "bootloader": "rewriting the boot menu (switch-to-configuration boot)",
        "collect": "collecting the garbage (nix-collect-garbage)",
    }[s.name]


# --- human output -------------------------------------------------------------------------


def _size(n: int | None) -> str:
    return generations.size_text(n)


def _gens(nums: list[int]) -> str:
    return generations.span(nums) if nums else "none"


def _estimate_text(e: dict[str, Any] | None) -> str:
    if e is None:
        return "unknown (see the warning)"
    g, d = e["generations"], e["dead"]
    text = f"about {_size(e['total']['bytes'])}: {_size(g['bytes'])} only those generations use"
    text += f" ({g['paths']} paths)"
    if d is not None:
        text += f", {_size(d['bytes'])} garbage already ({d['paths']} paths)"
    return text


def _entries(nums: list[int] | None) -> str:
    return "unknown" if nums is None else (_gens(nums) if nums else "none")


def render_dry_run(d: dict[str, Any], host: str) -> str:
    nums = [r["generation"] for r in d["delete"]]
    head = f"gc {host} --dry-run: would delete {len(nums)} generation(s) ({_gens(nums)}), "
    lines = [head + f"keep {len(d['keep'])} ({_gens(d['keep'])}); nothing was run"]
    for c in d["commands"]:
        lines.append(sub("[root]", c["by_hand"]))
    lines.append(row("frees", _estimate_text(d["estimate"])))
    s, b = d["store"], d["boot"]
    lines.append(row("store", f"{s['path']}: {_size(s['free_before'])} free"))
    entries = _entries(b["entries_before"])
    lines.append(
        row(b["path"], f"{_size(b['free_before'])} free of {_size(b['total'])}; entries {entries}")
    )
    p = d["privilege"]
    lines.append(row("sudo", "ready" if p["ready"] else f"not ready: {p['why']}"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)


def render(d: dict[str, Any], host: str) -> str:
    head = f"gc {host}: deleted {len(d['deleted'])} generation(s) ({_gens(d['deleted'])}), "
    lines = [head + f"{d['generations_left']} left ({_gens(d['keep'])})"]
    for c in d["privileged_commands"]:
        lines.append(sub("[root]", c))
    f, e = d["freed"], d["estimate"]
    if f:
        text = f"{_size(f['bytes'])}, {f['paths']} store paths (nix-collect-garbage)"
        if e:
            text += f"; estimated {_size(e['total']['bytes'])}"
        lines.append(row("freed", text))
    s, b = d["store"], d["boot"]
    lines.append(
        row("store", f"{s['path']}: free {_size(s['free_before'])} → {_size(s['free_after'])}")
    )
    lines.append(
        row(
            b["path"],
            f"free {_size(b['free_before'])} → {_size(b['free_after'])}; entries "
            f"{_entries(b['entries_before'])} → {_entries(b['entries_after'])}",
        )
    )
    t = d["timings"]
    parts = ", ".join(f"{k} {t[k]} s" for k in ("delete", "bootloader", "collect") if k in t)
    lines.append(row("took", f"{t['total']} s ({parts})"))
    lines += [row("warning", w) for w in d["warnings"]]
    return "\n".join(lines)
