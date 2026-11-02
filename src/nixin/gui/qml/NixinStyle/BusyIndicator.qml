pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Templates as T
import Nixin

// Eight cyan dots going round; the software renderer draws it too.
T.BusyIndicator {
    id: control

    implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
    implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding

    contentItem: Item {
        id: ring
        implicitWidth: 18
        implicitHeight: 18
        opacity: control.running ? 1 : 0
        visible: opacity > 0

        Behavior on opacity { NumberAnimation { duration: Theme.fadeMs } }

        Repeater {
            model: 8
            Rectangle {
                required property int index
                readonly property real a: index / 8 * 2 * Math.PI
                width: Math.max(2, ring.width / 5)
                height: width
                radius: width / 2
                color: Theme.cyan
                opacity: 0.15 + 0.85 * index / 7
                x: (ring.width - width) / 2 * (1 + Math.cos(a))
                y: (ring.height - height) / 2 * (1 + Math.sin(a))
            }
        }

        RotationAnimator on rotation {
            from: 0
            to: 360
            duration: 1100
            loops: Animation.Infinite
            running: control.running && control.visible
        }
    }
}
