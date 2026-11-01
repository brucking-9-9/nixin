"""The option index of a host (options.nix): declarations, definitions, values.

Two shapes of query, both cached by TruthCache (nix's flake fingerprint invalidates them):

* `index()`     every option of a scope (NixOS tree, or one HM user's tree) without values —
                heavy (19BK: ~20-40 s, 1.4 GiB; see README) but needed once per tree change
                for counts and for "which option is declared in that module file".
* `prefix_query()` everything under one option path WITH values and per-definition values —
                cheap (~3-4 s), what `where` runs.

A scope is the NixOS tree (hm=None) or a Home Manager user's tree (hm="brucking").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from importlib.resources import files
from typing import Any

from ..configrepo import ConfigRepo, NixinError, nix_value
from .cache import TruthCache, cache_dir

HELPER = files("nixin.truth") / "options.nix"
MARKER = re.compile(r"nixin-option ([^\s'\"`]+)")
MAX_RETRIES = 12
INDEX_VALUES = "none"  # the full index carries no values; `where` fetches them per prefix
PREFIX_MAX_ITEMS = 400

# Options whose definitions raise an uncatchable error in the full index (values=none),
# learned on nixpkgs 3ec6363 + home-manager c216bf5: 19BK and mbp13 hit the same eight, so
# they are properties of the modules, not of a config. The seed for a (nixpkgs,
# home-manager) pair nixin has not indexed yet; evaluate() still learns anything new.
SEED_EXCLUSIONS: dict[str, dict[str, str]] = {
    "nixos": {
        "hardware.nvidia.gsp.enable": "expected a set but found null: null",
        "hardware.nvidia.open": "expected a set but found null: null",
        "hardware.nvidia.powerManagement.kernelSuspendNotifier": (
            "expected a set but found null: null"
        ),
        "services.dawarich.redis.host": "attribute 'dawarich' missing",
        "services.greenlight.redis.host": "attribute 'greenlight' missing",
        "services.immich.redis.host": "attribute 'immich' missing",
        "services.kener.redis.host": "attribute 'kener' missing",
        "services.nagios.cgiConfigFile": "cannot coerce null to a string: null",
    },
    "home": {
        "programs.google-chrome-beta.package": "seed list (failed on 19BK)",
        "programs.google-chrome-dev.package": "seed list (failed on 19BK)",
        "programs.radicle.uri.web-rad.browser": "seed list (failed on 19BK)",
        "programs.vscode.haskell.hie.executablePath": "seed list (failed on 19BK)",
    },
}


def helper_text() -> str:
    return HELPER.read_text()


def scope_name(hm: str | None) -> str:
    return "nixos" if hm is None else f"home-{hm}"


def evaluate(
    repo: ConfigRepo,
    host: str,
    *,
    hm: str | None = None,
    prefix: list[str] | tuple[str, ...] = (),
    values: str = "none",
    max_items: int | None = None,
    exclude: list[str] | tuple[str, ...] = (),
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """Run options.nix; when an option's definitions raise an uncatchable error, exclude it
    (its name comes from the `nixin-option` marker in the trace) and run again."""
    excluded: dict[str, str] = {}
    t = time.monotonic()
    helper = helper_text()
    for _ in range(max_retries + 1):
        args: dict[str, Any] = {
            "prefix": list(prefix),
            "values": values,
            "hm": hm,
            "exclude": sorted(set(exclude) | set(excluded)),
        }
        if max_items:
            args["maxItems"] = max_items
        try:
            entries = repo.eval_json(repo.host_attr(host), apply=f"({helper}) {nix_value(args)}")
        except NixinError as e:
            names = MARKER.findall(e.stderr)
            if not names or names[0] in excluded:
                raise
            excluded[names[0]] = e.message
            continue
        return {
            "scope": scope_name(hm),
            "hm_user": hm,
            "prefix": list(prefix),
            "values": values,
            "entries": entries,
            "excluded": excluded,
            "retries": len(excluded),
            "seconds": round(time.monotonic() - t, 2),
        }
    raise NixinError(
        "eval-failed",
        f"option index of {host} still fails after excluding {len(excluded)} options: "
        + ", ".join(sorted(excluded)),
    )


class ExclusionStore:
    """The full index's exclusions, persisted per (nixpkgs rev, home-manager rev) as
    `$XDG_CACHE_HOME/nixin/truth/exclude-<nixpkgs>-<hm>.json` = {"nixos": {name: message},
    "home": {...}}, seeded with SEED_EXCLUSIONS. Kind "home" covers every HM user.

    Not used by prefix queries: `where` asks for values (other failures) and should still
    answer for an excluded option on a host where it does evaluate (nvidia enabled).
    Best effort like TruthCache; delete the file to re-learn."""

    _lock = threading.Lock()  # the NixOS and HM index may learn at the same time

    def __init__(self, repo: ConfigRepo) -> None:
        nixpkgs, hm = repo.lock("nixpkgs"), repo.lock("home-manager")
        self.revs = {
            "nixpkgs": nixpkgs.rev if nixpkgs else None,
            "home-manager": hm.rev if hm else None,
        }
        np_rev = (self.revs["nixpkgs"] or "unknown")[:12]
        hm_rev = (self.revs["home-manager"] or "none")[:12]
        self.path = cache_dir() / f"exclude-{np_rev}-{hm_rev}.json"

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {k: dict(v) for k, v in SEED_EXCLUSIONS.items()}
        return {k: dict(doc.get(k, {})) for k in SEED_EXCLUSIONS}

    def load(self, kind: str) -> dict[str, str]:
        return self._read()[kind]

    def add(self, kind: str, learned: dict[str, str]) -> None:
        """Record what an index pass learned; the first pass on a rev pair also writes the
        seed out, so the file shows what the index skipped."""
        if not learned and self.path.exists():
            return
        with self._lock:
            doc = self._read()
            doc[kind].update({k: v.splitlines()[0] if v else v for k, v in learned.items()})
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps({**self.revs, **doc}, indent=1, sort_keys=True))
                os.replace(tmp, self.path)
            except OSError:
                pass


def index(
    repo: ConfigRepo,
    host: str,
    cache: TruthCache,
    *,
    hm: str | None = None,
    store: ExclusionStore | None = None,
) -> dict:
    """The full index of a scope: one eval when the exclusion store already knows every
    option that cannot be evaluated; `excluded` lists known + newly learned ones."""
    name = f"options-{scope_name(hm)}"
    salt = helper_text() + INDEX_VALUES
    hit = cache.get(name, salt)
    if hit is not None:
        return hit
    store = store or ExclusionStore(repo)
    kind = "nixos" if hm is None else "home"
    known = store.load(kind)
    doc = evaluate(repo, host, hm=hm, values=INDEX_VALUES, exclude=list(known))
    store.add(kind, doc["excluded"])
    doc["excluded"] = {**known, **doc["excluded"]}
    cache.put(name, doc, salt)
    cache.put(f"{name}-summary", summarize(doc, repo.source().store_path), salt)
    return doc


def cached_summary(cache: TruthCache, *, hm: str | None = None) -> dict | None:
    """The index summary if a full index of this tree is cached (no eval)."""
    return cache.get(f"options-{scope_name(hm)}-summary", helper_text() + INDEX_VALUES)


def prefix_query(
    repo: ConfigRepo, host: str, cache: TruthCache, prefix: list[str], *, hm: str | None = None
) -> dict:
    key = hashlib.sha256(".".join(prefix).encode()).hexdigest()[:10]
    name = f"prefix-{scope_name(hm)}-{key}"
    salt = helper_text()
    hit = cache.get(name, salt)
    if hit is not None:
        return hit
    doc = evaluate(repo, host, hm=hm, prefix=prefix, values="all", max_items=PREFIX_MAX_ITEMS)
    cache.put(name, doc, salt)
    return doc


# --- reading an index -------------------------------------------------------------------


def is_set(entry: dict) -> bool:
    """Set by something other than its own default. isDefined alone is true for every
    option with a default: the module system adds the default as a definition from the
    declaring file, and that is all `definitions` holds when nothing overrides it."""
    defs = entry.get("definitions")
    if not entry.get("isDefined") or defs is None:
        return False
    return defs != entry.get("declarations", [])[:1]


def summarize(doc: dict, config_source: str | None = None) -> dict[str, Any]:
    """Counts for `status`; `in_config` = options with a definition in a file of the
    config tree (`config_source`, the /nix/store/…-source nix evaluated)."""
    entries = doc["entries"]
    out: dict[str, Any] = {
        "total": len(entries),
        "defined": sum(1 for e in entries if is_set(e)),
        "excluded": len(doc.get("excluded", {})),
        "definitions_error": sum(1 for e in entries if e.get("definitionsError")),
        "seconds": doc.get("seconds"),
    }
    if config_source:
        inside = config_source.rstrip("/") + "/"
        out["in_config"] = sum(
            1 for e in entries if any(f.startswith(inside) for f in e.get("definitions") or [])
        )
    return out


def lookup(entries: list[dict], name: str) -> dict | None:
    return next((e for e in entries if e["name"] == name), None)


def longest_parent(entries: list[dict], path: list[str]) -> tuple[dict | None, list[str]]:
    """The option whose name is the longest strict prefix of `path`, and the rest of the
    path below it (e.g. systemd.services + [foo, enable])."""
    best: dict | None = None
    for e in entries:
        parts = e["name"].split(".")
        if (
            len(parts) < len(path)
            and path[: len(parts)] == parts
            and (best is None or len(parts) > len(best["name"].split(".")))
        ):
            best = e
    if best is None:
        return None, path
    return best, path[len(best["name"].split(".")) :]


def dig(value: Any, rest: list[str]) -> tuple[bool, Any]:
    """Follow `rest` into a serialised value; (found, value)."""
    cur = value
    for seg in rest:
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, cur


def options_declared_in(entries: list[dict], raw_file: str) -> list[dict]:
    """Options a module file declares that the user's config defines (how a package that a
    module adds to a list is traced back to the `programs.x.enable` that caused it)."""
    return [e for e in entries if raw_file in e.get("declarations", []) and e.get("isDefined")]
