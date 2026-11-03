"""AppStream from a package's outputs in the local nix store.

Where the output comes from, in order:

    installed   a profile on this machine links a desktop entry or metainfo from a store
                path named after the package (installed.find): no nix at all
    store       the package's outputs in the locked nixpkgs (`nix eval` of their out
                paths on the config's flake.lock nixpkgs, or `--nixpkgs`) that are
                already in the store
    fetched     (`--appstream fetch` only) `nix build --no-link --print-out-paths` of the
                package when that builds nothing and downloads at most FETCH_CAP from the
                binary cache (`nix build --dry-run` says), with `--max-jobs 0`: nix may
                substitute but never compiles

What an output holds is cached by store path (`appstream/` in the nixin cache): a store
path never changes. The out paths are cached by (flakeref, attr) for as long as the
local-nix answers (the flakeref names a commit).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from ..cache import TTL, Cache
from ..discovery.local import NIX_FLAGS, current_system
from . import parse

SOURCE = "appstream"
FETCH_CAP = 50 * 1024 * 1024  # bytes of download `--appstream fetch` accepts
STORE_TTL = 365 * 24 * 3600.0  # a store path's contents never change
EVAL_TIMEOUT = 120.0
BUILD_TIMEOUT = 600.0
UNFREE_ENV = {
    "NIXPKGS_ALLOW_UNFREE": "1",
    "NIXPKGS_ALLOW_INSECURE": "1",
    "NIXPKGS_ALLOW_BROKEN": "1",
}
# outPath of every output, without building anything
OUTPATHS = (
    "p: builtins.listToAttrs (map (o: { name = o; value = p.${o}.outPath; }) "
    '(p.outputs or [ "out" ]))'
)
_REFUSED = (
    "refusing to evaluate",
    "has an unfree license",
    "is marked as insecure",
    "is marked as broken",
)


class StoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _nix(args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[str, str]:
    """(stdout, stderr) of a nix call that succeeded; StoreError otherwise."""
    try:
        proc = subprocess.run(
            ["nix", *NIX_FLAGS, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as exc:
        raise StoreError("nix-missing", "nix is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise StoreError("nix-timeout", f"nix {args[0]} took longer than {timeout:.0f}s") from exc
    if proc.returncode != 0:
        lines = [ln.strip() for ln in proc.stderr.splitlines() if ln.strip()]
        raise StoreError("nix-failed", f"nix {args[0]} failed: " + " ".join(lines[-3:]))
    return proc.stdout, proc.stderr


def _installable(flakeref: str, attr: str) -> str:
    return f"{flakeref}#legacyPackages.{current_system()}.{attr}"


def out_paths(cache: Cache, flakeref: str, attr: str) -> dict[str, str]:
    """{output: store path} of `attr` in that nixpkgs, from evaluation only. Unfree,
    insecure and broken packages are evaluated again with the NIXPKGS_ALLOW_* variables
    (impure): their out paths do not depend on it."""
    import json

    def fetch() -> dict[str, str]:
        args = ["eval", "--json", _installable(flakeref, attr), "--apply", OUTPATHS]
        try:
            out, _ = _nix(args, EVAL_TIMEOUT)
        except StoreError as exc:
            if exc.code != "nix-failed" or not any(r in str(exc) for r in _REFUSED):
                raise
            out, _ = _nix([*args, "--impure"], EVAL_TIMEOUT, UNFREE_ENV)
        data = json.loads(out)
        if not isinstance(data, dict):
            raise StoreError("nix-failed", "nix eval did not print the outputs")
        return {str(k): str(v) for k, v in data.items()}

    paths, _ = cache.fetch(
        "local", f"outpaths:{flakeref}:{current_system()}:{attr}", TTL["local"], fetch
    )
    return paths


def read(cache: Cache, path: str, hints: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """What one output ships (parse.read_output), cached by store path. A path that is not
    in the store is not cached (it may be fetched later)."""
    if not os.path.isdir(path):
        return None
    payload, _ = cache.fetch(
        SOURCE,
        f"output:{path}:{','.join(hints)}",
        STORE_TTL,
        lambda: {"record": parse.read_output(path, hints)},
    )
    return payload.get("record") if isinstance(payload, dict) else None


def read_first(cache: Cache, paths: list[str], hints: tuple[str, ...]) -> tuple[str, dict] | None:
    """The first of `paths` that ships a component or a desktop entry."""
    for p in paths:
        rec = read(cache, p, hints)
        if rec is not None:
            return p, rec
    return None


def ordered_outputs(paths: dict[str, str]) -> list[str]:
    """`out` first (where share/ usually is), then `bin`, then the rest in their order."""
    order = sorted(paths, key=lambda o: (o != "out", o != "bin"))
    return [paths[o] for o in order]


_FETCH = re.compile(
    r"(?:these (\d+) paths|this path) will be fetched \(([\d.]+) (KiB|MiB|GiB) download"
)
_BUILD = re.compile(r"(?:these (\d+) derivations|this derivation) will be built")
_UNITS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def dry_run_cost(stderr: str) -> tuple[int, int, int]:
    """`nix build --dry-run`'s summary: (derivations to build, paths to fetch, bytes)."""
    build = fetch = size = 0
    for m in _BUILD.finditer(stderr):
        build += int(m.group(1) or 1)
    for m in _FETCH.finditer(stderr):
        fetch += int(m.group(1) or 1)
        size += int(float(m.group(2)) * _UNITS[m.group(3)])
    return build, fetch, size


def human(n: int) -> str:
    for unit, div in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def fetch_cheaply(flakeref: str, attr: str, cap: int = FETCH_CAP) -> tuple[list[str], str]:
    """Substitute `attr` when that builds nothing and downloads at most `cap`; returns
    (out paths, what happened), or ([], why not)."""
    inst = _installable(flakeref, attr)
    env = UNFREE_ENV
    flags = ["--impure"]
    try:
        _, err = _nix(["build", "--dry-run", *flags, inst], EVAL_TIMEOUT, env)
    except StoreError as exc:
        return [], f"not fetched: {exc}"
    build, paths, size = dry_run_cost(err)
    if build:
        return [], f"not fetched: {build} derivation(s) would have to be built (not cached)"
    if size > cap:
        return (
            [],
            f"not fetched: {paths} path(s), {human(size)} to download (the cap is {human(cap)})",
        )
    try:
        out, _ = _nix(
            [
                "build",
                "--no-link",
                "--print-out-paths",
                "--max-jobs",
                "0",
                "--builders",
                "",
                *flags,
                inst,
            ],
            BUILD_TIMEOUT,
            env,
        )
    except StoreError as exc:
        return [], f"not fetched: {exc}"
    got = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("/nix/store/")]
    return got, f"fetched {paths} path(s), {human(size)}, from the binary cache"
