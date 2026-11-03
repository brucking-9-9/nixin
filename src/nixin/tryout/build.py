"""Building the package (into the store only: no profile, no GC root) and reading what it has.

`nix build --no-link --json <flakeref>#<attr>` builds the outputs `nix run`/`nix shell`
would (meta.outputsToInstall): substituted from cache.nixos.org when it has them, built
otherwise. Nothing roots them, so the next garbage collection removes them again; that is
the point of trying. Then, from the out paths: the programs in `bin/`, the desktop entries
in `share/applications/` (a GUI app has one that does not ask for a terminal), and the
closure size (`nix path-info --recursive --size`).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from ..configrepo import NixinError, trim_nix_error
from ..outdated import target as tgt
from . import runner

_FETCH = re.compile(
    r"(?:these (\d+)|this) paths? will be fetched \(([\d.]+) (\w+) download, ([\d.]+) (\w+) unpacked\)"
)
_BUILD = re.compile(r"(?:these (\d+)|this) derivations? will be built")
_UNITS = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40}


def fetched(stderr: str) -> dict[str, int]:
    """What nix said it would fetch and build (its summary lines before the work)."""
    doc = {"paths": 0, "download": 0, "unpacked": 0, "built": 0}
    m = _FETCH.search(stderr)
    if m:
        doc["paths"] = int(m.group(1) or 1)
        doc["download"] = round(float(m.group(2)) * _UNITS.get(m.group(3), 1))
        doc["unpacked"] = round(float(m.group(4)) * _UNITS.get(m.group(5), 1))
    m = _BUILD.search(stderr)
    if m:
        doc["built"] = int(m.group(1) or 1)
    return doc


def build(installable: str, *, impure: bool, stream: str) -> dict[str, Any]:
    """`outputs` (name -> out path) and `fetched` (what nix fetched and built; None when nix
    wrote to the terminal itself). `stream`: runner's capture | tee | inherit."""
    argv = ["nix", "build", "--no-link", "--json", installable]
    if impure:
        argv.append("--impure")
    p = runner.run(argv, stream=stream)
    if p.missing:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    if not p.ok:
        if p.err and tgt.is_network(p.err):
            raise NixinError(
                "network", f"nix could not fetch {installable}: {trim_nix_error(p.err)}"
            )
        raise NixinError("build-failed", _build_error(p.err, installable, p.rc), stderr=p.err)
    try:
        built = json.loads(p.out)
    except ValueError as e:
        raise NixinError("build-failed", f"nix build {installable} printed no JSON: {e}") from e
    outputs: dict[str, str] = {}
    for item in built if isinstance(built, list) else []:
        outputs.update(item.get("outputs") or {})
    if not outputs:
        raise NixinError("build-failed", f"nix build {installable} printed no out path")
    return {"outputs": outputs, "fetched": None if stream == "inherit" else fetched(p.err)}


def _build_error(stderr: str, installable: str, rc: int) -> str:
    """From the first `error: builder for …` block (nix's log tail) to the end; with the
    stderr shown on the terminal (inherit), only where to look."""
    if not stderr:
        return f"nix build {installable} failed (exit status {rc}); its log is above"
    lines = stderr.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*error:.*(builder for|Cannot build|build of)", ln):
            text = "\n".join(lines[i:]).strip()
            return text if len(text) <= 2000 else "…" + text[-1999:]
    return trim_nix_error(stderr) or f"nix build {installable} failed (exit status {rc})"


def programs(outs: list[str]) -> list[str]:
    """Executables in the outputs' bin/, sorted."""
    found: set[str] = set()
    for out in outs:
        d = Path(out) / "bin"
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or not os.access(e, os.X_OK) or e.is_dir():
                continue
            found.add(e.name)
    return sorted(found)


def where(outs: list[str], program: str) -> str | None:
    """The program's path in the first output that has it."""
    for out in outs:
        p = Path(out) / "bin" / program
        if p.exists():
            return str(p)
    return None


def desktop_entries(outs: list[str]) -> list[dict[str, Any]]:
    """The outputs' share/applications/*.desktop: name, the Exec line, Terminal=, hidden."""
    entries: list[dict[str, Any]] = []
    for out in outs:
        d = Path(out) / "share" / "applications"
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            try:
                e = parse_desktop(f.read_text(errors="replace"))
            except OSError:
                continue
            if e.get("Type", "Application") != "Application":
                continue
            entries.append(
                {
                    "file": f.name,
                    "name": e.get("Name"),
                    "exec": e.get("Exec"),
                    "program": exec_program(e.get("Exec")),
                    "terminal": e.get("Terminal", "").lower() == "true",
                    "hidden": "true"
                    in (e.get("NoDisplay", "").lower(), e.get("Hidden", "").lower()),
                }
            )
    return entries


def parse_desktop(text: str) -> dict[str, str]:
    """The [Desktop Entry] group's keys (first value wins; localised keys are other keys)."""
    entry: dict[str, str] = {}
    group = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            continue
        if group == "Desktop Entry" and "=" in line:
            k, v = line.split("=", 1)
            entry.setdefault(k.strip(), v.strip())
    return entry


def exec_program(exec_line: str | None) -> str | None:
    """The binary an Exec= line runs (its basename), past `env VAR=…` prefixes."""
    if not exec_line:
        return None
    try:
        words = shlex.split(exec_line)
    except ValueError:
        words = exec_line.split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if "=" not in w or w.startswith("-")]
    return os.path.basename(words[0]) if words else None


def is_gui(entries: list[dict[str, Any]]) -> bool:
    return any(not e["terminal"] and not e["hidden"] for e in entries)


def closure_size(outs: list[str]) -> int | None:
    """Bytes in the closure of the outputs (each store path once); None if nix cannot say."""
    try:
        text = runner.nix(["path-info", "--recursive", "--size", *outs], code="nix-failed")
    except NixinError:
        return None
    sizes: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            sizes[parts[0]] = int(parts[-1])
    return sum(sizes.values()) if sizes else None
