pragma ComponentBehavior: Bound

import QtQuick

// One pin: its name, what it holds and at which version (purple when stale: the lock is at
// the pinned revision, so it holds nothing yet), under them the nixpkgs commit and the why.
Rectangle {
    id: row

    required property int index
    required property string name
    required property string attrs
    required property string version
    required property string heldAt
    required property string mode
    required property bool stale
    required property string why
    required property string note
    required property string drop
    property bool selected: false
    property bool current: false

    signal clicked()

    objectName: "pin-" + name
    implicitHeight: 46
    radius: Theme.radius
    color: selected ? Theme.raised : mouse.containsMouse ? Qt.alpha(Theme.fg, 0.04) : "transparent"
    border.width: current ? 1 : 0
    border.color: Qt.alpha(Theme.cyan, 0.6)

    Rectangle {
        width: 3
        height: parent.height - 12
        anchors.verticalCenter: parent.verticalCenter
        radius: 1
        color: Theme.cyan
        visible: row.selected
    }

    Text {
        id: nameText
        anchors.left: parent.left
        anchors.leftMargin: 12
        y: 5
        text: row.name
        textFormat: Text.PlainText
        color: row.stale ? Theme.purple : Theme.cyan
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }
    Text {
        anchors.left: nameText.right
        anchors.leftMargin: 10
        anchors.right: held.left
        anchors.rightMargin: 8
        y: 7
        text: row.attrs
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Row {
        id: held
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 7
        spacing: 6
        Text {
            text: "held at"
            color: Theme.dim
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        Text {
            text: row.version
            textFormat: Text.PlainText
            color: Theme.fg
            font.family: Theme.font
            font.pixelSize: Theme.small
            font.bold: true
        }
    }
    Text {
        id: revText
        anchors.left: parent.left
        anchors.leftMargin: 12
        y: 25
        text: "nixpkgs " + row.heldAt + (row.mode !== "attr" ? " · " + row.mode : "")
        textFormat: Text.PlainText
        color: Theme.dim
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        anchors.left: revText.right
        anchors.leftMargin: 10
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 25
        text: [row.note, row.why].filter(s => s !== "").join("  ")
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.stale ? Theme.purple : Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: row.clicked()
    }
}
