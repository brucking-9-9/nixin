pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// The writer's unified diff (view.diff_spans: a header line per file, then its lines).
Rectangle {
    id: box

    property alias model: lines.model

    color: Theme.sunken
    radius: Theme.radius
    border.width: 1
    border.color: Theme.line
    clip: true

    ListView {
        id: lines
        objectName: "diffLines"
        anchors.fill: parent
        anchors.margins: 10
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { }
        ScrollBar.horizontal: ScrollBar { }
        contentWidth: Math.max(width, contentItem.childrenRect.width)
        flickableDirection: Flickable.AutoFlickIfNeeded
        delegate: Item {
            id: line
            required property string text
            required property string style
            required property int index
            readonly property bool head: style === "head"
            implicitWidth: shown.implicitWidth
            implicitHeight: shown.implicitHeight
            Text {
                id: shown
                text: line.text
                textFormat: Text.PlainText
                color: Theme.style(line.style)
                font.family: line.head ? Theme.font : Theme.mono
                font.pixelSize: line.head ? Theme.fontSize : Theme.small + 1
                font.bold: line.head
                topPadding: line.head && line.index > 0 ? 12 : 0
                bottomPadding: line.head ? 4 : 0
            }
        }
    }
}
