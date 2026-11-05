pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// update: `update --dry-run` (the command, the commit the lock has, the one it moves to and
// what `outdated` said that changes) → Update → `update` runs (nix fetches the new nixpkgs)
// → Pending, where Plan builds it and Apply… switches, as for any other change. The same
// dialog serves `update --undo` (flake.lock back to the committed one): Undo instead.
Dialog {
    id: dlg

    required property var packages
    readonly property string phase: packages.updatePhase
    readonly property bool want: packages.updateOpen
    readonly property bool working: phase === "checking" || phase === "running"
    readonly property bool undo: packages.updateConfirmLabel === "Undo"

    objectName: "updateDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 760, 820)
    title: packages.updateTitle + ({ checking: " — checking", running: " — running" }[phase] || "")
    closePolicy: phase === "running" ? Popup.NoAutoClose : Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onClosed: if (packages.updateOpen) packages.cancelUpdate()

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
                text: dlg.phase === "checking" ? (dlg.undo ? "update --undo --dry-run: the committed lock against the working one…"
                                                           : "update --dry-run: the command and the commit the lock has…")
                                               : (dlg.undo ? "git checkout HEAD -- flake.lock…"
                                                           : "nix flake update: fetching the new nixpkgs (about a minute the first time)…")
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
        }

        FieldList {
            objectName: "updateFields"
            Layout.fillWidth: true
            visible: dlg.phase !== "checking"
            model: dlg.packages.updateFields
            labelWidth: 80
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "updateCancel"
            text: "Cancel"
            enabled: dlg.phase !== "running"
            onClicked: dlg.packages.cancelUpdate()
        }
        Button {
            objectName: "updateConfirm"
            text: dlg.packages.updateConfirmLabel
            highlighted: true
            enabled: dlg.phase === "confirm"
            onClicked: dlg.packages.confirmUpdate()
        }
    }
}
