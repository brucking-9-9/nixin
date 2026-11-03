"""installer/state.json, the modules rendered from it, and the two external tools.

state.json is the source of truth for everything nixin added; the generated modules are a
pure function of it (rendered, then nixfmt'd), so re-rendering is always safe:

    {"version": 1,
     "nixos": {"<host>": {"packages": ["cowsay"], "options": {"services.x.enable": "true"}}},
     "home":  {"<user>": {"packages": [...], "options": {...}}},
     "imports": {"<file>": "<the imports list as it was before nixin expanded it>"}}

Packages are attribute paths under `pkgs`; option values are raw Nix expressions, rendered
verbatim as `path = value;` (dotted attrpaths, not quoted flat keys).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from ..configrepo import NixinError, nix_attr, trim_nix_error
from .workspace import GENERATED_DIR, HEADER, Workspace

STATE_FILE = f"{GENERATED_DIR}/state.json"
STATE_VERSION = 1
LIST_OPTION = {"nixos": "environment.systemPackages", "home": "home.packages"}
TOOL_TIMEOUT = 60.0  # nix-instantiate --parse and nixfmt answer in well under a second


def generated_file(kind: str, name: str) -> str:
    return f"{GENERATED_DIR}/{name}.nix" if kind == "nixos" else f"{GENERATED_DIR}/{name}-home.nix"


def attr_key(parts: list[str]) -> str:
    """Canonical attrpath text: components quoted only where Nix needs it."""
    return ".".join(nix_attr(p) for p in parts)


@dataclass
class State:
    data: dict[str, Any] = field(
        default_factory=lambda: {"version": STATE_VERSION, "nixos": {}, "home": {}}
    )

    @classmethod
    def load(cls, ws: Workspace) -> State:
        if not ws.exists(STATE_FILE):
            return cls()
        try:
            data = json.loads(ws.text(STATE_FILE))
        except json.JSONDecodeError as e:
            raise NixinError("invalid-state", f"{STATE_FILE}: {e}") from e
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise NixinError("invalid-state", f"{STATE_FILE}: expected version {STATE_VERSION}")
        data.setdefault("nixos", {})
        data.setdefault("home", {})
        return cls(data)

    def entry(self, kind: str, name: str) -> dict[str, Any]:
        e = self.data[kind].setdefault(name, {})
        e.setdefault("packages", [])
        e.setdefault("options", {})
        return e

    def peek(self, kind: str, name: str) -> dict[str, Any]:
        e = self.data[kind].get(name) or {}
        return {"packages": list(e.get("packages", [])), "options": dict(e.get("options", {}))}

    @property
    def imports(self) -> dict[str, str]:
        return self.data.setdefault("imports", {})

    def prune(self) -> None:
        for kind in ("nixos", "home"):
            for name in list(self.data[kind]):
                e = self.data[kind][name]
                if not e.get("packages") and not e.get("options"):
                    del self.data[kind][name]
                else:
                    e["packages"] = sorted(set(e["packages"]))
        if not self.data.get("imports"):
            self.data.pop("imports", None)

    def is_empty(self) -> bool:
        return not self.data["nixos"] and not self.data["home"] and not self.data.get("imports")

    def text(self) -> str:
        return json.dumps(self.data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render(kind: str, entry: dict[str, Any]) -> str:
    """The generated module for one host (kind "nixos") or HM user ("home"), before nixfmt."""
    out = [HEADER, "{ config, lib, pkgs, ... }:", "{"]
    pkgs_ = sorted(set(entry.get("packages", [])))
    if pkgs_:
        out.append(f"  {LIST_OPTION[kind]} = [")
        out += [f"    pkgs.{p}" for p in pkgs_]
        out.append("  ];")
    for key, value in sorted(entry.get("options", {}).items()):
        if "\n" in value or "#" in value:
            out.append(f"  {key} =\n{value}\n  ;")
        else:
            out.append(f"  {key} = {value};")
    out.append("}")
    return "\n".join(out) + "\n"


class Tools:
    """nix-instantiate --parse and nixfmt; tests replace this with a fake."""

    def parse(self, text: str) -> str | None:
        """None if `text` parses as Nix, else nix's error message (or that it timed out: a
        hung tool must not hang `set`/`add` and the TUI/GUI job running them)."""
        exe = shutil.which("nix-instantiate")
        if exe is None:
            raise NixinError("nix-missing", "`nix-instantiate` is not on PATH")
        try:
            proc = subprocess.run(
                [exe, "--parse", "-"],
                input=text,
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"nix-instantiate --parse did not finish in {TOOL_TIMEOUT:.0f}s"
        if proc.returncode == 0:
            return None
        return trim_nix_error(proc.stderr) or f"nix-instantiate exited {proc.returncode}"

    def fmt(self, text: str) -> tuple[str, str | None]:
        """(formatted text, None) or (text unchanged, why not)."""
        exe = shutil.which("nixfmt")
        if exe is None:
            return text, "nixfmt is not on PATH; generated files are not formatted"
        try:
            proc = subprocess.run(
                [exe, "-"],
                input=text,
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (
                text,
                f"nixfmt did not finish in {TOOL_TIMEOUT:.0f}s; generated files are not formatted",
            )
        if proc.returncode != 0:
            return text, f"nixfmt failed: {proc.stderr.strip()[:300]}"
        return proc.stdout, None
