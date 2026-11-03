"""nixin-state: nixin's numbers for bars, dashboards and notifications, kept on disk.

    nixin-state outdated [--timeout MS] [--quiet-hours H-H] [--no-notify]
        `nixin --json outdated --published` and `nixin --json outdated` on the
        authoritative tree (what is deployed), documents to
        $STATE/outdated-{published,lock}.json; a notification (notify-send) when the
        number of packages with a newer version changed since the last one.
        --quiet-hours holds a notification back until a run outside them.
    nixin-state status [--force]
        `nixin --json status` on the workspace -> $STATE/status.json, for its `pending`
        block. Skipped while neither tree nor /run/current-system changed and the last
        run is younger than an hour (--force: run anyway).
    nixin-state summary
        print summary.json
    nixin-state report LOCK.json PUBLISHED.json
        markdown of the two outdated documents (a flake report)

The modules' timers run the first two (programs.nixin.outdated, programs.nixin.pending;
README, "Modules"). $STATE = $NIXIN_STATE_DIR, else $XDG_STATE_HOME/nixin
(~/.local/state/nixin). The authoritative tree is $NIXIN_AUTHORITATIVE, else /etc/nixos;
the workspace is $NIXIN_CONFIG, else ~/nixos when it is a git checkout, else the
authoritative tree (then there is nothing pending to count).

summary.json is what the UIs read; every run rewrites it under a lock:
  outdated   {count, source (lock|published), total, kernel {from, to} | null,
             days_newer, at}: lock mode (exact) when it worked, else published
  lock, published   {count, total, kernel, days_newer, at} per document
  pending    {count, commits, dirty, untracked, apply, workspace, at}: count =
             commits + changed files (nixin's own history file aside); apply =
             the running system is not the authoritative HEAD's build
  errors     {lock|published|status: {code, message, at}}: the last failure, dropped
             on the next success. A failed nixin run keeps the last good document and
             still exits 0 (a network blip at boot should not leave a failed unit for
             a day); the UIs show errors.
  notified   {count, at}: what the last notification said
  at, host   when the file was written, and on which machine
`count` is packages whose version goes up (direction "up").

Moved here from the config repo's hand-wired copy (home/modules/tools/nixin/state.py on
the hub's nixin-integration branch); the nixin verbs now run in-process.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HISTORY = "installer/history.jsonl"  # nixin's apply log, inside the workspace
STATUS_MAX_AGE = 3600


def log(msg: str) -> None:
    print(f"nixin-state: {msg}", file=sys.stderr, flush=True)


def state_dir() -> Path:
    d = os.environ.get("NIXIN_STATE_DIR")
    if not d:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        d = os.path.join(base, "nixin")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def authoritative() -> str:
    return os.environ.get("NIXIN_AUTHORITATIVE") or "/etc/nixos"


def workspace() -> str:
    ws = os.environ.get("NIXIN_CONFIG")
    if ws:
        return ws
    home = os.path.expanduser("~/nixos")
    if os.path.isdir(os.path.join(home, ".git")):
        return home
    return authoritative()


def read_json(p: Path | str) -> Any:
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(p: Path, doc: Any) -> None:
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)


def nixin(*args: str) -> dict[str, Any]:
    """One `nixin --json ARGS` document, run in-process (the CLI's own document path)."""
    from . import cli

    try:
        ns = cli._parser().parse_args(["--json", *args])
    except cli.UsageError as e:
        return {"ok": False, "error": {"code": "usage", "message": str(e)}}
    doc = cli.document(ns)
    return {"verb": ns.verb, "host": ns.host, **doc}


class Summary:
    """summary.json, read-modify-written under an flock (the units can overlap)."""

    def __init__(self, sd: Path) -> None:
        self.path = sd / "summary.json"
        self.lockf = open(sd / ".summary.lock", "w")  # noqa: SIM115 -- held across __enter__/__exit__

    def __enter__(self) -> dict[str, Any]:
        fcntl.flock(self.lockf, fcntl.LOCK_EX)
        self.doc = read_json(self.path) or {}
        self.doc.setdefault("errors", {})
        return self.doc

    def __exit__(self, *exc: object) -> bool:
        if exc[0] is None:
            self.doc["at"] = int(time.time())
            self.doc["host"] = socket.gethostname()
            write_json(self.path, self.doc)
        fcntl.flock(self.lockf, fcntl.LOCK_UN)
        self.lockf.close()
        return False


def outdated_part(doc: dict[str, Any], at: int) -> dict[str, Any]:
    up = [c for c in doc.get("changed") or [] if c.get("direction") == "up"]
    k = doc.get("kernel") or {}
    kernel = (
        {"from": k.get("from"), "to": k.get("to")} if k.get("changed") and k.get("to") else None
    )
    return {
        "count": len(up),
        "total": doc.get("total"),
        "kernel": kernel,
        "days_newer": (doc.get("target") or {}).get("days_newer"),
        "at": at,
    }


def plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def message(o: dict[str, Any]) -> tuple[str, str]:
    """(summary, body) of the notification for summary['outdated']."""
    n = o["count"]
    if n:
        head = f"{n} {plural(n, 'package has a newer version', 'packages have newer versions')}"
    else:
        head = "all packages are up to date"
    body = []
    if o.get("kernel"):
        body.append(f"kernel {o['kernel']['from']} → {o['kernel']['to']}")
    if o.get("source") == "lock" and n and o.get("days_newer") is not None:
        days = round(o["days_newer"])
        body.append(f"nixpkgs {days} {plural(days, 'day', 'days')} newer")
    elif o.get("source") == "published":
        body.append("from search.nixos.org (the lock check failed)")
    return head, " · ".join(body)


def in_quiet(spec: str | None, now: float) -> bool:
    if not spec:
        return False
    try:
        a, b = (int(x) for x in spec.split("-"))
    except ValueError:
        log(f"--quiet-hours {spec!r}: want H-H")
        return False
    h = time.localtime(now).tm_hour
    return (h >= a or h < b) if a > b else (a <= h < b)


def notify(summary: str, body: str, timeout: str | None) -> bool:
    cmd = ["notify-send", "-a", "nixin", "-i", "system-software-update"]
    if timeout:
        cmd += ["-t", str(timeout)]
    try:
        r = subprocess.run(
            [*cmd, summary, body], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"notify-send: {e}")
        return False
    if r.returncode:
        log(f"notify-send exited {r.returncode}: {r.stderr.strip()[-200:]}")
        return False
    return True


def opt(argv: list[str], name: str, default: str | None = None) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cmd_outdated(argv: list[str]) -> int:
    sd = state_dir()
    auth = authoritative()
    timeout = opt(argv, "--timeout")
    quiet = opt(argv, "--quiet-hours")
    runs: dict[str, tuple[dict | None, int, dict | None]] = {}
    for name, extra in (("published", ["--published"]), ("lock", [])):
        t0 = time.time()
        doc = nixin("outdated", "--config", auth, *extra)
        at = int(time.time())
        if doc.get("ok"):
            write_json(sd / f"outdated-{name}.json", doc)
            runs[name] = (doc, at, None)
            took = time.time() - t0
            log(f"{name}: {doc.get('count')} changed of {doc.get('total')} ({took:.0f} s)")
        else:
            e = doc.get("error") or {}
            runs[name] = (None, at, e)
            log(f"{name}: failed: {e.get('code')}: {e.get('message')}")
    now = time.time()
    with Summary(sd) as s:
        for name, (doc, at, e) in runs.items():
            if doc is not None:
                s[name] = outdated_part(doc, at)
                s["errors"].pop(name, None)
            else:
                e = e or {}
                s["errors"][name] = {"code": e.get("code"), "message": e.get("message"), "at": at}
        src = next((n for n in ("lock", "published") if runs[n][0] is not None), None)
        if src is None:
            return 0  # both failed (errors above): the last good numbers stay, no notification
        s["outdated"] = dict(s[src], source=src)
        o = s["outdated"]
        last = (s.get("notified") or {}).get("count")
        if o["count"] == last or (last is None and o["count"] == 0):
            if last is None:
                s["notified"] = {"count": 0, "at": int(now)}
            return 0
        if "--no-notify" in argv:
            return 0
        if in_quiet(quiet, now):
            log(f"count {last} -> {o['count']}: quiet hours, notification held back")
            return 0
        head, body = message(o)
        if notify(head, body, timeout):
            log(f"notified: {head}; {body}")
            s["notified"] = {"count": o["count"], "at": int(now)}
    return 0


def git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "--no-optional-locks", *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def trees_key(ws: str, auth: str) -> str:
    """Changes whenever nixin's pending block can: either HEAD, the workspace's files,
    the running system. --no-optional-locks: no index refresh (the path unit watches
    .git/refs, not the index, but reads should not write)."""
    parts = [
        git("-C", ws, "rev-parse", "HEAD"),
        git("-C", ws, "status", "--porcelain=v1", "--untracked-files=all"),
        git("-c", f"safe.directory={auth}", "-C", auth, "rev-parse", "HEAD"),
        os.path.realpath("/run/current-system"),
    ]
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()


def pending_part(doc: dict[str, Any], at: int) -> tuple[dict | None, dict | None]:
    p = doc.get("pending") or {}
    if "error" in p:
        return None, p["error"]
    commits = p.get("commits")
    dirty = [f for f in p.get("dirty") or [] if f != HISTORY]
    return {
        "count": len(commits or []) + len(dirty),
        "commits": None if commits is None else len(commits),
        "dirty": len(dirty),
        "untracked": len(p.get("untracked") or []),
        "apply": p.get("running_matches") is False,
        "workspace": p.get("workspace"),
        "at": at,
    }, None


def cmd_status(argv: list[str]) -> int:
    sd = state_dir()
    ws, auth = workspace(), authoritative()
    key = trees_key(ws, auth)
    with Summary(sd) as s:
        fresh = time.time() - (s.get("status_at") or 0) < STATUS_MAX_AGE
        if "--force" not in argv and s.get("status_key") == key and fresh:
            return 0
    t0 = time.time()
    doc = nixin("status", "--config", ws)
    at = int(time.time())
    with Summary(sd) as s:
        s["status_key"], s["status_at"] = key, at
        part, err = pending_part(doc, at) if doc.get("ok") else (None, doc.get("error") or {})
        if part is None:
            err = err or {}
            s["errors"]["status"] = {
                "code": err.get("code"),
                "message": err.get("message"),
                "at": at,
            }
            log(f"status on {ws}: {err.get('code')}: {err.get('message')}")
            return 0
        write_json(sd / "status.json", doc)
        s["pending"] = part
        s["errors"].pop("status", None)
        log(f"status on {ws}: {part['count']} pending ({time.time() - t0:.1f} s)")
    return 0


# --- the flake report --------------------------------------------------------------------


def md_table(rows: list, head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def scope_name(sc: dict[str, Any]) -> str:
    return sc["scope"] if not sc.get("user") else f"{sc['scope']} ({sc['user']})"


def failed(verb: str, d: dict | None) -> list[str]:
    if not d:
        return [f"`{verb}`: no document (nixin did not run; see the unit's journal).", ""]
    e = d.get("error") or {}
    return [f"`{verb}` failed: {e.get('code')}: {e.get('message')}", ""]


def _same(c: dict[str, Any], sc: dict[str, Any]) -> bool:
    return c["scope"] == sc["scope"] and c.get("user") == sc.get("user")


def render_lock(d: dict | None) -> list[str]:
    if not d or not d.get("ok"):
        return failed("nixin outdated", d)
    lk, tg = d.get("lock") or {}, d.get("target") or {}
    head = (
        f"nixpkgs `{lk.get('input')}`: locked {lk.get('short_rev')} ({lk.get('date')}) → "
        f"{tg.get('flakeref')} {tg.get('short_rev')} ({tg.get('date')}, "
        f"{round(tg.get('days_newer') or 0)} days newer)"
    )
    out = [head, ""]
    if d.get("up_to_date"):
        return out + ["The lock already has the target commit: nothing changes.", ""]
    k = d.get("kernel") or {}
    line = f"**{d.get('count')} of {d.get('total')} packages change**"
    if k.get("changed"):
        line += f"; kernel {k.get('from')} → {k.get('to')}"
    out += [line + ".", ""]
    for sc in d.get("scopes") or []:
        rows = [
            (
                c["pname"],
                c["from"],
                c["to"] + ("" if c["direction"] == "up" else f" ({c['direction']})"),
            )
            for c in d.get("changed") or []
            if _same(c, sc)
        ]
        added = [a for a in d.get("added") or [] if _same(a, sc)]
        removed = [a for a in d.get("removed") or [] if _same(a, sc)]
        out.append(f"### {scope_name(sc)}: {sc.get('changed')} of {sc.get('total')} change")
        out.append("")
        if rows:
            out += md_table(rows, ["package", "from", "to"]) + [""]
        if added:
            out += ["added: " + ", ".join(f"{a['pname']} {a['version']}" for a in added), ""]
        if removed:
            out += ["removed: " + ", ".join(f"{a['pname']} {a['version']}" for a in removed), ""]
    return out


def render_published(d: dict | None, lock: dict | None) -> list[str]:
    """The channel's view; with a lock document only where the two disagree."""
    if not d or not d.get("ok"):
        return failed("nixin outdated --published", d)
    tg = d.get("target") or {}
    k = d.get("kernel") or {}
    kernel = (
        f"; kernel {k.get('from')} → {k.get('to')} (newest series: {k.get('latest')})"
        if k.get("changed")
        else ""
    )
    head = (
        f"search.nixos.org `{tg.get('channel')}` @ {tg.get('short_rev')}: "
        f"**{d.get('count')} of {d.get('total')} installed packages have a newer version "
        f"published**{kernel}."
    )
    out = [head, ""]
    rows = list(d.get("changed") or [])
    if lock and lock.get("ok"):
        to = {(c["pname"], c["scope"], c.get("user")): c["to"] for c in lock.get("changed") or []}
        rows = [
            c for c in rows if to.get((c["pname"], c["scope"], c.get("user"))) != c["published"]
        ]
        out += [
            f"{len(rows)} of them differ from the lock check above (the channel index and the "
            "branch head are different commits, or the attribute picked by pname is another "
            "one)" + (":" if rows else "."),
            "",
        ]
    table = [(c["pname"], c["installed"], c["published"], c["attr"], scope_name(c)) for c in rows]
    if table:
        out += md_table(table, ["package", "installed", "published", "attribute", "scope"]) + [""]
    unpub = d.get("unpublished") or []
    if unpub:
        out += [
            f"Not in the channel ({len(unpub)}): "
            + ", ".join(str(u) for u in unpub[:40])
            + (" …" if len(unpub) > 40 else ""),
            "",
        ]
    return out


def cmd_report(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: nixin-state report LOCK.json PUBLISHED.json", file=sys.stderr)
        return 2
    lock, pub = read_json(argv[0]), read_json(argv[1])
    out = ["## What `nix flake update nixpkgs` changes (`nixin outdated`, exact)", ""]
    out += render_lock(lock)
    out += ["## Newer versions in the channel (`nixin outdated --published`)", ""]
    out += render_published(pub, lock)
    notes = [n for d in (lock, pub) if d and d.get("ok") for n in d.get("notes") or []]
    if notes:
        out += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    print("\n".join(out).rstrip("\n"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd = argv[0] if argv else ""
    if cmd == "outdated":
        return cmd_outdated(argv[1:])
    if cmd == "status":
        return cmd_status(argv[1:])
    if cmd == "summary":
        print(json.dumps(read_json(state_dir() / "summary.json"), indent=1, sort_keys=True))
        return 0
    if cmd == "report":
        return cmd_report(argv[1:])
    print((__doc__ or "").strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
