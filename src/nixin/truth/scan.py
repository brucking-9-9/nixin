"""Text heuristics over the config's .nix files.

The module system reports *which file* defines an option (definitionsWithLocations) but
not the line, and nothing at all for a package inside a list. These scanners recover the
line: a brace-tracking pass that follows `a.b = { c = ...; }` nesting for option paths,
and a ranked token grep for package names. Results are marked `heuristic` in the output
and are never used to edit a file blindly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_COMPONENT = r'(?:[A-Za-z_][\w\'-]*|"[^"\n]*")'
_ASSIGN = re.compile(rf"^\s*({_COMPONENT}(?:\.{_COMPONENT})*)\s*=(?!=)\s*(.*)$")
_INNER_ASSIGN = re.compile(rf"(?<![\w.\"'])({_COMPONENT}(?:\.{_COMPONENT})*)\s*=(?!=)")
_COMMENT = re.compile(r"(^|\s)#.*$")
_FUNCTION_HEADER = re.compile(r"^\s*(\{[^{}]*\}\s*(@\w+)?\s*:|\w+\s*@\s*\{[^{}]*\}\s*:|\w+\s*:)\s*")
_SCOPE_OPENERS = (
    "{",
    "lib.mkIf",
    "mkIf",
    "lib.mkMerge",
    "mkMerge",
    "lib.mkForce",
    "mkForce",
    "lib.mkDefault",
    "mkDefault",
    "lib.mkOverride",
    "mkOverride",
    "rec {",
)


def _split_path(s: str) -> list[str]:
    return [c.strip('"') for c in re.findall(_COMPONENT, s)]


def _strip_strings(s: str) -> str:
    """Blank out "..." contents so braces and `=` inside strings do not count."""
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', s)


@dataclass
class Hit:
    line: int  # 1-based
    text: str
    score: int  # 3 exact path, 2 a suffix or an extension of it, 1 leaf name only
    path: list[str]

    def doc(self) -> dict:
        return {"line": self.line, "score": self.score, "text": self.text.strip()}


def find_option_lines(text: str, target: list[str]) -> list[Hit]:
    """Lines that (probably) set option `target` in a Nix file, best first.

    Tracks `name = {` scopes by brace depth; a candidate's full path is the scope path
    plus its own. `imports`/`with`/function headers add depth without names, which is
    what makes the suffix match (score 2) necessary.
    """
    hits: list[Hit] = []
    scopes: list[tuple[int, list[str]]] = []  # (depth at which the scope opened, path)
    depth = 0
    in_multiline = False
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw
        # '' multi-line strings: skip their insides (toggle on an odd count of '')
        quotes = len(re.findall(r"''(?!\$|\\|')", line))
        if in_multiline:
            if quotes % 2 == 1:
                in_multiline = False
            continue
        if quotes % 2 == 1:
            in_multiline = True
        line = _COMMENT.sub("", line)
        clean = _strip_strings(line)
        m = _ASSIGN.match(clean)
        scope_path = [c for _, p in scopes for c in p]
        if m:
            path = _split_path(m.group(1))
            # `outputs = { self, ... }@inputs: {` and `x = args: {`: the body is a function
            # result, not a named attrset scope
            is_function = _FUNCTION_HEADER.match(m.group(2)) is not None
            rhs = _FUNCTION_HEADER.sub("", m.group(2))
            full = scope_path + path
            hits.extend(_score(no, raw, full, target))
            opens = rhs.count("{") - rhs.count("}")
            if opens > 0 and not is_function and rhs.lstrip().startswith(_SCOPE_OPENERS):
                scopes.append((depth + 1, path))
            elif "{" in rhs and opens == 0:
                # one-line attrset: `x = { enable = true; };`
                for im in _INNER_ASSIGN.finditer(rhs):
                    inner = _split_path(im.group(1))
                    hits.extend(_score(no, raw, full + inner, target))
        depth += clean.count("{") - clean.count("}")
        while scopes and depth < scopes[-1][0]:
            scopes.pop()
    hits.sort(key=lambda h: (-h.score, h.line))
    return hits


def _score(no: int, raw: str, full: list[str], target: list[str]) -> list[Hit]:
    if full == target:
        return [Hit(no, raw, 3, full)]
    if len(full) > len(target) and full[: len(target)] == target:
        return [Hit(no, raw, 2, full)]  # `settings.PermitRootLogin = …` defines `settings`
    if len(full) < len(target) and target[-len(full) :] == full:
        return [Hit(no, raw, 2, full)]
    if full and full[-1] == target[-1] and len(full) == 1:
        return [Hit(no, raw, 1, full)]
    return []


def best_option_line(text: str, target: list[str]) -> Hit | None:
    hits = find_option_lines(text, target)
    return hits[0] if hits and hits[0].score >= 2 else None


# --- repo-wide grep -------------------------------------------------------------------


def nix_files(root: Path) -> list[Path]:
    """Tracked .nix files (what the flake sees); falls back to a walk without git."""
    if (root / ".git").exists() and shutil.which("git"):
        proc = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "-z", "--", "*.nix"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return [root / f for f in proc.stdout.split("\0") if f]
    return sorted(p for p in root.rglob("*.nix") if ".git" not in p.parts)


@dataclass
class Mention:
    file: str  # repo-relative
    line: int
    text: str
    score: int

    def doc(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "score": self.score,
            "text": self.text.strip(),
        }


def grep_package(root: Path, name: str, limit: int = 20) -> list[Mention]:
    """Lines mentioning a package attribute name, ranked: pkgs.NAME and bare list items
    first, programs.NAME/services.NAME next, other mentions last, comments lowest."""
    esc = re.escape(name)
    word = re.compile(rf"(?<![\w-]){esc}(?![\w-])")
    pkgs_ref = re.compile(rf"(?<![\w-])(pkgs|inputs\.\w+\.packages\.[\w-]+)\.{esc}(?![\w-])")
    bare = re.compile(rf"^\s*{esc}\s*(#.*)?$")
    enable = re.compile(rf"(programs|services)\.{esc}(\.|\s*=)|^\s*{esc}\s*=")
    out: list[Mention] = []
    for f in nix_files(root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(root))
        for no, line in enumerate(text.splitlines(), 1):
            if not word.search(line):
                continue
            if line.lstrip().startswith("#"):
                score = 0
            elif pkgs_ref.search(line) or bare.match(line):
                score = 3
            elif enable.search(line):
                score = 2
            else:
                score = 1
            out.append(Mention(rel, no, line, score))
    out.sort(key=lambda m: (-m.score, m.file, m.line))
    return out[:limit]


def grep_option(root: Path, target: list[str], limit: int = 10) -> list[Mention]:
    """Best option-path hits across every tracked .nix file (for inline modules and
    `where` on an option nix attributes to no readable file)."""
    out: list[Mention] = []
    for f in nix_files(root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(root))
        for h in find_option_lines(text, target):
            if h.score >= 2:
                out.append(Mention(rel, h.line, h.text, h.score))
    out.sort(key=lambda m: (-m.score, m.file, m.line))
    return out[:limit]
