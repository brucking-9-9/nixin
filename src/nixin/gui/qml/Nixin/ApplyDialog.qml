pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// apply: `apply --dry-run` (what would be committed, the closure diff, what root will run,
// whether sudo is ready) → a commit message → the apply with its log streaming → the new
// generation. Without a sudo ticket: the exact root commands, to copy, and Retry.
Dialog {
    id: dlg

    required property var apply
    required property string host
    readonly property string phase: apply.phase
    readonly property bool want: apply.open
    readonly property bool working: phase === "checking" || phase === "running"

    objectName: "applyDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 900, 960)
    height: Math.min(parent ? parent.height - 60 : 640, 720)
    title: "apply → " + host + ({ checking: " — checking", running: " — running", done: " — done",
                                 "needs-sudo": " — root is needed", failed: " — failed",
                                 stopped: " — stopped" }[phase] || "")
    closePolicy: working ? Popup.NoAutoClose : Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onOpened: message.text = ""
    onClosed: if (apply.open) apply.close()
    onPhaseChanged: if (phase === "confirm") message.forceActiveFocus()

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
                text: dlg.phase === "checking" ? "apply --dry-run: what would be committed, what root would run…"
                    : dlg.apply.stepNote !== "" ? dlg.apply.stepNote
                    : "commit, build, pull into the authoritative tree, switch (about 30 s after a plan)"
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
        }

        BuildTracker {
            objectName: "applyBuild"
            Layout.fillWidth: true
            visible: dlg.phase === "running" && dlg.apply.buildHead !== ""
            head: dlg.apply.buildHead
            where: dlg.apply.buildPlace
            rows: dlg.apply.build
            tail: dlg.apply.tail
        }

        ScrollView {
            id: dryScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: dlg.phase === "confirm"
            contentWidth: availableWidth
            FieldList {
                objectName: "applyDry"
                width: dryScroll.availableWidth - 12
                model: dlg.apply.dry
                labelWidth: 110
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: dlg.phase === "confirm"
            spacing: Theme.gap
            Text {
                text: "message"
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            TextField {
                id: message
                objectName: "applyMessage"
                Layout.fillWidth: true
                placeholderText: dlg.apply.placeholder !== "" ? dlg.apply.placeholder + "   (empty: this one)" : "commit message"
                onAccepted: dlg.apply.confirm(text)
            }
        }

        LogView {
            id: log
            objectName: "applyLog"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            visible: dlg.phase === "running" || ((dlg.phase === "done" || dlg.phase === "failed" || dlg.phase === "stopped" || dlg.phase === "needs-sudo") && count > 0)
            model: dlg.apply.log
        }

        Rectangle {
            Layout.fillWidth: true
            visible: dlg.apply.noticeCode !== "" && dlg.phase !== "running"
            implicitHeight: noticeText.implicitHeight + 16
            color: Theme.sunken
            radius: Theme.radius
            border.width: 1
            border.color: Qt.alpha(Theme.severity(dlg.apply.noticeSeverity), 0.7)
            RowLayout {
                id: noticeText
                x: 10
                y: 8
                width: parent.width - 20
                spacing: 10
                Text {
                    objectName: "applyCode"
                    text: dlg.apply.noticeCode
                    color: Theme.severity(dlg.apply.noticeSeverity)
                    font.family: Theme.mono
                    font.pixelSize: Theme.fontSize
                    font.bold: true
                }
                TextEdit {
                    objectName: "applyMessageText"
                    Layout.fillWidth: true
                    text: dlg.apply.noticeMessage
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: Theme.fg
                    selectionColor: Theme.sel
                    selectedTextColor: Theme.fg
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                }
                Button {
                    objectName: "applyCopyError"
                    Layout.alignment: Qt.AlignTop
                    text: "Copy"
                    focusPolicy: Qt.NoFocus
                    ToolTip.visible: hovered
                    ToolTip.text: "copy the error whole (code, message, its fields, stderr) and the apply's log"
                    onClicked: dlg.apply.copyError()
                }
            }
        }

        TextArea {
            objectName: "sudoCommands"
            Layout.fillWidth: true
            visible: dlg.apply.commands !== "" && dlg.phase === "needs-sudo"
            text: dlg.apply.commands
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            font.family: Theme.mono
            font.pixelSize: Theme.fontSize
        }
        Text {
            Layout.fillWidth: true
            visible: dlg.phase === "needs-sudo"
            text: "Run these as root (Copy), or run `sudo -v` in a terminal and Retry. The commit and the build are done."
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small + 1
        }

        FieldList {
            objectName: "applyResult"
            Layout.fillWidth: true
            visible: dlg.phase === "done" || dlg.phase === "failed" || dlg.phase === "stopped" || dlg.phase === "needs-sudo"
            model: dlg.apply.result
            labelWidth: 90
        }
        Text {
            Layout.fillWidth: true
            visible: dlg.phase === "done" && dlg.apply.closureHead !== ""
            text: dlg.apply.closureHead
            textFormat: Text.PlainText
            color: Theme.cyan
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
            font.bold: true
        }
        Item {  // keeps the rows together when neither the dry run nor the log fills the height
            Layout.fillHeight: true
            visible: !dryScroll.visible && !log.visible
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "copyCommands"
            visible: dlg.apply.commands !== "" && dlg.phase === "needs-sudo"
            text: "Copy commands"
            onClicked: dlg.apply.copyCommands()
        }
        DangerButton {
            objectName: "stopApply"
            visible: dlg.phase === "running"
            enabled: dlg.apply.stopEnabled
            text: dlg.apply.stopLabel
            onClicked: dlg.apply.stop()
        }
        Button {
            objectName: "retryApply"
            visible: dlg.phase === "needs-sudo" || dlg.phase === "failed" || dlg.phase === "stopped"
            text: "Retry"
            onClicked: dlg.apply.retry()
        }
        Button {
            objectName: "applyClose"
            text: dlg.phase === "confirm" || dlg.phase === "checking" ? "Cancel" : "Close"
            enabled: !dlg.working || dlg.phase === "checking"
            onClicked: dlg.apply.close()
        }
        DangerButton {
            objectName: "applyConfirm"
            visible: dlg.phase === "confirm"
            text: "Apply"
            onClicked: dlg.apply.confirm(message.text)
        }
    }
}
