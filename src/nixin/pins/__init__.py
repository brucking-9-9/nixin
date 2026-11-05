"""pins — hold a package or the kernel at a nixpkgs revision while the rest moves on.

    nixin pin NAME [--attr ATTR]... [--rev REV] [--whole-set] [--why TEXT] [--dry-run] [--no-check]
    nixin unpin NAME... | --all [--dry-run] [--no-check]
    nixin pins [--check]

A pin is written to installer/state.json (`nixos.<host>.pins`) and rendered into the
generated `installer/<host>.nix` as a second nixpkgs, fetched by the lock's narHash
(`builtins.fetchTarball`: fixed-output, so pure, and offline once nix has the tree — it is
the very tree the flake input had), plus an overlay that takes the pinned attributes from
it. flake.nix and flake.lock are never touched by a pin; `nix flake update` moves the rest.

NAME `kernel` holds the kernel attribute the host's `boot.kernelPackages` names (19BK:
`pkgs.linux_7_2.override …` under the user's own mkForce, which stays in charge; mbp13:
`pkgs.linuxPackages_7_2`). Both aliases are overlaid: the kernel from the pin and the module
set as `linuxPackagesFor` of it (mode `kernel`: modules from the new nixpkgs, built against
the held kernel), or the whole set from the pin (`--whole-set`, mode `kernel-set`). Any
other NAME is a top-level attribute of pkgs (`--attr` to pin several under one name).

The default revision is the one flake.lock has now: pin first, then `nixin update` (or
`nixin update --hold NAME,…`, which does both and checks the pins against the new lock).
A pin at the lock's own revision holds nothing yet and `pins` says so (stale).

Pins are per host; Home Manager users on a host with `home-manager.useGlobalPkgs` see the
host's overlay through the shared pkgs. Nested attributes (python3Packages.x, kdePackages.x)
and kernels from another flake input are `pin-unsupported`: an overlay does not reach them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
from typing import Any

from .. import writer
from ..configrepo import ConfigRepo, NixinError, nix_attr
from ..outdated import target as tgt
from ..writer.nixtext import parse_attrpath
from ..writer.state import STATE_FILE
from . import checks as chk
from . import kernel as kern
from . import render

VERBS = ("pin", "unpin", "pins")
HELP = {
    "pin": "hold a package (or `kernel`) at the nixpkgs revision flake.lock has now",
    "unpin": "drop a pin (NAME..., or --all)",
    "pins": "every pin of the host: what it holds, at which revision, whether it still does",
}
EVAL_TIMEOUT = 900.0
KERNEL = "kernel"


def register(sub: argparse._SubParsersAction) -> None:
    for verb in VERBS:
        p = sub.choices.get(verb)
        if p is None:
            p = sub.add_parser(verb, help=HELP[verb])
            p.add_argument("args", nargs="*")
        p.add_argument("--config", help="workspace clone (default: $NIXIN_CONFIG)")
        if verb == "pins":
            p.add_argument(
                "--check",
                action="store_true",
                help="evaluate the host: does each pin still hold, and the compatibility checks",
            )
            p.set_defaults(run=pins, no_eval=False, dry_run=False, home=None)
            continue
        p.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
        p.add_argument(
            "--no-check",
            "--no-eval",
            dest="no_eval",
            action="store_true",
            help="no nix evals: no version lookup before, no check after (text + parse only)",
        )
        if verb == "pin":
            p.add_argument(
                "--attr",
                action="append",
                metavar="ATTR",
                help="the pkgs attribute(s) to hold under NAME (default: NAME itself; for "
                "`kernel`: the kernel attribute, when the config names several)",
            )
            p.add_argument(
                "--rev",
                metavar="REV",
                help="a nixpkgs commit to hold at (default: the one flake.lock has now)",
            )
            p.add_argument(
                "--whole-set",
                action="store_true",
                help="kernel: take the out-of-tree module set from the pin too (mode kernel-set)",
            )
            p.add_argument("--why", metavar="TEXT", help="why, for `pins` and the generated file")
            p.set_defaults(run=pin, home=None)
        else:
            p.add_argument("--all", action="store_true", help="drop every pin of the host")
            p.set_defaults(run=unpin, home=None)


# --- reading pins without a workspace (outdated, plan, pins) ---------------------------------


def read_pins(path: str | os.PathLike[str], host: str) -> dict[str, dict[str, Any]]:
    """The host's pins from installer/state.json ({} when there is none or it is unreadable)."""
    try:
        with open(os.path.join(path, STATE_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    entry = ((data.get("nixos") or {}).get(host) or {}) if isinstance(data, dict) else {}
    pins_ = entry.get("pins") or {}
    return {k: dict(v) for k, v in pins_.items() if isinstance(v, dict)}


def pin_doc(
    name: str, p: dict[str, Any], lock_rev: str | None, now: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One pin as the documents list it; `now`: the attrs' versions as the host evaluates
    (held, so normally the pinned ones; `outdated` says what the lock would give instead)."""
    attrs = list(p.get("attrs") or [])
    stale, why = False, None
    if lock_rev and p.get("rev") == lock_rev:
        stale, why = True, f"flake.lock is at {lock_rev[:7]}, the revision it pins"
    doc = {
        "name": name,
        "mode": p.get("mode") or "attr",
        "attrs": attrs,
        "pname": p.get("pname"),
        "version": p.get("version"),
        "rev": p.get("rev"),
        "short_rev": (p.get("rev") or "")[:7] or None,
        "date": p.get("date"),
        "why": p.get("why"),
        "since": p.get("since"),
        "stale": stale,
        "stale_why": why,
        "drop": f"nixin unpin {shlex.quote(name)}",
    }
    if now is not None:
        doc["now"] = {a: now.get(a) for a in attrs}
    return doc


def summary(path: str | os.PathLike[str], host: str, lock_rev: str | None) -> list[dict[str, Any]]:
    """`pins[]` for plan/apply/outdated: cheap, no eval."""
    return [pin_doc(n, p, lock_rev) for n, p in sorted(read_pins(path, host).items())]


def stale_warnings(pins_: list[dict[str, Any]]) -> list[str]:
    return [
        f"pin {json.dumps(p['name'])} holds nothing: {p['stale_why']} ({p['drop']})"
        for p in pins_
        if p.get("stale")
    ]


# --- shared ------------------------------------------------------------------------------------


def _today() -> str:
    return dt.date.today().isoformat()


def _nixpkgs(repo: ConfigRepo) -> tgt.Nixpkgs:
    np = tgt.nixpkgs_input(repo)  # not-found without a nixpkgs input
    lk = np.locked
    if lk.type != "github" or not (lk.owner and lk.repo and lk.rev and lk.nar_hash):
        raise NixinError(
            "pin-unsupported",
            f"the {lk.name} input is locked as {lk.type or 'unknown'}, not a github tree with a "
            "narHash; nixin fetches a pinned nixpkgs by narHash from GitHub",
        )
    return np


def _tarball(owner: str, repo: str, rev: str) -> str:
    return f"https://github.com/{owner}/{repo}/archive/{rev}.tar.gz"


def _prefetch(repo: ConfigRepo, np: tgt.Nixpkgs, rev: str) -> tuple[str, str | None, str]:
    """(narHash, date, full commit) of another commit of the locked repository
    (`nix flake prefetch`; a short `--rev` comes back as the 40-digit commit)."""
    lk = np.locked
    ref = f"github:{lk.owner}/{lk.repo}/{rev}"
    try:
        meta = json.loads(repo.nix(["flake", "prefetch", "--json", ref], code="nix-failed"))
    except NixinError as e:
        text = e.stderr or e.message
        if tgt.is_network(text):
            raise NixinError("network", f"cannot fetch {ref}: {e.message}") from e
        if tgt.is_bad_ref(text):
            raise NixinError("bad-channel", f"{ref} does not resolve: {e.message}") from e
        raise
    nar = meta.get("hash") or (meta.get("locked") or {}).get("narHash")
    if not nar:
        raise NixinError("nix-failed", f"nix flake prefetch {ref} gave no hash")
    locked = meta.get("locked") or {}
    lm = locked.get("lastModified")
    date = dt.datetime.fromtimestamp(lm, dt.UTC).strftime("%Y-%m-%d") if lm else None
    full = locked.get("rev") or meta.get("revision") or rev
    return nar, date, full


def _eval_check(repo: ConfigRepo, host: str, attrs: list[str], *, kernel: bool) -> dict[str, Any]:
    return repo.eval_json(
        repo.host_attr(host), apply=chk.check_apply(attrs, kernel=kernel), timeout=EVAL_TIMEOUT
    )


def _pinned_expr(url: str, sha256: str, attrs: list[str]) -> str:
    lists = json.dumps([parse_attrpath(a) for a in attrs])
    return (
        f"let p = import (builtins.fetchTarball {{ url = {json.dumps(url)}; sha256 = "
        f"{json.dumps(sha256)}; }}) {{ system = builtins.currentSystem; config = {{ allowUnfree = "
        "true; }; overlays = [ ]; }; lib = p.lib; safe = x: let r = builtins.tryEval x; in if "
        "r.success then r.value else null; in map (a: let v = safe (lib.attrByPath a null p); in "
        "if v == null then null else { version = safe (v.version or null); pname = safe (v.pname "
        f"or null); }}) (builtins.fromJSON {json.dumps(lists)})"
    )


def _pinned_versions(
    repo: ConfigRepo, url: str, sha256: str, attrs: list[str]
) -> dict[str, dict[str, Any] | None]:
    """version/pname of the attrs in the pinned tree itself (`--rev` other than the lock)."""
    expr = _pinned_expr(url, sha256, attrs)
    got = json.loads(repo.nix(["eval", "--json", "--impure", "--expr", expr], code="eval-failed"))
    return dict(zip(attrs, got, strict=True))


def _pinned_glibc(repo: ConfigRepo, pins_: dict[str, dict[str, Any]]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for p in pins_.values():
        rev = p["rev"]
        if rev in out:
            continue
        try:
            got = repo.nix(
                [
                    "eval",
                    "--raw",
                    "--impure",
                    "--expr",
                    chk.pinned_glibc_apply(_tarball(p["owner"], p["repo"], rev), p["nar_hash"]),
                ],
                code="eval-failed",
            )
            out[rev] = got.strip() or None
        except NixinError:
            out[rev] = None
    return out


def run_checks(
    repo: ConfigRepo,
    host: str,
    pins_: dict[str, dict[str, Any]],
    lock_rev: str | None,
    *,
    ws: writer.Workspace | None = None,
    mods: list | None = None,
    with_glibc: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The eval + the judgement for these pins on the host as it is: (checks[], the eval)."""
    attrs = [a for p in pins_.values() for a in p["attrs"]]
    kernel = any((p.get("mode") or "attr") != "attr" for p in pins_.values())
    got = _eval_check(repo, host, attrs, kernel=kernel)
    set_refs: list[tuple[str, int, str]] = []
    if kernel and ws is not None and mods is not None:
        set_refs = kern.set_references(ws, mods, kern.declarations(ws, mods))
    glibc = {}
    if with_glibc and any(p["rev"] != lock_rev for p in pins_.values()):
        glibc = _pinned_glibc(repo, {n: p for n, p in pins_.items() if p["rev"] != lock_rev})
    return chk.judge(pins_, got=got, lock_rev=lock_rev, pinned_glibc=glibc, set_refs=set_refs), got


def _check_callback(ctx: writer.Ctx, pins_: dict[str, dict[str, Any]], lock_rev: str | None):
    """The writer's post-write eval check: the pins hold (no `block`), with the checks."""

    def check() -> dict[str, Any]:
        try:
            items, got = run_checks(
                ctx.ws.repo, ctx.scope.host, pins_, lock_rev, ws=ctx.ws, mods=ctx.scope.mods
            )
        except NixinError as e:
            return {"ok": False, "code": e.code, "message": e.message}
        blocks = [c for c in items if c["level"] == "block"]
        attrs = [a for p in pins_.values() for a in p["attrs"]]
        versions = dict(zip(attrs, got.get("attrs") or [], strict=False))
        now = ", ".join(  # a package set (linuxPackages_X) has no version: left out
            f"{a} {(versions.get(a) or {}).get('version')}"
            for a in attrs[:4]
            if (versions.get(a) or {}).get("version")
        )
        doc = {
            "ok": not blocks,
            "note": now if not blocks else blocks[0]["text"],
            "checks": items,
            "kernel": got.get("kernel"),
        }
        if blocks:  # the write stays (nothing is broken, something will not hold or build)
            doc.update({"code": "pin-blocked", "message": blocks[0]["text"]})
        return doc

    return check


def _message_with_checks(doc: dict[str, Any]) -> None:
    if "message" not in doc:
        return
    items = doc.get("hold_checks") or []
    if items:
        doc["message"] += "\n" + "\n".join(render.checks(items))


# --- pin -----------------------------------------------------------------------------------


def pin(ns: argparse.Namespace) -> dict[str, Any]:
    return writer._run(ns, _pin)


def _pin(ns: argparse.Namespace) -> dict[str, Any]:
    if len(ns.args) != 1:
        raise NixinError(
            "usage", "pin NAME [--attr ATTR]... [--rev REV] [--whole-set] [--why TEXT]"
        )
    name = ns.args[0]
    if getattr(ns, "home", None):
        raise NixinError(
            "usage",
            "pins are per host: a pin on the host covers its Home Manager users through the "
            "shared pkgs (home-manager.useGlobalPkgs); drop --home",
        )
    if ns.whole_set and name != KERNEL:
        raise NixinError("usage", "--whole-set is for `pin kernel`")
    ctx = writer._context(ns)
    ws, sc, repo = ctx.ws, ctx.scope, ctx.ws.repo
    entry = ctx.state.entry("nixos", sc.host)
    np = _nixpkgs(repo)
    lk = np.locked
    assert lk.rev and lk.nar_hash  # _nixpkgs checked
    if name in entry["pins"]:
        have = entry["pins"][name]
        ctx.actions.append({"name": name, "action": "already-pinned", "file": sc.gen})
        doc = writer._finish(
            ctx,
            {
                "pin": name,
                "pins": [pin_doc(name, have, lk.rev)],
                "hold_checks": [],
            },
            f"{name} is already pinned at {have.get('version') or '?'} "
            f"(nixpkgs {have['rev'][:7]}); nixin unpin {name} first to change it",
        )
        return doc
    # the revision
    rev, nar, date = lk.rev, lk.nar_hash, lk.date
    if ns.rev and not (lk.rev.startswith(ns.rev) or ns.rev == lk.rev):
        if not tgt._HEX.fullmatch(ns.rev):
            raise NixinError(
                "usage", f"--rev {ns.rev}: a commit (7-40 hex digits) of {lk.owner}/{lk.repo}"
            )
        nar, date, rev = _prefetch(repo, np, ns.rev)
    url = _tarball(lk.owner or "nixos", lk.repo or "nixpkgs", rev)
    # the attributes
    if name == KERNEL:
        mode = "kernel-set" if ns.whole_set else "kernel"
        if ns.attr:
            if len(ns.attr) != 1:
                raise NixinError("usage", "pin kernel takes one --attr: the kernel attribute")
            kernel_attr, set_attr = kern.pair(ns.attr[0].removeprefix("pkgs."))
            decl = "given"
        else:
            found = kern.detect(ws, sc.mods, sc.host)
            pairs = {(k.kernel, k.pkgset) for k in found}
            if len(pairs) > 1:
                raise NixinError(
                    "pin-unsupported",
                    f"{sc.host} defines boot.kernelPackages from different kernels: "
                    + "; ".join(f"{k.decl} ({k.kernel})" for k in found)
                    + ". Say which: nixin pin kernel --attr ATTR",
                )
            kernel_attr, set_attr, decl = found[0].kernel, found[0].pkgset, found[0].decl
        attrs = [kernel_attr, set_attr]
    else:
        mode = "attr"
        attrs = [a.removeprefix("pkgs.") for a in (ns.attr or [name])]
        for a in attrs:
            chk.refuse_unsupported(a)
        decl = None
    # what it holds now (one eval), unless --no-check
    version = pname = None
    if ctx.use_eval:
        got = _eval_check(repo, sc.host, attrs, kernel=mode != "attr")
        answers = dict(zip(attrs, got.get("attrs") or [], strict=False))
        missing = [a for a in attrs if answers.get(a) is None]
        if missing:
            raise NixinError(
                "unknown-package",
                f"not in the pkgs of {sc.host}: {', '.join(missing)} (try `nixin search`)",
            )
        first = answers[attrs[0]] or {}
        version, pname = first.get("version"), first.get("pname")
        if mode != "attr":
            running = got.get("kernel")
            if running and version and running != version:
                raise NixinError(
                    "pin-unsupported",
                    f"{sc.host}'s kernel is {running} but pkgs.{kernel_attr} is {version} "
                    f"({decl}); boot.kernelPackages is not built from that attribute, so holding "
                    "it would not hold the kernel. Say which: nixin pin kernel --attr ATTR",
                )
        if rev != lk.rev:  # the held version is the pinned tree's, not the lock's
            pinned = _pinned_versions(repo, url, nar, attrs)
            missing = [a for a in attrs if pinned.get(a) is None]
            if missing:
                raise NixinError(
                    "unknown-package",
                    f"not in nixpkgs {rev[:7]}: {', '.join(missing)}",
                )
            version = (pinned[attrs[0]] or {}).get("version")
            pname = (pinned[attrs[0]] or {}).get("pname") or pname
    record = {
        "attrs": attrs,
        "mode": mode,
        "rev": rev,
        "nar_hash": nar,
        "date": date,
        "owner": lk.owner,
        "repo": lk.repo,
        "version": version,
        "pname": pname,
        "why": ns.why or None,
        "since": _today(),
    }
    if decl and decl != "given":
        record["decl"] = decl
    entry["pins"][name] = record
    ctx.actions.append({"name": name, "action": "pinned", "file": sc.gen, "rev": rev})
    writer._sync_generated(ctx)
    pre = chk.judge(
        {name: record},
        got=None,
        lock_rev=lk.rev,
        pinned_glibc={},
        set_refs=[],
        with_dependents=False,
    )
    held = (
        f"{attrs[0]} at {version or '?'}"
        if mode == "attr" and len(attrs) == 1
        else (
            f"the kernel ({kernel_attr}) at {version or '?'}"
            if mode != "attr"
            else ", ".join(attrs)
        )
    )
    summary_ = f"pinned {name}: {held}, nixpkgs {rev[:7]}" + (f" ({date})" if date else "")
    check = _check_callback(ctx, {name: record}, lk.rev) if ctx.use_eval else None
    doc = writer._finish(
        ctx,
        {"pin": name, "pins": [pin_doc(name, record, lk.rev)], "hold_checks": pre},
        summary_,
        check,
    )
    ev = (doc.get("checks") or {}).get("eval") or {}
    if ev.get("checks"):
        seen = {(c["id"], c.get("data", {}).get("pin"), c.get("data", {}).get("attr")) for c in pre}
        for c in ev["checks"]:
            key = (c["id"], c.get("data", {}).get("pin"), c.get("data", {}).get("attr"))
            if key not in seen:
                doc["hold_checks"].append(c)
        doc["hold_checks"].sort(key=lambda c: (chk.level_order(c["level"]), c["id"]))
    _message_with_checks(doc)
    return doc


# --- unpin ----------------------------------------------------------------------------------


def unpin(ns: argparse.Namespace) -> dict[str, Any]:
    return writer._run(ns, _unpin)


def _unpin(ns: argparse.Namespace) -> dict[str, Any]:
    if not ns.args and not ns.all:
        raise NixinError("usage", "unpin NAME... | --all")
    if ns.args and ns.all:
        raise NixinError("usage", "unpin takes NAME... or --all, not both")
    ctx = writer._context(ns)
    sc, repo = ctx.scope, ctx.ws.repo
    entry = ctx.state.entry("nixos", sc.host)
    names = sorted(entry["pins"]) if ns.all else list(dict.fromkeys(ns.args))
    unknown = [n for n in names if n not in entry["pins"]]
    if unknown:
        have = ", ".join(sorted(entry["pins"])) or "none"
        raise NixinError("not-found", f"{sc.host} has no pin {', '.join(unknown)} (pins: {have})")
    lock_rev = None
    try:
        lock_rev = tgt.nixpkgs_input(repo).locked.rev
    except NixinError:
        pass
    dropped = {}
    for n in names:
        p = entry["pins"].pop(n)
        dropped[n] = p
        ctx.actions.append({"name": n, "action": "unpinned", "file": sc.gen, "rev": p.get("rev")})
    if names:
        writer._sync_generated(ctx)
    attrs = [a for p in dropped.values() for a in p.get("attrs") or []]

    def check() -> dict[str, Any]:
        try:
            got = _eval_check(repo, sc.host, attrs, kernel=True)
        except NixinError as e:
            return {"ok": False, "code": e.code, "message": e.message}
        versions = dict(zip(attrs, got.get("attrs") or [], strict=False))
        now = ", ".join(  # a package set (linuxPackages_X) has no version: left out
            f"{a} {(versions.get(a) or {}).get('version')}"
            for a in attrs[:4]
            if (versions.get(a) or {}).get("version")
        )
        return {"ok": True, "note": f"now {now}" if now else "ok", "kernel": got.get("kernel")}

    summary_ = f"unpinned {', '.join(names)} on {sc.host}" if names else f"{sc.host} has no pins"
    return writer._finish(
        ctx,
        {"unpinned": [pin_doc(n, p, lock_rev) for n, p in dropped.items()]},
        summary_,
        check if ctx.use_eval and names else None,
    )


# --- pins -------------------------------------------------------------------------------------


def pins(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _pins(ns)
    except NixinError as e:
        return e.doc()


def _pins(ns: argparse.Namespace) -> dict[str, Any]:
    if ns.args:
        raise NixinError("usage", f"pins takes no arguments (got {' '.join(ns.args)!r})")
    repo = ConfigRepo.locate(ns.config)
    host = ns.host
    pins_ = read_pins(repo.path, host)
    lock_rev = None
    lock: dict[str, Any] = {}
    try:
        np = tgt.nixpkgs_input(repo)
        lock_rev = np.locked.rev
        lock = np.doc()
    except NixinError:
        pass
    now: dict[str, Any] | None = None
    items: list[dict[str, Any]] = []
    sizes: dict[str, Any] = {}
    if ns.check and pins_:
        items, got = run_checks(repo, host, pins_, lock_rev)
        attrs = [a for p in pins_.values() for a in p["attrs"]]
        answers = dict(zip(attrs, got.get("attrs") or [], strict=False))
        now = {a: (answers.get(a) or {}).get("version") for a in attrs}
        for a in attrs:
            out = (answers.get(a) or {}).get("out")
            if out and os.path.exists(out):
                sizes[a] = _closure_size(repo, out)
    docs = [pin_doc(n, p, lock_rev, now) for n, p in sorted(pins_.items())]
    for d in docs:
        d["size"] = {a: sizes.get(a) for a in d["attrs"]} if sizes else None
    doc = {
        "ok": True,
        "config": str(repo.path),
        "host": host,
        "lock": {k: lock.get(k) for k in ("input", "node", "rev", "short_rev", "date")},
        "pins": docs,
        "count": len(docs),
        "stale": sum(1 for d in docs if d["stale"]),
        "checked": bool(ns.check and pins_),
        "checks": items,
    }
    if not ns.json:
        doc["message"] = render.pins(doc)
    return doc


def _closure_size(repo: ConfigRepo, out: str) -> int | None:
    try:
        got = json.loads(repo.nix(["path-info", "--json", "-S", out], code="nix-failed"))
    except (NixinError, ValueError):
        return None
    if isinstance(got, dict):  # nix ≥ 2.19: {path: {closureSize, …}}
        info = got.get(out) or next(iter(got.values()), {})
        return info.get("closureSize")
    if isinstance(got, list) and got:  # older: [{path, closureSize}]
        return got[0].get("closureSize")
    return None


# --- update --hold ------------------------------------------------------------------------------


def hold_before_update(
    ns: argparse.Namespace, repo: ConfigRepo, inp: tgt.Nixpkgs
) -> dict[str, Any]:
    """`nixin pin NAME` for each `--hold` name, at the lock as it is; a failure stops the update
    before the lock moves. Returns {"pins": [...], "names": [...]}."""
    names = [n.strip() for n in ns.hold.split(",") if n.strip()]
    if not names:
        raise NixinError("usage", "--hold takes NAME[,NAME...]")
    out = []
    for name in names:
        sub = argparse.Namespace(
            args=[name],
            attr=None,
            rev=None,
            whole_set=False,
            why=f"held by `nixin update --hold` on {_today()}",
            config=ns.config,
            host=ns.host,
            json=True,
            no_eval=False,
            dry_run=bool(ns.dry_run),
            home=None,
        )
        doc = _pin(sub)  # raises NixinError on failure (nothing written then)
        if not doc.get("ok"):
            raise NixinError(doc["error"]["code"], doc["error"]["message"])
        out += doc["pins"]
    return {"pins": out, "names": names}


def check_after_update(ns: argparse.Namespace, repo: ConfigRepo, holds: dict[str, Any]) -> list:
    """The held pins' checks against the lock as `update` left it."""
    pins_ = {n: p for n, p in read_pins(repo.path, ns.host).items() if n in holds["names"]}
    if not pins_:
        return []
    lock_rev = tgt.nixpkgs_input(repo).locked.rev
    try:
        ws = writer.Workspace.open(ns.config, nix=writer.NIX)
        sc = writer._scope(ws, ns.host, None, use_eval=True)
        items, _got = run_checks(repo, ns.host, pins_, lock_rev, ws=ws, mods=sc.mods)
    except NixinError as e:
        return [chk.check("check-failed", "warn", f"could not check the holds: {e.message}")]
    return items


__all__ = ["HELP", "VERBS", "nix_attr", "read_pins", "register", "stale_warnings", "summary"]
