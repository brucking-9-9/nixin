pragma ComponentBehavior: Bound

import QtQuick

// One application in Browse: its icon (installed apps: their own; others: the letter
// tile, nothing is fetched for the list), the app's name in cyan, ● installed and ★
// well-known, the attribute and version on the right, the summary under them.
Rectangle {
    id: row

    required property int index
    required property string name
    required property string target
    required property string version
    required property string summary
    required property string icon
    required property string marks
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

    AppIcon {
        id: appIcon
        x: 10
        anchors.verticalCenter: parent.verticalCenter
        width: 34
        height: 34
        source: row.icon
        name: row.name
    }
    Text {
        id: nameText
        anchors.left: appIcon.right
        anchors.leftMargin: 10
        anchors.right: marksText.left
        anchors.rightMargin: 6
        y: 7
        text: row.name
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.cyan
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }
    Text {
        id: marksText
        anchors.right: targetText.left
        anchors.rightMargin: 8
        y: 7
        text: row.marks
        textFormat: Text.PlainText
        color: Theme.purple
        font.family: Theme.font
        font.pixelSize: Theme.fontSize
    }
    Text {
        id: targetText
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 9
        width: Math.min(implicitWidth, row.width * 0.4)
        text: row.target + (row.version !== "" ? " " + row.version : "")
        textFormat: Text.PlainText
        elide: Text.ElideMiddle
        horizontalAlignment: Text.AlignRight
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        anchors.left: nameText.left
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 29
        text: row.summary
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.fg
        opacity: 0.8
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
