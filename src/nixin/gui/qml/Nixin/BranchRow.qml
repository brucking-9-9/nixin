pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// One branch against main: a tick (clean ones only: what Merge selected… takes), its name
// and state badge (clean cyan, conflict purple, merged dim; "· hub" when only the hub has
// it), +ahead -behind, how many files it touches, its age and its last commit's subject.
Rectangle {
    id: row

    required property int index
    required property string name
    required property string badge
    required property string style
    required property int ahead
    required property int behind
    required property int files
    required property string age
    required property string subject
    required property bool mergeable
    required property bool merged
    required property bool checked
    property bool selected: false
    property bool current: false

    signal clicked()
    signal toggled()

    readonly property color tone: Theme.style(style)

    objectName: "branch-" + name
    implicitHeight: 36
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

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        onClicked: row.clicked()
    }

    CheckBox {
        id: tick
        objectName: "tick-" + row.name
        anchors.left: parent.left
        anchors.leftMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        visible: row.mergeable
        checked: row.checked
        focusPolicy: Qt.NoFocus
        ToolTip.visible: hovered
        ToolTip.text: "merge this one (Merge selected…)"
        onClicked: row.toggled()
    }

    Text {
        id: nameText
        anchors.left: parent.left
        anchors.leftMargin: 36
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, 260)
        text: row.name
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.merged ? Theme.dim2 : row.tone
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }

    Rectangle {
        id: pill
        anchors.left: nameText.right
        anchors.leftMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        width: badgeText.implicitWidth + 12
        height: 18
        radius: 9
        color: Qt.alpha(row.tone, 0.12)
        border.width: 1
        border.color: Qt.alpha(row.tone, 0.6)
        Text {
            id: badgeText
            objectName: "badge-" + row.name
            anchors.centerIn: parent
            text: row.badge
            textFormat: Text.PlainText
            color: row.tone
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
    }

    Text {
        id: counts
        anchors.left: pill.right
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: "+" + row.ahead + " -" + row.behind
        textFormat: Text.PlainText
        color: Theme.fg
        font.family: Theme.mono
        font.pixelSize: Theme.small
    }
    Text {
        id: meta
        anchors.left: counts.right
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: row.files + (row.files === 1 ? " file" : " files") + " · " + row.age
        textFormat: Text.PlainText
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
    Text {
        anchors.left: meta.right
        anchors.leftMargin: 14
        anchors.right: parent.right
        anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        text: row.subject
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.small
    }
}
