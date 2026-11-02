pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

T.Dialog {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            contentWidth + leftPadding + rightPadding,
                            implicitHeaderWidth, implicitFooterWidth)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             contentHeight + topPadding + bottomPadding
                             + (implicitHeaderHeight > 0 ? implicitHeaderHeight + spacing : 0)
                             + (implicitFooterHeight > 0 ? implicitFooterHeight + spacing : 0))
    padding: 18
    topPadding: 6
    spacing: 6
    font.family: Theme.font
    font.pixelSize: Theme.fontSize

    background: Rectangle {
        color: Theme.panel
        radius: Theme.radius
        border.width: 1
        border.color: Qt.alpha(Theme.cyan, 0.5)
    }

    header: Item {
        implicitHeight: control.title !== "" ? heading.implicitHeight + 26 : 0
        visible: control.title !== ""
        Text {
            id: heading
            x: 18
            y: 16
            width: parent.width - 36
            text: control.title
            textFormat: Text.PlainText
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.big
            font.bold: true
            elide: Text.ElideRight
        }
    }

    enter: Transition { NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.fadeMs } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.fadeMs } }

    T.Overlay.modal: Rectangle { color: Qt.alpha(Theme.bg, 0.78) }
    T.Overlay.modeless: Rectangle { color: Qt.alpha(Theme.bg, 0.4) }
}
