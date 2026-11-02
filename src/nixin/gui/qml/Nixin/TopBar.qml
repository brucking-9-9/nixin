import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// `nixin · mbp13 · ~/nixos · unstable`, and on the right what is running: the busy
// indicator, the verb as the CLI would say it, how long, and what to expect.
Rectangle {
    id: bar

    required property var backend

    implicitHeight: Theme.topH
    color: Theme.bg

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.line
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.pad
        anchors.rightMargin: Theme.pad
        spacing: 8

        Text {
            text: "nixin"
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.big
            font.bold: true
        }
        Text { text: "·"; color: Theme.dim; font.pixelSize: Theme.fontSize }
        Text {
            text: bar.backend.host
            textFormat: Text.PlainText
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }
        Text { text: "·"; color: Theme.dim; font.pixelSize: Theme.fontSize }
        Text {
            text: bar.backend.configLabel
            textFormat: Text.PlainText
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }
        Text { text: "·"; color: Theme.dim; font.pixelSize: Theme.fontSize }
        Text {
            text: bar.backend.channel
            color: Theme.purple
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }
        Text {
            visible: bar.backend.busy !== ""
            text: "[" + bar.backend.busy + "]"
            color: Theme.purple
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }

        Item { Layout.fillWidth: true }

        BusyIndicator {
            objectName: "topBusy"
            running: bar.backend.running
            visible: bar.backend.running
            Layout.preferredWidth: 18
            Layout.preferredHeight: 18
        }
        Text {
            objectName: "activity"
            visible: bar.backend.running
            text: bar.backend.activity + (bar.backend.activitySeconds > 0 ? " · " + bar.backend.activitySeconds + " s" : "")
            textFormat: Text.PlainText
            color: Theme.fg
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
            elide: Text.ElideMiddle
            Layout.maximumWidth: bar.width * 0.38
        }
        Text {
            visible: bar.backend.running && bar.backend.activityHint !== ""
            text: bar.backend.activityHint
            textFormat: Text.PlainText
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
            elide: Text.ElideRight
            Layout.maximumWidth: bar.width * 0.3
        }
    }
}
