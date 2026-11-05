"""update — take the nixpkgs update `outdated` describes: move one input in flake.lock.

    nixin update [--input NAME] [--to FLAKEREF|REV] [--dry-run] [--config DIR]

Runs exactly the command `nixin outdated` prints under `next:`, in the workspace (a clone
you own; /etc/nixos and trees you cannot write are `not-a-workspace`):

    nix flake update <input> --flake DIR                          (default: the input's own ref)
    nix flake lock DIR --override-input <input> <ref>[/<rev>]     (--to: pins that ref or commit)

and reports the input's locked commit before and after. It writes flake.lock and nothing
else: no build, no commit, no switch (`nixin plan`, `nixin apply` take it from there).
The default input is the nixpkgs one (`outdated`'s pick). nix fetches the new commit's
tarball (about a minute the first time).

Only the named input may move. After nix ran, the lock is compared node by node: the
requested root input and the nodes reachable only through it (its private sub-inputs) may
change; if any other node moved, the saved lock is put back and the verb fails with
`lock-moved-others` (`--allow-others` keeps nix's result and lists the moves in `moved[]`).
`--hold NAME,…` pins those (`nixin pin`, at the lock as it is now) before the input moves,
and runs the pins' checks against the new lock afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import time
from typing import Any

from ..configrepo import ConfigRepo, LockedInput, NixinError, parse_lock
from . import target as tgt

VERBS = ("update",)
HELP = "move the nixpkgs input in the workspace's flake.lock (what `outdated` would change)"
_HEX = re.compile(r"[0-9a-f]{7,40}")
_REPO_TYPES = ("github", "gitlab", "sourcehut")  # where `--to REV` means <type>:owner/repo/REV


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.choices.get("update")
    if p is None:
        p = sub.add_parser("update", help=HELP)
        p.add_argument("args", nargs="*")
    p.add_argument("--config", help="workspace clone (default: $NIXIN_CONFIG)")
    p.add_argument(
        "--input",
        metavar="NAME",
        help="the flake input to move (default: the nixpkgs input, as `outdated` finds it)",
    )
    p.add_argument(
        "--to",
        metavar="FLAKEREF|REV",
        help="pin this flakeref, or this commit of the locked repository "
        "(default: the input's own ref, as `nix flake update` follows it)",
    )
    p.add_argument("--dry-run", action="store_true", help="say what would run; run nothing")
    p.add_argument(
        "--allow-others",
        action="store_true",
        help="keep the new lock even when nix moved other inputs too (listed in moved[])",
    )
    p.add_argument(
        "--hold",
        metavar="NAME,...",
        help="pin these (kernel, or package attributes) at the current lock first, then "
        "check them against the new lock (see `nixin pin`)",
    )
    p.set_defaults(run=update)


def update(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _update(ns)
    except NixinError as e:
        return e.doc()


def _workspace(explicit: str | None) -> ConfigRepo:
    from ..writer.workspace import Workspace  # the writer's rule for what may be written

    return Workspace.open(explicit).repo  # config-not-found / not-a-workspace


def _input(repo: ConfigRepo, name: str | None) -> tgt.Nixpkgs:
    """The input to move, with its lock node and `original`."""
    if name is None:
        return tgt.nixpkgs_input(repo)  # not-found when there is no nixpkgs input
    lock = _read_lock(repo)
    nodes = lock.get("nodes", {})
    names = sorted(nodes.get(lock.get("root", "root"), {}).get("inputs", {}))
    locked = parse_lock(lock, name) if name in names else None
    if locked is None:
        raise NixinError(
            "not-found",
            f"{repo.path}/flake.lock has no input {name!r} (inputs: {', '.join(names) or 'none'})",
        )
    return tgt.Nixpkgs(locked, nodes[locked.node].get("original", {}))


def _read_lock(repo: ConfigRepo) -> dict[str, Any]:
    lock_file = repo.path / "flake.lock"
    try:
        return json.loads(lock_file.read_text())
    except (OSError, ValueError) as e:
        raise NixinError("not-found", f"no readable flake.lock in {repo.path}: {e}") from e


def _pin(inp: tgt.Nixpkgs, to: str) -> str:
    """`--to` as the ref given to --override-input: a commit of the locked repository
    (as `outdated` pins it), or a flakeref as is."""
    if not _HEX.fullmatch(to):
        return to
    lk = inp.locked
    if lk.type not in _REPO_TYPES or not (lk.owner and lk.repo):
        raise NixinError(
            "usage",
            f"--to {to}: a bare commit needs a github/gitlab/sourcehut input; "
            f"{inp.input} is {lk.type or 'unknown'}, so give a flakeref",
        )
    return f"{lk.type}:{lk.owner}/{lk.repo}/{to}"


def _command(repo: ConfigRepo, inp: tgt.Nixpkgs, ref: str | None) -> list[str]:
    """The nix arguments (after `nix`): what `outdated`'s `next:` prints."""
    if ref is None:
        return ["flake", "update", inp.input, "--flake", str(repo.path)]
    return ["flake", "lock", str(repo.path), "--override-input", inp.input, ref]


def _side(lk: LockedInput | None) -> dict[str, Any]:
    if lk is None:
        return {"rev": None, "short_rev": None, "date": None, "last_modified": None}
    return {
        "rev": lk.rev,
        "short_rev": lk.short_rev,
        "date": lk.date,
        "last_modified": lk.last_modified,
        "nar_hash": lk.nar_hash,
    }


def _lock_nodes(lock: dict[str, Any]) -> dict[str, Any]:
    return lock.get("nodes") or {}


def _follow(nodes: dict[str, Any], path: list[str]) -> str | None:
    """A `follows` path ([] = root, ["nixpkgs"] = root's nixpkgs) to its node name."""
    node = "root"
    for name in path:
        ref = (nodes.get(node, {}).get("inputs") or {}).get(name)
        if ref is None:
            return None
        node = ref if isinstance(ref, str) else _follow(nodes, ref)
        if node is None:
            return None
    return node


def _resolve_ref(nodes: dict[str, Any], node: str, ref: Any) -> str | None:
    return ref if isinstance(ref, str) else _follow(nodes, ref) if isinstance(ref, list) else None


def lock_moves(before: dict[str, Any], after: dict[str, Any], node: str) -> list[dict[str, Any]]:
    """Nodes whose `locked` differs between the two locks and that are not `node` or one of
    the nodes only `node` reaches (its private sub-inputs, in the *after* lock: a new
    revision may bring one). Each: {node, from, to} with the short revs (or narHashes)."""
    b, a = _lock_nodes(before), _lock_nodes(after)
    root = after.get("root", "root")
    private = _reach(a, node) - _reach(a, root, avoid=node)
    allowed = {node} | private
    out = []
    for name in sorted(set(b) | set(a)):
        if name in allowed or name == root:
            continue
        lb, la = (b.get(name) or {}).get("locked"), (a.get(name) or {}).get("locked")
        if lb == la:
            continue
        out.append({"node": name, "from": _ident(lb), "to": _ident(la)})
    return out


def _reach(nodes: dict[str, Any], start: str, avoid: str | None = None) -> set[str]:
    seen: set[str] = set()
    queue = [start]
    while queue:
        n = queue.pop()
        if n is None or n in seen or n == avoid or n not in nodes:
            continue
        seen.add(n)
        for ref in (nodes[n].get("inputs") or {}).values():
            queue.append(_resolve_ref(nodes, n, ref))
    return seen


def _ident(locked: dict[str, Any] | None) -> str | None:
    if not locked:
        return None
    rev = locked.get("rev")
    return rev[:7] if isinstance(rev, str) else locked.get("narHash")


def _update(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    if ns.args:
        raise NixinError("usage", f"update takes no arguments (got {' '.join(ns.args)!r})")
    repo = _workspace(ns.config)
    inp = _input(repo, ns.input)
    ref = _pin(inp, ns.to) if ns.to else None
    args = _command(repo, inp, ref)
    shown = "nix " + shlex.join(args)
    before = _side(inp.locked)
    doc: dict[str, Any] = {
        "ok": True,
        "config": str(repo.path),
        "input": inp.input,
        "node": inp.locked.node,
        "original": inp.original_ref,
        "to_ref": ref or inp.original_ref,
        "command": shown,
        "dry_run": bool(ns.dry_run),
        "from": before,
    }
    if ns.dry_run:
        doc.update({"to": None, "changed": None, "commands": [shown]})
        doc["timings"] = {"total": round(time.monotonic() - t0, 2)}
        if not ns.json:
            doc["message"] = _render(doc)
        return doc
    holds = None
    if ns.hold:
        from ..pins import hold_before_update  # lazy: the pins layer imports the writer

        holds = hold_before_update(ns, repo, inp)
        doc["holds"] = holds["pins"]
    lock_file = repo.path / "flake.lock"
    saved = lock_file.read_text()
    t = time.monotonic()
    try:
        repo.nix(args, code="nix-failed")
    except NixinError as e:
        text = e.stderr or e.message
        what = ref or inp.original_ref or inp.input
        if tgt.is_network(text):
            raise NixinError("network", f"cannot fetch {what}: {e.message}") from e
        if tgt.is_bad_ref(text):
            raise NixinError("bad-channel", f"{what} does not resolve: {e.message}") from e
        raise
    seconds = round(time.monotonic() - t, 2)
    new_lock = _read_lock(repo)
    moved = lock_moves(json.loads(saved), new_lock, inp.locked.node)
    if moved and not ns.allow_others:
        lock_file.write_text(saved)
        what = ", ".join(f"{m['node']} {m['from']} → {m['to']}" for m in moved)
        raise UpdateError(
            "lock-moved-others",
            f"nix moved more than {inp.input} ({inp.locked.node}): {what}; flake.lock was "
            f"put back as it was. `nixin update --input NAME` for each of them, or "
            f"--allow-others to take nix's result",
            moved=moved,
        )
    after = parse_lock(new_lock, inp.input)
    to = _side(after)
    changed = (to["rev"], to["nar_hash"]) != (before["rev"], before["nar_hash"])
    nixin = f"--config {shlex.quote(str(repo.path))} --host {shlex.quote(ns.host)}"
    doc.update(
        {
            "to": to,
            "changed": changed,
            "moved": moved,
            "commands": [f"nixin plan {nixin}", f"nixin apply {nixin}"] if changed else [],
            "timings": {"nix": seconds, "total": round(time.monotonic() - t0, 2)},
        }
    )
    if moved:
        doc.setdefault("warnings", []).append(
            "other inputs moved too (--allow-others): "
            + ", ".join(f"{m['node']} {m['from']} → {m['to']}" for m in moved)
        )
    if holds is not None:
        from ..pins import check_after_update

        doc["checks"] = check_after_update(ns, repo, holds)
        if any(c["level"] == "block" for c in doc["checks"]):
            doc.setdefault("warnings", []).append(
                "a hold check is blocking: see checks[] (the lock moved anyway; "
                "`nixin pin NAME --whole-set` or `nixin unpin NAME`)"
            )
    if not ns.json:
        doc["message"] = _render(doc)
    return doc


class UpdateError(NixinError):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(code, message)
        self.extra = extra

    def doc(self) -> dict[str, Any]:
        d = super().doc()
        d["error"].update(self.extra)
        return d


def _when(side: dict[str, Any] | None) -> str:
    if not side or not side.get("rev"):
        return "?"
    return side["short_rev"] + (f" ({side['date']})" if side.get("date") else "")


def _render(doc: dict[str, Any]) -> str:
    head = f"{doc['input']} ({doc['to_ref']})"
    if doc["dry_run"]:
        return f"{head}: at {_when(doc['from'])}\nwould run: {doc['command']}"
    lines = [f"ran: {doc['command']}"]
    if doc["changed"]:
        lines.insert(0, f"{head}: {_when(doc['from'])} → {_when(doc['to'])}")
    else:
        lines.insert(0, f"{head}: already at {_when(doc['to'])}; flake.lock unchanged")
    for h in doc.get("holds") or []:
        lines.append(f"held: {h['name']} ({', '.join(h['attrs'])}) at {h['version'] or '?'}")
    for c in doc.get("checks") or []:
        lines.append(f"{c['level']}: {c['text']}")
    lines += [f"warning: {w}" for w in doc.get("warnings") or []]
    if doc["changed"]:
        lines += ["next:", *(f"  {c}" for c in doc["commands"])]
    return "\n".join(lines)
