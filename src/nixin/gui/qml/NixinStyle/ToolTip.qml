pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

T.ToolTip {
    id: control

    x: parent ? (parent.width - implicitWidth) / 2 : 0
    y: -implicitHeight - 4
    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            contentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             contentHeight + topPadding + bottomPadding)
    margins: 6
    padding: 6
    font.family: Theme.font
    font.pixelSize: Theme.small
    closePolicy: T.Popup.CloseOnEscape | T.Popup.CloseOnPressOutsideParent | T.Popup.CloseOnReleaseOutsideParent

    contentItem: Text {
        text: control.text
        font: control.font
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Theme.fg
    }

    background: Rectangle {
        color: Theme.panel
        radius: Theme.radius
        border.width: 1
        border.color: Qt.alpha(Theme.purple, 0.6)
    }
}
