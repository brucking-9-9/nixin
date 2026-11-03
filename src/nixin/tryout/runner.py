"""Running nix for `try`: the one seam its tests replace (`runner.RUN`).

Three ways to run a command. `capture`: output kept, nothing shown (--json, the probe).
`tee`: stderr teed line by line to `sys.stderr` while it runs (human mode when stderr is
not a terminal; the TUI and GUI, which route `sys.stderr` into their log view). `inherit`:
the child writes to our stderr itself (human mode on a terminal, so nix draws its own
progress bar); its stderr is not kept then. stdin is always closed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

from ..apply import proc
from ..configrepo import NixinError, trim_nix_error

Proc = proc.Proc


def run_process(argv: list[str], *, stream: str = "capture", timeout: float | None = None) -> Proc:
    if stream != "inherit":
        return proc.run_process(
            argv, stream="stderr" if stream == "tee" else False, timeout=timeout
        )
    exe = shutil.which(argv[0])
    if exe is None:
        return Proc(argv, 127, "", f"{argv[0]}: not on PATH", missing=True)
    p = subprocess.run(
        [exe, *argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    return Proc(argv, p.returncode, p.stdout, "")


RUN: Callable[..., Proc] = run_process  # tests swap this for a fake


def run(argv: list[str], **kw) -> Proc:
    return RUN(argv, **kw)


def nix(args: list[str], *, code: str) -> str:
    """`nix ARGS` captured; stdout, or NixinError(code) with nix's trimmed message."""
    p = run(["nix", *args])
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    if not p.ok:
        msg = trim_nix_error(p.err) or f"nix exited with status {p.rc}"
        raise NixinError(code, msg, stderr=p.err)
    return p.out


def stderr_is_terminal() -> bool:
    """Our stderr is the process's own and a terminal (not a UI's capture, not a pipe)."""
    return sys.stderr is sys.__stderr__ and bool(sys.stderr) and sys.stderr.isatty()
