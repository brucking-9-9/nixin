"""Running nix, git, sudo and the switch helper: the one seam the apply tests replace.

`run(argv, ...)` returns a `Proc` and never raises for a non-zero exit (callers decide
what a failure means). `stream` tees the child's output to our stderr while it runs
(human mode: nix's build progress, nixos-rebuild's activation log), so stdout stays the
verb's own. Under --json nothing is streamed: the contract keeps stderr empty.
stdin is always closed: nothing nixin starts may stop to ask.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..configrepo import NixinError


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
        """The last n non-empty lines of stderr (stdout when stderr is empty)."""
        lines = [ln for ln in (self.err or self.out).splitlines() if ln.strip()]
        return "\n".join(lines[-n:])


Runner = Callable[..., Proc]


def run_process(
    argv: list[str],
    *,
    stream: bool | str = False,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> Proc:
    """Run argv (argv[0] looked up on PATH). `stream`: False, "stderr" (tee the child's
    stderr), or "all" (stdout merged into stderr and teed: the child's stdout is a log)."""
    exe = shutil.which(argv[0])
    if exe is None:
        return Proc(argv, 127, "", f"{argv[0]}: not on PATH", missing=True)
    full_env = None if env is None else {**os.environ, **env}
    if not stream:
        try:
            p = subprocess.run(
                [exe, *argv[1:]],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=full_env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Proc(argv, 124, "", f"{argv[0]} did not finish in {timeout:.0f}s")
        return Proc(argv, p.returncode, p.stdout, p.stderr)
    merged = stream == "all"
    p = subprocess.Popen(
        [exe, *argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merged else subprocess.PIPE,
        text=True,
        bufsize=1,
        env=full_env,
    )
    teed: list[str] = []

    def pump(fh) -> None:
        for line in fh:
            teed.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()

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
