pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Every write is previewed: the writer's `--dry-run` (what it does, the eval proof, the
// unified diff), the user it goes to (the system or a Home Manager user: changing it runs
// the preview again), then Write. After a write whose eval check failed it shows again.
Dialog {
    id: dlg

    required property var writer
    readonly property string phase: writer.phase
    readonly property bool want: writer.open

    objectName: "diffDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 900, 980)
    height: Math.min(parent ? parent.height - 60 : 640, 720)
    title: phase === "failed" ? writer.title
         : writer.title + (phase === "writing" ? " — writing…" : " — preview")
    closePolicy: phase === "writing" ? Popup.NoAutoClose : Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onClosed: if (writer.open) writer.cancel()

    Shortcut {
        sequences: ["Ctrl+Return", "Ctrl+Enter"]
        enabled: dlg.visible
        onActivated: dlg.writer.write()
    }

    contentItem: ColumnLayout {
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Text {
                objectName: "diffCommand"
                Layout.fillWidth: true
                text: dlg.writer.command
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: Theme.dim2
                font.family: Theme.mono
                font.pixelSize: Theme.small + 1
            }
            BusyIndicator {
                running: dlg.phase === "previewing" || dlg.phase === "writing"
                visible: running
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
            }
            Text {
                visible: dlg.writer.canHome
                text: "for"
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            ComboBox {
                id: home
                objectName: "diffHome"
                visible: dlg.writer.canHome
                enabled: dlg.phase === "preview"
                model: dlg.writer.homeChoices
                currentIndex: Math.max(0, dlg.writer.homeChoices.indexOf(dlg.writer.homeUser))
                displayText: dlg.writer.homeUser === "" ? "the system" : "home: " + dlg.writer.homeUser
                implicitWidth: 170
                onActivated: index => dlg.writer.setHome(dlg.writer.homeChoices[index])
                delegate: ItemDelegate {
                    required property string modelData
                    required property int index
                    width: ListView.view.width
                    text: modelData === "" ? "the system" : "home: " + modelData
                    highlighted: home.highlightedIndex === index
                }
            }
        }

        FieldList {
            objectName: "diffFields"
            Layout.fillWidth: true
            model: dlg.writer.fields
            labelWidth: 80
            note: dlg.phase === "previewing" ? "running the preview…"
                : dlg.writer.nothing ? "no file would change" : ""
        }

        DiffView {
            objectName: "diff"
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: dlg.writer.diff
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "diffCancel"
            text: dlg.phase === "failed" ? "Close" : "Cancel"
            enabled: dlg.phase !== "writing"
            onClicked: dlg.writer.cancel()
        }
        Button {
            objectName: "diffWrite"
            visible: dlg.phase !== "failed"
            text: "Write  (Ctrl+Enter)"
            highlighted: true
            enabled: dlg.phase === "preview" && !dlg.writer.nothing
            onClicked: dlg.writer.write()
        }
    }
}
