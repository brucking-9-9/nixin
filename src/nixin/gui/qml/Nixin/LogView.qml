pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// nix's build output / nixos-rebuild's log as plan and apply stream it (a LogModel of
// lines). Follows the end while it grows, unless the person scrolled up.
Rectangle {
    id: box

    property alias model: lines.model
    readonly property int count: lines.count

    implicitHeight: 200
    color: Theme.sunken
    radius: Theme.radius
    border.width: 1
    border.color: Theme.line
    clip: true

    ListView {
        id: lines
        objectName: "logLines"
        anchors.fill: parent
        anchors.margins: 8
        property bool follow: true
        boundsBehavior: Flickable.StopAtBounds
        onMovementEnded: follow = atYEnd
        onCountChanged: if (follow) Qt.callLater(positionViewAtEnd)
        ScrollBar.vertical: ScrollBar { }
        delegate: Item {
            id: line
            required property string text
            width: lines.width - 10
            implicitHeight: shown.implicitHeight
            Text {
                id: shown
                width: parent.width
                text: line.text
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.mono
                font.pixelSize: Theme.small
                elide: Text.ElideRight
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: lines.count === 0
        text: "waiting for output…"
        color: Theme.dim
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
}
