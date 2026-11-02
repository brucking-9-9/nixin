import QtQuick

// Label/value rows from the view-model (view.Field: label, value, style), then an optional
// note. The model is a Rows list model or a JS array of the same objects (status sections).
// Values are plain text, selectable: paths and commands are there to be copied.
Column {
    id: list

    property alias model: rows.model
    property string note: ""
    property string noteStyle: "dim"
    property int labelWidth: Theme.labelW

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

    Text {
        width: list.width
        visible: list.note !== ""
        text: list.note
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Theme.style(list.noteStyle)
        font.family: Theme.font
        font.pixelSize: Theme.fontSize
    }
}
