"""Command-line contract.

Every verb accepts --json and then prints exactly one JSON document on stdout:

    {"ok": true,  "verb": "<verb>", "host": "<host>", ...verb-specific fields}
    {"ok": false, "verb": "<verb>", "host": "<host>", "error": {"code": "<slug>", "message": "..."}}

Human-readable output (no --json) is free-form but never interleaved with the
JSON form. Exit codes: 0 ok, 1 verb failed (JSON error printed), 2 usage (code
"usage"; under --json also for argparse's own errors, with "verb" null when no
verb was recognised). docs/CONTRACT.md has the whole contract.
Nothing here prompts for input: privileged steps fail with code
"needs-sudo" and the command to run, so a UI (or an agent) can decide.

The verbs are stubs until the layers behind them land; they already return the
error shape so UIs can be built against it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import traceback

from . import __version__, apply, discovery, gui, truth, tui, writer

VERBS: dict[str, tuple[str, str]] = {
    # verb: (layer, one-line help)
    "search": ("discovery", "search packages / options / programs; --kind filters"),
    "info": ("discovery", "details for one package or option"),
    "versions": ("discovery", "version history of a package (which nixpkgs rev shipped what)"),
    "status": ("truth", "this host: lock rev, generation, counts, pending (unapplied) changes"),
    "where": ("truth", "where a package / option is declared in the config, with its value"),
    "add": ("writer", "add a package (system, or --home for Home Manager)"),
    "rm": ("writer", "remove a package, wherever it is declared"),
    "set": ("writer", "set an option to a Nix value"),
    "unset": ("writer", "remove an option definition, wherever it is declared"),
    "plan": ("apply", "build the pending config and show the closure diff"),
    "apply": ("apply", "commit into the authoritative config and switch (needs sudo)"),
    "sync": ("apply", "push/pull the config hub repo"),
    "tui": ("tui", "interactive terminal UI over the same documents"),
    "gui": ("gui", "desktop UI (Qt) over the same documents"),
}


class UsageError(Exception):
    """argparse rejected the command line; main() answers it (JSON under --json)."""

    def __init__(self, prog: str, message: str, usage: str) -> None:
        super().__init__(f"{prog}: {message}")
        self.prog, self.message, self.usage = prog, message, usage


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that raises instead of printing and exiting (subparsers inherit it)."""

    def error(self, message: str):  # type: ignore[override]
        raise UsageError(self.prog, message, self.format_usage())


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="nixin", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=f"nixin {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--host",
        default=socket.gethostname(),
        help="nixosConfigurations.<host> to act on (default: this machine)",
    )
    p.add_argument(  # a verb's own --config wins; verbs without one ignore it
        "--config",
        dest="config_before_verb",
        metavar="DIR",
        help="config flake for the verbs that take one (as `VERB --config DIR`)",
    )
    sub = p.add_subparsers(dest="verb", metavar="VERB")
    discovery.register(sub)
    for verb, (_layer, help_) in VERBS.items():
        if verb in sub.choices:  # a layer registered it with its own arguments
            continue
        sp = sub.add_parser(verb, help=help_)
        sp.add_argument("args", nargs="*")
    truth.register(sub)
    writer.register(sub)
    apply.register(sub)
    tui.register(sub)
    gui.register(sub)
    for sp in sub.choices.values():  # the global flags also work after the verb
        sp.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="as `nixin --json`"
        )
        sp.add_argument("--host", default=argparse.SUPPRESS, help="as `nixin --host`")
    return p


def _emit(ns: argparse.Namespace, doc: dict) -> int:
    doc = {"verb": ns.verb, "host": ns.host, **doc}
    if ns.json:
        json.dump(doc, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    elif doc["ok"]:
        print(doc.get("message", "ok"))
    else:
        print(f"nixin {ns.verb}: {doc['error']['message']}", file=sys.stderr)
    if doc["ok"]:
        return 0
    return 2 if doc["error"]["code"] == "usage" else 1


def _internal(exc: Exception) -> dict:
    """An unexpected exception as the error document: under --json a traceback would leave
    stdout empty. The message names the exception and where it was raised."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f" at {os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else ""
    return {
        "ok": False,
        "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}{where}"},
    }


def _usage(argv: list[str], e: UsageError) -> int:
    """argparse's errors (and no verb at all): exit 2; under --json one `usage` document."""
    head = argv[: argv.index("--")] if "--" in argv else argv
    if "--json" not in head:
        sys.stderr.write(e.usage)
        print(f"{e.prog}: error: {e.message}", file=sys.stderr)
        return 2
    host = socket.gethostname()
    for i, a in enumerate(head):
        if a == "--host" and i + 1 < len(head):
            host = head[i + 1]
        elif a.startswith("--host="):
            host = a.removeprefix("--host=")
    verb = next((a for a in head if a in VERBS), None)
    ns = argparse.Namespace(verb=verb, host=host, json=True)
    return _emit(ns, {"ok": False, "error": {"code": "usage", "message": str(e)}})


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = _parser()
    try:
        ns = parser.parse_args(argv)
    except UsageError as e:
        return _usage(argv, e)
    if ns.verb is None:
        if ns.json:
            return _usage(argv, UsageError("nixin", "no VERB given (nixin --help lists them)", ""))
        parser.print_help()
        return 2
    return _emit(ns, document(ns))


def document(ns: argparse.Namespace) -> dict:
    """The verb's document for a parsed command line, without the envelope keys: what
    main() emits. In-process callers (the TUI) use it to get the --json document without a
    subprocess; `ns.json` False only adds the human `message` (and lets plan/apply tee
    nix's output to stderr)."""
    if hasattr(ns, "config") and ns.config is None:
        ns.config = ns.config_before_verb
    run = getattr(ns, "run", None)  # set by a layer's register(); stubs have none
    if run is not None:
        try:
            return run(ns)
        except Exception as exc:  # noqa: BLE001 -- every failure must become the error document
            return _internal(exc)
    layer = VERBS[ns.verb][0]
    return {
        "ok": False,
        "error": {
            "code": "not-implemented",
            "message": f"{ns.verb!r} belongs to the {layer} layer, which is not written yet",
        },
    }


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
