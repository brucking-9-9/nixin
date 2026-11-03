"""The lightweight AppStream hint from a search.nixos.org package document.

Since index generation 52 the package documents carry what search.nixos.org's indexer
read from the package's outputs: `package_desktop_entries` (name, comment, icon,
categories, noDisplay of each desktop file), `package_desktop_icons` (icon name → a file
it hosts under /icons/), `package_categories_set` (a keyword field: queryable),
`package_component_ids` (AppStream ids) and `package_screenshots` (the upstream URL and
caption, plus small webp copies it hosts under /screenshots/). They come in the same
request as the rest of the document: no extra request, no AppStream pool.

What the hint can and cannot say (`gui`):

    true   the package ships a desktop entry a launcher lists (search.nixos.org says so,
           or the shipped list names it)
    false  it has no programs at all (a library, data, fonts): nothing to launch
    null   anything else. search.nixos.org has no desktop data for unfree packages and
           wrappers (firefox, steam, …), so "no desktop entries" is not "not an app"
"""

from __future__ import annotations

from typing import Any

from . import curated

SITE = "https://search.nixos.org"
ICON_URL = f"{SITE}/icons/"
SCREENSHOT_URL = f"{SITE}/screenshots/"
ES_FIELDS = [
    "package_desktop_entries",
    "package_desktop_icons",
    "package_categories_set",
    "package_component_ids",
    "package_screenshots",
]


def empty(source: str | None = None) -> dict[str, Any]:
    """The `appstream` object with nothing known yet: every key present."""
    return {
        "source": source,
        "store_path": None,
        "gui": None,
        "id": None,
        "name": None,
        "summary": None,
        "description": None,
        "categories": [],
        "icon": None,
        "screenshots": [],
        "license": None,
        "launchable": None,
        "homepage": None,
        "desktop_ids": [],
        "files": [],
        "notes": [],
    }


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _strs(value: Any) -> list[str]:
    return [str(v) for v in value if v] if isinstance(value, list) else []


def has_fields(src: dict[str, Any]) -> bool:
    """Whether the index this document came from has desktop data at all (generation ≥ 52)."""
    return any(f in src for f in ES_FIELDS)


def _main_entry(entries: list[dict[str, Any]], hints: tuple[str, ...]) -> dict[str, Any] | None:
    listed = [
        e
        for e in entries
        if e.get("type", "Application") == "Application" and not e.get("noDisplay")
    ]
    if not listed:
        return None

    def rank(e: dict[str, Any]) -> tuple[bool, bool]:
        names = {str(e.get("icon") or "").lower(), str(e.get("desktopName") or "").lower()}
        exact = any(h in names for h in hints)
        return (exact, any(h and any(h in n for n in names) for h in hints))

    return max(listed, key=rank)


def _screenshots(value: Any) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(_dicts(value)):
        url = s.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        out.append(
            {
                "url": url,
                "caption": s.get("caption") or None,
                "default": i == 0,
                "thumbnail": SCREENSHOT_URL + s["file"] if s.get("file") else None,
                "large": SCREENSHOT_URL + s["largeFile"] if s.get("largeFile") else None,
            }
        )
    return out


def from_es(src: dict[str, Any]) -> dict[str, Any]:
    """The `appstream` object a search.nixos.org document gives (source `search.nixos.org`,
    or `curated`/null when the document has no desktop data)."""
    attr = str(src.get("package_attr_name") or "")
    pname = str(src.get("package_pname") or "")
    main_program = str(src.get("package_mainProgram") or "")
    hints = tuple(h.lower() for h in (pname, main_program, attr.rsplit(".", 1)[-1]) if h)
    entries = _dicts(src.get("package_desktop_entries"))
    main = _main_entry(entries, hints)
    icons = src.get("package_desktop_icons")
    icons = icons if isinstance(icons, dict) else {}
    ids = _strs(src.get("package_component_ids"))
    shots = _screenshots(src.get("package_screenshots"))
    cats = _strs(main.get("categories")) if main is not None else []
    cats = cats + [c for c in _strs(src.get("package_categories_set")) if c not in cats]
    out = empty("search.nixos.org" if has_fields(src) else None)
    if main is not None or ids or shots or cats:
        icon_name = str(main.get("icon") or "") if main else ""
        icon_name = icon_name or next(iter(icons), "")
        out.update(
            gui=main is not None or None,
            id=ids[0] if ids else None,
            name=(main or {}).get("desktopName") or None,
            summary=(main or {}).get("comment") or (main or {}).get("genericName") or None,
            categories=cats,
            icon={
                "name": icon_name,
                "path": None,
                "url": ICON_URL + str(icons[icon_name]) if icons.get(icon_name) else None,
            }
            if icon_name
            else None,
            screenshots=shots,
        )
    elif curated.of(attr):
        out.update(source="curated", gui=True, categories=curated.of(attr))
    elif not _strs(src.get("package_programs")) and not main_program:
        out.update(gui=False)  # nothing to launch
    if not has_fields(src):
        out["notes"].append(
            "this search.nixos.org index has no desktop data (generation 52 and later do)"
        )
    elif out["source"] == "curated":
        out["notes"].append(
            "search.nixos.org has no desktop data for this package (unfree or a wrapper); "
            "nixin's own list names it"
        )
    return out
