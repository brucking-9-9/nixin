"""How nixin reaches another host: `$XDG_CONFIG_HOME/nixin/hosts.toml`.

    [mbp13]
    ssh = "mbp13"                     # what `ssh` connects to: an alias from ~/.ssh/config,
                                      # or user@address (default: the host name)
    workspace = "/home/master/nixos"  # the host's own workspace clone (default: ~/nixos,
                                      # `~` expanded by the remote shell)

    ["19BK"]
    ssh = "brucking@19bk"
    authoritative = "/etc/nixos"      # its root-owned clone (the default)

A host without a table, or no file at all, gets the defaults. The file is only read: by
`apply --host H` for a host that is not this machine (remote.py).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..configrepo import DEFAULT_CONFIG, NixinError

FILE = "hosts.toml"
DEFAULT_WORKSPACE = "~/nixos"
KEYS = ("ssh", "workspace", "authoritative")
_TILDE = re.compile(r"~[a-z_][a-z0-9_-]*")


@dataclass
class Remote:
    host: str  # nixosConfigurations.<host>
    ssh: str  # ssh's destination
    workspace: str  # on the remote, as its shell reads it (~/nixos)
    authoritative: str = DEFAULT_CONFIG
    source: str | None = None  # the hosts.toml it came from, None: all defaults
    warnings: list[str] = field(default_factory=list)

    def doc(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "ssh": self.ssh,
            "workspace": self.workspace,
            "authoritative": self.authoritative,
            "config": self.source,
        }


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(base):  # unset, empty or relative: ignored, as for the cache
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "nixin" / FILE


def load(path: Path | None = None) -> dict[str, Any]:
    """The parsed file ({} when there is none); `invalid-hosts-config` when it does not parse."""
    path = path or config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise NixinError("invalid-hosts-config", f"cannot read {path}: {e.strerror}") from None
    try:
        return tomllib.loads(raw.decode("utf-8", errors="replace"))
    except tomllib.TOMLDecodeError as e:
        raise NixinError("invalid-hosts-config", f"{path} is not valid TOML: {e}") from None


def remote(host: str, path: Path | None = None) -> Remote:
    """The ssh destination and the two trees of `host`, from hosts.toml or the defaults."""
    path = path or config_path()
    data = load(path)
    table = data.get(host)
    if table is None:
        return Remote(host, host, DEFAULT_WORKSPACE, source=str(path) if data else None)
    if not isinstance(table, dict):
        raise NixinError("invalid-hosts-config", f"{path}: {host} must be a table ([{host}])")
    for k in KEYS:
        if k in table and not (isinstance(table[k], str) and table[k].strip()):
            raise NixinError(
                "invalid-hosts-config", f"{path}: {host}.{k} must be a non-empty string"
            )
    r = Remote(
        host,
        table.get("ssh", host).strip(),
        table.get("workspace", DEFAULT_WORKSPACE).strip(),
        table.get("authoritative", DEFAULT_CONFIG).strip(),
        source=str(path),
    )
    if r.ssh.startswith("-") or any(c.isspace() for c in r.ssh):
        raise NixinError(
            "invalid-hosts-config", f"{path}: {host}.ssh = {r.ssh!r} is not an ssh destination"
        )
    head = r.workspace.split("/", 1)[0]
    if not (r.workspace.startswith("/") or head == "~" or _TILDE.fullmatch(head)):
        raise NixinError(
            "invalid-hosts-config",
            f"{path}: {host}.workspace = {r.workspace!r}: give an absolute path, ~/… or ~user/…",
        )
    if not r.authoritative.startswith("/"):
        raise NixinError(
            "invalid-hosts-config",
            f"{path}: {host}.authoritative = {r.authoritative!r}: give an absolute path",
        )
    unknown = sorted(set(table) - set(KEYS))
    if unknown:
        r.warnings.append(f"{path}: unknown key(s) in [{host}]: {', '.join(unknown)} (ignored)")
    return r
