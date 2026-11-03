"""Starting what `try` built: the terminal handoff (CLI), detached apps and terminals (UIs).

`handoff(argv)` replaces nixin with `nix run …` / `nix shell …` (exec): the program gets
the terminal, its exit status is nixin's. The front ends instead start a GUI app detached
(`detached`: its own session, as `setsid` does, output to a log file) and open CLI programs
in the person's terminal (`in_terminal`: `$TERMINAL -e …`, else the first known terminal on
PATH).

Every child gets `child_env()`: nixin's own wrapper prefixes PATH (its Python, nixfmt),
QT_PLUGIN_PATH and NIXPKGS_QT6_QML_IMPORT_PATH (its Qt) with store paths and sets
PYTHONNOUSERSITE. A Qt app built against another Qt would load nixin's plugins and abort,
so those prefixes are dropped (the session's own entries, profile paths, stay).

`EXEC` and `SPAWN` are the seams the tests replace.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..cache import default_root

STORE = "/nix/store/"
QT_PATHS = ("QT_PLUGIN_PATH", "NIXPKGS_QT6_QML_IMPORT_PATH", "QML2_IMPORT_PATH")
# known terminals: the words that go before the command (kitty and foot take it bare)
TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kitty", ()),
    ("foot", ()),
    ("alacritty", ("-e",)),
    ("wezterm", ("start", "--")),
    ("ghostty", ("-e",)),
    ("konsole", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("xfce4-terminal", ("-x",)),
    ("xterm", ("-e",)),
)
HOLD = (
    'status=$?; printf "\\n[%s exited with %d; Enter closes this window]" "$0" "$status"; read -r _'
)


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment without what nixin's wrapper added (see the module doc)."""
    e = dict(os.environ if env is None else env)
    for var in QT_PATHS:
        keep = [p for p in e.get(var, "").split(":") if p and not p.startswith(STORE)]
        if keep:
            e[var] = ":".join(keep)
        else:
            e.pop(var, None)
    path = e.get("PATH", "").split(":")
    while path and path[0].startswith(STORE):
        path.pop(0)
    e["PATH"] = ":".join(path)
    if e.get("PYTHONNOUSERSITE") == "true":
        del e["PYTHONNOUSERSITE"]
    return e


EXEC: Callable[..., Any] = os.execvpe


def resolve(exe: str) -> str:
    """exe looked up on nixin's own PATH (child_env's may have lost the directory)."""
    return shutil.which(exe) or exe


def handoff(argv: list[str]) -> None:
    """exec argv in place of nixin; returns only when EXEC is faked."""
    sys.stdout.flush()
    sys.stderr.flush()
    EXEC(resolve(argv[0]), argv, child_env())


def has_display(env: Mapping[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(e.get("WAYLAND_DISPLAY") or e.get("DISPLAY"))


def log_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "app"
    return default_root() / "try" / f"{safe}.log"


def _spawn(argv: list[str], *, log: Path | None) -> int:
    out: Any = subprocess.DEVNULL
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        out = log.open("wb")
    try:
        p = subprocess.Popen(
            [resolve(argv[0]), *argv[1:]],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # setsid(): no controlling terminal, outlives nixin
            close_fds=True,
            cwd=os.path.expanduser("~"),
            env=child_env(),
        )
    finally:
        if log is not None:
            out.close()
    return p.pid


SPAWN: Callable[..., int] = _spawn


def detached(argv: list[str], *, name: str) -> dict[str, Any]:
    """Start argv in its own session; its output goes to `<cache>/nixin/try/<name>.log`."""
    log = log_path(name)
    pid = SPAWN(argv, log=log)
    return {"pid": pid, "argv": argv, "log": str(log)}


def terminal(env: Mapping[str, str] | None = None) -> list[str] | None:
    """The words that run a command in the person's terminal: `$TERMINAL -e` (a known
    terminal's own form when $TERMINAL names one), else the first known terminal on PATH."""
    e = os.environ if env is None else env
    known = dict(TERMINALS)
    if e.get("TERMINAL"):
        words = shlex.split(e["TERMINAL"])
        if words:
            return [*words, *known.get(os.path.basename(words[0]), ("-e",))]
    for exe, flags in TERMINALS:
        if shutil.which(exe, path=e.get("PATH")):
            return [exe, *flags]
    return None


def in_terminal(
    command: str, *, hold: bool, name: str = "nixin try", env: Mapping[str, str] | None = None
) -> list[str]:
    """argv that opens the person's terminal running `command` (a shell line). `hold`: keep
    the window until Enter, so a program that exits at once (cowsay) can be read; `name`
    is what the closing line calls it."""
    term = terminal(env)
    if term is None:
        raise LookupError("no terminal found: set $TERMINAL")
    if not hold:
        return [*term, "sh", "-c", command]
    return [*term, "sh", "-c", f"{command}\n{HOLD}", name]
