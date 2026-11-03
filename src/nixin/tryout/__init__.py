"""try — run a package without installing it (nix-software-center's "launch without installing").

    nixin try PKG [-- ARGS...] [--program NAME] [--nixpkgs FLAKEREF|REV] [--impure]
    nixin try PKG --shell
    nixin try PKG --build-only            (implied by --json)

PKG is an attribute of the host's *locked* nixpkgs (the config flake's nixpkgs input, as
`flake.lock` pins it; `--nixpkgs` picks another), so what you try is what `nixin add`
would install. Three steps: probe.nix evaluates the attribute (meta, outputs, out paths;
about half a second, no build), `nix build --no-link` fetches or builds it into the store
(nix's progress on stderr in human mode), then the outputs say what it has (bin/,
desktop entries, closure size). Nothing is installed or rooted: a garbage collection
removes it again.

Human mode then hands the terminal over: nixin execs `nix run <flakeref>#PKG -- ARGS`
(`nix shell … --command PROG` for a program other than nix run's own), or with
`--shell` `nix shell <flakeref>#PKG`, which starts $SHELL with the package on PATH.
Under --json (and with --build-only) it stops after the build and returns the document a
UI needs to say "ready, 12 MiB" and offer to launch: `out`, `programs`, `size`,
`cached`, `command`, `gui`.

The program to run: --program NAME, else meta.mainProgram, else the only program in bin/,
else the one a graphical desktop entry starts; several left is `ambiguous-program` (with
`programs[]`), none is `no-program`. Unfree packages are refused by nixpkgs unless
NIXPKGS_ALLOW_UNFREE=1 reaches it, which nix only allows with --impure: `unfree` says so.

`register(subparsers)` is the only thing cli.py calls (before its stub loop, so `try` gets
its own positionals); the handler returns the --json document (cli adds verb/host).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any

from ..apply.proc import shown
from ..configrepo import NixinError
from ..discovery.local import current_system
from . import build, launch, nixpkgs, render, runner

HELP = "run a package from the locked nixpkgs without installing it (--shell: a shell with it)"
_ATTR = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_'+-]*(?:\.[A-Za-z0-9_][A-Za-z0-9_'+-]*)*")
# why nixpkgs refuses to evaluate a package, and the variable that lets it (with --impure)
REFUSALS = (
    ("unfree", "NIXPKGS_ALLOW_UNFREE", "unfree"),
    ("broken", "NIXPKGS_ALLOW_BROKEN", "marked broken"),
    ("insecure", "NIXPKGS_ALLOW_INSECURE", "marked insecure (known vulnerabilities)"),
)


class TryError(NixinError):
    """A NixinError whose error object carries more than code and message."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(code, message)
        self.extra = extra

    def doc(self) -> dict[str, Any]:
        d = super().doc()
        d["error"].update(self.extra)
        return d


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("try", help=HELP, description=HELP)
    p.add_argument("pkg", nargs="?", metavar="PKG", help="attribute path: cowsay, python3")
    p.add_argument("args", nargs="*", metavar="ARGS", help="after --: given to the program")
    p.add_argument(
        "--shell",
        action="store_true",
        help="start $SHELL with the package on PATH (nix shell) instead of running it",
    )
    p.add_argument("--program", metavar="NAME", help="which of the package's programs to run")
    p.add_argument(
        "--nixpkgs",
        metavar="FLAKEREF|REV",
        help="build from this nixpkgs (default: the config's nixpkgs input as locked)",
    )
    p.add_argument(
        "--impure",
        action="store_true",
        help="pass --impure to nix, so NIXPKGS_ALLOW_UNFREE=1 (or _BROKEN, _INSECURE) works",
    )
    p.add_argument(
        "--build-only",
        action="store_true",
        help="build and report, do not run (always so under --json)",
    )
    p.add_argument("--config", help="config flake dir (default: $NIXIN_CONFIG or /etc/nixos)")
    p.set_defaults(run=try_)


def try_(ns: argparse.Namespace) -> dict[str, Any]:
    try:
        return _try(ns)
    except NixinError as e:
        return e.doc()


def _check(ns: argparse.Namespace) -> str:
    if not ns.pkg:
        raise NixinError("usage", "try needs a package: nixin try PKG [-- ARGS...]")
    if not _ATTR.fullmatch(ns.pkg):
        raise NixinError(
            "usage", f"{ns.pkg!r} is not an attribute path (cowsay, python3Packages.httpie)"
        )
    if ns.shell and ns.args:
        raise NixinError(
            "usage", "--shell starts a shell and takes no ARGS (drop --shell to run with them)"
        )
    if ns.shell and ns.program:
        raise NixinError("usage", "--program picks what runs; --shell runs your $SHELL instead")
    return ns.pkg


def _try(ns: argparse.Namespace) -> dict[str, Any]:
    t0 = time.monotonic()
    attr = _check(ns)
    human = not ns.json
    build_only = ns.json or ns.build_only
    src = nixpkgs.choose(ns.config, ns.nixpkgs)
    system = current_system()
    info = nixpkgs.probe(src, attr, system=system, impure=ns.impure)
    timings = {"probe": round(time.monotonic() - t0, 2)}
    installable = f"{src.flakeref}#{attr}"
    if not info.get("found"):
        _not_found(attr, src, info.get("names") or [])
    if not info.get("derivation"):
        raise TryError(
            "not-found",
            f"{attr} is a package set in {src.label()}, not a package: try {attr}.<name>",
            suggestions=[],
        )
    if info.get("out_paths") is None:
        _refused(ns, attr, info, installable)
    known = list((info.get("out_paths") or {}).values())
    cached = bool(known) and all(os.path.exists(p) for p in known)
    stream = "capture" if ns.json else ("inherit" if runner.stderr_is_terminal() else "tee")
    if human and not cached:
        print(f"nixin try: fetching {attr} from {src.label()} …", file=sys.stderr, flush=True)
    t = time.monotonic()
    built = build.build(installable, impure=ns.impure, stream=stream)
    outputs = built["outputs"]
    timings["build"] = round(time.monotonic() - t, 2)
    outs = list(outputs.values())
    progs = build.programs(outs)
    desktop = build.desktop_entries(outs)
    t = time.monotonic()
    size = build.closure_size(outs)
    timings["size"] = round(time.monotonic() - t, 2)
    impure = ["--impure"] if ns.impure else []
    env = _env_prefix(info) if ns.impure else ""
    shell_argv = ["nix", "shell", *impure, installable]
    doc: dict[str, Any] = {
        "ok": True,
        "attr": attr,
        "name": info.get("name"),
        "pname": info.get("pname"),
        "version": info.get("version"),
        "description": info.get("description"),
        "license": info.get("license") or [],
        "unfree": bool(info.get("unfree")),
        "nixpkgs": {**src.doc(), "label": src.label()},
        "system": system,
        "mode": "shell" if ns.shell else "run",
        "build_only": build_only,
        "impure": ns.impure,
        "outputs": outputs,
        "out": outputs.get("out") or outs[0],
        "programs": progs,
        "main_program": info.get("main_program"),
        "desktop": desktop,
        "gui": build.is_gui(desktop),
        "size": size,
        "cached": cached,
        "fetched": built["fetched"],
        "shell_command": env + shown(shell_argv),
    }
    program = _program(ns, attr, info, progs, desktop, doc)
    default = info.get("main_program") or info.get("pname") or attr.rsplit(".", 1)[-1]
    if ns.shell:
        argv = shell_argv
    elif program == default:
        argv = ["nix", "run", *impure, installable, *(["--", *ns.args] if ns.args else [])]
    else:
        argv = ["nix", "shell", *impure, installable, "--command", program, *ns.args]
    where = build.where(outs, program) if program else None
    doc.update(
        {
            "program": program,
            "argv": argv,
            "command": env + shown(argv),
            "exec": [where, *ns.args] if where else None,
        }
    )
    timings["total"] = round(time.monotonic() - t0, 2)
    doc["timings"] = timings
    if human:
        doc["message"] = render.ready(doc)
    if not build_only:
        if ns.shell:
            say = f"a shell with {render.title(doc)} on PATH, from {src.label()}; exit leaves it"
        else:
            say = doc["command"]
        print(f"nixin try: {say}", file=sys.stderr, flush=True)
        launch.handoff(argv)
    return doc


def _not_found(attr: str, src: nixpkgs.Source, names: list[str]) -> None:
    hints = nixpkgs.suggest(attr, names)
    also = f"; did you mean {', '.join(hints)}?" if hints else ""
    raise TryError(
        "not-found",
        f"{src.label()} has no package {attr}{also} (`nixin search {attr}` searches)",
        suggestions=hints,
    )


def _refused(ns: argparse.Namespace, attr: str, info: dict[str, Any], installable: str) -> None:
    """nixpkgs would not evaluate the package's out paths: why, and how to allow it."""
    why = [(flag, var, words) for flag, var, words in REFUSALS if info.get(flag)]
    if not why:
        return  # another reason (platform, an assertion): the build says which
    flag, var, words = why[0]
    allowed = os.environ.get(var) == "1"
    again = _again(ns, impure=True)
    commands = [f"{var}=1 {again}", f"{var}=1 {shown(['nix', 'run', '--impure', installable])}"]
    lic = ", ".join(info.get("license") or [])
    what = f"{attr} is {words}" + (f" ({lic})" if flag == "unfree" and lic else "")
    if allowed and not ns.impure:
        reason = f"{var}=1 is set, but nix hides the environment from a pure eval: add --impure"
    elif ns.impure and not allowed:
        reason = f"--impure is given, but {var}=1 is not set"
    else:
        reason = f"nixpkgs refuses it unless {var}=1 reaches it, which takes --impure"
    note = ""
    if flag == "unfree":
        note = " (try uses nixpkgs as published: your config's allowUnfree does not apply)"
    raise TryError(
        "unfree" if flag == "unfree" else "build-failed",
        f"{what}: {reason}{note}. Run:\n  {commands[0]}",
        commands=commands,
    )


def _again(ns: argparse.Namespace, *, impure: bool) -> str:
    """This command line again (for an error's commands[])."""
    argv = ["nixin", "try", ns.pkg]
    if ns.shell:
        argv.append("--shell")
    if ns.program:
        argv += ["--program", ns.program]
    if ns.nixpkgs:
        argv += ["--nixpkgs", ns.nixpkgs]
    if impure or ns.impure:
        argv.append("--impure")
    if ns.args:
        argv += ["--", *ns.args]
    return shown(argv)


def _env_prefix(info: dict[str, Any]) -> str:
    """`NIXPKGS_ALLOW_UNFREE=1 ` (etc.) for a command that only evaluates with it."""
    words = [f"{var}=1 " for flag, var, _ in REFUSALS if info.get(flag)]
    return "".join(words)


def _program(
    ns: argparse.Namespace,
    attr: str,
    info: dict[str, Any],
    progs: list[str],
    desktop: list[dict[str, Any]],
    doc: dict[str, Any],
) -> str | None:
    """--program, mainProgram, the only program, the graphical entry's; else an error
    (outside --shell, which runs no program of the package)."""
    extra = {
        "programs": progs,
        "out": doc["out"],
        "shell_command": doc["shell_command"],
        "size": doc["size"],
        "cached": doc["cached"],
    }
    if ns.program:
        if progs and ns.program not in progs:
            raise TryError(
                "not-found",
                f"{attr} has no program {ns.program!r} in bin/ ({', '.join(progs)})",
                **extra,
            )
        return ns.program
    if info.get("main_program"):
        return info["main_program"]
    if len(progs) == 1:
        return progs[0]
    apps = {e["program"] for e in desktop if not e["terminal"] and not e["hidden"]}
    apps &= set(progs)
    if len(apps) == 1:
        return apps.pop()
    if ns.shell:
        return None
    if not progs:
        raise TryError(
            "no-program",
            f"{attr} has no programs (nothing in bin/), so there is nothing to run; "
            f"--shell puts it on PATH: {doc['shell_command']}",
            **extra,
        )
    listed = ", ".join(progs[:20]) + (f", … {len(progs) - 20} more" if len(progs) > 20 else "")
    raise TryError(
        "ambiguous-program",
        f"{attr} has {len(progs)} programs and no meta.mainProgram: pick one with "
        f"--program NAME ({listed})",
        **extra,
    )
