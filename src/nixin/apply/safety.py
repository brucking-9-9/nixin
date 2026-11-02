"""What a new system does to boot: whether it needs a reboot, and whether it fits on /boot.

`compare(old, new)` reads two toplevels (no nix, no root): the `kernel`, `initrd` and
`kernel-modules` links, `kernel-params`, the bootspec (`boot.json`) and every
`specialisation/*`'s kernel and initrd. A changed kernel, initrd, module tree or kernel
command line of the default entry only takes effect at the next boot: `reboot_needed`.

The bootloader step (systemd-boot's builder) keeps the newest `configurationLimit`
generations: it deletes the kernel/initrd files no kept generation uses, then copies
each distinct kernel and initrd store file the kept ones need. `boot_space` models
that. The limit is read from the new system's own builder (`CONFIGURATION_LIMIT`,
reached from `bin/switch-to-configuration`); /boot now = the files of the newest N
generations, after = the newest N-1 plus the new system (a rollback keeps the window).
Needed = files after minus files now; it must fit in `df /boot`'s free space plus what
the builder deletes first. More than 80 % of that is a warning, more than all of it is
`full`: apply and rollback refuse (the bootloader step would fail half-way, after the
profile moved). When the limit cannot be read (another bootloader), "on /boot now"
means "the running system uses it" and nothing counts as freed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import system

BOOT = "/boot"
STORE = "/nix/store"
WARN_RATIO = 0.8
LINKS = (("kernel", "kernel"), ("initrd", "initrd"), ("modules", "kernel-modules"))
_VOLATILE = {"init", "toplevel"}  # differ for every system; not bootloader content


def disk_free(path: str) -> tuple[int, int] | None:
    """(free bytes for a user, as `df` shows them, total bytes) of the filesystem at path."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def _real(out: str, name: str) -> str | None:
    return system.realpath(str(Path(out) / name))


def _params(out: str) -> list[str] | None:
    try:
        return (Path(out) / "kernel-params").read_text().split()
    except OSError:
        return None


def _specialisations(out: str) -> dict[str, str]:
    d = Path(out) / "specialisation"
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return {}
    return {n: p for n in names if (p := system.realpath(str(d / n)))}


def _bootspec(out: str) -> dict[str, Any] | None:
    """boot.json without the paths that differ for every system, specialisations' too."""
    try:
        doc = json.loads((Path(out) / "boot.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None

    def strip(spec: Any) -> Any:
        if not isinstance(spec, dict):
            return spec
        out = {}
        for k, v in spec.items():
            if k == "org.nixos.bootspec.v1" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk not in _VOLATILE}
            elif k == "org.nixos.specialisation.v1" and isinstance(v, dict):
                v = {name: strip(s) for name, s in v.items()}
            out[k] = v
        return out

    return strip(doc)


def boot_files(out: str) -> dict[str, list[str]]:
    """{store file: [entries using it]} for the kernel and initrd of the default entry
    and of each specialisation (what the bootloader copies to /boot)."""
    files: dict[str, list[str]] = {}
    entries = {"default": out, **_specialisations(out)}
    for label, top in entries.items():
        for name in ("kernel", "initrd"):
            p = _real(top, name)
            if p:
                files.setdefault(p, []).append(label)
    return files


def _size(p: str) -> int:
    try:
        return os.stat(p).st_size
    except OSError:
        return 0


def _changes(old: str, new: str) -> list[str]:
    return [key for key, name in LINKS if _real(old, name) != _real(new, name)]


_LIMIT = re.compile(rb'CONFIGURATION_LIMIT = int\("(\d+)"\)|configurationLimit = (\d+)\b')


def boot_window(out: str) -> int | None:
    """systemd-boot's configurationLimit as built into `out` (0: no limit), found by
    following store paths with "boot" in their name from bin/switch-to-configuration
    (the installer script, then the builder); None when there is none."""
    store = re.compile(re.escape(STORE.encode()) + rb"/[0-9a-z]{32}-[^\s\"'/\x00;)]*")
    todo = [Path(out) / "bin" / "switch-to-configuration"]
    seen: set[Path] = set()
    while todo and len(seen) < 24:
        f = todo.pop(0)
        if f in seen:
            continue
        seen.add(f)
        if f.is_dir():
            todo += sorted((f / "bin").glob("*")) if (f / "bin").is_dir() else []
            continue
        try:
            if f.stat().st_size > 16 * 1024**2:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        m = _LIMIT.search(data)
        if m:
            return int(m.group(1) or m.group(2))
        for ref in dict.fromkeys(store.findall(data)):
            name = ref.rsplit(b"/", 1)[-1]
            if b"boot" in name and not name.endswith((b".drv", b".efi")):
                todo.append(Path(ref.decode(errors="replace")))
    return None


def _newest(items: list, limit: int) -> list:
    """The last `limit` items; all of them for 0 (configurationLimit's "no limit")."""
    return items[-limit:] if limit > 0 else list(items)


def _files_of(paths: list[str]) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {}
    for top in paths:
        for f, used in boot_files(top).items():
            files.setdefault(f, [])
            files[f] += [u for u in used if u not in files[f]]
    return files


def boot_space(old: str | None, new: str, *, generation: int | None = None) -> dict[str, Any]:
    """The boot_space check for switching to `new`: as a new generation (plan, apply), or
    to the existing `generation` (rollback: the profile moves, the window stays)."""
    limit = boot_window(new)
    window: list[int] | None = None
    if limit is None:
        now = boot_files(old) if old else {}
        after = boot_files(new)
    else:
        gens = [p for _, p in system.generations()]
        nums = [n for n, _ in system.generations()]
        window = _newest(nums, limit)
        # on /boot now: what the running system's builder kept at its last install
        last = boot_window(old) if old else None
        if last is None:
            now = boot_files(old) if old else {}
        else:
            now = _files_of(_newest(gens, last))
        if generation is not None:  # the profile moves back; the window stays
            after = _files_of(_newest(gens, limit))
        else:  # a new generation: it and the newest limit-1 others
            after = _files_of([*(_newest(gens, limit - 1) if limit != 1 else []), new])
    fresh = [
        {"path": p, "size": _size(p), "for": used}
        for p, used in sorted(after.items())
        if p not in now
    ]
    needed = sum(f["size"] for f in fresh)
    freed = sum(_size(p) for p in now if p not in after) if limit is not None else 0
    df = disk_free(BOOT)
    free, total = df if df else (None, None)
    room = None if free is None else free + freed
    if room is None:
        state, ratio = "unknown", None
    elif needed == 0:
        state, ratio = "ok", 0.0
    else:
        ratio = round(needed / room, 3) if room else None
        state = "full" if needed > room else "warn" if needed > WARN_RATIO * room else "ok"
    return {
        "path": BOOT,
        "limit": limit,
        "window": window,
        "files": fresh,
        "needed": needed,
        "freed": freed,
        "free": free,
        "total": total,
        "ratio": ratio,
        "state": state,
    }


def compare(
    old: str | None, new: str | None, *, generation: int | None = None
) -> dict[str, Any] | None:
    """The boot block of plan/apply/rollback for switching from `old` to `new` (rollback:
    to the existing `generation`); None when either toplevel is missing (another host, no
    running system)."""
    if not old or not new or not os.path.isdir(old) or not os.path.isdir(new):
        return None
    doc: dict[str, Any] = {}
    for key, name in LINKS:
        a, b = _real(old, name), _real(new, name)
        doc[key] = {"changed": a != b, "from_path": a, "to_path": b}
    doc["kernel"]["from"] = system.kernel_version(old)
    doc["kernel"]["to"] = system.kernel_version(new)
    pa, pb = _params(old), _params(new)
    doc["kernel_params"] = {"changed": pa != pb, "from": pa, "to": pb}
    sa, sb = _bootspec(old), _bootspec(new)
    keys = []
    if sa is not None and sb is not None:
        va, vb = sa.get("org.nixos.bootspec.v1") or {}, sb.get("org.nixos.bootspec.v1") or {}
        keys = sorted(k for k in set(va) | set(vb) if va.get(k) != vb.get(k))
        if sa.get("org.nixos.specialisation.v1") != sb.get("org.nixos.specialisation.v1"):
            keys.append("specialisations")
        rest = (set(sa) | set(sb)) - {"org.nixos.bootspec.v1", "org.nixos.specialisation.v1"}
        keys += sorted(k for k in rest if sa.get(k) != sb.get(k))
    doc["bootloader"] = {"bootspec": sb is not None, "changed": bool(keys), "keys": keys}
    specs_a, specs_b = _specialisations(old), _specialisations(new)
    specs = []
    for name in sorted(set(specs_a) | set(specs_b)):
        if name not in specs_a:
            specs.append({"name": name, "state": "added", "changed": []})
        elif name not in specs_b:
            specs.append({"name": name, "state": "removed", "changed": []})
        else:
            ch = _changes(specs_a[name], specs_b[name])
            spec = {"name": name, "state": "changed" if ch else "same", "changed": ch}
            if "kernel" in ch:
                spec["kernel"] = {
                    "from": system.kernel_version(specs_a[name]),
                    "to": system.kernel_version(specs_b[name]),
                }
            specs.append(spec)
    doc["specialisations"] = specs
    reasons = [key for key, _ in LINKS if doc[key]["changed"]]
    if doc["kernel_params"]["changed"]:
        reasons.append("kernel_params")
    doc["reboot_needed"] = bool(reasons)
    doc["reasons"] = reasons
    booted = system.realpath(system.BOOTED)
    doc["pending_reboot"] = bool(booted and os.path.isdir(booted) and _changes(booted, old))
    space = boot_space(old, new, generation=generation)
    doc["boot_space"] = space
    doc["boot_entry"] = None
    if generation is not None and space["window"] is not None:
        doc["boot_entry"] = generation in space["window"]
    doc["warnings"] = _warnings(doc, generation)
    return doc


def _mib(n: int | None) -> str:
    return "?" if n is None else f"{n / 1024**2:.1f} MiB"


def reboot_text(b: dict[str, Any]) -> str:
    """`kernel 7.2.12 → 7.3.0, initrd, kernel-modules`: what makes the reboot needed."""
    parts = []
    for r in b["reasons"]:
        if r == "kernel":
            k = b["kernel"]
            parts.append(f"kernel {k['from'] or '?'} → {k['to'] or '?'}")
        else:
            parts.append({"modules": "kernel-modules", "kernel_params": "kernel params"}.get(r, r))
    return ", ".join(parts)


def space_text(s: dict[str, Any]) -> str:
    """`+57.3 MiB of 80.4 MiB free (71 %)`, with what the bootloader deletes first."""
    pct = f" ({round(s['ratio'] * 100)} %)" if s.get("ratio") is not None else ""
    freed = f" + {_mib(s['freed'])} it deletes first" if s.get("freed") else ""
    return f"+{_mib(s['needed'])} of {_mib(s['free'])} free{freed}{pct}"


def _warnings(b: dict[str, Any], generation: int | None) -> list[str]:
    out = []
    if b["reboot_needed"]:
        out.append(f"needs a reboot to take effect: {reboot_text(b)}")
    s = b["boot_space"]
    if s["state"] == "full":
        out.append(f"{s['path']} is too small: {space_text(s)}; the switch would be refused")
    elif s["state"] == "warn":
        out.append(f"{s['path']} is tight: {space_text(s)}")
    if b["boot_entry"] is False:
        newest = s["window"][-1] if s["window"] else "?"
        out.append(
            f"generation {generation} is not among the {s['limit']} newest the bootloader keeps "
            f"(configurationLimit): it gets no boot entry, so the next boot starts generation "
            f"{newest} again. To keep it across reboots, apply its config as a new generation "
            "instead (nixin apply)"
        )
    return out


def full_error_message(b: dict[str, Any]) -> str:
    s = b["boot_space"]
    files = ", ".join(f"{Path(f['path']).parent.name} {_mib(f['size'])}" for f in s["files"])
    freed = f" (+ {_mib(s['freed'])} the bootloader deletes first)" if s["freed"] else ""
    return (
        f"{s['path']} has {_mib(s['free'])} free{freed} and the new system adds "
        f"{_mib(s['needed'])} "
        f"of kernels and initrds ({files}); the bootloader step would fail half-way. Free "
        f"{s['path']} first (keep fewer generations: boot.loader.systemd-boot."
        "configurationLimit; or sudo nix-collect-garbage --delete-older-than 7d && sudo "
        "/run/current-system/bin/switch-to-configuration boot, which drops their files), "
        "then try again"
    )
