"""Which nixpkgs a lock update would move to, and which commit that is right now.

The lock names the host's nixpkgs twice: `locked` (the commit evaluated today) and
`original` (what flake.nix asked for: `github:nixos/nixpkgs/nixos-unstable`). The
default target is `original` as it resolves now, i.e. what `nix flake update nixpkgs`
would write; `--nixpkgs FLAKEREF|REV` and `--channel NAME` pick another. Resolving
is `nix flake metadata` on the target, which asks GitHub for the branch head and
downloads the tarball when it is new (nix caches both for its `tarball-ttl`, 1 h).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ..configrepo import ConfigRepo, LockedInput, NixinError, parse_lock

_HEX = re.compile(r"[0-9a-f]{7,40}")
_RELEASE = re.compile(r"\d{2}\.\d{2}")
_BRANCH = re.compile(r"(?:nixos|nixpkgs|release)-[\w.-]+")
# nix's (curl's) wording when the network, not the ref, is the problem
_NETWORK = re.compile(
    r"Could not resolve host|Couldn't resolve host|Could not connect|Failed to connect"
    r"|Connection (?:refused|timed out|reset)|Timeout was reached|Network is unreachable"
    r"|SSL connect error|HTTP error (?:5\d\d|429|403)|in offline mode",
    re.IGNORECASE,
)
_BAD_REF = re.compile(r"HTTP error (?:404|422)|No commit found|cannot find (?:Git )?revision")


@dataclass(frozen=True)
class Nixpkgs:
    """The config's nixpkgs input: its name in flake.nix, its lock node, both refs."""

    locked: LockedInput
    original: dict[str, Any]  # the lock node's `original`

    @property
    def input(self) -> str:
        return self.locked.name

    @property
    def original_ref(self) -> str | None:
        return render_ref(self.original)

    def doc(self) -> dict[str, Any]:
        lk = self.locked
        return {
            "input": lk.name,
            "node": lk.node,
            "rev": lk.rev,
            "short_rev": lk.short_rev,
            "date": lk.date,
            "last_modified": lk.last_modified,
            "flakeref": lk.flakeref,
            "original": self.original_ref,
        }


@dataclass(frozen=True)
class Target:
    """What the target side evaluates: `locked` goes to --override-input."""

    input: str
    flakeref: str  # as asked: github:nixos/nixpkgs/nixos-unstable
    source: str  # "lock" (the input's `original`), "nixpkgs" (--nixpkgs), "channel"
    rev: str | None
    last_modified: int | None
    locked: str  # github:nixos/nixpkgs/<rev>?narHash=…, fully locked
    path: str | None

    @property
    def short_rev(self) -> str | None:
        return self.rev[:7] if self.rev else None

    @property
    def date(self) -> str | None:
        if self.last_modified is None:
            return None
        return dt.datetime.fromtimestamp(self.last_modified, dt.UTC).strftime("%Y-%m-%d")

    def doc(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "flakeref": self.flakeref,
            "from": self.source,
            "rev": self.rev,
            "short_rev": self.short_rev,
            "date": self.date,
            "last_modified": self.last_modified,
            "locked": self.locked,
        }


def render_ref(original: dict[str, Any]) -> str | None:
    """A lock node's `original` as a flakeref (github:owner/repo/ref, a tarball URL, …)."""
    kind = original.get("type")
    if kind in ("github", "gitlab", "sourcehut"):
        ref = original.get("rev") or original.get("ref")
        base = f"{kind}:{original.get('owner')}/{original.get('repo')}"
        if not ref:
            return base
        return f"{base}/{ref}" if "/" not in ref else f"{base}?{urlencode({'ref': ref})}"
    if kind == "indirect":
        ref = original.get("rev") or original.get("ref")
        return f"flake:{original.get('id')}" + (f"/{ref}" if ref else "")
    if kind in ("tarball", "file"):
        return original.get("url")
    if kind == "git":
        q = {k: original[k] for k in ("ref", "rev") if original.get(k)}
        url = original.get("url") or ""
        return f"git+{url}" + (f"?{urlencode(q)}" if q else "")
    if kind == "path":
        return f"path:{original.get('path')}"
    return None


def nixpkgs_input(repo: ConfigRepo) -> Nixpkgs:
    """The root input that is nixpkgs: the one called `nixpkgs`, else the one locked to a
    nixpkgs repository. Its lock node is resolved, never assumed (`nixpkgs_2` here)."""
    lock_file = repo.path / "flake.lock"
    try:
        lock = json.loads(lock_file.read_text())
    except (OSError, ValueError) as e:
        raise NixinError("not-found", f"no readable flake.lock in {repo.path}: {e}") from e
    nodes = lock.get("nodes", {})
    names = list(nodes.get(lock.get("root", "root"), {}).get("inputs", {}))
    names.sort(key=lambda n: n != "nixpkgs")
    for name in names:
        locked = parse_lock(lock, name)
        if locked is None:
            continue
        original = nodes[locked.node].get("original", {})
        if name == "nixpkgs" or locked.repo == "nixpkgs" or original.get("repo") == "nixpkgs":
            return Nixpkgs(locked, original)
    raise NixinError("not-found", f"{lock_file} locks no nixpkgs input")


def channel_branch(name: str, stable: str | None = None) -> str:
    """`--channel` -> a nixpkgs branch: unstable -> nixos-unstable, 26.05 -> nixos-26.05,
    a branch name as is; `stable` needs the newest release (from search.nixos.org)."""
    if name == "unstable":
        return "nixos-unstable"
    if name == "stable":
        if stable is None:
            raise NixinError("bad-channel", "no release channel is known for `stable` right now")
        return f"nixos-{stable}"
    if _RELEASE.fullmatch(name):
        return f"nixos-{name}"
    if _BRANCH.fullmatch(name) or name == "master":
        return name
    raise NixinError(
        "bad-channel",
        f"unknown channel {name!r} (unstable, stable, NN.MM or a branch like nixos-26.05)",
    )


def requested(np: Nixpkgs, nixpkgs: str | None, channel: str | None) -> tuple[str, str]:
    """(flakeref, source) of the target the command line asks for."""
    lk = np.locked
    repo = f"github:{lk.owner or 'nixos'}/{lk.repo or 'nixpkgs'}"
    if nixpkgs:
        return (f"{repo}/{nixpkgs}" if _HEX.fullmatch(nixpkgs) else nixpkgs), "nixpkgs"
    if channel:
        return f"{repo}/{channel}", "channel"
    ref = np.original_ref
    if ref is None:
        raise NixinError(
            "not-found",
            f"the lock's {lk.name} input has no `original` to update from; pass --nixpkgs",
        )
    return ref, "lock"


def is_network(stderr: str) -> bool:
    return bool(_NETWORK.search(stderr))


def resolve(
    repo: ConfigRepo, np: Nixpkgs, flakeref: str, source: str, *, refresh: bool = False
) -> Target:
    """`nix flake metadata` on the target: its commit and date as of now (network)."""
    args = ["flake", "metadata", "--json", flakeref]
    if refresh:
        args.append("--refresh")  # re-ask GitHub for the branch head (nix caches it 1 h)
    try:
        meta = json.loads(repo.nix(args, code="nix-failed"))
    except NixinError as e:
        text = e.stderr or e.message
        if is_network(text):
            raise NixinError(
                "network", f"cannot fetch {flakeref} (the target nixpkgs): {e.message}"
            ) from e
        if _BAD_REF.search(text):
            raise NixinError("bad-channel", f"{flakeref} does not resolve: {e.message}") from e
        raise
    except ValueError as e:
        raise NixinError("nix-failed", f"nix flake metadata {flakeref}: {e}") from e
    locked = meta.get("locked") or {}
    rev = locked.get("rev") or meta.get("revision")
    url = meta.get("url")
    if not url:
        if locked.get("type") == "github" and rev:
            url = f"github:{locked.get('owner')}/{locked.get('repo')}/{rev}"
        else:
            url = flakeref
    return Target(
        input=np.input,
        flakeref=flakeref,
        source=source,
        rev=rev,
        last_modified=locked.get("lastModified") or meta.get("lastModified"),
        locked=url,
        path=meta.get("path"),
    )
