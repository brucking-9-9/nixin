pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls

// Label/value rows from the view-model (view.Field: label, value, style), then an optional
// note. The model is a Rows list model or a JS array of the same objects (status sections).
// Values are plain text, selectable: paths and commands are there to be copied; so is the
// note. `copyable`: an error note gets a Copy button (copyRequested: the page copies the
// error document and its log).
Column {
    id: list

    property alias model: rows.model
    property string note: ""
    property string noteStyle: "dim"
    property int labelWidth: Theme.labelW
    property bool copyable: false

    signal copyRequested()

    spacing: 4

    Repeater {
        id: rows
        delegate: Row {
            id: row
            required property string label
            required property string value
            required property string style
            width: list.width
            spacing: 10

            Text {
                width: list.labelWidth
                text: row.label
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
            TextEdit {
                width: row.width - list.labelWidth - row.spacing
                text: row.value
                textFormat: TextEdit.PlainText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                color: Theme.style(row.style)
                selectionColor: Theme.sel
                selectedTextColor: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
        }
    }

    Row {
        width: list.width
        visible: list.note !== ""
        spacing: 10
        TextEdit {
            objectName: list.objectName + "Note"
            width: parent.width - (noteCopy.visible ? noteCopy.width + parent.spacing : 0)
            text: list.note
            textFormat: TextEdit.PlainText
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            color: Theme.style(list.noteStyle)
            selectionColor: Theme.sel
            selectedTextColor: Theme.fg
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }
        Button {
            id: noteCopy
            objectName: list.objectName + "Copy"
            visible: list.copyable && list.noteStyle === "error"
            text: "Copy"
            focusPolicy: Qt.NoFocus
            ToolTip.visible: hovered
            ToolTip.text: "copy the error whole (code, message, its fields, stderr) and the run's log"
            onClicked: list.copyRequested()
        }
    }
}
