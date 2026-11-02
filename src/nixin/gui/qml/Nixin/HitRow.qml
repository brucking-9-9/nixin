pragma ComponentBehavior: Bound

import QtQuick

// One search result: badge, name (cyan; purple for options), version or type; the
// description under it. `selected` is the row in the details pane, `current` the
// keyboard cursor.
Rectangle {
    id: row

    required property int index
    required property string badge
    required property string name
    required property string detail
    required property string description
    required property bool isOption
    property bool selected: false
    property bool current: false

    signal clicked()

    implicitHeight: 54
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
        id: badgeText
        x: 12
        y: 8
        width: 30
        text: row.badge
        color: Theme.dim
        font.family: Theme.mono
        font.pixelSize: Theme.small
    }
    Text {
        id: nameText
        anchors.left: badgeText.right
        anchors.right: detailText.left
        anchors.rightMargin: 8
        y: 6
        text: row.name
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.isOption ? Theme.purple : Theme.cyan
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }
    Text {
        id: detailText
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 8
        width: Math.min(implicitWidth, row.width * 0.32)
        text: row.detail
        textFormat: Text.PlainText
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignRight
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        anchors.left: badgeText.right
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 29
        text: row.description || "(no description)"
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.description ? Theme.dim2 : Theme.dim
        font.family: Theme.font
        font.pixelSize: Theme.small + 1
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: row.clicked()
    }
}
