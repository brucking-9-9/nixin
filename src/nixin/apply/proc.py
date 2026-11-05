"""Running nix, git, sudo and the switch helper: the one seam the apply tests replace.

`run(argv, ...)` returns a `Proc` and never raises for a non-zero exit (callers decide
what a failure means). `stream` tees the child's output to our stderr while it runs
(human mode: nix's build progress, nixos-rebuild's activation log), so stdout stays the
verb's own. Under --json nothing is streamed: the contract keeps stderr empty.
stdin is closed: nothing nixin starts may stop to ask. The one exception is `tty=True`,
apply on another host: `ssh -t` keeps the terminal, so the remote sudo can ask for its
password there (remote.py refuses that run under --json and without a terminal).

Stopping and shielding. `Cancel` is the stop request of one plan/apply run: the build's
nix process registers with it, `request()` kills that process group, and the UIs read
`step`, `stoppable` and `snapshot()` from it while the run goes (backend.run puts the
token in `ns.cancel`; apply's verbs make one when none came). A child started with
`cancel=` runs in its own process group (the terminal's Ctrl-C no longer reaches it;
the token does). Inside `shielded()` (apply's root steps) children also get their own
group, so Ctrl-C cannot interrupt a pull or a switch half-way; `interrupts(token)`
turns SIGINT on the main thread into `token.request()`. Neither applies to `tty=True`:
a prompt on the terminal must stay in the foreground group.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..configrepo import NixinError

KILL_GRACE = 5.0  # seconds between SIGTERM and SIGKILL of a cancelled process group


@dataclass
class Proc:
    argv: list[str]
    rc: int
    out: str
    err: str
    missing: bool = False  # the executable was not on PATH (rc is 127 then)

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def tail(self, n: int = 20) -> str:
        """The last n non-empty lines of stderr, then stdout (nixos-rebuild prints the
        new system's path on stdout, after its log on stderr)."""
        text = self.err + ("\n" + self.out if self.out else "")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[-n:])


Runner = Callable[..., Proc]
Sink = Callable[[str], "str | None"]  # a stderr line in, the text to keep (None: drop it)


class Cancel:
    """The stop request of one plan/apply run, and what a UI reads while it runs.

    `request()` from any thread (a Stop button, the SIGINT handler): kills the process
    groups registered with the token and returns True; False when the run is inside a
    step that must finish (`shield`: a commit, the pull, the switch), with `refused`
    saying so. `check()` in the run itself raises `cancelled` once a request is in.
    `step` is what the run does now (apply sets it: prepare, commit, eval, build, pull,
    switch, …) and `snapshot` the build tracker's document while a build runs."""

    def __init__(self, *, watched: bool = False) -> None:
        self.watched = watched  # a UI shows the tracker: no status line on stderr
        self.step = ""
        self.shield = ""  # the step that must finish, while one runs
        self.refused = ""  # why the last request() was refused
        self.snapshot: Callable[[], dict[str, Any]] | None = None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def stoppable(self) -> bool:
        return not self.shield and not self.requested

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a request comes (or the timeout passes): for fakes and tests."""
        return self._event.wait(timeout)

    def request(self) -> bool:
        with self._lock:
            if self.shield:
                self.refused = f"{self.shield} is running and cannot be stopped; waiting for it"
                return False
            self.refused = ""
            self._event.set()
            procs = list(self._procs)
        for p in procs:
            kill_group(p)
        return True

    def check(self) -> None:
        if self.requested:
            raise NixinError("cancelled", "stopped on request")

    @contextlib.contextmanager
    def shielded(self, step: str) -> Iterator[None]:
        """A step that must run to its end: requests are refused meanwhile; a request
        already in is honoured before the step starts."""
        with self._lock:
            if self._event.is_set():
                raise NixinError("cancelled", f"stopped on request before {step}")
            outer = self.shield
            self.shield = step
            self.step = step
        try:
            with shielded():
                yield
        finally:
            self.shield = outer

    def attach(self, p: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(p)
            late = self._event.is_set()
        if late:  # requested between the decision to start and the start
            kill_group(p)

    def detach(self, p: subprocess.Popen) -> None:
        with self._lock:
            if p in self._procs:
                self._procs.remove(p)


def kill_group(p: subprocess.Popen, grace: float = KILL_GRACE) -> None:
    """SIGTERM the child's process group (nix stops its builds on it); SIGKILL after
    `grace` seconds if it is still there. Returns at once."""
    if p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        p.terminate()

    def reap() -> None:
        try:
            p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(p.pid, signal.SIGKILL)

    threading.Thread(target=reap, name="nixin-kill", daemon=True).start()


_local = threading.local()


@contextlib.contextmanager
def shielded() -> Iterator[None]:
    """While active on this thread, children get their own process group (the terminal's
    Ctrl-C does not reach them), except `tty=True` runs."""
    before = getattr(_local, "shield", 0)
    _local.shield = before + 1
    try:
        yield
    finally:
        _local.shield = before


def _own_group(tty: bool, cancel: Cancel | None) -> bool:
    return not tty and (cancel is not None or getattr(_local, "shield", 0) > 0)


@contextlib.contextmanager
def interrupts(token: Cancel, verb: str = "nixin") -> Iterator[None]:
    """On the main thread: Ctrl-C (SIGINT) becomes `token.request()` instead of a
    KeyboardInterrupt, with one line on stderr saying what happened; restored after.
    Elsewhere (the UIs' worker threads) a no-op."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    told = {"n": 0}

    def handler(signum, frame) -> None:
        told["n"] += 1
        if token.request():
            msg = f"{verb}: stopping (Ctrl-C): waiting for nix to clean up…"
        elif told["n"] <= 1:
            msg = f"{verb}: {token.refused}"
        else:
            return
        with contextlib.suppress(OSError, ValueError):
            sys.stderr.write(f"\n{msg}\n")
            sys.stderr.flush()

    try:
        old = signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread of the main interpreter after all
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


def run_process(
    argv: list[str],
    *,
    stream: bool | str = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    tty: bool = False,
    on_stderr: Sink | None = None,
    cancel: Cancel | None = None,
) -> Proc:
    """Run argv (argv[0] looked up on PATH). `stream`: False, "stderr" (tee the child's
    stderr), or "all" (stdout merged into stderr and teed: the child's stdout is a log).
    `tty`: stdin stays ours (the terminal), output as with "all". `on_stderr`: every
    stderr line goes through it as it arrives (a parser); what it returns is kept as
    `err` and, with `stream`, written to our stderr. `cancel`: the child runs in its own
    process group, registered with the token (see `Cancel`)."""
    exe = shutil.which(argv[0])
    if exe is None:
        return Proc(argv, 127, "", f"{argv[0]}: not on PATH", missing=True)
    full_env = None if env is None else {**os.environ, **env}
    if tty:
        return _run_tty([exe, *argv[1:]], argv, full_env)
    group: dict[str, Any] = {"process_group": 0} if _own_group(tty, cancel) else {}
    if not stream and on_stderr is None:
        p = subprocess.Popen(
            [exe, *argv[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=full_env,
            **group,
        )
        if cancel is not None:
            cancel.attach(p)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if group:
                kill_group(p)
            else:
                p.kill()
            p.communicate()
            return Proc(argv, 124, "", f"{argv[0]} did not finish in {timeout:.0f}s")
        finally:
            if cancel is not None:
                cancel.detach(p)
        return Proc(argv, p.returncode, out, err)
    merged = stream == "all"
    p = subprocess.Popen(
        [exe, *argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merged else subprocess.PIPE,
        text=True,
        bufsize=1,
        env=full_env,
        **group,
    )
    if cancel is not None:
        cancel.attach(p)
    teed: list[str] = []

    def pump(fh) -> None:
        for line in fh:
            if on_stderr is not None:
                kept = on_stderr(line)
                if kept is None:
                    continue
                line = kept
                if not stream:
                    teed.append(line)
                    continue
            teed.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()

    try:
        if merged:
            pump(p.stdout)
            p.wait()
            return Proc(argv, p.returncode, "", "".join(teed))
        t = threading.Thread(target=pump, args=(p.stderr,), daemon=True)
        t.start()
        out = p.stdout.read()
        p.wait()
        t.join()
        return Proc(argv, p.returncode, out, "".join(teed))
    finally:
        if cancel is not None:
            cancel.detach(p)


def _run_tty(cmd: list[str], argv: list[str], env: dict[str, str] | None) -> Proc:
    """stdin inherited; stdout and stderr merged, teed to our stderr in chunks as they
    arrive (a password prompt has no newline). ssh -t puts the terminal in raw mode and
    the remote pty sends CRLF, so the bytes go out as they came; the captured text has
    plain newlines."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    seen: list[str] = []
    fd = p.stdout.fileno()
    while chunk := os.read(fd, 4096):
        text = dec.decode(chunk)
        seen.append(text)
        sys.stderr.write(text)
        sys.stderr.flush()
    seen.append(dec.decode(b"", final=True))
    p.stdout.close()
    p.wait()
    return Proc(argv, p.returncode, "", "".join(seen).replace("\r\n", "\n"))


RUN: Runner = run_process  # tests swap this for a fake


def run(argv: list[str], **kw) -> Proc:
    return RUN(argv, **kw)


_BARE = re.compile(r"[A-Za-z0-9_@%+=:,./^-][A-Za-z0-9_@%+=:,./^#-]*")


def shown(argv: list[str]) -> str:
    """argv as a person would type it (a `#` inside a word, as in `/etc/nixos#host`, needs
    no quotes)."""
    return " ".join(a if _BARE.fullmatch(a) else shlex.quote(a) for a in argv)


def need(p: Proc, code: str, what: str) -> Proc:
    """p if it succeeded, else NixinError(code) with the command and its last lines."""
    if p.ok:
        return p
    detail = p.tail(12) or f"exit status {p.rc}"
    raise NixinError(code, f"{what} failed ({shown(p.argv)}):\n{detail}")
