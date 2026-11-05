pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts

// The build while plan or apply runs it: the status line (built/total, fetched, the
// current derivation, elapsed, ETA), the derivations building and queued, and the current
// build's last log lines (nixin.ui.view.build_head/build_rows/build_tail on the tracker's
// snapshot, refreshed twice a second by the view-model).
Rectangle {
    id: box

    property string head: ""
    property string note: ""     // the step when it is not the build: "switching (cannot be stopped)"
    property var rows: null      // a Rows model of view.Field (label, value, style)
    property var tail: null      // a Rows model of lines (text)

    implicitHeight: column.implicitHeight + 16
    color: Theme.panel
    radius: Theme.radius
    border.width: 1
    border.color: Qt.alpha(Theme.cyan, 0.5)

    ColumnLayout {
        id: column
        x: 10
        y: 8
        width: parent.width - 20
        spacing: 4

        Text {
            objectName: "buildHead"
            Layout.fillWidth: true
            text: box.head
            textFormat: Text.PlainText
            color: Theme.cyan
            font.family: Theme.mono
            font.pixelSize: Theme.fontSize
            font.bold: true
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: box.note !== ""
            text: box.note
            textFormat: Text.PlainText
            color: Theme.purple
            font.family: Theme.font
            font.pixelSize: Theme.small
            elide: Text.ElideRight
        }
        FieldList {
            objectName: "buildRows"
            Layout.fillWidth: true
            model: box.rows
            labelWidth: 220
        }
        Repeater {
            model: box.tail
            delegate: Item {
                id: line
                required property string text
                width: column.width
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
    }
}
