"""The privileged steps and who runs them.

apply has exactly two root steps, each a plain command:

    pull    git -C <authoritative> pull --ff-only <workspace> <branch>
    switch  nixos-rebuild switch|boot --flake <authoritative>#<host>

rollback has two more (and the same pull):

    profile   nix-env --profile /nix/var/nix/profiles/system --switch-generation <N>
    activate  <generation N's store path>/bin/switch-to-configuration switch

gc has three:

    delete      nix-env --profile /nix/var/nix/profiles/system --delete-generations <N>...
    bootloader  /nix/var/nix/profiles/system/bin/switch-to-configuration boot
    collect     nix-collect-garbage

A `Backend` turns a step into what nixin runs and into what a person would type.
`Sudo` (the default) prefixes `sudo -n`, which never prompts. When that is refused and a
person is at a terminal (human mode, stdin and stderr are ttys, NIXIN_SUDO_PROMPT is not
0), `Sudo` runs `sudo -v` once on that terminal, exactly what the needs-sudo hint tells
people to type, and repeats the step with the fresh ticket. Without a terminal or under
--json (the GUI and the TUI run the verbs in-process that way), it runs `sudo -A -v`
when SUDO_ASKPASS names an executable helper: the prompt is graphical and stdio stays
clean. Otherwise, or when the password is wrong, apply answers `needs-sudo`.
`NixosSwitch` is the desktop helper on 19BK (`nixos-switch`, a transient user unit that
authenticates with Touch ID / an askpass and runs `nixos-rebuild switch --flake
/etc/nixos`). It returns immediately and leaves its result in
`$XDG_RUNTIME_DIR/nixos-switch/{status,log,auth}`, so the backend waits for `status`.
It only covers the switch step, only `switch` (not `boot`), only for this machine and
only for /etc/nixos (the helper hard-codes the flake and takes the host from the
hostname). Everything else stays with sudo.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..configrepo import DEFAULT_CONFIG
from . import proc

HELPER = "nixos-switch"
HELPER_WAIT = 4 * 3600.0  # the helper waits for a person at the desktop, then builds


@dataclass
class Step:
    name: str  # "pull" | "switch" | "profile" | "activate" | "delete" | "bootloader" | "collect"
    argv: list[str]  # the command root runs, without any sudo
    mode: str = ""  # switch: "switch" | "boot"


def pull_step(authoritative: Path, workspace: Path, branch: str) -> Step:
    return Step(
        "pull", ["git", "-C", str(authoritative), "pull", "--ff-only", str(workspace), branch]
    )


def switch_step(authoritative: Path, host: str, mode: str = "switch") -> Step:
    return Step("switch", ["nixos-rebuild", mode, "--flake", f"{authoritative}#{host}"], mode=mode)


def profile_step(profile: str, generation: int) -> Step:
    return Step(
        "profile", ["nix-env", "--profile", profile, "--switch-generation", str(generation)]
    )


def activate_step(out: str) -> Step:
    return Step("activate", [f"{out}/bin/switch-to-configuration", "switch"], mode="switch")


def delete_step(profile: str, generations: list[int]) -> Step:
    return Step(
        "delete",
        ["nix-env", "--profile", profile, "--delete-generations", *map(str, generations)],
    )


def bootloader_step(profile: str) -> Step:
    """The boot default's own switch-to-configuration, reached through the profile link:
    it rewrites the boot entries for the generations left and keeps the default."""
    return Step("bootloader", [f"{profile}/bin/switch-to-configuration", "boot"], mode="boot")


def collect_step() -> Step:
    return Step("collect", ["nix-collect-garbage"])


class Backend:
    name = "?"

    def blocked(self) -> str | None:
        """Why run() must not start now (checked right before it), else None."""
        return None

    def probe(self) -> str | None:
        """For --dry-run: None if run() would get root without a prompt, else why not."""
        return self.blocked()

    def argv(self, step: Step) -> list[str]:
        raise NotImplementedError

    def display(self, step: Step) -> str:
        """The command a person runs by hand to do this step."""
        raise NotImplementedError

    def run(self, step: Step, *, stream: bool) -> proc.Proc:
        raise NotImplementedError


def prompting_allowed() -> bool:
    """NIXIN_SUDO_PROMPT=0 keeps the old never-prompt behaviour everywhere."""
    return os.environ.get("NIXIN_SUDO_PROMPT", "1") != "0"


def interactive() -> bool:
    """A person at a terminal who can answer sudo's prompt there: stdin and stderr are ttys."""
    if not prompting_allowed():
        return False
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def askpass() -> str | None:
    """A graphical askpass helper sudo can use without a terminal (`sudo -A`): the path in
    SUDO_ASKPASS when it is executable. That is how the GUI and the TUI, which run the
    verbs in-process under the --json contract, still get a Touch ID / password prompt."""
    if not prompting_allowed():
        return None
    helper = os.environ.get("SUDO_ASKPASS", "")
    return helper if helper and os.access(helper, os.X_OK) else None


class Sudo(Backend):
    name = "sudo"

    def probe(self) -> str | None:
        p = proc.run(["sudo", "-n", "true"])
        if p.ok:
            return None
        if p.missing:
            return "sudo is not on PATH"
        why = p.tail(1) or "sudo -n true failed (no cached ticket, no NOPASSWD rule)"
        if interactive():
            return f"{why}; nixin will ask for the password on this terminal (sudo -v)"
        if askpass():
            return f"{why}; nixin will ask through the askpass helper (sudo -A -v)"
        return why

    def argv(self, step: Step) -> list[str]:
        return ["sudo", "-n", *step.argv]

    def display(self, step: Step) -> str:
        return proc.shown(["sudo", *step.argv])

    def ask(self, step: Step, *, stream: bool) -> bool:
        """Get a sudo ticket for the `-n` steps. On a terminal in human mode: `sudo -v`
        there. Otherwise, with an askpass helper (SUDO_ASKPASS): `sudo -A -v`, which
        prompts graphically and leaves stdout/stderr alone (the --json contract holds,
        so the GUI and the TUI get the prompt too). True = granted."""
        if stream and interactive():
            sys.stderr.write(f"nixin: root is needed for `{proc.shown(step.argv)}`\n")
            sys.stderr.flush()
            return proc.run(["sudo", "-v"], tty=True).ok
        if askpass():
            return proc.run(["sudo", "-A", "-v"]).ok
        return False

    def run(self, step: Step, *, stream: bool) -> proc.Proc:
        p = proc.run(self.argv(step), stream="all" if stream else False)
        if needs_password(p) and self.ask(step, stream=stream):
            p = proc.run(self.argv(step), stream="all" if stream else False)
        return p


def needs_password(p: proc.Proc) -> bool:
    """sudo -n refused (the ticket expired between steps, or never existed)."""
    text = p.err.lower()
    return p.rc == 1 and ("password is required" in text or "a terminal is required" in text)


@dataclass
class NixosSwitch(Backend):
    name = HELPER
    exe: str = HELPER
    state_dir: Path | None = None  # default $XDG_RUNTIME_DIR/nixos-switch
    wait: float = HELPER_WAIT
    poll: float = 2.0
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    @property
    def dir(self) -> Path:
        if self.state_dir is not None:
            return self.state_dir
        run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return Path(run) / HELPER

    def blocked(self) -> str | None:
        busy = proc.run(["systemctl", "--user", "is-active", "-q", HELPER])
        if busy.ok:
            return f"a {HELPER} run is already in progress (systemctl --user status {HELPER})"
        return None

    def argv(self, step: Step) -> list[str]:
        return [self.exe]

    def display(self, step: Step) -> str:
        return self.exe

    def run(self, step: Step, *, stream: bool) -> proc.Proc:
        status = self.dir / "status"
        started = proc.run(self.argv(step))
        if not started.ok:
            return started
        deadline = self.clock() + self.wait
        while not _nonempty(status):
            if self.clock() > deadline:
                return proc.Proc(
                    self.argv(step), 124, "", f"{HELPER}: no result after {self.wait:.0f}s"
                )
            self.sleep(self.poll)
        rc_text = status.read_text().strip()
        rc = int(rc_text) if rc_text.lstrip("-").isdigit() else 1
        log = _read(self.dir / "log")
        auth = _read(self.dir / "auth")
        if stream and log:
            sys.stderr.write(log)
        err = log if rc == 0 or log.strip() else auth
        return proc.Proc(self.argv(step), rc, "", err)


def _nonempty(p: Path) -> bool:
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def helper_installed() -> bool:
    return shutil.which(HELPER) is not None


def helper_usable(step: Step, authoritative: Path, host_is_local: bool) -> bool:
    return (
        step.name == "switch"
        and step.mode == "switch"
        and host_is_local
        and str(authoritative) == DEFAULT_CONFIG
        and helper_installed()
    )


def backends(steps: list[Step], authoritative: Path, host_is_local: bool) -> dict[str, Backend]:
    """Which backend runs each step: the helper for a local switch from /etc/nixos when it
    is installed, sudo -n for everything else."""
    sudo = Sudo()
    out: dict[str, Backend] = {}
    for s in steps:
        out[s.name] = NixosSwitch() if helper_usable(s, authoritative, host_is_local) else sudo
    return out
