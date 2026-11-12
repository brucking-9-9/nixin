pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// nix's build output / nixos-rebuild's log as plan, apply and a branch merge stream it (a
// LogModel of lines). Follows the end while it grows, unless the person scrolled up. Copy
// (top right) puts the whole log on the clipboard; a line's text can be selected with the
// mouse too (one line at a time: the lines are separate items).
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
            clip: true
            TextEdit {
                id: shown
                width: parent.width
                text: line.text
                textFormat: TextEdit.PlainText
                readOnly: true
                selectByMouse: true
                color: Theme.dim2
                selectionColor: Theme.sel
                selectedTextColor: Theme.fg
                font.family: Theme.mono
                font.pixelSize: Theme.small
            }
        }
    }

    Button {
        objectName: box.objectName + "Copy"
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 4
        anchors.rightMargin: 14
        visible: lines.count > 0
        text: "Copy"
        flat: true
        padding: 2
        horizontalPadding: 8
        implicitHeight: 22
        focusPolicy: Qt.NoFocus
        font.pixelSize: Theme.small
        background: Rectangle {
            radius: Theme.radius
            color: Theme.panel
            border.width: 1
            border.color: Qt.alpha(Theme.cyan, 0.5)
        }
        ToolTip.visible: hovered
        ToolTip.text: "copy the whole log"
        onClicked: box.model.copyAll()
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
