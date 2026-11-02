"""The palette: the one place the TUI's colours live.

The anchor is the person's terminal palette (the same in every project): background
#120B10, cyan #00FFC8 for the accent and the selection, pink #FF0055 for errors and
destructive actions, purple #D57BFF for secondary things and option names, light text.

`surface` is the background itself, so widgets that paint `$surface` (inputs, tables) do
not draw solid blocks on top of the terminal; structure comes from thin lines and colour.
The stylesheet (nixin.tcss) only uses Textual's standard variables ($accent, $error,
$secondary, $text-muted, $border, ...), so it still works if someone switches theme from
the command palette.

`STYLES` maps the view-model's semantic styles (view.py) to markup colours; the GUI maps
the same names to its own palette.
"""

from __future__ import annotations

from textual.theme import Theme

BACKGROUND = "#120B10"
CYAN = "#00FFC8"
PINK = "#FF0055"
PURPLE = "#D57BFF"
TEXT = "#EDE4EA"
MUTED = "#9A8794"  # dim text: halfway between TEXT and the background, still readable
LINE = "#4A3444"  # borders and rules, low contrast on purpose

THEME = Theme(
    name="nixin",
    primary=CYAN,
    secondary=PURPLE,
    accent=CYAN,
    error=PINK,
    warning=PURPLE,
    success=CYAN,
    foreground=TEXT,
    background=BACKGROUND,
    surface=BACKGROUND,
    panel="#1D131A",
    boost="#24182040",
    dark=True,
    variables={
        "text-muted": MUTED,
        "border": CYAN,
        "border-blurred": LINE,
        "block-cursor-background": CYAN,
        "block-cursor-foreground": BACKGROUND,
        "block-cursor-text-style": "bold",
        "block-cursor-blurred-background": "#00FFC833",
        "block-cursor-blurred-foreground": TEXT,
        "block-hover-background": "#00FFC81A",
        "input-cursor-background": CYAN,
        "input-cursor-foreground": BACKGROUND,
        "input-selection-background": "#00FFC84D",
        "footer-background": BACKGROUND,
        "footer-key-foreground": CYAN,
        "footer-description-foreground": MUTED,
        "scrollbar": LINE,
        "scrollbar-hover": PURPLE,
        "scrollbar-active": CYAN,
        "scrollbar-background": BACKGROUND,
        "scrollbar-background-hover": BACKGROUND,
        "scrollbar-background-active": BACKGROUND,
        "scrollbar-corner-color": BACKGROUND,
        "link-color": CYAN,
        "link-background-hover": "#00FFC833",
    },
)

STYLES: dict[str, str] = {
    "": "",
    "dim": "$text-muted",
    "accent": "$accent",
    "option": "$secondary",
    "add": "$accent",
    "del": "$error",
    "warn": "$secondary",
    "error": "$error",
    "ok": "$accent",
    "head": "bold $accent",
}
