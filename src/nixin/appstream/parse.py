"""AppStream metainfo, desktop entries and icons, read from a package's output tree.

Pure functions over text and directories: no nix, no network. `read_output(path)` is what
the rest of the layer calls; the parsers are separate so the tests can feed them fixtures.

What is read (the paths GNOME Software and nix-software-center read):

    share/metainfo/*.xml, share/appdata/*.xml   AppStream components (the legacy <application>
                                                root and <components> collections too)
    share/applications/*.desktop               freedesktop desktop entries
    share/icons/<theme>/<size>/apps/NAME.*,    the icon a component or desktop entry names,
    share/pixmaps/NAME.*                       SVG first, then the largest PNG

Translations (`xml:lang`, `Name[de]=`) are skipped: nixin shows the untranslated text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# The freedesktop menu spec's main categories, in the order Browse shows them.
MAIN_CATEGORIES = (
    "AudioVideo",
    "Audio",
    "Video",
    "Development",
    "Education",
    "Game",
    "Graphics",
    "Network",
    "Office",
    "Science",
    "Settings",
    "System",
    "Utility",
)
APP_TYPES = ("desktop-application", "desktop", "console-application", "web-application")
SKIP_TYPES = ("addon", "codec", "font", "inputmethod", "localization", "repository", "icon-theme")
ICON_EXTS = (".svg", ".png", ".svgz")
MAX_FILE = 2 * 1024 * 1024  # a metainfo or desktop file bigger than this is not read
MAX_FILES = 64  # per directory: a package shipping hundreds of desktop files (KDE) is capped


# --- metainfo --------------------------------------------------------------------------------


def _untranslated(el: ET.Element, tag: str) -> list[ET.Element]:
    return [c for c in el.findall(tag) if XML_LANG not in c.attrib]


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None


def _first_text(el: ET.Element, tag: str) -> str | None:
    for c in _untranslated(el, tag):
        text = _text(c)
        if text:
            return text
    return None


def description_text(el: ET.Element | None) -> str | None:
    """AppStream's <description> markup as plain text: paragraphs separated by a blank
    line, list items as `• item` lines (`1. item` in an <ol>)."""
    if el is None:
        return None
    blocks: list[str] = []
    for child in el:
        if XML_LANG in child.attrib:
            continue
        tag = child.tag
        if tag == "p":
            text = _text(child)
            if text:
                blocks.append(text)
        elif tag in ("ul", "ol"):
            items = []
            for i, li in enumerate(_untranslated(child, "li"), start=1):
                text = _text(li)
                if text:
                    items.append(f"{i}. {text}" if tag == "ol" else f"• {text}")
            if items:
                blocks.append("\n".join(items))
    if not blocks:  # bare text inside <description> (seen in old appdata)
        text = _text(el)
        return text
    return "\n\n".join(blocks)


def _screenshots(root: ET.Element) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    shots = root.find("screenshots")
    if shots is None:
        return out
    for shot in shots.findall("screenshot"):
        images = _untranslated(shot, "image")
        source = [i for i in images if i.get("type", "source") == "source"]
        url = _text((source or images or [None])[0])  # type: ignore[arg-type]
        if url is None and not images:
            url = _text(shot)  # the oldest form: the URL is the element's text
        if not url or not url.startswith(("http://", "https://")):
            continue
        out.append(
            {
                "url": url,
                "caption": _first_text(shot, "caption"),
                "default": shot.get("type") == "default",
            }
        )
    out.sort(key=lambda s: not s["default"])  # the default one first, the others in order
    return out


def _icon(root: ET.Element) -> dict[str, Any] | None:
    """The first usable <icon>: stock (a theme name) or local (an absolute path); cached
    and remote icons point into a distro pool nixin does not have."""
    for el in root.findall("icon"):
        kind = el.get("type", "stock")
        text = _text(el)
        if not text:
            continue
        if kind == "stock":
            return {"name": text}
        if kind == "local":
            return {"name": Path(text).stem, "local": text}
    return None


def _component(root: ET.Element) -> ET.Element:
    if root.tag == "components":
        comps = root.findall("component")
        if not comps:
            raise ValueError("an empty <components> collection")
        return comps[0]
    if root.tag not in ("component", "application"):
        raise ValueError(f"not an AppStream file (root <{root.tag}>)")
    return root


def parse_metainfo(text: str | bytes) -> dict[str, Any]:
    """One AppStream file → a dict; ValueError when it is not AppStream XML."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"not well-formed XML: {exc}") from exc
    comp = _component(root)
    kind = comp.get("type") or ("desktop-application" if comp.tag == "application" else None)
    cid = _first_text(comp, "id")
    launchable = None
    for el in comp.findall("launchable"):
        if el.get("type") == "desktop-id" and _text(el):
            launchable = _text(el)
            break
    if launchable is None and cid and cid.endswith(".desktop"):
        launchable = cid  # legacy ids name the desktop file
    categories = [
        t for t in (_text(c) for c in comp.findall("categories/category")) if t is not None
    ]
    homepage = None
    for el in comp.findall("url"):
        if el.get("type") == "homepage" and _text(el):
            homepage = _text(el)
            break
    developer = _first_text(comp, "developer/name") or _first_text(comp, "developer_name")
    return {
        "id": cid.removesuffix(".desktop") if cid else None,
        "type": kind,
        "name": _first_text(comp, "name"),
        "summary": _first_text(comp, "summary"),
        "description": description_text(next(iter(_untranslated(comp, "description")), None)),
        "categories": categories,
        "icon": _icon(comp),
        "screenshots": _screenshots(comp),
        "license": _first_text(comp, "project_license") or _first_text(comp, "licence"),
        "launchable": launchable,
        "homepage": homepage,
        "developer": developer,
    }


# --- desktop entries -------------------------------------------------------------------------

_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\", ";": ";"}


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), value)


def _split_list(value: str) -> list[str]:
    parts = re.split(r"(?<!\\);", value)
    return [p for p in (_unescape(x).strip() for x in parts) if p]


def parse_desktop(text: str) -> dict[str, Any]:
    """The [Desktop Entry] group of a desktop file; ValueError when there is none."""
    entries: dict[str, str] = {}
    seen = inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            inside = line == "[Desktop Entry]" and not seen  # only the first such group
            seen = seen or inside
            continue
        if not inside or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if "[" in key:  # Name[de]=…: a translation
            continue
        entries.setdefault(key, value.strip())
    if not seen:
        raise ValueError("no [Desktop Entry] group")

    def flag(key: str) -> bool:
        return entries.get(key, "").lower() == "true"

    return {
        "type": entries.get("Type") or None,
        "name": _unescape(entries["Name"]) if entries.get("Name") else None,
        "generic_name": _unescape(entries["GenericName"]) if entries.get("GenericName") else None,
        "comment": _unescape(entries["Comment"]) if entries.get("Comment") else None,
        "icon": _unescape(entries["Icon"]) if entries.get("Icon") else None,
        "categories": _split_list(entries.get("Categories", "")),
        "keywords": _split_list(entries.get("Keywords", "")),
        "no_display": flag("NoDisplay"),
        "hidden": flag("Hidden"),
        "exec": entries.get("Exec") or None,
    }


def shown(entry: dict[str, Any]) -> bool:
    """A desktop entry a launcher would list: an Application, not NoDisplay or Hidden."""
    return entry.get("type") == "Application" and not entry["no_display"] and not entry["hidden"]


def main_categories(categories: list[str]) -> list[str]:
    """The main categories among `categories`, in MAIN_CATEGORIES order."""
    have = set(categories)
    return [c for c in MAIN_CATEGORIES if c in have]


def merge_categories(*lists: list[str]) -> list[str]:
    seen: list[str] = []
    for xs in lists:
        for c in xs:
            if c and c not in seen:
                seen.append(c)
    return seen


# --- icons -----------------------------------------------------------------------------------


def _size(dirname: str) -> int:
    if dirname == "scalable":
        return 10_000
    m = re.match(r"(\d+)x\d+(?:@(\d+))?$", dirname)
    if not m:
        return 0
    return int(m.group(1)) * int(m.group(2) or 1)


def resolve_icon(root: Path, name: str | None) -> str | None:
    """The file for icon `name` in an output: an absolute path as is (if it exists), else
    share/icons/<theme>/<size>/<context>/NAME.{svg,png} (hicolor first, scalable then the
    largest size), else share/pixmaps/NAME.*. None when the output has no such icon."""
    if not name:
        return None
    if name.startswith("/"):
        return name if Path(name).is_file() else None
    stem = name
    for ext in ICON_EXTS:
        stem = stem.removesuffix(ext)
    best: tuple[tuple[int, int, int], Path] | None = None
    icons = root / "share" / "icons"
    if icons.is_dir():
        for theme in sorted(icons.iterdir()):
            if not theme.is_dir():
                continue
            for size in theme.iterdir():
                for ctx in size.iterdir() if size.is_dir() else []:
                    for ext in ICON_EXTS:
                        f = ctx / f"{stem}{ext}"
                        if f.is_file():
                            # hicolor first, then size, then svg over png
                            rank = (theme.name == "hicolor", _size(size.name), ext == ".svg")
                            if best is None or rank > best[0]:
                                best = (rank, f)
    if best is not None:
        return str(best[1])
    pixmaps = root / "share" / "pixmaps"
    for ext in (*ICON_EXTS, ".xpm"):
        f = pixmaps / f"{stem}{ext}"
        if f.is_file():
            return str(f)
    return None


# --- one output ------------------------------------------------------------------------------


def _files(root: Path, sub: str, pattern: str) -> list[Path]:
    d = root / sub
    if not d.is_dir():
        return []
    files = sorted(p for p in d.glob(pattern) if p.is_file())
    return files[:MAX_FILES]


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _pick_component(comps: list[dict[str, Any]], hints: tuple[str, ...]) -> dict[str, Any] | None:
    usable = [c for c in comps if c.get("type") not in SKIP_TYPES]
    if not usable:
        return None

    def rank(c: dict[str, Any]) -> tuple[bool, bool, bool]:
        ident = (c.get("id") or "").lower()
        hinted = any(h and (ident == h or ident.endswith("." + h)) for h in hints)
        return (c.get("type") in APP_TYPES, bool(c.get("launchable")), hinted)

    return max(usable, key=rank)  # max keeps the first of equals: files are sorted


def _pick_desktop(
    entries: dict[str, dict[str, Any]], launchable: str | None, hints: tuple[str, ...]
) -> str | None:
    if launchable and launchable in entries:
        return launchable
    listed = [k for k, e in entries.items() if shown(e)]
    if not listed:
        return None

    def rank(k: str) -> tuple[bool, bool, int]:
        stem = k.removesuffix(".desktop").lower()
        exact = stem in hints or stem.rsplit(".", 1)[-1] in hints
        return (exact, any(h and h in stem for h in hints), -len(k))

    return max(listed, key=rank)


def read_output(root: str | Path, hints: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """The AppStream view of one output directory, or None when it ships neither a
    component nor a desktop entry. `hints` (pname, mainProgram) pick between several
    desktop entries or components (`kitty.desktop` over `kitty-open.desktop`)."""
    root = Path(root)
    hints = tuple(h.lower() for h in hints if h)
    comps: list[dict[str, Any]] = []
    files: list[str] = []
    for sub in ("share/metainfo", "share/appdata"):
        for f in _files(root, sub, "*.xml"):
            text = _read(f)
            if text is None:
                continue
            try:
                comp = parse_metainfo(text)
            except ValueError:
                continue
            comp["file"] = f"{sub}/{f.name}"
            comps.append(comp)
    entries: dict[str, dict[str, Any]] = {}
    for f in _files(root, "share/applications", "*.desktop"):
        text = _read(f)
        if text is None:
            continue
        try:
            entries[f.name] = parse_desktop(text)
        except ValueError:
            continue
    comp = _pick_component(comps, hints)
    desktop_id = _pick_desktop(entries, comp.get("launchable") if comp else None, hints)
    desk = entries.get(desktop_id) if desktop_id else None
    if comp is None and desk is None:
        return None
    if comp is not None:
        files.append(comp["file"])
    if desktop_id:
        files.append(f"share/applications/{desktop_id}")
    comp = comp or {}
    desk = desk or {}
    icon_name = (desk.get("icon") or (comp.get("icon") or {}).get("name")) or None
    icon_path = resolve_icon(root, icon_name)
    if icon_path is None and comp.get("icon", {}) and comp["icon"].get("local"):
        local = comp["icon"]["local"]
        icon_path = local if Path(local).is_file() else None
    return {
        "id": comp.get("id") or (desktop_id.removesuffix(".desktop") if desktop_id else None),
        "type": comp.get("type") or ("desktop-application" if desk else None),
        "name": comp.get("name") or desk.get("name"),
        "summary": comp.get("summary") or desk.get("comment") or desk.get("generic_name"),
        "description": comp.get("description"),
        "categories": merge_categories(comp.get("categories") or [], desk.get("categories") or []),
        "icon": {"name": icon_name, "path": icon_path, "url": None} if icon_name else None,
        "screenshots": comp.get("screenshots") or [],
        "license": comp.get("license"),
        "launchable": comp.get("launchable") or desktop_id,
        "homepage": comp.get("homepage"),
        "developer": comp.get("developer"),
        "desktop_ids": [k for k, e in entries.items() if shown(e)],
        "gui": (bool(desk) and shown(desk))
        or comp.get("type") in ("desktop-application", "desktop"),
        "files": files,
    }
