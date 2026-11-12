pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Merge selected…: the ticked branches, where the result is built, "build first" (on: main
// moves only when the merged config builds) and "push to the hub", then `branches merge`.
Dialog {
    id: dlg

    required property var branches
    readonly property bool want: branches.mergeOpen

    objectName: "branchMergeDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 620, 620)
    title: "merge into main"
    closePolicy: Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onOpened: {
        build.checked = true;
        push.checked = false;
    }
    onClosed: if (branches.mergeOpen) branches.cancelMerge()

    contentItem: ColumnLayout {
        spacing: Theme.gap

        Text {
            Layout.fillWidth: true
            text: dlg.branches.mergeNames.length === 1 ? "this branch, in this order:" : "these " + dlg.branches.mergeNames.length + " branches, in this order:"
            textFormat: Text.PlainText
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        Repeater {
            model: dlg.branches.mergeNames
            Text {
                required property string modelData
                required property int index
                objectName: "mergeName-" + index
                Layout.fillWidth: true
                Layout.leftMargin: 8
                text: (index + 1) + ".  " + modelData
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.cyan
                font.family: Theme.mono
                font.pixelSize: Theme.fontSize
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 4
            spacing: 6
            Text {
                text: "built on"
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            Text {
                objectName: "mergeHost"
                Layout.fillWidth: true
                text: dlg.branches.mergeHost + " (the host's toplevel, in a throwaway worktree)"
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
        }
        CheckBox {
            id: build
            objectName: "mergeBuild"
            text: "build first: main moves only if the merged config builds"
            checked: true
        }
        CheckBox {
            id: push
            objectName: "mergePush"
            text: "push main to the hub afterwards"
        }
        Text {
            Layout.fillWidth: true
            text: "Nothing is switched: Pending's Plan and Apply take it from there (on main)."
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "mergeCancel"
            text: "Cancel"
            onClicked: dlg.branches.cancelMerge()
        }
        Button {
            objectName: "mergeConfirm"
            text: "Merge"
            highlighted: true
            onClicked: dlg.branches.confirmMerge(build.checked, push.checked)
        }
    }
}
