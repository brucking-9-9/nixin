pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// One `outdated` row: the package, `from → to` (pink for a removed one, cyan for an added
// one, purple when the new version sorts older or a pin holds it); under them for whom and
// the note; hold / unhold on the right (pin / unpin through the diff dialog).
Rectangle {
    id: row

    required property int index
    required property string name
    required property string before
    required property string after
    required property string tag
    required property string note
    required property string style
    required property bool isHome
    required property string held
    property bool selected: false
    property bool current: false

    signal clicked()
    signal holdClicked()

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

    // hold (pin) / unhold (unpin) the row: shown while the pointer or the cursor is on it,
    // always for a held row
    Button {
        id: holdButton
        objectName: "hold-" + row.name
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 3
        height: 20
        padding: 2
        leftPadding: 8
        rightPadding: 8
        flat: true
        visible: row.held !== "" || row.selected || row.current || mouse.containsMouse
        text: row.held !== "" ? "unhold" : "hold"
        font.family: Theme.font
        font.pixelSize: Theme.small
        ToolTip.visible: hovered
        ToolTip.text: row.held !== "" ? "unpin " + row.held + ": let the lock update bring it along"
                                      : "pin " + row.name + ": keep it at " + row.before + " while the rest moves on"
        onClicked: row.holdClicked()
    }

    Text {
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.right: versions.left
        anchors.rightMargin: 8
        y: 5
        text: row.name
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.style === "accent" ? Theme.cyan : Theme.style(row.style)
        font.family: Theme.font
        font.pixelSize: Theme.fontSize + 1
        font.bold: row.selected
    }
    Row {
        id: versions
        anchors.right: parent.right
        anchors.rightMargin: 10
        y: 7
        spacing: 6
        Text {
            text: row.before
            textFormat: Text.PlainText
            color: row.style === "del" || row.style === "add" ? Theme.style(row.style) : Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        Text {
            text: "→"
            color: Theme.dim
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        Text {
            text: row.after
            textFormat: Text.PlainText
            color: row.style === "accent" ? Theme.fg : Theme.style(row.style)
            font.family: Theme.font
            font.pixelSize: Theme.small
            font.bold: true
        }
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
        anchors.right: holdButton.visible ? holdButton.left : parent.right
        anchors.rightMargin: 10
        y: 25
        text: row.note
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.dim2
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
