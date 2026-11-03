"""The Browse categories and a small shipped list of well-known applications per category.

Why a shipped list at all: search.nixos.org's package index carries desktop entries and
their categories (`package_categories_set`, index generation 52 and later), which makes a
category query possible, but it only has them for packages whose outputs its indexer
read. Unfree packages and wrappers come back empty: firefox, chromium, thunderbird,
steam, lutris, heroic, discord, spotify, vscode, obsidian, zotero, bottles, kicad and
others (checked against unstable@0bb2eedc on 2026-11-02). Those are exactly the
applications people look for first, so each category starts with a few named here.
Older index generations have no desktop data at all; then this list and the installed
applications are all Browse has.

The attribute names were checked against search.nixos.org unstable on 2026-11-02; a name
that disappears from nixpkgs simply drops out of Browse (it is looked up, not trusted).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str  # what `browse CATEGORY` takes (case-insensitive), a freedesktop main category
    label: str
    matches: tuple[str, ...]  # the desktop-entry categories that put an app here
    aliases: tuple[str, ...] = ()


CATEGORIES: tuple[Category, ...] = (
    Category("AudioVideo", "Audio & Video", ("AudioVideo", "Audio", "Video"), ("audio", "video")),
    Category("Development", "Development", ("Development",), ("dev",)),
    Category("Education", "Education", ("Education",)),
    Category("Game", "Games", ("Game",), ("games",)),
    Category("Graphics", "Graphics", ("Graphics",)),
    Category("Network", "Internet", ("Network",), ("internet",)),
    Category("Office", "Office", ("Office",)),
    Category("Science", "Science", ("Science",)),
    Category("Settings", "Settings", ("Settings",)),
    Category("System", "System", ("System",)),
    Category("Utility", "Utilities", ("Utility",), ("utilities",)),
)

WELL_KNOWN: dict[str, tuple[str, ...]] = {
    "AudioVideo": (
        "vlc",
        "mpv",
        "audacity",
        "obs-studio",
        "kdePackages.kdenlive",
        "handbrake",
        "ardour",
        "strawberry",
        "spotify",
        "shotcut",
    ),
    "Development": (
        "vscodium",
        "vscode",
        "zed-editor",
        "kdePackages.kate",
        "meld",
        "gnome-builder",
        "dbeaver-bin",
        "gitg",
        "sublime4",
        "android-studio",
    ),
    "Education": (
        "anki",
        "gcompris",
        "stellarium",
        "geogebra6",
        "kdePackages.kalzium",
        "kdePackages.marble",
    ),
    "Game": (
        "steam",
        "lutris",
        "heroic",
        "prismlauncher",
        "supertuxkart",
        "luanti",
        "xonotic",
        "wesnoth",
        "zeroad",
        "openttd",
    ),
    "Graphics": (
        "gimp",
        "inkscape",
        "krita",
        "blender",
        "darktable",
        "kdePackages.gwenview",
        "shotwell",
        "rawtherapee",
        "drawio",
        "pinta",
    ),
    "Network": (
        "firefox",
        "chromium",
        "thunderbird",
        "telegram-desktop",
        "discord",
        "signal-desktop",
        "element-desktop",
        "transmission_4-gtk",
        "qbittorrent",
        "filezilla",
        "remmina",
    ),
    "Office": (
        "libreoffice",
        "onlyoffice-desktopeditors",
        "obsidian",
        "zotero",
        "calibre",
        "kdePackages.okular",
        "evince",
        "gnumeric",
        "abiword",
        "xournalpp",
    ),
    "Science": ("qgis", "kicad", "freecad", "octave", "avogadro2", "rstudio"),
    "Settings": (
        "pavucontrol",
        "networkmanagerapplet",
        "blueman",
        "nwg-look",
        "gnome-tweaks",
        "arandr",
        "lxappearance",
        "piper",
    ),
    "System": (
        "kitty",
        "alacritty",
        "wezterm",
        "gnome-console",
        "kdePackages.konsole",
        "gparted",
        "baobab",
        "mission-center",
        "btop",
        "kdePackages.partitionmanager",
    ),
    "Utility": (
        "keepassxc",
        "flameshot",
        "kdePackages.ark",
        "file-roller",
        "gnome-calculator",
        "kdePackages.kcalc",
        "localsend",
        "bottles",
        "copyq",
        "bitwarden-desktop",
    ),
}


def category(name: str) -> Category | None:
    """`browse CATEGORY`: a key, a label or an alias, any case."""
    low = name.strip().lower()
    for c in CATEGORIES:
        if low in (c.key.lower(), c.label.lower(), *c.aliases):
            return c
    return None


def of(attr: str) -> list[str]:
    """The categories the shipped list puts `attr` in (usually one)."""
    return [key for key, attrs in WELL_KNOWN.items() if attr in attrs]


def for_categories(categories: list[str]) -> list[Category]:
    """The Browse categories a desktop entry's `Categories=` puts an app in."""
    have = set(categories)
    return [c for c in CATEGORIES if have.intersection(c.matches)]
