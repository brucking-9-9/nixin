pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

// The checked tab: cyan text over a cyan underline; the others secondary text.
T.TabButton {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    padding: 6
    horizontalPadding: 14
    font.family: Theme.font
    font.pixelSize: Theme.fontSize

    contentItem: Text {
        text: control.text
        font: control.font
        textFormat: Text.PlainText
        color: !control.enabled ? Theme.dim
             : control.checked ? Theme.cyan
             : control.hovered ? Theme.fg : Theme.dim2
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    background: Item {
        implicitHeight: Theme.controlH
        Rectangle {
            anchors.fill: parent
            color: Qt.alpha(Theme.cyan, control.down ? 0.16 : 0.07)
            visible: control.down || control.hovered || control.visualFocus
        }
        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 2
            color: Theme.cyan
            visible: control.checked
        }
    }
}
