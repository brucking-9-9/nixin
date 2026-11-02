pragma ComponentBehavior: Bound

import QtQuick

// One installed package (`list`): the name in cyan, the version on the right; under them
// for whom (the system, or a Home Manager user in purple) and the file:line listing it.
Rectangle {
    id: row

    required property int index
    required property string name
    required property string drv
    required property string version
    required property string tag
    required property string where
    required property bool isHome
    property bool selected: false
    property bool current: false

    signal clicked()

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
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.right: versionText.left
        anchors.rightMargin: 8
        y: 5
        text: row.name
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.cyan
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }
    Text {
        id: versionText
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 7
        width: Math.min(implicitWidth, row.width * 0.36)
        text: row.version
        textFormat: Text.PlainText
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignRight
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        id: tagText
        anchors.left: parent.left
        anchors.leftMargin: 12
        y: 25
        text: row.tag
        textFormat: Text.PlainText
        color: row.isHome ? Theme.purple : Theme.dim
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        anchors.left: tagText.right
        anchors.leftMargin: 10
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 25
        text: row.where !== "" ? row.where : "added by a module"
        textFormat: Text.PlainText
        elide: Text.ElideLeft  // keep the file name and the line
        color: row.where !== "" ? Theme.dim2 : Theme.dim
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
