"""Building on a remote nix builder: `plan --remote [--all|--split]`, `apply --remote …`.

nix only honours `--builders` for a trusted user (the daemon's `trusted-users`), so a
run that asks for a remote first checks `nix store info --json` (`builders-untrusted`).
The builders spec itself comes from a command, run only when a build is really needed
(a cached plan contacts nothing), with the mode passed through as its argument:

    $NIXIN_BUILDERS_CMD, else `builders_cmd` in installer/state.json, else mbp13-builders
    mode big:    <cmd>            only big-parallel derivations (kernels) go there
    mode all:    <cmd> --all      plus `--max-jobs 0`: everything not substituted builds there
    mode split:  <cmd> --split    any derivation may go there; this machine builds too

The command prints the spec on stdout and exits 0; any failure (the builder asleep, the
command missing) means a plain local build with a warning, as the user's `rbuild` does.
`--builders SPEC` passes a literal spec instead (no command). `--builders-use-substitutes`
is always on. The remote's `name` (the command's basename without `-builders`, or the
spec's hosts) labels the UIs' selector and the tracker's header.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path
from typing import Any

from ..configrepo import NixinError
from . import proc
from .progress import host_of

MODES = ("big", "all", "split")
LOCAL = "local"
DEFAULT_CMD = "mbp13-builders"
ENV_CMD = "NIXIN_BUILDERS_CMD"
STATE_KEY = "builders_cmd"
CMD_TIMEOUT = 30.0  # the default command answers in 4 s at most (its tcp probe)
MODE_LABEL = {"big": "big jobs", "all": "everything", "split": "split"}

UNTRUSTED = (
    "{user} is not a nix trusted user, so the daemon would ignore --builders. Add it to "
    "nix.settings.trusted-users (then nixos-rebuild switch), or build without --remote"
)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--remote",
        action="store_true",
        help=f"build with the remote builder ${ENV_CMD} (default: {DEFAULT_CMD}) describes; "
        "unreachable: a local build with a warning",
    )
    where = p.add_mutually_exclusive_group()
    where.add_argument(
        "--all",
        action="store_true",
        help="with --remote: everything not substituted builds there (--max-jobs 0)",
    )
    where.add_argument(
        "--split",
        action="store_true",
        help="with --remote: any derivation may build there, this machine builds too",
    )
    p.add_argument(
        "--builders",
        metavar="SPEC",
        help="a literal nix --builders spec instead of the command (implies --remote)",
    )


def mode_of(ns: argparse.Namespace) -> str:
    """`local`, `big`, `all` or `split` from the parsed flags; `usage` for --all/--split
    without --remote/--builders."""
    remote = bool(getattr(ns, "remote", False) or getattr(ns, "builders", None))
    all_, split = bool(getattr(ns, "all", False)), bool(getattr(ns, "split", False))
    if (all_ or split) and not remote:
        flag = "--all" if all_ else "--split"
        raise NixinError("usage", f"{flag} needs --remote (or --builders SPEC)")
    if not remote:
        return LOCAL
    return "all" if all_ else "split" if split else "big"


def command(config: str | os.PathLike[str] | None) -> str:
    """The builders command: $NIXIN_BUILDERS_CMD, else state.json's `builders_cmd`, else
    the default. A shell-split string (`"ssh host nix-builders"` works)."""
    env = os.environ.get(ENV_CMD, "").strip()
    if env:
        return env
    if config is not None:
        try:
            data = json.loads((Path(config) / "installer" / "state.json").read_text())
            cmd = data.get(STATE_KEY) if isinstance(data, dict) else None
            if isinstance(cmd, str) and cmd.strip():
                return cmd.strip()
        except (OSError, ValueError):
            pass
    return DEFAULT_CMD


def remote_name(config: str | os.PathLike[str] | None = None, spec: str | None = None) -> str:
    """What to call the remote: the hosts of a literal spec, else the command's basename
    without a `-builders` suffix (`mbp13-builders` -> `mbp13`)."""
    if spec:
        hosts = spec_hosts(spec)
        if hosts:
            return ", ".join(hosts)
    try:
        words = shlex.split(command(config))
    except ValueError:
        words = []
    base = os.path.basename(words[0]) if words else DEFAULT_CMD
    return base.removesuffix("-builders") or base


def spec_hosts(spec: str) -> list[str]:
    """The host of every machine in a `--builders` spec (`;`-separated, uri first)."""
    hosts = []
    for entry in spec.replace("\n", ";").split(";"):
        words = entry.split()
        if not words:
            continue
        uri = words[0]
        if uri.startswith("@"):  # a machines file: nothing to name
            continue
        h = host_of(uri, "")
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def trusted() -> bool | None:
    """Whether the daemon trusts this user (`nix store info --json`); None when unknown."""
    p = proc.run(["nix", "store", "info", "--json"], timeout=60.0)
    if not p.ok:
        return None
    try:
        doc = json.loads(p.out)
    except ValueError:
        return None
    t = doc.get("trusted") if isinstance(doc, dict) else None
    return bool(t) if isinstance(t, (bool, int)) else None


def check_trusted() -> None:
    """`builders-untrusted` unless the daemon trusts this user (unknown counts as trusted:
    nix itself then says what it ignored)."""
    if trusted() is False:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "this user"
        raise NixinError("builders-untrusted", UNTRUSTED.format(user=user))


def requested(
    ns: argparse.Namespace, config: str | os.PathLike[str] | None
) -> dict[str, Any] | None:
    """The remote block before the spec is known (None for a local build), after the
    trust check: {mode, name, builders (a literal spec or None), command, fell_back}.
    `config`: the workspace, for state.json's `builders_cmd`."""
    mode = mode_of(ns)
    if mode == LOCAL:
        return None
    check_trusted()
    literal = getattr(ns, "builders", None) or None
    cmd = None
    if not literal:
        cmd = command(config) + (f" --{mode}" if mode in ("all", "split") else "")
    return {
        "mode": mode,
        "name": remote_name(config, literal),
        "builders": literal,
        "command": cmd,
        "fell_back": False,
    }


def resolve(remote: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Run the builders command for the spec (a literal one is kept). On failure the
    block says `fell_back` with `builders` None and `warnings` gains why."""
    r = dict(remote)
    if r.get("builders") or not r.get("command"):
        return r
    try:
        argv = shlex.split(r["command"])
    except ValueError as e:
        argv = []
        why = f"cannot parse {r['command']!r}: {e}"
    if argv:
        p = proc.run(argv, timeout=CMD_TIMEOUT)
        spec = " ".join(p.out.split()) if p.ok else ""
        if p.ok and spec:
            r["builders"] = spec
            return r
        if p.missing:
            why = f"{argv[0]} is not on PATH"
        elif p.rc == 124:
            why = f"{argv[0]} did not answer in {CMD_TIMEOUT:.0f} s"
        elif p.ok:
            why = f"{argv[0]} printed no spec"
        else:
            why = p.tail(1) or f"{argv[0]} exited with status {p.rc}"
    r["builders"] = None
    r["fell_back"] = True
    warnings.append(f"{r['name']} is not available ({why}): built locally instead")
    return r


def nix_args(remote: dict[str, Any] | None) -> list[str]:
    """What the build's `nix build` gets: nothing for a local build or after a fallback."""
    if not remote or not remote.get("builders"):
        return []
    args = ["--builders", remote["builders"], "--builders-use-substitutes"]
    if remote.get("mode") == "all":
        args += ["--max-jobs", "0"]
    return args


def where(remote: dict[str, Any] | None) -> str:
    """One phrase for a person: `building on mbp13 · big jobs`, `building on both · split`,
    `mbp13 unreachable · building locally`; empty for a plain local build."""
    if not remote:
        return ""
    name = remote.get("name") or "the remote"
    if remote.get("fell_back"):
        return f"{name} unreachable · building locally"
    mode = remote.get("mode")
    if mode == "split":
        return f"building on both ({name} takes any job) · split"
    return f"building on {name} · {MODE_LABEL.get(mode, mode)}"


def choices(config: str | os.PathLike[str] | None = None) -> list[tuple[str, str]]:
    """The UIs' selector: (mode, label) for local and each remote mode."""
    name = remote_name(config)
    return [
        (LOCAL, "local"),
        ("big", f"{name}: big jobs"),
        ("all", f"{name}: everything"),
        ("split", f"split ({name} + local)"),
    ]


def flags(mode: str) -> list[str]:
    """The CLI flags of a selector mode: none for local (the argv stays as before)."""
    if mode == LOCAL:
        return []
    return ["--remote"] + ([f"--{mode}"] if mode in ("all", "split") else [])
