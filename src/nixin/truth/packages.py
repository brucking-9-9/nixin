"""Installed packages per host: environment.systemPackages and, per HM user, home.packages.

One `nix eval --apply` per host (packages.nix), ~4.5 s / 450 MiB on 19BK, cached by
TruthCache under the name "packages".
"""

from __future__ import annotations

import time
from importlib.resources import files
from typing import Any

from ..configrepo import ConfigRepo
from .cache import TruthCache

HELPER = files("nixin.truth") / "packages.nix"
CACHE_NAME = "packages"


def helper_text() -> str:
    return HELPER.read_text()


def collect(repo: ConfigRepo, host: str, cache: TruthCache | None = None) -> dict[str, Any]:
    """{"system": {...}, "home": {user: {...}}, "hm_users": [...], "seconds": float}."""
    salt = helper_text()
    if cache is not None:
        hit = cache.get(CACHE_NAME, salt)
        if hit is not None:
            return hit
    t = time.monotonic()
    raw = repo.eval_json(repo.host_attr(host), apply=helper_text())
    doc = {
        "system": raw["system"],
        "home": raw["home"],
        "hm_users": sorted(raw["home"]),
        "seconds": round(time.monotonic() - t, 2),
    }
    if cache is not None:
        cache.put(CACHE_NAME, doc, salt)
    return doc


def counts(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "system": len(doc["system"]["packages"]),
        "home": {u: len(h["packages"]) for u, h in doc["home"].items()},
    }


def _matches(pkg: dict[str, Any], name: str) -> bool:
    if pkg.get("pname") == name or pkg.get("name") == name:
        return True
    n = pkg.get("name") or ""
    return n.startswith(name + "-") and n[len(name) + 1 : len(name) + 2].isdigit()


def find(doc: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Installed copies of a package, by pname or name, in system and every HM user."""
    found: list[dict[str, Any]] = []
    for pkg in doc["system"]["packages"]:
        if _matches(pkg, name):
            found.append({"scope": "system", "user": None, **pkg})
    for user, home in doc["home"].items():
        for pkg in home["packages"]:
            if _matches(pkg, name):
                found.append({"scope": "home", "user": user, **pkg})
    return found


def programs(doc: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Scopes where programs.<name>.enable is true."""
    out: list[dict[str, Any]] = []
    if name in doc["system"]["programs"]:
        out.append({"scope": "system", "user": None, "option": f"programs.{name}.enable"})
    for user, home in doc["home"].items():
        if name in home["programs"]:
            out.append({"scope": "home", "user": user, "option": f"programs.{name}.enable"})
    return out
