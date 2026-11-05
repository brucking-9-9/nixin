"""Which nixpkgs attribute a host's kernel comes from, read from the text of its
`boot.kernelPackages` declaration(s).

19BK: `lib.mkForce (pkgs.linuxPackagesFor (pkgs.linux_7_2.override { … }))` names
`linux_7_2`; mbp13: `pkgs.linuxPackages_7_2` names the package set. A hold has to put the
pinned kernel where the config reads it, and `linux_X` / `linuxPackages_X` are two aliases
in nixpkgs (`linuxKernel.kernels.X` / `linuxKernel.packages.X`), so both are overlaid: the
kernel from the pin, the set as `linuxPackagesFor` of it (mode `kernel`), or the set from the
pin too (mode `kernel-set`).

Only top-level definitions count (`specialisation.<n>.configuration.boot.kernelPackages`
is a different path); a kernel from another flake input (`inputs.…`) or an expression that
names no `pkgs.linux…` attribute is `pin-unsupported`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..configrepo import NixinError
from ..writer.workspace import Found, ModRef, Workspace

KERNEL_OPTION = ("boot", "kernelPackages")
_ATTR = re.compile(r"\bpkgs\.(linuxPackages(?:_[A-Za-z0-9_]+)?|linux(?:_[A-Za-z0-9_]+)?)\b")
# every `pkgs.linuxPackages_X` in a hand-written module (kernel-set-mismatch looks for the
# ones outside the boot.kernelPackages declaration)
SET_REF = re.compile(r"\bpkgs\.(linuxPackages(?:_[A-Za-z0-9_]+)?)\b")


@dataclass(frozen=True)
class KernelAttrs:
    kernel: str  # linux_7_2
    pkgset: str  # linuxPackages_7_2
    decl: str  # file:line of the declaration the attrs were read from
    text: str  # the declaration's first line

    @property
    def attrs(self) -> list[str]:
        return [self.kernel, self.pkgset]


def pair(attr: str) -> tuple[str, str]:
    """(kernel attr, package-set attr) for either alias."""
    if attr.startswith("linuxPackages"):
        suffix = attr.removeprefix("linuxPackages")
        return f"linux{suffix}", attr
    suffix = attr.removeprefix("linux")
    return attr, f"linuxPackages{suffix}"


def declarations(ws: Workspace, mods: list[ModRef]) -> list[Found]:
    exact, _covering, _below = ws.option_decls(mods, KERNEL_OPTION)
    return exact


def detect(ws: Workspace, mods: list[ModRef], host: str) -> list[KernelAttrs]:
    """The kernel attrs each top-level `boot.kernelPackages` definition names (one per
    definition; several when modules define it at different priorities)."""
    found = declarations(ws, mods)
    if not found:
        raise NixinError(
            "pin-unsupported",
            f"{host} does not define boot.kernelPackages in a module nixin can read, so the "
            "kernel comes from nixpkgs' default or from another input; nothing to hold. Hold "
            "the whole nixpkgs instead: nixin update --to <rev>",
        )
    out: list[KernelAttrs] = []
    for f in found:
        text = ws.text(f.file)
        lo, hi = f.value_span or (f.start, f.end)
        value = text[lo:hi]
        names = list(dict.fromkeys(m.group(1) for m in _ATTR.finditer(value)))
        if not names:
            where = "another flake input" if "inputs." in value else "an expression"
            raise NixinError(
                "pin-unsupported",
                f"{f.where}: boot.kernelPackages comes from {where} that names no "
                f"pkgs.linux… attribute ({f.text}); nixin cannot redirect it. Hold the whole "
                "nixpkgs instead: nixin update --to <rev>",
            )
        if len(names) > 1:
            raise NixinError(
                "pin-unsupported",
                f"{f.where}: boot.kernelPackages names {len(names)} kernel attributes "
                f"({', '.join(names)}); nixin holds one. Pin it by attribute: "
                f"nixin pin kernel --attr {names[0]}",
            )
        kernel, pkgset = pair(names[0])
        out.append(KernelAttrs(kernel, pkgset, f.where, f.text))
    return out


def set_references(
    ws: Workspace, mods: list[ModRef], skip: list[Found]
) -> list[tuple[str, int, str]]:
    """(file, line, attr) of every `pkgs.linuxPackages…` in a hand-written module outside the
    boot.kernelPackages declarations: a module set taken beside config.boot.kernelPackages."""
    out = []
    spans = {(f.file, f.start, f.end) for f in skip}
    for m in ws.hand_written(mods):
        try:
            text = ws.text(m.file)
        except OSError:
            continue
        for hit in SET_REF.finditer(text):
            if any(f == m.file and s <= hit.start() < e for f, s, e in spans):
                continue
            line = text.count("\n", 0, hit.start()) + 1
            out.append((m.file, line, hit.group(1)))
    return out
