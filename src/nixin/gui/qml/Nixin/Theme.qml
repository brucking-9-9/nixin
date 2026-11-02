pragma Singleton
import QtQuick

// The palette, named and toned as the person's quickshell Theme.qml has it (kitty
// "cyberpunk"): background #120b10, cyan for the accent, the selection and focus, pink for
// errors and destructive actions, purple for secondary things and option names, light
// text. Fixed colours: nothing here follows the wallpaper. The style (NixinStyle) and the
// pages read every colour, size and font from here.
QtObject {
    readonly property color bg:     "#120b10"
    readonly property color panel:  "#1a1017"   // panes, cards, dialogs: a step above bg
    readonly property color raised: "#24161f"   // hovered inputs, the selected row
    readonly property color sunken: "#0c070a"   // logs, diffs, commands
    readonly property color fg:     "#eeffff"
    readonly property color dim:    "#6d5a78"   // disabled, rules
    readonly property color dim2:   "#9a86a6"   // secondary text: labels, paths, hints
    readonly property color sel:    "#563466"   // text selection fill
    readonly property color line:   "#3a2836"   // borders, low contrast on purpose
    readonly property color cyan:   "#00ffc8"
    readonly property color pink:   "#ff0055"
    readonly property color purple: "#d57bff"
    readonly property color yellow: "#f4ef00"
    readonly property color green:  "#00ff9c"

    readonly property string font: "CaskaydiaCove Nerd Font Propo"
    readonly property string mono: "CaskaydiaCove Nerd Font Mono"
    readonly property int fontSize: 13
    readonly property int small: 11
    readonly property int big: 17

    readonly property int pad: 14          // page margins
    readonly property int gap: 8
    readonly property int radius: 4
    readonly property int controlH: 30
    readonly property int navW: 172
    readonly property int topH: 40
    readonly property int labelW: 96       // field labels
    readonly property int fadeMs: 140

    // The view-model's semantic styles (nixin/ui/view.py) → colours; the TUI maps the same
    // names in nixin/tui/theme.py.
    function style(name: string): color {
        switch (name) {
        case "dim": return dim2;
        case "accent": case "add": case "ok": case "head": return cyan;
        case "option": case "warn": return purple;
        case "del": case "error": return pink;
        default: return fg;
        }
    }

    // A notice's severity → its colour: errors pink, root-needed purple, the rest cyan.
    function severity(name: string): color {
        switch (name) {
        case "error": return pink;
        case "action": case "warn": return purple;
        default: return cyan;
        }
    }
}
