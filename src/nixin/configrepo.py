"""The config flake: where it is, what git and nix say about it, which hosts it has.

One checkout for now (`--config PATH`, `$NIXIN_CONFIG`, else /etc/nixos). The later
"workspace vs authoritative" pair is two ConfigRepo instances: nothing here assumes
the path is writable or that it is the one the running system was built from.

Everything that talks to nix goes through `run_nix()`, the one seam the unit tests
replace (ConfigRepo(path, nix=fake)). nix is the host's own binary, taken from PATH.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = "/etc/nixos"
ENV_CONFIG = "NIXIN_CONFIG"
RUNNING_SYSTEM = "/run/current-system"
ERROR_MESSAGE_CAP = 2000


class NixinError(Exception):
    """A verb failure, carrying the --json error shape."""

    def __init__(self, code: str, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stderr = stderr  # untrimmed nix stderr, for callers that need the trace

    def doc(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


# --- nix ----------------------------------------------------------------------------

_NOISE = re.compile(r"^\s*(trace:|warning:|evaluating file )")
_ERROR_LINE = re.compile(r"^\s*error:\s*(.*)$")


def trim_nix_error(stderr: str) -> str:
    """The last `error:` block of a nix stderr, dedented, without traces and warnings.

    nix prints "error:" + trace frames + "(stack trace truncated)" + a second "error:"
    with the real message; that second block is what a person wants to read.
    """
    lines = [ln for ln in stderr.splitlines() if not _NOISE.match(ln)]
    starts = [i for i, ln in enumerate(lines) if _ERROR_LINE.match(ln)]
    if not starts:
        text = "\n".join(lines)
    else:
        i = starts[-1]
        first = _ERROR_LINE.match(lines[i]).group(1)
        block = ([first] if first else []) + lines[i + 1 :]
        text = textwrap.dedent("\n".join(block))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > ERROR_MESSAGE_CAP:
        text = text[: ERROR_MESSAGE_CAP - 1] + "…"
    return text


def run_nix(args: list[str], *, code: str = "eval-failed", timeout: float | None = None) -> str:
    """Run the host's `nix` and return stdout; failures become NixinError(code)."""
    exe = shutil.which("nix")
    if exe is None:
        raise NixinError("nix-missing", "`nix` is not on PATH")
    cmd = [exe, *args, "--option", "warn-dirty", "false"]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise NixinError("eval-timeout", f"nix did not finish in {timeout:.0f}s") from e
    if proc.returncode != 0:
        msg = trim_nix_error(proc.stderr) or f"nix exited with status {proc.returncode}"
        raise NixinError(code, msg, stderr=proc.stderr)
    return proc.stdout


NixRunner = Callable[..., str]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*")


def nix_str(s: str) -> str:
    """A Nix string literal."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${").replace("\n", "\\n")
    return f'"{s}"'


def nix_attr(name: str) -> str:
    """One attribute-path component, quoted when it is not an identifier (e.g. 19BK)."""
    return name if _IDENT.fullmatch(name) else nix_str(name)


def nix_list_of_str(items: list[str]) -> str:
    return "[ " + " ".join(nix_str(x) for x in items) + " ]"


def nix_value(v: Any) -> str:
    """JSON-ish Python value -> Nix literal (for passing arguments to helper expressions)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return nix_str(v)
    if isinstance(v, (list, tuple)):
        return "[ " + " ".join(nix_value(x) for x in v) + " ]"
    if isinstance(v, dict):
        return "{ " + " ".join(f"{nix_attr(k)} = {nix_value(x)};" for k, x in v.items()) + " }"
    raise TypeError(f"cannot render {type(v).__name__} as Nix")


# --- lock ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockedInput:
    """One node of flake.lock, as the writer/apply layers and `status` need it."""

    name: str  # input name as the flake calls it ("nixpkgs")
    node: str  # node name in the lock ("nixpkgs_2" here: do not assume name == node)
    type: str | None
    owner: str | None
    repo: str | None
    rev: str | None
    ref: str | None  # from `original`, e.g. "nixos-unstable"
    last_modified: int | None
    nar_hash: str | None

    @property
    def date(self) -> str | None:
        if self.last_modified is None:
            return None
        return dt.datetime.fromtimestamp(self.last_modified, dt.UTC).strftime("%Y-%m-%d")

    @property
    def short_rev(self) -> str | None:
        return self.rev[:7] if self.rev else None

    @property
    def flakeref(self) -> str | None:
        if self.type == "github" and self.owner and self.repo and self.rev:
            return f"github:{self.owner}/{self.repo}/{self.rev}"
        return None

    def doc(self) -> dict[str, Any]:
        return {
            "input": self.name,
            "node": self.node,
            "type": self.type,
            "owner": self.owner,
            "repo": self.repo,
            "rev": self.rev,
            "ref": self.ref,
            "last_modified": self.last_modified,
            "date": self.date,
            "flakeref": self.flakeref,
            "nar_hash": self.nar_hash,
        }


def resolve_lock_input(nodes: dict, node: str, name: str) -> str | None:
    """Node name that input `name` of `node` points at; follows `follows` paths."""
    ref = nodes.get(node, {}).get("inputs", {}).get(name)
    if ref is None:
        return None
    if isinstance(ref, str):
        return ref
    cur = "root"  # a list is a path from the root node
    for seg in ref:
        cur = resolve_lock_input(nodes, cur, seg)
        if cur is None:
            return None
    return cur


def parse_lock(lock: dict, name: str = "nixpkgs") -> LockedInput | None:
    nodes = lock.get("nodes", {})
    node = resolve_lock_input(nodes, "root", name)
    if node is None or node not in nodes:
        return None
    locked = nodes[node].get("locked", {})
    original = nodes[node].get("original", {})
    return LockedInput(
        name=name,
        node=node,
        type=locked.get("type"),
        owner=locked.get("owner"),
        repo=locked.get("repo"),
        rev=locked.get("rev"),
        ref=original.get("ref") or locked.get("ref"),
        last_modified=locked.get("lastModified"),
        nar_hash=locked.get("narHash"),
    )


# --- the repo -----------------------------------------------------------------------


@dataclass
class Source:
    """What `nix flake metadata` says about the checkout as nix sees it."""

    store_path: str | None  # /nix/store/<hash>-source: the tree every eval reads
    fingerprint: str | None  # nix's own eval-cache key over source + lock
    head: str | None  # git HEAD (without the -dirty suffix)
    dirty: bool
    last_modified: int | None

    @property
    def source_hash(self) -> str | None:
        """The store hash: a content hash of the tracked tree, dirty edits included."""
        if not self.store_path:
            return None
        return os.path.basename(self.store_path).split("-", 1)[0]

    def doc(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "dirty": self.dirty,
            "source": self.store_path,
            "source_hash": self.source_hash,
            "fingerprint": self.fingerprint,
            "last_modified": self.last_modified,
        }


@dataclass
class Location:
    """A file nix reported (declaration or definition), mapped back to something readable."""

    raw: str
    source: str  # "config" | "inline" | input name ("nixpkgs", "home-manager", ...) | "other"
    file: str | None  # repo-relative for config, input-relative for inputs, None for inline
    line: int | None = None
    column: int | None = None

    def doc(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.source, "file": self.file, "raw": self.raw}
        if self.line is not None:
            d["line"] = self.line
        if self.column is not None:
            d["column"] = self.column
        return d


@dataclass
class ConfigRepo:
    path: Path
    nix: NixRunner = field(default=run_nix, repr=False)
    _meta: dict | None = field(default=None, repr=False)
    _inputs: dict[str, str] | None = field(default=None, repr=False)

    @classmethod
    def locate(cls, explicit: str | None = None, nix: NixRunner = run_nix) -> ConfigRepo:
        raw = explicit or os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG
        path = Path(raw).expanduser()
        if not (path / "flake.nix").is_file():
            raise NixinError(
                "config-not-found",
                f"no flake.nix in {path} (use --config PATH or set {ENV_CONFIG})",
            )
        return cls(path.resolve(), nix=nix)

    # flake references -----------------------------------------------------------------

    @property
    def flake(self) -> str:
        return str(self.path)

    def host_attr(self, host: str) -> str:
        return f"nixosConfigurations.{nix_attr(host)}"

    def installable(self, attr: str) -> str:
        return f"{self.flake}#{attr}"

    def eval_json(self, attr: str, apply: str | None = None, **kw: Any) -> Any:
        args = ["eval", "--json", self.installable(attr)]
        if apply is not None:
            args += ["--apply", apply]
        return json.loads(self.nix(args, **kw))

    def eval_raw(self, attr: str, **kw: Any) -> str:
        return self.nix(["eval", "--raw", self.installable(attr)], **kw)

    # metadata ---------------------------------------------------------------------------

    def metadata(self) -> dict:
        if self._meta is None:
            out = self.nix(["flake", "metadata", "--json", self.flake], code="not-a-flake")
            self._meta = json.loads(out)
        return self._meta

    def source(self) -> Source:
        meta = self.metadata()
        rev = meta.get("revision")
        dirty_rev = meta.get("dirtyRevision")
        head = rev or (dirty_rev.removesuffix("-dirty") if dirty_rev else None) or self.git_head()
        return Source(
            store_path=meta.get("path"),
            fingerprint=meta.get("fingerprint"),
            head=head,
            dirty=dirty_rev is not None,
            last_modified=meta.get("lastModified"),
        )

    def git_head(self) -> str | None:
        """Fallback for checkouts nix does not report a revision for; never writes."""
        if not (self.path / ".git").exists() or shutil.which("git") is None:
            return None
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(self.path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None

    def lock(self, name: str = "nixpkgs") -> LockedInput | None:
        lock_file = self.path / "flake.lock"
        if not lock_file.is_file():
            return None
        return parse_lock(json.loads(lock_file.read_text()), name)

    def inputs(self) -> dict[str, str]:
        """Input name -> store path (nested inputs as "outer/inner"); from `nix flake archive`."""
        if self._inputs is None:
            out = self.nix(
                ["flake", "archive", "--json", "--dry-run", self.flake], code="not-a-flake"
            )
            flat: dict[str, str] = {}

            def walk(prefix: str, inputs: dict) -> None:
                for name, node in inputs.items():
                    full = f"{prefix}{name}"
                    if node.get("path"):
                        flat[full] = node["path"]
                    walk(f"{full}/", node.get("inputs", {}))

            walk("", json.loads(out).get("inputs", {}))
            self._inputs = flat
        return self._inputs

    def locate_path(self, raw: str) -> Location:
        """Map a /nix/store/...-source/... path nix reported to its origin."""
        src = self.source().store_path
        if src and (raw == src or raw.startswith(src + "/")):
            rel = raw[len(src) + 1 :] or "flake.nix"
            if os.path.isfile(os.path.join(raw, "default.nix")):
                rel += "/default.nix"  # a module imported as a directory (./hosts/mbp13)
            return Location(raw, "config", rel)
        inputs = self.inputs()
        nixpkgs = inputs.get("nixpkgs")
        if nixpkgs and raw == f"{nixpkgs}/flake.nix":
            # nixpkgs.lib.nixosSystem stamps anonymous modules (attrsets written inline
            # in the config's own nixosSystem call) with nixpkgs' flake.nix.
            return Location(raw, "inline", None)
        for name, p in sorted(inputs.items(), key=lambda kv: -len(kv[1])):
            if raw == p or raw.startswith(p + "/"):
                return Location(raw, name, raw[len(p) + 1 :])
        return Location(raw, "other", raw)

    # hosts -----------------------------------------------------------------------------

    def hosts(self) -> list[str]:
        return list(self.eval_json("nixosConfigurations", apply="builtins.attrNames"))

    def hm_users(self, host: str) -> list[str]:
        """Home Manager users of a host; [] when the host has no HM module."""
        return list(
            self.eval_json(
                f"{self.host_attr(host)}.config",
                apply="c: builtins.attrNames (c.home-manager.users or { })",
            )
        )

    def cache_key(self, host: str, salt: str = "") -> str:
        """Stable id of (tree as nix sees it + lock, host, salt). Changes on any tracked edit."""
        s = self.source()
        basis = s.fingerprint or s.source_hash or str(self.path)
        return hashlib.sha256(f"{basis}|{host}|{salt}".encode()).hexdigest()[:16]


# --- the running system ---------------------------------------------------------------


def running_system(root: str = RUNNING_SYSTEM) -> dict[str, Any]:
    """The booted generation: its store path and nixos-version string (no nix needed)."""
    p = Path(root)
    doc: dict[str, Any] = {"system": None, "version": None, "nixpkgs_rev": None}
    if not p.exists():
        return doc
    doc["system"] = os.path.realpath(p)
    vf = p / "nixos-version"
    if vf.is_file():
        version = vf.read_text().strip()
        doc["version"] = version
        tail = version.rsplit(".", 1)[-1]
        if re.fullmatch(r"[0-9a-f]{7,40}", tail):
            doc["nixpkgs_rev"] = tail
    return doc


def local_hostname() -> str:
    return socket.gethostname()
