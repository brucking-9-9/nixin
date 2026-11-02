pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

// Outlined in the palette's accent; `highlighted` fills it (the primary action). A
// destructive button sets `palette.accent: Theme.pink` (Nixin's DangerButton).
T.Button {
    id: control

    readonly property color tint: control.palette.accent

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    padding: 6
    horizontalPadding: 14
    spacing: 6
    font.family: Theme.font
    font.pixelSize: Theme.fontSize
    focusPolicy: Qt.StrongFocus

    contentItem: Text {
        text: control.text
        font: control.font
        textFormat: Text.PlainText
        color: !control.enabled ? Theme.dim : control.highlighted ? Theme.bg : control.tint
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        implicitWidth: 72
        implicitHeight: Theme.controlH
        radius: Theme.radius
        color: !control.enabled ? "transparent"
             : control.highlighted ? (control.down ? Qt.darker(control.tint, 1.3)
                                      : control.hovered ? Qt.lighter(control.tint, 1.15) : control.tint)
             : Qt.alpha(control.tint, control.down ? 0.24 : control.hovered ? 0.12 : 0.0)
        border.width: control.visualFocus ? 2 : 1
        border.color: !control.enabled ? Theme.line
                    : control.visualFocus ? Theme.fg
                    : control.flat ? "transparent" : Qt.alpha(control.tint, 0.75)
    }
}
