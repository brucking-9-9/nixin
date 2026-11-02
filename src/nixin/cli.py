"""Command-line contract.

Every verb accepts --json and then prints exactly one JSON document on stdout:

    {"ok": true,  "verb": "<verb>", "host": "<host>", ...verb-specific fields}
    {"ok": false, "verb": "<verb>", "error": {"code": "<slug>", "message": "..."}}

Human-readable output (no --json) is free-form but never interleaved with the
JSON form. Exit codes: 0 ok, 1 verb failed (JSON error printed), 2 usage.
Nothing here prompts for input: privileged steps fail with code
"needs-sudo" and the command to run, so a UI (or an agent) can decide.

The verbs are stubs until the layers behind them land; they already return the
error shape so UIs can be built against it.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys

from . import __version__, discovery

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
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nixin", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=f"nixin {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--host",
        default=socket.gethostname(),
        help="nixosConfigurations.<host> to act on (default: this machine)",
    )
    sub = p.add_subparsers(dest="verb", metavar="VERB")
    discovery.register(sub)
    for verb, (_layer, help_) in VERBS.items():
        if verb in sub.choices:  # a layer registered it with its own arguments
            continue
        sp = sub.add_parser(verb, help=help_)
        sp.add_argument("args", nargs="*")
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
    return 0 if doc["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.verb is None:
        _parser().print_help()
        return 2
    run = getattr(ns, "run", None)  # set by a layer's register(); stubs have none
    if run is not None:
        return _emit(ns, run(ns))
    layer = VERBS[ns.verb][0]
    return _emit(
        ns,
        {
            "ok": False,
            "error": {
                "code": "not-implemented",
                "message": f"{ns.verb!r} belongs to the {layer} layer, which is not written yet",
            },
        },
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
