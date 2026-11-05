"""The compatibility checks a hold can need, as `checks[] = {id, level, text, data}`.

Levels: `info` (said), `warn` (a UI's confirm step shows it), `block` (something will not
build or does not hold). Text-only checks run before anything is written (a nested
attribute, a language package set: `pin-unsupported`). The eval checks run on the host as
the generated module leaves it (after the write, or on `pins --check` / `update --hold`):
one `nix eval` with the markers the tests' fake nix answers to (`__nixinPinCheck`), plus
`nix-store` queries for the dependents of a pinned package. Nothing is built.

Texts are the ones docs/DESIGN-pins.md fixes; the ids are stable for the UIs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from ..configrepo import NixinError, nix_value
from ..writer.nixtext import parse_attrpath

RUNNING_SYSTEM = "/run/current-system"
# a package set an overlay on a member does not reach
LANG_SETS = re.compile(
    r"^(python\d*Packages|pythonPackages|nodePackages(_latest)?|haskellPackages|haskell\.packages"
    r"|perlPackages|rubyPackages|luaPackages|rPackages|ocamlPackages|emacsPackages|vimPlugins"
    r"|qt6Packages|libsForQt5|kdePackages|gnomeExtensions|php\d*Packages|beamPackages|xorg)$"
)
# runtime library families whose ABI the system's drivers share (gl-skew)
GL_NAMES = re.compile(
    r"^(mesa|libglvnd|libGL|wayland|qtbase|gtk3|gtk4|gtk\+3|vulkan-loader|libdrm)$"
)
DEPENDENTS_WARN = 5


def level_order(level: str) -> int:
    return {"block": 0, "warn": 1, "info": 2}.get(level, 3)


def check(id_: str, level: str, text: str, **data: Any) -> dict[str, Any]:
    return {"id": id_, "level": level, "text": text, "data": data}


# --- before writing: text only ------------------------------------------------------------


def refuse_unsupported(attr: str) -> None:
    """`pin-unsupported` for an attribute an overlay cannot hold."""
    parts = parse_attrpath(attr)
    if len(parts) == 1:
        return
    head = parts[0]
    if LANG_SETS.match(head) or (len(parts) > 2 and parts[0] == "haskell"):
        raise NixinError(
            "pin-unsupported",
            f"{attr} lives in a package set ({head}); an overlay on it does not reach the set "
            f"(its members are built from the set's own fix point). Not pinnable; hold the "
            "whole nixpkgs instead: nixin update --to <rev>",
        )
    raise NixinError(
        "pin-unsupported",
        f"{attr} is a nested attribute; nixin pins top-level attributes of pkgs (and the "
        "kernel). Hold the whole nixpkgs instead: nixin update --to <rev>",
    )


# --- the eval ------------------------------------------------------------------------------


def check_apply(attrs: list[str], *, kernel: bool) -> str:
    """The `--apply` function: per attribute version/pname/outPath/inputs, the kernel and the
    out-of-tree modules' buildability (drvPath under tryEval), the specialisations' too."""
    lists = nix_value([parse_attrpath(a) for a in attrs])
    return (
        "c: let lib = c.pkgs.lib; "
        "safe = x: let r = builtins.tryEval x; in if r.success then r.value else null; "
        "orElse = v: d: if v == null then d else v; "
        "builds = p: (builtins.tryEval (builtins.seq p.drvPath true)).success; "
        'name = p: safe (p.pname or p.name or "?"); '
        "mods = cfg: map (p: { name = name p; ok = builds p; }) "
        "(orElse (safe cfg.boot.extraModulePackages) [ ]); "
        'nvidia = cfg: if (safe (builtins.elem "nvidia" cfg.services.xserver.videoDrivers)) '
        "== true then { name = name cfg.hardware.nvidia.package; ok = builds "
        "cfg.hardware.nvidia.package; } else null; "
        "kernelOf = cfg: safe (cfg.boot.kernelPackages.kernel.version); "
        "info = a: let r = builtins.tryEval (lib.attrByPath a null c.pkgs); in "
        "if r.success && r.value != null then { "
        "version = safe (r.value.version or null); pname = safe (r.value.pname or null); "
        "out = safe (r.value.outPath or null); "
        "inputs = map name (orElse (safe ((r.value.buildInputs or [ ]) ++ "
        "(r.value.propagatedBuildInputs or [ ]))) [ ]); } else null; "
        "in { __nixinPinCheck = true; "
        f"attrs = map info {lists}; "
        "glibc = safe (c.pkgs.glibc.version); "
        "useGlobalPkgs = safe (c.config.home-manager.useGlobalPkgs or null); "
        + (
            "kernel = kernelOf c.config; modules = mods c.config; nvidia = nvidia c.config; "
            "specialisations = lib.mapAttrs (n: s: let sc = (c.extendModules { modules = "
            "[ s.configuration ]; }).config; in { kernel = kernelOf sc; modules = mods sc; "
            "nvidia = nvidia sc; }) (orElse (safe c.config.specialisation) { }); "
            if kernel
            else "kernel = kernelOf c.config; "
        )
        + "}"
    )


def pinned_glibc_apply(url: str, sha256: str) -> str:
    """`nix eval --expr`: the pinned nixpkgs' glibc version (offline when nix has the tree)."""
    return (
        f"(import (builtins.fetchTarball {{ url = {json.dumps(url)}; sha256 = {json.dumps(sha256)}; }}) "
        "{ system = builtins.currentSystem; config = { }; overlays = [ ]; }).glibc.version"
    )


# --- nix-store: dependents ------------------------------------------------------------------


def nix_store(args: list[str]) -> list[str] | None:
    """`nix-store ARGS` as lines; None when nix-store is missing or the query fails."""
    exe = shutil.which("nix-store")
    if exe is None:
        return None
    proc = subprocess.run(
        [exe, *args], check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


STORE = nix_store  # tests swap this


def dependents(out: str | None, system: str = RUNNING_SYSTEM) -> list[str] | None:
    """Store paths of the running system's closure that depend on `out` (`out` itself not
    counted); None when the path is not in the store or there is no running system."""
    if not out or not os.path.exists(system):
        return None
    refs = STORE(["-q", "--referrers-closure", out])
    if refs is None:
        return None
    closure = STORE(["-qR", system])
    if closure is None:
        return None
    return sorted((set(refs) & set(closure)) - {out})


# --- judging --------------------------------------------------------------------------------


def judge(
    pins: dict[str, dict[str, Any]],
    *,
    got: dict[str, Any] | None,
    lock_rev: str | None,
    pinned_glibc: dict[str, str | None],
    set_refs: list[tuple[str, int, str]],
    with_dependents: bool = True,
) -> list[dict[str, Any]]:
    """The checks for these pins from one eval's answer (`got`, None: no eval)."""
    out: list[dict[str, Any]] = []
    answers: dict[str, Any] = {}
    if got:
        flat = [a for p in pins.values() for a in p["attrs"]]
        answers = dict(zip(flat, got.get("attrs") or [], strict=False))
    for name in sorted(pins):
        p = pins[name]
        mode = p.get("mode") or "attr"
        if lock_rev and p.get("rev") == lock_rev:
            out.append(
                check(
                    "pin-stale",
                    "info",
                    f"pin {json.dumps(name)} holds nothing yet: flake.lock is still at "
                    f"{lock_rev[:7]}, the revision it pins; it takes effect once the lock moves "
                    "(nixin update)",
                    pin=name,
                )
            )
        if mode == "kernel":
            out.append(
                check(
                    "kernel-toolchain",
                    "info",
                    "out-of-tree modules are built with the new nixpkgs' toolchain against the "
                    "held kernel; vermagic is by version, so they load. A compiler-version "
                    f"complaint means: nixin pin {name} --whole-set",
                    pin=name,
                )
            )
        if mode in ("kernel", "kernel-set") and got:
            held = got.get("kernel")
            dropped = [m for m in (got.get("modules") or []) if not m.get("ok")]
            nv = got.get("nvidia")
            if nv and not nv.get("ok"):
                dropped.append(nv)
            for sname, s in (got.get("specialisations") or {}).items():
                if s.get("kernel") != held:  # its own kernel: not this hold's business
                    continue
                dropped += [
                    {**m, "name": f"{m.get('name')} ({sname})"}
                    for m in (s.get("modules") or []) + ([s["nvidia"]] if s.get("nvidia") else [])
                    if not m.get("ok")
                ]
            if dropped:
                names = ", ".join(str(m.get("name")) for m in dropped)
                out.append(
                    check(
                        "kernel-modules-dropped",
                        "block",
                        f"kernel held at {held or '?'} but {names} from the new nixpkgs does not "
                        f"build for it (the eval of its derivation throws). Hold the module set "
                        f"too: nixin pin {name} --whole-set",
                        pin=name,
                        modules=dropped,
                    )
                )
            if held and p.get("version") and held != p["version"]:
                out.append(
                    check(
                        "pin-not-holding",
                        "block",
                        f"pin {json.dumps(name)} should hold the kernel at {p['version']} but the "
                        f"host evaluates to {held}: boot.kernelPackages is not read from "
                        f"pkgs.{p['attrs'][0]} any more. nixin unpin {name}, then pin again",
                        pin=name,
                    )
                )
            for file, line, attr in set_refs:
                if attr != p["attrs"][1]:
                    out.append(
                        check(
                            "kernel-set-mismatch",
                            "warn",
                            f"{file}:{line} takes pkgs.{attr}, not config.boot.kernelPackages; "
                            "with the kernel held they no longer match",
                            pin=name,
                            file=file,
                            line=line,
                        )
                    )
            continue
        # a plain attribute pin
        for attr in p["attrs"]:
            a = answers.get(attr)
            if got and a is None:
                out.append(
                    check(
                        "pin-not-holding",
                        "block",
                        f"pin {json.dumps(name)}: pkgs.{attr} does not evaluate any more "
                        f"(removed or renamed in the new nixpkgs?). nixin unpin {name}",
                        pin=name,
                        attr=attr,
                    )
                )
                continue
            if a and p.get("version") and a.get("version") != p["version"]:
                out.append(
                    check(
                        "pin-not-holding",
                        "block",
                        f"pin {json.dumps(name)} should hold {attr} at {p['version']} but the host "
                        f"evaluates to {a.get('version')}; another overlay or override wins. "
                        f"nixin unpin {name}",
                        pin=name,
                        attr=attr,
                    )
                )
            sys_glibc, pin_glibc = (got or {}).get("glibc"), pinned_glibc.get(p["rev"])
            if sys_glibc and pin_glibc and sys_glibc != pin_glibc:
                gl = [n for n in (a or {}).get("inputs") or [] if GL_NAMES.match(n or "")]
                if gl:
                    out.append(
                        check(
                            "gl-skew",
                            "warn",
                            f"{attr} held at {p.get('version') or '?'} brings its own glibc "
                            f"{pin_glibc}; the system's Mesa/Wayland drivers are built against "
                            f"glibc {sys_glibc}. The program is self-contained but can fail to find "
                            "a GL/EGL driver at run time (seen with dorion 2026-10-11). Prefer "
                            f"nixin update --to {p['rev'][:7]} for everything, or expect to wrap it "
                            "with the pinned Mesa",
                            pin=name,
                            attr=attr,
                            inputs=gl,
                        )
                    )
                else:
                    out.append(
                        check(
                            "glibc-skew",
                            "info",
                            f"{attr} held at {p.get('version') or '?'} runs on its own glibc "
                            f"{pin_glibc} (system: {sys_glibc}); fine for a leaf package",
                            pin=name,
                            attr=attr,
                        )
                    )
            if with_dependents and a and a.get("out"):
                deps = dependents(a["out"])
                if deps:
                    n = len(deps)
                    names = ", ".join(d.split("-", 1)[-1] for d in deps[:5])
                    out.append(
                        check(
                            "rebuilds-dependents",
                            "warn" if n >= DEPENDENTS_WARN else "info",
                            f"{n} installed packages depend on {attr} and will rebuild against the "
                            f"held version ({names}): an overlay pin is not a leaf pin",
                            pin=name,
                            attr=attr,
                            count=n,
                            dependents=deps[:20],
                        )
                    )
    if pins:
        revs = sorted({p["rev"] for p in pins.values()})
        out.append(
            check(
                "eval-cost",
                "info",
                f"a second nixpkgs tree ({len(revs)} pinned revision{'s' if len(revs) > 1 else ''}) "
                "adds about 1-2 s and 300 MiB to every evaluation; after a garbage collection nix "
                "re-downloads it (~60 s)",
                revisions=revs,
            )
        )
    out.sort(key=lambda c: (level_order(c["level"]), c["id"]))
    return out
