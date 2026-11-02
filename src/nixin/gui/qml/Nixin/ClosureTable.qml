pragma ComponentBehavior: Bound

import QtQuick

// plan's closure diff: + added, - removed, ~ changed; name, version, size.
Column {
    id: table

    property alias model: rows.model
    readonly property int count: rows.count

    spacing: 2

    Row {
        spacing: 12
        Text { width: 14; text: ""; font.pixelSize: Theme.small }
        Text { width: table.width * 0.42; text: "package"; color: Theme.dim; font.family: Theme.font; font.pixelSize: Theme.small }
        Text { width: table.width * 0.32; text: "version"; color: Theme.dim; font.family: Theme.font; font.pixelSize: Theme.small }
        Text { width: 90; text: "size"; color: Theme.dim; font.family: Theme.font; font.pixelSize: Theme.small; horizontalAlignment: Text.AlignRight }
    }
    Repeater {
        id: rows
        delegate: Row {
            id: row
            required property string sign
            required property string name
            required property string version
            required property string size
            required property string style
            spacing: 12
            Text { width: 14; text: row.sign; color: Theme.style(row.style); font.family: Theme.mono; font.pixelSize: Theme.fontSize; font.bold: true }
            Text { width: table.width * 0.42; text: row.name; textFormat: Text.PlainText; color: Theme.style(row.style); font.family: Theme.font; font.pixelSize: Theme.fontSize; elide: Text.ElideRight }
            Text { width: table.width * 0.32; text: row.version; textFormat: Text.PlainText; color: Theme.fg; font.family: Theme.font; font.pixelSize: Theme.fontSize; elide: Text.ElideRight }
            Text { width: 90; text: row.size; color: Theme.dim2; font.family: Theme.font; font.pixelSize: Theme.fontSize; horizontalAlignment: Text.AlignRight }
        }
    }
}
