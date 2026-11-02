import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A section title in small capitals, a spinner while its verb runs, a rule to the edge.
RowLayout {
    id: head

    property string text: ""
    property bool loading: false

    spacing: 8

    Text {
        text: head.text.toUpperCase()
        textFormat: Text.PlainText
        color: Theme.cyan
        font.family: Theme.font
        font.pixelSize: Theme.small
        font.bold: true
        font.letterSpacing: 1.2
    }
    BusyIndicator {
        running: head.loading
        visible: head.loading
        Layout.preferredWidth: 14
        Layout.preferredHeight: 14
    }
    Rectangle {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
        height: 1
        color: Theme.line
    }
}
