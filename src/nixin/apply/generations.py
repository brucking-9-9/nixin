"""generations: this machine's system generations, what each costs, and what gc would drop.

The rows are history's (`history.generation_rows`: number, date, store path, kernel,
NixOS version, current/booted/default, and the commit nixin applied when a config with
`installer/history.jsonl` is at hand), plus:

closure_size  `nix path-info -S` of the store path: its whole closure, paths shared with
              other generations included (so the sizes do not add up; `gc --dry-run`
              estimates what deleting some frees). A store path's closure never changes,
              so the sizes are cached by path (`apply/closure-sizes.json`).
boot_entry    whether the boot menu has an entry for it: systemd-boot's entry files when
              /boot is readable (`loader/entries/nixos-generation-N.conf`), else the
              newest `configurationLimit` generations, the window the boot default's
              builder keeps (read as plan's boot check reads it); null for another
              bootloader.
drop          a `nixin gc` with the same --keep / --older-than would delete it.

The policy (`decide`) is shared with gc: the newest --keep N are kept; with --older-than
DAYS every generation made since then is kept, and the newest one made before (it was
running at the cutoff: nix-collect-garbage --delete-older-than's rule); with both, a
generation is kept when either keeps it. The running generation, the booted one and the
boot default are never dropped. Without either flag, `generations` shows `gc --keep 3`
(DEFAULT_KEEP) and `gc` deletes no generation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..cache import default_root
from ..configrepo import ENV_CONFIG, ConfigRepo, NixinError
from ..truth.render import term_width
from . import history, proc, safety, system
from .closure import human_size

DEFAULT_KEEP = 3
SIZES = "closure-sizes.json"
_ENTRY = re.compile(r"^nixos-generation-(\d+)(?:-specialisation-.+)?\.conf$")


def add_arguments(p: argparse.ArgumentParser, *, preview: bool) -> None:
    """--keep / --older-than; `preview` (generations): neither flag shows --keep 3."""
    default = f" (with neither flag: {DEFAULT_KEEP})" if preview else ""
    p.add_argument("--keep", type=int, metavar="N", help=f"keep the newest N generations{default}")
    p.add_argument(
        "--older-than",
        type=float,
        metavar="DAYS",
        help="keep every generation made in the last DAYS days, and the one running then",
    )


# --- the policy -------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    keep: int | None
    older_than: float | None  # days

    @classmethod
    def of(cls, ns: argparse.Namespace, *, default_keep: int | None) -> Policy:
        keep, days = ns.keep, ns.older_than
        if keep is not None and keep < 1:
            raise NixinError("usage", "--keep must be 1 or more (the running one stays anyway)")
        if days is not None and days < 0:
            raise NixinError("usage", "--older-than must be 0 days or more")
        if keep is None and days is None:
            keep = default_keep
        return cls(keep, days)

    @property
    def given(self) -> bool:
        return self.keep is not None or self.older_than is not None

    def doc(self) -> dict[str, Any]:
        return {"keep": self.keep, "older_than_days": self.older_than}

    def text(self) -> str:
        parts = []
        if self.keep is not None:
            parts.append(f"--keep {self.keep}")
        if self.older_than is not None:
            parts.append(f"--older-than {self.older_than:g}")
        return " ".join(parts)


def _mtime(n: int) -> float | None:
    try:
        return os.lstat(system.link(n)).st_mtime
    except OSError:
        return None


def decide(
    numbers: list[int],
    policy: Policy,
    guard: dict[str, int | None],
    now: float | None = None,
) -> dict[int, list[str]]:
    """{generation: why it stays} for every generation (oldest first in `numbers`); an
    empty list means the policy drops it. Reasons: current, booted, default (never
    dropped), newest (within --keep), recent (made after the --older-than cutoff),
    cutoff (the newest one made before it), no-policy (neither flag: nothing drops)."""
    nums = sorted(numbers)
    why: dict[int, list[str]] = {n: [k for k, g in guard.items() if g == n] for n in nums}
    if not policy.given:
        for n in nums:
            why[n].append("no-policy")
        return why
    if policy.keep is not None:
        for n in nums[-policy.keep :]:
            why[n].append("newest")
    if policy.older_than is not None:
        cutoff = (time.time() if now is None else now) - policy.older_than * 86400
        at_cutoff = False
        for n in reversed(nums):
            t = _mtime(n)
            if t is not None and t >= cutoff:
                why[n].append("recent")
            elif not at_cutoff:
                why[n].append("cutoff")
                at_cutoff = True
    return why  # with both flags: kept when either keeps it (the union of the reasons)


# --- what each costs, which have a boot entry ---------------------------------------------


def _sizes_file() -> Path:
    return default_root() / "apply" / SIZES


def closure_sizes(paths: list[str]) -> tuple[dict[str, int | None], dict[str, Any]]:
    """`nix path-info -S` for each store path, from the cache when it was measured before
    (a closure never changes). ({path: bytes or None}, {cached, measured, seconds, error})."""
    f = _sizes_file()
    try:
        known = json.loads(f.read_text())
        if not isinstance(known, dict):
            known = {}
    except (OSError, ValueError):
        known = {}
    want = sorted({p for p in paths if not isinstance(known.get(p), int)})
    t = time.monotonic()
    error = None
    if want:
        p = proc.run(["nix", "path-info", "--closure-size", *want])
        if p.missing:
            error = "nix is not on PATH"
        elif not p.ok:
            error = p.tail(3) or f"nix path-info exited {p.rc}"
        for line in p.out.splitlines() if p.ok else []:
            parts = line.split()
            if len(parts) >= 2 and parts[-1].isdigit():
                known[parts[0]] = int(parts[-1])
        keep = {k: v for k, v in known.items() if k in set(paths) and isinstance(v, int)}
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(keep, sort_keys=True))
            os.replace(tmp, f)
        except OSError:
            pass
    sizes = {p: known.get(p) if isinstance(known.get(p), int) else None for p in paths}
    info = {
        "cached": len(set(paths)) - len(want),
        "measured": sum(1 for p in want if sizes.get(p) is not None),
        "seconds": round(time.monotonic() - t, 2),
        "error": error,
    }
    return sizes, info


def boot_entries(numbers: list[int]) -> dict[str, Any]:
    """Which generations the boot menu has: {source, limit, generations[]}. `source` is
    `entries` (systemd-boot's files, /boot readable), `configurationLimit` (the newest N,
    N read from the boot default's builder), or null (unknown: generations is null)."""
    d = Path(safety.BOOT) / "loader" / "entries"
    default = system.realpath(system.PROFILE)
    limit = safety.boot_window(default) if default else None
    try:
        names = os.listdir(d)
    except OSError:
        names = None
    if names is not None:
        found = {int(m.group(1)) for n in names if (m := _ENTRY.match(n))}
        return {"source": "entries", "limit": limit, "generations": sorted(found)}
    if limit is None:
        return {"source": None, "limit": None, "generations": None}
    window = sorted(numbers)[-limit:] if limit > 0 else sorted(numbers)
    return {"source": "configurationLimit", "limit": limit, "generations": window}


# --- the verb -----------------------------------------------------------------------------


def generations(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _generations(ns)
    except NixinError as e:
        return e.doc()


def _entries(ns: argparse.Namespace, warnings: list[str]) -> tuple[list, dict | None]:
    """installer/history.jsonl of the config: --config or $NIXIN_CONFIG (which must
    exist), else /etc/nixos when it is there; without one the rows have no commits."""
    explicit = ns.config or os.environ.get(ENV_CONFIG)
    try:
        repo = ConfigRepo.locate(ns.config)
    except NixinError:
        if explicit:
            raise
        return [], None
    from . import authoritative_path

    auth = authoritative_path(getattr(ns, "authoritative", None))
    if auth.exists() and auth.resolve() == repo.path:
        warnings.append(
            f"{repo.path} is the authoritative tree: its {system.HISTORY} lags the "
            "workspace's by one apply (pass the workspace with --config)"
        )
    entries = system.read_history(repo.path)
    return entries, {"path": str(repo.path / system.HISTORY), "entries": len(entries)}


def rows(policy: Policy, entries: list[dict[str, Any]], *, sizes: bool = True) -> dict[str, Any]:
    """history's rows plus closure_size, boot_entry, drop and kept_by; the summary."""
    j = history.generation_rows(entries)
    out = j["rows"]
    numbers = [r["generation"] for r in out]
    guard = {"current": j["current"], "booted": j["booted"], "default": j["default"]}
    why = decide(numbers, policy, guard)
    boot = boot_entries(numbers)
    size_of, size_info = closure_sizes([r["store_path"] for r in out]) if sizes else ({}, None)
    for r in out:
        n = r["generation"]
        r["closure_size"] = size_of.get(r["store_path"])
        r["boot_entry"] = None if boot["generations"] is None else n in boot["generations"]
        r["kept_by"] = why[n]
        r["drop"] = not why[n]
    j.update({"guard": guard, "boot": boot, "sizes": size_info})
    return j


def _generations(ns: argparse.Namespace) -> dict[str, Any]:
    if getattr(ns, "args", None):
        raise NixinError("usage", f"generations takes no arguments (got {' '.join(ns.args)!r})")
    from . import local_hostname  # the package's (tests replace it there)

    policy = Policy.of(ns, default_keep=DEFAULT_KEEP)
    warnings: list[str] = []
    entries, hist = _entries(ns, warnings)
    if ns.host != local_hostname():
        raise NixinError(
            "host-not-local",
            f"the generations on this machine are {local_hostname()}'s; run nixin "
            f"generations on {ns.host}",
        )
    j = rows(policy, entries)
    gens = j["rows"]
    if j["sizes"]["error"]:
        warnings.append(f"closure sizes unknown: {j['sizes']['error']}")
    drop = sorted(r["generation"] for r in gens if r["drop"])
    doc = {
        "ok": True,
        "generations": gens,
        "total": len(gens),
        "current": j["current"],
        "booted": j["booted"],
        "default": j["default"],
        "policy": policy.doc(),
        "drop": drop,
        "boot": j["boot"],
        "sizes": j["sizes"],
        "history": None if hist is None else {**hist, "unmatched": j["unmatched"]},
        "warnings": warnings,
    }
    if not ns.json:
        doc["message"] = render(doc, ns.host, policy)
    return doc


# --- human output -------------------------------------------------------------------------


def _flags(r: dict[str, Any]) -> str:
    return history.flags(r) + ("e" if r["boot_entry"] else " ")


def render(d: dict[str, Any], host: str, policy: Policy) -> str:
    gens = d["generations"]
    drop = d["drop"]
    head = f"generations {host}: {d['total']}"
    if policy.given:
        head += f"; nixin gc {policy.text()} would delete {len(drop)}"
        if drop:
            head += f" ({span(drop)})"
    lines = [head]
    if gens:
        kw = max(6, *(len(r["kernel"] or "?") for r in gens))
        gw = max(3, *(len(str(r["generation"])) for r in gens))
        lines.append(
            f"{'':4} {'gen':>{gw}}  {'date':<16}  {'kernel':<{kw}}  {'closure':>9}  gc    "
            "commit   message"
        )
        width = term_width()
        for r in gens:
            size = size_text(r["closure_size"])
            line = (
                f"{_flags(r)} {r['generation']:>{gw}}  {history._local(r['date']):<16}  "
                f"{r['kernel'] or '?':<{kw}}  {size:>9}  {'drop' if r['drop'] else '    '}  "
                f"{history._what(r)}"
            ).rstrip()
            lines.append(line if len(line) <= width else line[: width - 1] + "…")
        lines.append("* running now, b booted, d boot default, e has a boot entry")
    b = d["boot"]
    if b["source"] == "configurationLimit":
        lines.append(
            f"boot entries: the newest {b['limit']} (configurationLimit; /boot is not readable)"
        )
    elif b["source"] is None:
        lines.append("boot entries: unknown (not systemd-boot)")
    lines.append(
        "closure sizes overlap (shared paths); nixin gc --dry-run estimates what deleting frees"
    )
    lines += [f"warning   {w}" for w in d["warnings"]]
    return "\n".join(lines)


def size_text(n: int | None) -> str:
    """`22.0 GiB` (unsigned), `?` for unknown."""
    return "?" if n is None else human_size(n).lstrip("+")


def span(nums: list[int]) -> str:
    """`87–117, 119`: runs of consecutive numbers."""
    out: list[str] = []
    start = prev = nums[0]
    for n in [*nums[1:], None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out)
