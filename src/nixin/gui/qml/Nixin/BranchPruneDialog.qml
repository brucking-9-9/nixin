pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Prune merged…: what `branches prune --dry-run` would delete (and what it keeps, why),
// "also on the hub" (the dry run again with --hub), then `branches prune [--hub]`.
Dialog {
    id: dlg

    required property var branches
    readonly property bool want: branches.pruneOpen
    readonly property string phase: branches.prunePhase

    objectName: "branchPruneDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 720, 720)
    height: Math.min(parent ? parent.height - 80 : 560, 560)
    title: "prune merged branches" + ({ checking: " — checking", pruning: " — deleting" }[phase] || "")
    closePolicy: phase === "pruning" ? Popup.NoAutoClose : Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onClosed: if (branches.pruneOpen) branches.cancelPrune()

    contentItem: ColumnLayout {
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            BusyIndicator {
                running: dlg.phase !== "confirm"
                visible: dlg.phase !== "confirm"
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
            }
            Text {
                Layout.fillWidth: true
                text: dlg.phase === "checking" ? "branches prune --dry-run" + (dlg.branches.pruneHub ? " --hub" : "") + ": what main already contains…"
                    : dlg.phase === "pruning" ? "deleting…"
                    : dlg.branches.pruneCount > 0 ? "these go (main contains every one of them):"
                    : "nothing to delete"
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
        }

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            FieldList {
                objectName: "pruneList"
                width: scroll.availableWidth - 12
                model: dlg.branches.prune
                labelWidth: 110
            }
        }

        CheckBox {
            objectName: "pruneHub"
            text: "also on the hub (git push --delete for the hub's merged branches)"
            checked: dlg.branches.pruneHub
            enabled: dlg.phase === "confirm"
            onToggled: dlg.branches.setPruneHub(checked)
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "pruneCancel"
            text: "Cancel"
            enabled: dlg.phase !== "pruning"
            onClicked: dlg.branches.cancelPrune()
        }
        DangerButton {
            objectName: "pruneConfirm"
            text: dlg.branches.pruneCount > 0 ? "Delete " + dlg.branches.pruneCount : "Delete"
            enabled: dlg.phase === "confirm" && dlg.branches.pruneCount > 0
            onClicked: dlg.branches.confirmPrune()
        }
    }
}
