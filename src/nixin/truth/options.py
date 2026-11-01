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
import re
import time
from importlib.resources import files
from typing import Any

from ..configrepo import ConfigRepo, NixinError, nix_value
from .cache import TruthCache

HELPER = files("nixin.truth") / "options.nix"
MARKER = re.compile(r"nixin-option ([^\s'\"`]+)")
MAX_RETRIES = 12
INDEX_VALUES = "none"  # the full index carries no values; `where` fetches them per prefix
PREFIX_MAX_ITEMS = 400


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


def index(repo: ConfigRepo, host: str, cache: TruthCache, *, hm: str | None = None) -> dict:
    name = f"options-{scope_name(hm)}"
    salt = helper_text() + INDEX_VALUES
    hit = cache.get(name, salt)
    if hit is not None:
        return hit
    doc = evaluate(repo, host, hm=hm, values=INDEX_VALUES)
    cache.put(name, doc, salt)
    return doc


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


def summarize(doc: dict) -> dict[str, Any]:
    entries = doc["entries"]
    return {
        "total": len(entries),
        "defined": sum(1 for e in entries if e.get("isDefined")),
        "excluded": len(doc.get("excluded", {})),
        "definitions_error": sum(1 for e in entries if e.get("definitionsError")),
        "seconds": doc.get("seconds"),
    }


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
