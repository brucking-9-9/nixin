import QtQuick
import QtQuick.Templates as T
import Nixin

T.Popup {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    padding: 8

    background: Rectangle {
        color: Theme.panel
        radius: Theme.radius
        border.width: 1
        border.color: Qt.alpha(Theme.cyan, 0.45)
    }

    T.Overlay.modal: Rectangle { color: Qt.alpha(Theme.bg, 0.78) }
    T.Overlay.modeless: Rectangle { color: Qt.alpha(Theme.bg, 0.4) }
}
