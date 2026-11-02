pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

// Thin, low contrast at rest; purple under the pointer, cyan while dragged (as the TUI's).
T.ScrollBar {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    padding: 2
    visible: control.policy !== T.ScrollBar.AlwaysOff
    minimumSize: orientation === Qt.Horizontal ? height / width : width / height

    contentItem: Rectangle {
        implicitWidth: control.interactive ? 6 : 2
        implicitHeight: control.interactive ? 6 : 2
        radius: 3
        color: control.pressed ? Theme.cyan : control.hovered ? Theme.purple : Theme.dim
        opacity: control.policy === T.ScrollBar.AlwaysOn || control.size < 1.0
                 ? (control.active || control.hovered ? 1.0 : 0.55) : 0.0
        Behavior on opacity { NumberAnimation { duration: Theme.fadeMs } }
    }
}
