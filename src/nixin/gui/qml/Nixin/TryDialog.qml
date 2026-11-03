pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// try: `try PKG --build-only` with nix's log streaming → what it is, how big, the commands →
// a graphical app starts detached, a CLI program opens in the terminal (`nix run …`).
// Several programs and no mainProgram: pick one (runs try again with --program).
// Nothing is installed; the next garbage collection removes it.
Dialog {
    id: dlg

    required property var trying
    readonly property string phase: trying.phase
    readonly property bool want: trying.open
    readonly property bool working: phase === "building"

    objectName: "tryDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 900, 900)
    height: Math.min(parent ? parent.height - 60 : 600, 640)
    title: "try " + trying.target + (trying.program !== "" ? " --program " + trying.program : "")
           + ({ building: " — building", done: " — ready", pick: " — which program?", failed: " — failed" }[phase] || "")
    closePolicy: working ? Popup.NoAutoClose : Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onClosed: if (trying.open) trying.close()

    contentItem: ColumnLayout {
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            visible: dlg.working
            spacing: Theme.gap
            BusyIndicator {
                running: dlg.working
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
            }
            Text {
                Layout.fillWidth: true
                text: "nix build: fetching from cache.nixos.org, or building; nothing is installed"
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
        }

        LogView {
            id: log
            objectName: "tryLog"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 100
            visible: dlg.working || (dlg.phase === "failed" && count > 0)
            model: dlg.trying.log
        }

        Rectangle {
            Layout.fillWidth: true
            visible: dlg.trying.noticeCode !== "" && !dlg.working
            implicitHeight: noticeRow.implicitHeight + 16
            color: Theme.sunken
            radius: Theme.radius
            border.width: 1
            border.color: Qt.alpha(Theme.severity(dlg.trying.noticeSeverity), 0.7)
            RowLayout {
                id: noticeRow
                x: 10
                y: 8
                width: parent.width - 20
                spacing: 10
                Text {
                    objectName: "tryCode"
                    Layout.alignment: Qt.AlignTop
                    text: dlg.trying.noticeCode
                    color: Theme.severity(dlg.trying.noticeSeverity)
                    font.family: Theme.mono
                    font.pixelSize: Theme.fontSize
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    text: dlg.trying.noticeMessage
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Theme.fg
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: dlg.phase === "pick"
            text: "Pick the program to run (the build is done):"
            textFormat: Text.PlainText
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }
        ListView {
            id: programs
            objectName: "tryPrograms"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 80
            visible: dlg.phase === "pick"
            clip: true
            model: dlg.trying.programs
            ScrollBar.vertical: ScrollBar {}
            delegate: ItemDelegate {
                required property string modelData
                required property int index
                objectName: "tryProgram-" + modelData
                width: ListView.view.width
                text: modelData
                font.family: Theme.mono
                onClicked: dlg.trying.pick(modelData)
            }
        }

        Text {
            objectName: "tryHead"
            Layout.fillWidth: true
            visible: dlg.phase === "done"
            text: dlg.trying.head
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
            font.bold: true
        }
        ScrollView {
            id: resultScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: dlg.phase === "done"
            contentWidth: availableWidth
            FieldList {
                objectName: "tryResult"
                width: resultScroll.availableWidth - 12
                model: dlg.trying.result
                labelWidth: 90
            }
        }

        TextArea {
            objectName: "tryCommands"
            Layout.fillWidth: true
            visible: dlg.trying.commands !== "" && !dlg.working
            text: dlg.trying.commands
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            font.family: Theme.mono
            font.pixelSize: Theme.fontSize
        }
        Text {
            objectName: "tryNote"
            Layout.fillWidth: true
            visible: dlg.trying.note !== ""
            text: dlg.trying.note
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.style(dlg.trying.noteStyle)
            font.family: Theme.font
            font.pixelSize: Theme.small + 1
        }
        Item {  // keeps the rows together when neither the log nor a list fills the height
            Layout.fillHeight: true
            visible: !log.visible && !programs.visible && !resultScroll.visible
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "tryLaunch"
            visible: dlg.phase === "done" && dlg.trying.canLaunch
            text: "Launch again"
            highlighted: true
            onClicked: dlg.trying.launchApp()
        }
        Button {
            objectName: "tryTerminal"
            visible: dlg.phase === "done" && dlg.trying.canTerminal
            text: dlg.trying.isGui ? "Run in terminal" : "Run again in terminal"
            onClicked: dlg.trying.runInTerminal()
        }
        Button {
            objectName: "tryShell"
            visible: (dlg.phase === "done" || dlg.phase === "pick") && dlg.trying.canTerminal
            text: "Shell in terminal"
            ToolTip.visible: hovered
            ToolTip.text: "nix shell: your $SHELL with the package on PATH"
            onClicked: dlg.trying.shellInTerminal()
        }
        Button {
            objectName: "tryCopy"
            visible: dlg.trying.commands !== "" && !dlg.working
            text: "Copy command"
            onClicked: dlg.trying.copyCommand()
        }
        Button {
            objectName: "tryRetry"
            visible: dlg.phase === "failed"
            text: "Retry"
            onClicked: dlg.trying.retry()
        }
        Button {
            objectName: "tryClose"
            text: "Close"
            enabled: !dlg.working
            onClicked: dlg.trying.close()
        }
    }
}
