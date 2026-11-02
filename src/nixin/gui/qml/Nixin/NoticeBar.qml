import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// An error document as `code` + message, non-modal, over the bottom of the page: the
// candidates to pick (ambiguous writes), the commands to copy, hints and files. Passing
// messages (written, copied) go away by themselves.
Rectangle {
    id: bar

    required property var notice

    objectName: "noticeBar"
    readonly property color tone: Theme.severity(notice.severity)

    visible: opacity > 0
    opacity: notice.visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.fadeMs } }

    implicitHeight: body.implicitHeight + 2 * Theme.gap + 6
    color: Theme.panel
    radius: Theme.radius
    border.width: 1
    border.color: Qt.alpha(tone, 0.7)

    Rectangle {
        width: 4
        height: parent.height
        radius: Theme.radius
        color: bar.tone
    }

    Timer {
        id: hide
        interval: 5000
        onTriggered: bar.notice.dismiss()
    }
    Connections {
        target: bar.notice
        function onSerialChanged() {
            if (bar.notice.transient)
                hide.restart();
            else
                hide.stop();
        }
    }

    ColumnLayout {
        id: body
        x: 18
        y: Theme.gap + 3
        width: bar.width - 18 - Theme.pad
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                objectName: "noticeCode"
                visible: bar.notice.code !== ""
                text: bar.notice.code
                textFormat: Text.PlainText
                color: bar.tone
                font.family: Theme.mono
                font.pixelSize: Theme.fontSize
                font.bold: true
            }
            Text {
                objectName: "noticeMessage"
                Layout.fillWidth: true
                text: bar.notice.message
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 6
                elide: Text.ElideRight
                color: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            Text {
                visible: bar.notice.context !== ""
                text: bar.notice.context
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.mono
                font.pixelSize: Theme.small
                elide: Text.ElideLeft
                Layout.maximumWidth: bar.width * 0.3
            }
            Button {
                objectName: "noticeDismiss"
                text: "✕"
                flat: true
                focusPolicy: Qt.NoFocus
                implicitWidth: 30
                onClicked: bar.notice.dismiss()
            }
        }

        Repeater {
            model: bar.notice.items
            Text {
                required property string modelData
                Layout.fillWidth: true
                text: "· " + modelData
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.small + 1
            }
        }

        Flow {
            Layout.fillWidth: true
            visible: bar.notice.candidates.length > 0
            spacing: 6
            Repeater {
                model: bar.notice.candidates
                Button {
                    required property var modelData
                    objectName: "candidate-" + modelData.where
                    text: modelData.label
                    onClicked: bar.notice.pick(modelData.where)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: bar.notice.commands !== ""
            spacing: 8
            TextArea {
                Layout.fillWidth: true
                text: bar.notice.commands
                readOnly: true
                selectByMouse: true
                font.family: Theme.mono
                font.pixelSize: Theme.small + 1
            }
            Button {
                objectName: "noticeCopy"
                text: "Copy"
                onClicked: bar.notice.copyCommands()
            }
        }
    }
}
