"""Which nixpkgs `try` builds from, and what probe.nix says about one attribute of it.

The default is the config flake's own nixpkgs input as locked (`flake.lock`, the node
`outdated` finds: `nixpkgs_2` here), so a try builds exactly what `nixin add` would
install. `--nixpkgs FLAKEREF|REV` takes another one and needs no config. The probe is one
`nix eval` on `<flakeref>#legacyPackages.<system>`; for a locked commit nix answers from
its eval cache and the store, without the network.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from ..configrepo import ConfigRepo, NixinError, nix_list_of_str
from ..outdated import target as tgt
from . import runner

PROBE = files("nixin.tryout") / "probe.nix"
SUGGEST = 5
_HEX = re.compile(r"[0-9a-f]{7,40}")
# nixpkgs' aliases.nix: a name that is gone throws with a message that says where it went
_GONE = re.compile(r"has been (?:removed|renamed|replaced|dropped|deprecated)|is (?:now|no longer)")
_NO_LEGACY = re.compile(r"does not provide attribute '[^']*legacyPackages")


def probe_text() -> str:
    return PROBE.read_text()


@dataclass(frozen=True)
class Source:
    """The nixpkgs a try evaluates and builds: `flakeref` goes into every command."""

    flakeref: str  # github:nixos/nixpkgs/<rev> for the lock
    origin: str  # "lock" (the config's input) | "nixpkgs" (--nixpkgs)
    rev: str | None = None
    input: str | None = None  # the input's name in flake.nix
    config: str | None = None  # the config flake whose lock it is
    date: str | None = None

    @property
    def short_rev(self) -> str | None:
        return self.rev[:7] if self.rev else None

    def label(self) -> str:
        """`nixpkgs 3ec6363 (the lock of /home/me/nixos)`, for human text and logs."""
        if self.origin == "lock":
            return f"{self.input} {self.short_rev or self.flakeref} (the lock of {self.config})"
        return self.flakeref

    def doc(self) -> dict[str, Any]:
        return {
            "flakeref": self.flakeref,
            "from": self.origin,
            "rev": self.rev,
            "short_rev": self.short_rev,
            "date": self.date,
            "input": self.input,
            "config": self.config,
        }


def choose(config: str | None, nixpkgs: str | None) -> Source:
    """--nixpkgs as given (a bare commit means github:nixos/nixpkgs/<rev>), else the lock."""
    if nixpkgs:
        if _HEX.fullmatch(nixpkgs):
            return Source(f"github:nixos/nixpkgs/{nixpkgs}", "nixpkgs", rev=nixpkgs)
        return Source(nixpkgs, "nixpkgs")
    try:
        repo = ConfigRepo.locate(config)
    except NixinError as e:
        raise NixinError(e.code, f"{e.message}, or pass --nixpkgs FLAKEREF") from e
    np = tgt.nixpkgs_input(repo)
    lock = json.loads((repo.path / "flake.lock").read_text())
    locked = lock["nodes"][np.locked.node].get("locked") or {}
    ref = tgt.render_ref(locked)
    if ref is None:
        raise NixinError(
            "not-found",
            f"cannot name {np.input}'s locked source ({locked.get('type')!r}) as a flakeref; "
            "pass --nixpkgs FLAKEREF",
        )
    return Source(
        ref, "lock", rev=np.locked.rev, input=np.input, config=str(repo.path), date=np.locked.date
    )


def split_attr(attr: str) -> list[str]:
    return attr.split(".")


def probe(src: Source, attr: str, *, system: str, impure: bool) -> dict[str, Any]:
    """probe.nix on `attr`: meta, the outputs to install and their out paths (no build)."""
    apply = f"({probe_text()}) {{ path = {nix_list_of_str(split_attr(attr))}; }}"
    args = ["eval", "--json", f"{src.flakeref}#legacyPackages.{system}", "--apply", apply]
    if impure:
        args.append("--impure")
    try:
        info = json.loads(runner.nix(args, code="eval-failed"))
    except NixinError as e:
        text = e.stderr or e.message
        if e.code != "eval-failed":
            raise
        if tgt.is_network(text):
            raise NixinError("network", f"cannot fetch {src.flakeref}: {e.message}") from e
        if tgt.is_bad_ref(text):
            raise NixinError("bad-channel", f"{src.flakeref} does not resolve: {e.message}") from e
        if _NO_LEGACY.search(text):
            raise NixinError(
                "not-found", f"{src.flakeref} has no legacyPackages.{system}: is it a nixpkgs?"
            ) from e
        if _GONE.search(text):
            raise NixinError("not-found", f"{attr}: {e.message}") from e
        raise
    except ValueError as e:
        raise NixinError("eval-failed", f"the probe of {attr} did not print JSON: {e}") from e
    return info


def suggest(attr: str, names: list[str], n: int = SUGGEST) -> list[str]:
    """Attribute names close to the missing last component: near spellings, then names that
    start with it, then names that contain it (shortest first)."""
    path = split_attr(attr)
    want, prefix = path[-1], ".".join(path[:-1])
    low = want.lower()
    close = difflib.get_close_matches(want, names, n=n, cutoff=0.75)
    starts = sorted((x for x in names if x.lower().startswith(low)), key=lambda x: (len(x), x))
    within = sorted((x for x in names if low in x.lower()), key=lambda x: (len(x), x))
    out: list[str] = []
    for x in [*close, *starts, *within]:
        if x not in out and x != want:
            out.append(x)
    return [f"{prefix}.{x}" if prefix else x for x in out[:n]]
