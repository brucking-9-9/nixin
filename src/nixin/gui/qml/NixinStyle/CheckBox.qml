pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

// A square in the line colour; checked: filled in the accent with a dark tick.
T.CheckBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)
    padding: 4
    spacing: 8
    font.family: Theme.font
    font.pixelSize: Theme.fontSize
    focusPolicy: Qt.StrongFocus

    indicator: Rectangle {
        implicitWidth: 16
        implicitHeight: 16
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 3
        color: control.checked ? (control.enabled ? Theme.cyan : Theme.dim) : "transparent"
        border.width: control.visualFocus ? 2 : 1
        border.color: !control.enabled ? Theme.line
                    : control.visualFocus ? Theme.fg
                    : control.checked ? Theme.cyan
                    : control.hovered ? Theme.dim2 : Theme.dim
        Text {
            anchors.centerIn: parent
            visible: control.checked
            text: "✓"
            color: Theme.bg
            font.family: Theme.font
            font.pixelSize: 12
            font.bold: true
        }
    }

    contentItem: Text {
        leftPadding: control.text !== "" ? control.indicator.width + control.spacing : 0
        text: control.text
        font: control.font
        textFormat: Text.PlainText
        color: control.enabled ? Theme.fg : Theme.dim
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
