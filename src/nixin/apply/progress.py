"""The build tracker: nix's `--log-format internal-json` events as a progress document.

plan and apply run `nix build … --log-format internal-json -v`. nix then writes one
`@nix {…}` JSON line per event to stderr instead of its progress bar (this is also what
`nix-plan` on the user's config parses). `Tracker.feed_line` takes such a line and keeps:

    - the derivations of the plan ("these N derivations will be built:" and the path
      lines after it) with a status each: queued, building (where: the builder host),
      done, failed (with nix's log tail) or skipped (a dependency failed);
    - the newest build's log tail (`focus`), phase and last log line;
    - the downloads (paths copied from a binary cache, bytes transferred);
    - an ETA from the durations of earlier builds of the same names, kept in nixin's
      cache dir (`durations.json`; names without a history count at the known average).

`snapshot()` is the document the UIs show while the build runs and `summary()` the
`build` object plan/apply put in their documents. `feed_line` also returns the readable
form of the event (what nix prints without the JSON log format: the "will be built"
lines, "building '…'", warnings and errors; not the per-line build logs), so human mode
still has nix's output and `build-failed` still gets the failing builder's block.

`Reporter` is the CLI's live status line on stderr: redrawn in place on a terminal, a
plain line now and then otherwise, nothing when a UI reads the snapshot itself.

The event format (nix/util/logging.hh): activity types 100 copy path, 101 file transfer,
104 builds, 105 build (fields: drv, builder host, round, rounds); result types 101 log
line, 104 phase, 105 progress (done, expected, running, failed), 106 expected.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, TextIO

ACT_COPY_PATH, ACT_TRANSFER, ACT_BUILDS, ACT_BUILD = 100, 101, 104, 105
RES_LOG, RES_PHASE, RES_PROGRESS, RES_EXPECTED = 101, 104, 105, 106
LOG_TAIL = 40
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
DRV = re.compile(r"/nix/store/[0-9a-z]{32}-[^\s'\"`]+?\.drv")
_WILL_BUILD = re.compile(r"^(?:these (\d+) derivations|this derivation) will be built")
_WILL_FETCH = re.compile(
    r"^(?:these (\d+) paths|this path) will be fetched"
    r"(?: \(([\d.]+) ([KMGT]?iB) download, ([\d.]+) ([KMGT]?iB) unpacked\))?"
)
_UNITS = {"iB": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}
_VERSION = re.compile(r"-\d[\w.+~-]*$")
DURATIONS_KEEP = 2000  # names remembered


def clean(s: str | None) -> str:
    return ANSI.sub("", s or "").replace("\r", "").rstrip()


def drv_name(path: str) -> str:
    """`/nix/store/<hash>-cowsay-3.8.4.drv` → `cowsay-3.8.4`."""
    base = path.rsplit("/", 1)[-1]
    if len(base) > 33 and base[32] == "-":
        base = base[33:]
    return base.removesuffix(".drv")


def duration_key(name: str) -> str:
    """The durations history key: the name without a trailing version."""
    return _VERSION.sub("", name) or name


def host_of(machine: str, local: str) -> str:
    """The builder host of a build activity's `machine` field (`ssh-ng://user@host:22`,
    or empty for this machine)."""
    if not machine:
        return local
    m = re.match(r"^(?:[\w+-]+://)?(?:[^@/]*@)?(\[[^\]]+\]|[^/:?@]+)", machine)
    return m.group(1).strip("[]") if m else machine


def _bytes(amount: str | None, unit: str | None) -> int | None:
    if not amount or not unit:
        return None
    return int(float(amount) * _UNITS.get(unit, 1))


def human_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if x < 1024 or unit == "GiB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} GiB"


# --- the durations history ---------------------------------------------------------------


class Durations:
    """Seconds earlier builds of a name took (`duration_key`), in one JSON file; None as
    the path means an in-memory history (tests)."""

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.path = str(path) if path else None
        self.data: dict[str, float] = {}
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self.data = {
                        str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))
                    }
            except (OSError, ValueError):
                pass

    def get(self, name: str) -> float | None:
        return self.data.get(duration_key(name))

    def note(self, name: str, seconds: float) -> None:
        """Remember one build; smoothed against the old value so one odd run does not
        throw the next ETA off."""
        k = duration_key(name)
        old = self.data.get(k)
        self.data[k] = round(seconds if old is None else 0.7 * seconds + 0.3 * old, 1)

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            items = sorted(self.data.items())[-DURATIONS_KEEP:]
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(dict(items), fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError:
            pass  # best effort, like every nixin cache


def default_durations() -> Durations:
    from ..cache import default_root

    return Durations(default_root() / "apply" / "durations.json")


# --- the tracker ---------------------------------------------------------------------------


class Tracker:
    """Feed it nix's stderr lines; read `snapshot()` from any thread."""

    def __init__(
        self,
        *,
        durations: Durations | None = None,
        clock: Callable[[], float] = time.monotonic,
        local_host: str | None = None,
    ) -> None:
        self.clock = clock
        self.t0 = clock()
        self.end: float | None = None
        self.state = "running"  # running | done | failed | cancelled
        self.local = local_host or _hostname()
        self.durations = durations if durations is not None else Durations(None)
        self.drvs: dict[str, dict[str, Any]] = {}  # drv path → row
        self.order: list[str] = []
        self.acts: dict[Any, str] = {}  # build activity id → drv path
        self.builds_act: Any = None
        self.progress: list[int] | None = (
            None  # the builds activity's [done, expected, running, failed]
        )
        self.collect: str | None = None  # "build" | "fetch" while a path list streams in
        self.newest: str | None = None
        self.last_failed: str | None = None
        self.tails: dict[str, list[str]] = {}
        self.copies: set[Any] = set()  # open copy-path activities
        self.transfers: dict[Any, int] = {}  # open file transfers → bytes done
        self.fetch = {
            "expected": 0,
            "done": 0,
            "running": 0,
            "bytes_done": 0,
            "bytes_expected": None,
        }
        self.errors = 0
        self.events = 0
        self.lock = threading.Lock()

    # --- events

    def feed_line(self, line: str) -> str | None:
        """One stderr line. Returns the text to show a person (with its newline), or None
        for an event that has no readable form (a build's log line, progress)."""
        text = line.rstrip("\n")
        if not text.startswith("@nix "):
            return line if line.endswith("\n") else line + "\n"
        try:
            ev = json.loads(text[5:])
        except ValueError:
            return text + "\n"
        if not isinstance(ev, dict):
            return None
        with self.lock:
            self.events += 1
            readable = self._feed(ev)
        return readable

    def _feed(self, ev: dict[str, Any]) -> str | None:
        action = ev.get("action")
        if action == "msg":
            level = ev.get("level", 3)
            msg = str(ev.get("msg", ""))
            self._msg(level, msg)
            return msg + "\n" if level <= 3 else None
        self.collect = None  # any activity ends a "will be built/fetched" list
        if action == "start":
            return self._start(ev)
        if action == "stop":
            self._stop(ev)
        elif action == "result":
            self._result(ev)
        return None

    def _row(self, path: str) -> dict[str, Any]:
        r = self.drvs.get(path)
        if r is None:
            r = {
                "drv": path,
                "name": drv_name(path),
                "status": "queued",
                "host": None,
                "machine": "",
                "started": None,
                "ended": None,
                "phase": None,
                "last_log_line": None,
            }
            self.drvs[path] = r
            self.order.append(path)
        return r

    def _msg(self, level: int, msg: str) -> None:
        text = clean(msg)
        first = text.split("\n", 1)[0]
        if _WILL_BUILD.match(first):
            self.collect = "build"
            for p in DRV.findall(text):
                self._row(p)
            return
        m = _WILL_FETCH.match(first)
        if m:
            self.collect = "fetch"
            self.fetch["expected"] += int(m.group(1) or 1)
            size = _bytes(m.group(2), m.group(3))
            if size is not None:
                self.fetch["bytes_expected"] = (self.fetch["bytes_expected"] or 0) + size
            return
        if self.collect and text.startswith("  /nix/store/"):
            if self.collect == "build":
                for p in DRV.findall(text):
                    self._row(p)
            return
        self.collect = None
        if level == 0:
            self._error(text, first)

    def _error(self, text: str, first: str) -> None:
        self.errors += 1
        paths = [p for p in DRV.findall(first) if p in self.drvs]
        if not paths:
            return
        path = paths[0]
        r = self.drvs[path]
        m = re.search(r"Reason: (.*)", text)
        reason = m.group(1).strip() if m else ""
        if re.search(r"dependenc(y|ies)( of derivation .*)? failed", first + " " + reason):
            if r["status"] in ("queued", "skipped"):
                r["status"] = "skipped"
                r["last_log_line"] = reason or "a dependency failed"
            return
        r["status"] = "failed"
        r["ended"] = r["ended"] or self.clock()
        logl = [ln.strip()[2:] for ln in text.splitlines() if ln.strip().startswith("> ")]
        r["last_log_line"] = (logl[-1] if logl else r["last_log_line"] or reason or first)[:200]
        if logl and not self.tails.get(path):
            self.tails[path] = logl[-LOG_TAIL:]
        self.last_failed = path

    def _start(self, ev: dict[str, Any]) -> str | None:
        t = ev.get("type")
        fields = ev.get("fields") or []
        if t == ACT_BUILD and fields:
            path, machine = str(fields[0]), str(fields[1]) if len(fields) > 1 else ""
            r = self._row(path)
            self.acts[ev.get("id")] = path
            if r["status"] == "building" and r["machine"] and not machine:
                return None  # the remote daemon's own build activity, nested in ours
            r.update(
                status="building",
                host=host_of(machine, self.local),
                machine=machine,
                started=self.clock(),
                ended=None,
            )
            self.newest = path
            self.tails[path] = []
        elif t == ACT_BUILDS:
            self.builds_act = ev.get("id")
        elif t == ACT_COPY_PATH:
            self.copies.add(ev.get("id"))
            self.fetch["running"] = len(self.copies)
        elif t == ACT_TRANSFER and ev.get("parent") in self.copies:
            self.transfers[ev.get("id")] = 0  # a NAR, not a .narinfo lookup
        text = ev.get("text")
        if text and ev.get("level", 3) <= 3 and t in (ACT_BUILD, ACT_COPY_PATH):
            return str(text) + "\n"
        return None

    def _stop(self, ev: dict[str, Any]) -> None:
        ident = ev.get("id")
        path = self.acts.pop(ident, None)
        if path is not None:
            if path in self.acts.values():
                return  # an outer or inner build activity of the same derivation is open
            r = self.drvs[path]
            if r["status"] == "building":
                r["status"] = "done"
                r["ended"] = self.clock()
                if r["started"] is not None:
                    self.durations.note(r["name"], r["ended"] - r["started"])
            return
        if ident in self.copies:
            self.copies.discard(ident)
            self.fetch["running"] = len(self.copies)
            self.fetch["done"] += 1
        elif ident in self.transfers:
            self.fetch["bytes_done"] += self.transfers.pop(ident)

    def _result(self, ev: dict[str, Any]) -> None:
        t, fields, ident = ev.get("type"), ev.get("fields") or [], ev.get("id")
        if t == RES_PROGRESS and ident == self.builds_act and len(fields) >= 4:
            self.progress = [int(x) for x in fields[:4]]
            return
        if t == RES_PROGRESS and ident in self.transfers and fields:
            self.transfers[ident] = int(fields[0])
            return
        path = self.acts.get(ident)
        if not path:
            return
        r = self.drvs[path]
        if t == RES_LOG and fields:
            line = clean(str(fields[0]))
            if line.strip():
                r["last_log_line"] = line.strip()[:200]
                tail = self.tails.setdefault(path, [])
                tail.append(line[:240])
                del tail[:-LOG_TAIL]
        elif t == RES_PHASE and fields:
            r["phase"] = str(fields[0])[:40]

    # --- the end

    def finish(self, state: str) -> None:
        """`done`, `failed` or `cancelled`: closes the open builds and saves the durations."""
        with self.lock:
            if self.state == "running":
                self.state = state
                self.end = self.clock()
            for r in self.drvs.values():
                if r["status"] == "building":
                    r["status"] = "failed" if state == "failed" else "queued"
                    r["ended"] = self.clock() if state == "failed" else None
            self.durations.save()

    # --- reading

    def elapsed(self) -> float:
        return (self.end if self.end is not None else self.clock()) - self.t0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self._snapshot()

    def _snapshot(self) -> dict[str, Any]:
        now = self.clock()
        counts = {"queued": 0, "building": 0, "done": 0, "failed": 0, "skipped": 0}
        rows: list[dict[str, Any]] = []
        remaining, known, running = 0.0, 0, 0
        for p in self.order:
            r = self.drvs[p]
            counts[r["status"]] += 1
            expected = self.durations.get(r["name"])
            row = {
                "name": r["name"],
                "drv": r["drv"],
                "status": r["status"],
                "host": r["host"],
                "seconds": None,
                "phase": r["phase"],
                "last_log_line": r["last_log_line"],
                "expected_s": round(expected) if expected is not None else None,
            }
            if r["started"] is not None:
                end = r["ended"] if r["ended"] is not None else now
                row["seconds"] = round(end - r["started"], 1)
            if r["status"] in ("queued", "building"):
                if r["status"] == "building":
                    running += 1
                if expected is not None:
                    known += 1
                    left = (
                        expected - (row["seconds"] or 0) if r["status"] == "building" else expected
                    )
                    remaining += max(left, 5.0)
            rows.append(row)
        open_n = counts["queued"] + counts["building"]
        eta = None
        if open_n and known:
            remaining += (open_n - known) * (remaining / known)
            eta = round(remaining / max(running, 1))
        focus = self.newest
        building = [p for p in self.order if self.drvs[p]["status"] == "building"]
        if building and focus not in building:
            focus = max(building, key=lambda p: self.drvs[p]["started"] or 0)
        if not building and self.last_failed:
            focus = self.last_failed
        focus_doc = None
        if focus:
            r = self.drvs[focus]
            focus_doc = {
                "name": r["name"],
                "status": r["status"],
                "host": r["host"],
                "phase": r["phase"],
                "lines": list(self.tails.get(focus, [])),
            }
        fetch = dict(self.fetch)
        fetch["bytes_done"] += sum(self.transfers.values())
        return {
            "state": self.state,
            "elapsed_s": round(self.elapsed(), 1),
            "local_host": self.local,
            "total": len(rows),
            "counts": counts,
            "progress": list(self.progress) if self.progress else None,
            "drvs": rows,
            "focus": focus_doc,
            "downloads": fetch,
            "eta_s": eta,
            "eta_known": [known, open_n] if eta is not None else None,
            "errors": self.errors,
        }

    def summary(self) -> dict[str, Any]:
        """The `build` object of a plan/apply document: what the build amounted to."""
        snap = self.snapshot()
        failed = [r["name"] for r in snap["drvs"] if r["status"] == "failed"]
        return {
            "state": snap["state"],
            "cancelled": snap["state"] == "cancelled",
            "duration_s": snap["elapsed_s"],
            "total": snap["total"],
            "counts": snap["counts"],
            "failed": failed,
            "downloads": {
                "done": snap["downloads"]["done"],
                "bytes_done": snap["downloads"]["bytes_done"],
            },
        }


def _hostname() -> str:
    import socket

    return socket.gethostname()


# --- the status line -----------------------------------------------------------------------


def status_line(snap: dict[str, Any], width: int = 100) -> str:
    """`[7/39 built, 2 building, 12/40 fetched 35.1 MiB] system-path: configurePhase · 1m12s
    (eta 3m)`; what a terminal shows in place and a UI shows as its head line."""
    c, d = snap["counts"], snap["downloads"]
    parts = []
    if snap["total"]:
        parts.append(f"{c['done']}/{snap['total']} built")
        if c["building"]:
            parts.append(f"{c['building']} building")
        if c["failed"]:
            parts.append(f"{c['failed']} failed")
    if d["expected"] or d["done"] or d["running"]:
        text = f"{d['done']}/{d['expected']} fetched" if d["expected"] else f"{d['done']} fetched"
        if d["bytes_done"]:
            text += f" {human_bytes(d['bytes_done'])}"
        parts.append(text)
    head = f"[{', '.join(parts)}]" if parts else "[waiting for nix]"
    tail = ""
    f = snap.get("focus")
    if f and f["status"] == "building":
        tail = f" {f['name']}"
        if f.get("phase"):
            tail += f": {f['phase']}"
        if f.get("host") and f["host"] != _hostname():
            tail += f" on {f['host']}"
    line = f"{head}{tail} · {human_time(snap['elapsed_s'])}"
    if snap.get("eta_s") is not None:
        line += f" (eta {human_time(snap['eta_s'])})"
    return line if len(line) <= width else line[: width - 1] + "…"


class Reporter:
    """nix's readable output plus the live status line, on stderr (human mode).

    `tty=True`: the status line is redrawn in place (`\\r`, clear to the end of the line)
    under the readable lines; otherwise a status line is printed when the counts change,
    at most every `plain_every` seconds. `sys.stderr` is looked up at each write: the TUI
    and the GUI swap it for their log while a run streams."""

    def __init__(
        self,
        tracker: Tracker,
        *,
        tty: bool,
        stream: TextIO | None = None,
        plain_every: float = 10.0,
        tick: float = 1.0,
    ) -> None:
        self.tracker = tracker
        self.tty = tty
        self._stream = stream
        self.plain_every = plain_every
        self._tick = tick
        self._lock = threading.Lock()
        self._shown = ""  # the status line on the terminal now
        self._last_plain = ("", 0.0)
        self._last_status = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def out(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def start(self) -> None:
        if self._tick > 0:
            self._thread = threading.Thread(target=self._ticker, name="nixin-progress", daemon=True)
            self._thread.start()

    def _ticker(self) -> None:
        while not self._stop.wait(self._tick):
            self.refresh()

    def line(self, text: str | None) -> None:
        """A readable line from the tracker (None: nothing to print; the status may move)."""
        with self._lock:
            if text:
                out = self.out
                if self._shown:
                    out.write("\r\x1b[K")
                out.write(text)
                self._shown = ""
                out.flush()
            elif time.monotonic() - self._last_status < 0.25:
                return  # a build's log lines come fast; the ticker catches up anyway
            self._status(self.tracker.snapshot())

    def refresh(self) -> None:
        with self._lock:
            self._status(self.tracker.snapshot())

    def _status(self, snap: dict[str, Any], final: bool = False) -> None:
        self._last_status = time.monotonic()
        if not (snap["total"] or snap["downloads"]["expected"] or snap["downloads"]["done"]):
            return
        if self.tracker.collect and not final:
            return  # the "will be built/fetched" lists are still streaming in
        text = status_line(snap, self._width())
        out = self.out
        if self.tty:
            if text != self._shown:
                out.write(f"\r\x1b[K{text}")
                out.flush()
                self._shown = text
            return
        key = text.split(" · ", 1)[0]  # the counts and the current build, not the clock
        last, at = self._last_plain
        now = time.monotonic()
        if key != last and (final or now - at >= self.plain_every):
            out.write(text + "\n")
            out.flush()
            self._last_plain = (key, now)

    def close(self) -> None:
        """Stop the ticker; clear the terminal's status line, or print the final one."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            if self.tty:
                if self._shown:
                    self.out.write("\r\x1b[K")
                    self.out.flush()
                    self._shown = ""
            else:
                self._status(self.tracker.snapshot(), final=True)

    def _width(self) -> int:
        try:
            return max(40, os.get_terminal_size(self.out.fileno()).columns - 1)
        except (OSError, ValueError, AttributeError):
            return 100
