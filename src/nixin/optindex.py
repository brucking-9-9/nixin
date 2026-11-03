"""The option indexes the nixin modules build with the system (lib/options-index.nix):

    /etc/nixin/options-nixos.json                  nixosModules.default: the system's options
    $XDG_CONFIG_HOME/nixin/options-home.json       homeModules.default: this user's HM options

Each is one JSON document, `{"format": "nixin-options/1", "scope", "host", "user",
"nixpkgs", "home_manager", "roots", "count", "options": [...]}`, made when the
generation was built, so it holds exactly the options of that generation's module set,
the config's own included. `search --kind opt|hm` and `info` answer from it instead of
asking upstream (discovery), and `where` takes the option's documentation from it and
skips the scopes it says do not have the option (truth). `status` reports both.

An index describes this machine's running generation (`fresh`: the file under /etc or
~/.config is the one the running system's, or the active Home Manager generation's,
etc links to), so it is used only for the host it was built for (`applies`), and for
Home Manager only for the user running nixin. `NIXIN_INDEX_NIXOS` and `NIXIN_INDEX_HOME`
name other files; `NIXIN_NO_INDEX=1` ignores both (as `--no-index` does for one call).

No nix, no network: the files are read as they are. Stdlib only (truth imports this).
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE = "index"
FORMAT = "nixin-options/1"
NIXOS_FILE = "/etc/nixin/options-nixos.json"
HOME_FILE = "nixin/options-home.json"  # under $XDG_CONFIG_HOME
ENV_NIXOS = "NIXIN_INDEX_NIXOS"
ENV_HOME = "NIXIN_INDEX_HOME"
ENV_OFF = "NIXIN_NO_INDEX"
SCOPES = ("nixos", "home")
PEEK_BYTES = 1 << 16

# The active Home Manager generation (its activation script keeps this gcroot) and the
# profile whose home-manager-N-link names its number. Module constants for the tests.
HM_CURRENT = "home-manager/gcroots/current-home"  # under $XDG_STATE_HOME
HM_PROFILES = "nix/profiles"  # under $XDG_STATE_HOME
_HM_GEN = re.compile(r"^home-manager-(\d+)-link$")

GITHUB = {
    "nixpkgs": "https://github.com/NixOS/nixpkgs/blob/{rev}/{file}",
    "home-manager": "https://github.com/nix-community/home-manager/blob/{rev}/{file}",
}


def disabled() -> bool:
    return os.environ.get(ENV_OFF, "") not in ("", "0")


def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else Path.home() / default


def path_for(scope: str) -> Path:
    if scope == "nixos":
        return Path(os.environ.get(ENV_NIXOS) or NIXOS_FILE)
    return Path(os.environ.get(ENV_HOME) or _xdg("XDG_CONFIG_HOME", ".config") / HOME_FILE)


def _realpath(p: str | Path) -> str | None:
    return os.path.realpath(p) if os.path.lexists(p) else None


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- which generation an index belongs to ----------------------------------------------


def _system_generation(target: str) -> dict[str, Any]:
    """The system generation whose etc carries this index file; the running one first."""
    from .apply import system

    rel = "etc/nixin/options-nixos.json"
    running = system.realpath(system.CURRENT)
    gens = system.generations()
    fresh = bool(running) and _realpath(os.path.join(running, rel)) == target
    path = running if fresh else None
    if path is None:
        path = next(
            (p for _n, p in reversed(gens) if _realpath(os.path.join(p, rel)) == target), None
        )
    number = system.pick_generation(gens, path) if path else None
    return {
        "kind": "system",
        "number": number,
        "path": path,
        "date": system.link_time(number) if number is not None else None,
        "fresh": fresh,
    }


def _home_generation(link: Path, target: str) -> dict[str, Any]:
    """The Home Manager generation whose home-files carry this index file."""
    state = _xdg("XDG_STATE_HOME", ".local/state")
    current = _realpath(state / HM_CURRENT)
    out: dict[str, Any] = {
        "kind": "home-manager",
        "number": None,
        "path": current,
        "date": None,
        "fresh": None,
    }
    if current is None:
        return out  # no Home Manager activation on record (or not this layout)
    try:
        rel = os.path.relpath(link, Path.home())
    except ValueError:
        rel = None
    if rel and not rel.startswith(".."):
        out["fresh"] = _realpath(os.path.join(current, "home-files", rel)) == target
    profiles = state / HM_PROFILES
    try:
        names = os.listdir(profiles)
    except OSError:
        names = []
    hits = []
    for name in names:
        m = _HM_GEN.match(name)
        if m and _realpath(profiles / name) == current:
            hits.append((int(m.group(1)), profiles / name))
    if hits:
        n, p = max(hits)
        out["number"] = n
        out["date"] = _iso(os.lstat(p).st_mtime)
    else:
        try:
            out["date"] = _iso(os.lstat(state / HM_CURRENT).st_mtime)
        except OSError:
            pass
    return out


# --- reading -------------------------------------------------------------------------------


def _check(doc: Any, path: Path) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} document")
    return doc


def peek(path: Path) -> dict[str, Any]:
    """The header (everything but `options`) without parsing the list: builtins.toJSON
    writes keys in order, so the document is `{…header…,"options":[…],…header…}` and both
    ends are small. Falls back to reading it whole."""
    try:
        with open(path, "rb") as f:
            head = f.read(PEEK_BYTES)
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - PEEK_BYTES))
            tail = f.read()
        i = head.index(b',"options":[')
        j = tail.rindex(b'],"roots":')
        doc = json.loads(head[:i] + b"," + tail[j + 2 :])
    except (ValueError, OSError):
        doc = json.loads(Path(path).read_bytes())
        doc.pop("options", None)
    return _check(doc, path)


@dataclass
class Index:
    scope: str  # "nixos" | "home"
    path: str  # the file as found (the /etc or ~/.config link)
    target: str  # the store file it resolves to
    header: dict[str, Any]
    generation: dict[str, Any]
    _options: list[dict[str, Any]] | None = field(default=None, repr=False)
    _names: dict[str, dict] | None = field(default=None, repr=False)
    _prefixes: set[str] | None = field(default=None, repr=False)

    # -- the document ------------------------------------------------------------------

    @property
    def options(self) -> list[dict[str, Any]]:
        if self._options is None:
            doc = _check(json.loads(Path(self.target).read_bytes()), Path(self.path))
            self._options = list(doc.get("options") or [])
        return self._options

    @property
    def host(self) -> str | None:
        return self.header.get("host")

    @property
    def user(self) -> str | None:
        return self.header.get("user")

    @property
    def rev(self) -> str | None:
        return (self.header.get("nixpkgs") or {}).get("revision")

    @property
    def kind(self) -> str:
        """discovery's kind for these options"""
        return "opt" if self.scope == "nixos" else "hm"

    def applies(self, host: str, user: str | None = None) -> bool:
        """Built for this host (and HM user)? A standalone HM index names no host: then
        only this machine's own name matches."""
        built_for = self.host or hostname()
        if built_for != host:
            return False
        return self.scope == "nixos" or user is None or self.user == user

    def doc(self) -> dict[str, Any]:
        """The `index` block: where it is, which generation, what it was built from."""
        return {
            "scope": self.scope,
            "path": self.path,
            "store_path": self.target,
            "count": self.header.get("count"),
            "host": self.host,
            "user": self.user,
            "nixpkgs": self.header.get("nixpkgs"),
            "home_manager": self.header.get("home_manager"),
            "generation": self.generation,
            "fresh": self.generation.get("fresh"),
        }

    # -- lookups -------------------------------------------------------------------------

    def _by_name(self) -> dict[str, dict[str, Any]]:
        if self._names is None:
            self._names = {o["name"]: o for o in self.options}
        return self._names

    def lookup(self, name: str) -> dict[str, Any] | None:
        return self._by_name().get(name)

    def covers(self, path: list[str]) -> bool:
        """An option at `path`, above it (an attrsOf/submodule option it is inside), or
        under it (a prefix)."""
        names = self._by_name()
        if any(".".join(path[:i]) in names for i in range(len(path), 0, -1)):
            return True
        if self._prefixes is None:
            pre: set[str] = set()
            for n in names:
                parts = n.split(".")
                pre.update(".".join(parts[:i]) for i in range(1, len(parts)))
            self._prefixes = pre
        return ".".join(path) in self._prefixes

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Options matching `query`, best first: the Home Manager source's scoring (exact
        path, prefix, segment, substring, description), then the shorter path. Internal
        and hidden options are left out, as on search.nixos.org."""
        from .discovery.home_manager import score

        scored = []
        for o in self.options:
            if o.get("internal") or o.get("visible") is False:
                continue
            s = score(o["name"], o.get("description"), query)
            if s:
                scored.append((-s, len(o["loc"]), o["name"].casefold(), o))
        scored.sort(key=lambda t: t[:3])  # equal scores: the shallower path first
        return [t[3] for t in scored[:limit]]

    # -- records -------------------------------------------------------------------------

    def url(self, decl: dict[str, Any]) -> str | None:
        """A declaration on GitHub at the revision the index was built from."""
        tmpl = GITHUB.get(decl.get("source") or "")
        if tmpl is None or not decl.get("file"):
            return None
        if decl["source"] == "nixpkgs":
            rev = self.rev
        else:
            rev = (self.header.get("home_manager") or {}).get("revision")
        u = tmpl.format(rev=rev or "master", file=decl["file"])
        return u + (f"#L{decl['line']}" if decl.get("line") else "")

    def docs(self, o: dict[str, Any]) -> dict[str, Any]:
        """An option's documentation as the index has it (`where`'s `docs`)."""
        decls = o.get("declarations") or []
        return {
            "source": SOURCE,
            "type": o.get("type"),
            "description": (o.get("description") or "").strip() or None,
            "default": o.get("default"),
            "example": o.get("example"),
            "read_only": bool(o.get("readOnly")),
            "declarations": [{**d, "url": self.url(d)} for d in decls],
            "in_config": any(d.get("source") == "config" for d in decls),
        }

    def record(self, o: dict[str, Any]) -> dict[str, Any]:
        """A discovery result (the fields of search.nixos.org's and Home Manager's option
        records, plus where each declaration is and which generation said so)."""
        d = self.docs(o)
        return {
            "kind": self.kind,
            "source": SOURCE,
            "channel": None,
            "rev": self.rev,
            "name": o["name"],
            "type": d["type"],
            "default": d["default"],
            "example": d["example"],
            "description": d["description"],
            "declarations": [x["file"] for x in d["declarations"]],
            "declaration_urls": [x["url"] for x in d["declarations"] if x["url"]],
            "read_only": d["read_only"],
            "locations": [
                {"source": x.get("source"), "file": x.get("file"), "line": x.get("line")}
                for x in d["declarations"]
            ],
            "in_config": d["in_config"],
            "scope": self.scope,
            "user": self.user,
            "generation": self.generation,
        }


_LOADED: dict[tuple[str, str], Index] = {}  # by (scope, store file): the UIs ask often


def load(scope: str) -> Index | None:
    """The index of `scope` ("nixos" or "home") on this machine, or None (absent,
    unreadable, or NIXIN_NO_INDEX). The parsed list stays cached for the process: a store
    file never changes."""
    if disabled():
        return None
    link = path_for(scope)
    target = _realpath(link)
    if target is None or not os.path.isfile(target):
        return None
    hit = _LOADED.get((scope, target))
    if hit is not None:
        return hit
    try:
        header = peek(Path(target))
    except (OSError, ValueError):
        return None
    if header.get("scope") != scope:
        return None
    gen = _system_generation(target) if scope == "nixos" else _home_generation(link, target)
    idx = Index(scope, str(link), target, header, gen)
    _LOADED[(scope, target)] = idx
    return idx


def usable(scope: str, host: str, user: str | None = None) -> Index | None:
    """The index for this host (and HM user), if this machine has one built for it."""
    idx = load(scope)
    if idx is None or not idx.applies(host, user):
        return None
    return idx


def hostname() -> str:
    return socket.gethostname()


def current_user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return os.environ.get("USER", "")


# --- the verbs' parts ----------------------------------------------------------------------


def status(host: str) -> dict[str, Any]:
    """`status.indexes`: for each scope, whether the file is there, which generation it
    belongs to, whether that is the running one, and whether it describes `host` (and,
    for Home Manager, the user running nixin)."""
    out: dict[str, Any] = {}
    user = current_user()
    for scope in SCOPES:
        entry: dict[str, Any] = {"path": str(path_for(scope)), "present": False}
        if scope == "home":
            entry["user"] = user
        if disabled():
            entry["disabled"] = True
        idx = load(scope)
        if idx is not None:
            entry.update(idx.doc(), present=True)
            entry["applies"] = idx.applies(host, user if scope == "home" else None)
        out[scope] = entry
    return out


def annotate_where(doc: dict[str, Any], host: str) -> None:
    """`where OPTION`: each option of a scope this machine has an index for gets `docs`
    (type, the whole description, default, example, declarations with lines and links)
    from that index; `doc["index"]` lists the indexes used."""
    used: dict[tuple[str, str | None], Index] = {}
    for s in doc.get("scopes") or []:
        idx = usable(s["scope"], host, s.get("user"))
        if idx is None:
            continue
        for o in s.get("options") or []:
            e = idx.lookup(o.get("option") or o.get("name") or "")
            if e is not None:
                o["docs"] = idx.docs(e)
                used[(idx.scope, idx.user)] = idx
    doc["index"] = [i.doc() for i in used.values()]


def prune_scopes(
    host: str, scopes: list[tuple[str, str | None]], path: list[str]
) -> dict[tuple[str, str | None], str]:
    """Scopes `where` need not evaluate: when an index of one scope has the option (or
    options under the path), the scopes whose own index does not are skipped. A scope
    without an index for this host, or whose index is not the running generation's, is
    always evaluated. {(scope, user): why}."""
    have: dict[tuple[str, str | None], tuple[Index, bool]] = {}
    for scope, user in scopes:
        idx = usable("nixos" if scope == "nixos" else "home", host, user)
        if idx is not None and idx.generation.get("fresh"):
            have[(scope, user)] = (idx, idx.covers(path))
    if not any(found for _idx, found in have.values()):
        return {}
    name = ".".join(path)
    out = {}
    for key, (idx, found) in have.items():
        if not found:
            n = idx.generation.get("number")
            gen = f"generation {n}" if n is not None else "the running generation"
            out[key] = f"{idx.scope} index of {gen} ({idx.path}) has no option {name!r}"
    return out
