"""outdated — which installed packages a nixpkgs update would change, or which are behind.

    nixin outdated [--nixpkgs FLAKEREF|REV | --channel NAME] [--home USER] [--refresh]
    nixin outdated --published [--channel NAME] [--home USER] [--refresh]

Default ("lock" mode): evaluate the host's environment.systemPackages and every Home
Manager user's home.packages twice, as the flake is locked and with
`--override-input <nixpkgs> <target>` (default: the lock's own `original`, so what
`nix flake update nixpkgs` would bring), and compare them by pname. Two package-list
evals (outdated.nix), run side by side, never system.build.toplevel: nothing is built. The target side
needs the network once per target commit (the nixpkgs tarball); its eval is cached under
the truth cache by (flake fingerprint, target rev).

`--published` (brew-outdated style): the installed versions against search.nixos.org's
channel index, one `terms` query per 50 pnames; no target eval.

`register(subparsers)` is the only thing cli.py calls; the handler returns the --json
document (cli adds verb/host), with the rendered text under "message" in human mode.
"""

from __future__ import annotations

import argparse
import json
import shlex
import time
from importlib.resources import files
from typing import Any

from ..configrepo import ConfigRepo, NixinError
from ..truth.cache import TruthCache
from . import compare, render, update
from . import target as tgt

VERBS = ("outdated", "update")  # update: outdated/update.py, registered with it
HELP = "what a nixpkgs lock update would change; --published: newer versions on a channel"
HELPER = files("nixin.outdated") / "outdated.nix"
BATCH = 50  # pnames per search.nixos.org query
PUBLISHED_NOTE = (
    "search.nixos.org has many attributes per pname; the one defined in the same file as "
    "the installed package is compared (else the same major version, then the attribute "
    "named like the pname). `nixin outdated` without --published is exact."
)


def helper_text() -> str:
    return HELPER.read_text()


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.choices.get("outdated")
    if p is None:
        p = sub.add_parser("outdated", help=HELP)
        p.add_argument("args", nargs="*")
    p.add_argument("--config", help="config flake dir (default: $NIXIN_CONFIG or /etc/nixos)")
    p.add_argument(
        "--nixpkgs",
        metavar="FLAKEREF|REV",
        help="compare against this nixpkgs (a flakeref, or a commit of the locked repo); "
        "default: the lock's own ref (github:nixos/nixpkgs/nixos-unstable)",
    )
    p.add_argument(
        "--channel",
        metavar="NAME",
        help="unstable, stable, NN.MM or a branch: the target branch, or with --published "
        "the search.nixos.org channel (default unstable)",
    )
    p.add_argument(
        "--published",
        action="store_true",
        help="compare with the versions search.nixos.org's channel has (no second eval)",
    )
    p.add_argument("--home", metavar="USER", help="only this Home Manager user's home.packages")
    p.add_argument(
        "--refresh",
        action="store_true",
        help="ignore cached evals and answers; re-resolve the target branch",
    )
    p.set_defaults(run=outdated)
    update.register(sub)


def outdated(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _outdated(ns)
    except NixinError as e:
        return e.doc()


# --- evals --------------------------------------------------------------------------


def _eval(
    repo: ConfigRepo, host: str, cache: TruthCache, target: tgt.Target | None = None
) -> dict[str, Any]:
    """outdated.nix on the host, as locked or with the target nixpkgs; cached."""
    helper = helper_text()
    if target is None:
        name, salt = "outdated-lock", helper
    else:
        name, salt = "outdated-target", f"{helper}|{target.input}|{target.locked}"
    hit = cache.get(name, salt)
    if hit is not None:
        return {**hit, "cached": True}
    args = ["eval", "--json", repo.installable(repo.host_attr(host)), "--apply", helper]
    if target is not None:  # implies --no-write-lock-file; said anyway: never touch the lock
        args += ["--override-input", target.input, target.locked, "--no-write-lock-file"]
    t = time.monotonic()
    try:
        raw = json.loads(repo.nix(args))
    except NixinError as e:
        if target is None:
            raise
        if tgt.is_network(e.stderr or e.message):
            raise NixinError(
                "network", f"fetching an input for {target.flakeref} failed: {e.message}"
            ) from e
        raise NixinError(
            "eval-failed",
            f"{host} does not evaluate with nixpkgs {target.short_rev} ({target.flakeref}), "
            f"so this update would break it: {e.message}",
            stderr=e.stderr,
        ) from e
    doc = {**raw, "seconds": round(time.monotonic() - t, 2)}
    cache.put(name, doc, salt)
    return {**doc, "cached": False}


# --- the verb -----------------------------------------------------------------------


def _outdated(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    if ns.args:
        raise NixinError("usage", f"outdated takes no arguments (got {' '.join(ns.args)!r})")
    if ns.nixpkgs and ns.channel:
        raise NixinError("usage", "--nixpkgs and --channel both pick the target; give one")
    if ns.published and ns.nixpkgs:
        raise NixinError(
            "usage", "--published compares with a search.nixos.org channel: use --channel"
        )
    repo = ConfigRepo.locate(ns.config)
    host = ns.host
    hosts = repo.hosts()
    if host not in hosts:
        raise NixinError(
            "unknown-host", f"{host!r} is not in nixosConfigurations ({', '.join(hosts)})"
        )
    np = tgt.nixpkgs_input(repo)
    cache = TruthCache(repo, host, enabled=not ns.refresh)
    timings: dict[str, float] = {}
    if ns.published:
        doc = _published(ns, repo, host, np, cache, timings)
    else:
        doc = _lock(ns, repo, host, np, cache, timings)
    doc.update(
        {
            "ok": True,
            "config": {"path": str(repo.path), **repo.source().doc()},
            "lock": {**np.doc(), "release": doc.pop("_release")},
            "cache": cache.doc(),
            "timings": {**timings, "total": round(time.monotonic() - t0, 2)},
        }
    )
    if not ns.json:
        doc["message"] = render.outdated(doc, host)
    return doc


def _lock_side(
    repo: ConfigRepo, host: str, cache: TruthCache, ns: argparse.Namespace, timings: dict
) -> dict[str, Any]:
    lock = _eval(repo, host, cache)
    timings["lock"] = 0.0 if lock["cached"] else lock["seconds"]
    if ns.home is not None and ns.home not in (lock.get("home") or {}):
        users = ", ".join(sorted(lock.get("home") or {})) or "none"
        raise NixinError("unknown-user", f"{ns.home!r} is not an HM user of {host} ({users})")
    return lock


def _stable_release(refresh: bool) -> str | None:
    """The newest release channel search.nixos.org lists (for `--channel stable`)."""
    from ..cache import Cache
    from ..discovery import DiscoveryError
    from ..discovery import nixos_search as es
    from ..http import NetworkError

    try:
        by_name, _ = es.channels(Cache.from_flags(refresh=refresh))
    except (NetworkError, DiscoveryError) as e:
        raise NixinError("network", f"cannot ask {es.SOURCE} which release is stable: {e}") from e
    releases = sorted(
        (n for n in by_name if n != "unstable"), key=lambda n: tuple(map(int, n.split(".")))
    )
    return releases[-1] if releases else None


def _lock(
    ns: argparse.Namespace,
    repo: ConfigRepo,
    host: str,
    np: tgt.Nixpkgs,
    cache: TruthCache,
    timings: dict,
) -> dict[str, Any]:
    if ns.channel:
        stable = _stable_release(ns.refresh) if ns.channel == "stable" else None
        flakeref, source = tgt.requested(np, None, tgt.channel_branch(ns.channel, stable))
    else:
        flakeref, source = tgt.requested(np, ns.nixpkgs, None)
    from concurrent.futures import ThreadPoolExecutor  # here: 10 ms at import (logging)

    repo.source()  # the cache key's `nix flake metadata`, once, before two threads need it
    # The two evals are independent: the lock side runs in a thread while this one
    # resolves the target and evaluates it. Errors keep the sequential order: resolve,
    # then the lock side, then the target.
    with ThreadPoolExecutor(max_workers=1) as pool:
        lock_job = pool.submit(_lock_side, repo, host, cache, ns, timings)
        t = time.monotonic()
        try:
            target = tgt.resolve(repo, np, flakeref, source, refresh=ns.refresh)
        finally:
            timings["resolve"] = round(time.monotonic() - t, 2)
        same = bool(target.rev and target.rev == np.locked.rev)
        new = None
        target_error: NixinError | None = None
        if not same:
            try:
                new = _eval(repo, host, cache, target)
            except NixinError as e:
                target_error = e
        lock = lock_job.result()
    if target_error is not None:
        raise target_error
    if new is None:  # the lock already has it: nothing to evaluate
        new, timings["target"] = lock, 0.0
    else:
        timings["target"] = 0.0 if new["cached"] else new["seconds"]
    d = compare.diff(lock, new, ns.home)
    days = None
    if target.last_modified and np.locked.last_modified:
        days = round((target.last_modified - np.locked.last_modified) / 86400, 1)
    kernel_from, kernel_to = lock.get("kernel"), new.get("kernel")
    return {
        "mode": "lock",
        "target": {**target.doc(), "release": new.get("release"), "days_newer": days},
        "up_to_date": same,
        "changed": d["changed"],
        "added": d["added"],
        "removed": d["removed"],
        "kernel": {
            "from": kernel_from,
            "to": kernel_to,
            "changed": kernel_from != kernel_to,
        },
        "count": len(d["changed"]),
        "total": d["total"],
        "scopes": d["scopes"],
        "commands": [] if same else _commands(repo, host, np, target),
        "notes": [] if same else _notes(np, target),
        "_release": lock.get("release"),
    }


def _commands(repo: ConfigRepo, host: str, np: tgt.Nixpkgs, target: tgt.Target) -> list[str]:
    """What to run to take the update: write the lock, build it, switch."""
    cfg = shlex.quote(str(repo.path))
    nixin = f"--config {cfg} --host {shlex.quote(host)}"
    if target.source == "lock":  # the input's own ref: exactly what flake update follows
        bump = f"nix flake update {shlex.quote(np.input)} --flake {cfg}"
    else:  # pins the compared commit; flake.nix (and `original`) keep their ref
        bump = f"nix flake lock {cfg} --override-input {shlex.quote(np.input)} {_pin(np, target)}"
    return [bump, f"nixin plan {nixin}", f"nixin apply {nixin}"]


def _pin(np: tgt.Nixpkgs, target: tgt.Target) -> str:
    lk = np.locked
    if lk.type == "github" and target.rev:
        return f"github:{lk.owner}/{lk.repo}/{target.rev}"
    return shlex.quote(target.locked)


def _notes(np: tgt.Nixpkgs, target: tgt.Target) -> list[str]:
    if target.source == "lock":
        return []
    note = (
        f"flake.nix asks for {np.original_ref}: the pin holds {target.short_rev} until the next "
        f"`nix flake update`, which goes back to that ref. To follow {target.flakeref} for "
        f'good, set inputs.{np.input}.url = "{target.flakeref}" and run '
        f"`nix flake update {np.input}`."
    )
    return [note]


# --- --published --------------------------------------------------------------------


def _published(
    ns: argparse.Namespace,
    repo: ConfigRepo,
    host: str,
    np: tgt.Nixpkgs,
    cache: TruthCache,
    timings: dict,
) -> dict[str, Any]:
    from ..cache import Cache
    from ..discovery import DiscoveryError
    from ..discovery import nixos_search as es
    from ..http import NetworkError

    lock = _lock_side(repo, host, cache, ns, timings)
    names = compare.pnames(lock, ns.home)
    kernel = lock.get("kernel")
    asked = sorted(set(names) | ({"linux"} if kernel else set()))
    es_cache = Cache.from_flags(refresh=ns.refresh)
    t = time.monotonic()
    try:
        ch = es.resolve_channel(es_cache, ns.channel or "unstable")
        hits: list[dict[str, Any]] = []
        cached = True
        for i in range(0, len(asked), BATCH):
            got, hit = es._es_search(es_cache, ch, _terms_query(asked[i : i + BATCH]))
            hits += got
            cached = cached and hit
    except (NetworkError, DiscoveryError) as e:
        raise NixinError(e.code, e.message) from e
    timings["search"] = round(time.monotonic() - t, 2)
    res = compare.published(lock, hits, ch.name, ch.rev, ns.home)
    kern = compare.kernel_published(kernel, [h for h in hits if h.get("package_pname") == "linux"])
    cfg = shlex.quote(str(repo.path))
    nixin = f"--config {cfg} --host {shlex.quote(host)}"
    return {
        "mode": "published",
        "target": {
            "source": es.SOURCE,
            "channel": ch.name,
            "index": ch.index,
            "rev": ch.rev,
            "short_rev": ch.rev[:7] if ch.rev else None,
            "queries": -(-len(asked) // BATCH),
            "cached": cached,
        },
        "changed": res["changed"],
        "added": [],
        "removed": [],
        "unpublished": res["unpublished"],
        "notes": [PUBLISHED_NOTE],
        "kernel": kern,
        "count": len(res["changed"]),
        "total": res["total"],
        "scopes": res["scopes"],
        "commands": [
            f"nixin outdated {nixin}",
            f"nix flake update {shlex.quote(np.input)} --flake {cfg}",
            f"nixin plan {nixin}",
            f"nixin apply {nixin}",
        ],
        "_release": lock.get("release"),
    }


def _terms_query(names: list[str]) -> dict[str, Any]:
    from ..discovery import nixos_search as es

    return {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"type": "package"}},
                    {"terms": {"package_pname": names}},
                ]
            }
        },
        "size": es.MAX_SIZE,
        "_source": [
            "package_attr_name",
            "package_pname",
            "package_pversion",
            "package_position",
        ],
    }
