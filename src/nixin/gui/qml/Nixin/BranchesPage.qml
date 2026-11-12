pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Branches: the config's branches against main (`branches`): unmerged first, newest first,
// then the merged. Tick clean ones and Merge selected… (BranchMergeDialog: built first,
// pushed or not) merges them into main, the build's log streaming here with the tracker;
// Prune merged… (BranchPruneDialog) lists what `branches prune --dry-run` would delete and
// deletes it. A row's detail (its commits, files, conflicts, worktree) is selectable text.
Item {
    id: page

    required property var backend
    readonly property var vm: backend.branches
    readonly property bool hasResult: vm.merging || vm.resultTitle !== "" || vm.log.count > 0

    objectName: "branchesPage"

    function focusList() {
        list.forceActiveFocus();
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Text {
                text: "Branches"
                color: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.big
                font.bold: true
            }
            BusyIndicator {
                running: page.vm.loading
                visible: page.vm.loading
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
            }
            Text {
                objectName: "branchesHead"
                Layout.fillWidth: true
                text: page.vm.loading && page.vm.head === "" ? "branches…" : page.vm.head
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
            Button {
                objectName: "branchesRefresh"
                text: "Refresh"
                flat: true
                enabled: !page.vm.loading
                onClicked: page.vm.startBranches()
            }
            DangerButton {
                objectName: "branchesPrune"
                text: "Prune merged…"
                enabled: page.backend.busy === "" && !page.vm.loading
                ToolTip.visible: hovered
                ToolTip.text: "delete the branches main already contains (lists them first; the hub's too if you tick it)"
                onClicked: page.vm.openPrune()
            }
            DangerButton {
                objectName: "branchesStop"
                text: page.vm.stopLabel
                visible: page.vm.merging
                enabled: page.vm.stopEnabled
                ToolTip.visible: hovered
                ToolTip.text: "stop the build (main does not move)"
                onClicked: page.vm.stop()
            }
            Button {
                objectName: "branchesMerge"
                text: page.vm.checked.length > 0 ? "Merge selected (" + page.vm.checked.length + ")…" : "Merge selected…"
                highlighted: true
                enabled: page.vm.checked.length > 0 && page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "merge the ticked branches into main, built first (asks first)"
                onClicked: page.vm.openMerge()
            }
        }

        Text {
            objectName: "branchesNote"
            Layout.fillWidth: true
            visible: page.vm.note !== "" && list.count === 0
            text: page.vm.note
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.style(page.vm.noteStyle)
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }

        ListView {
            id: list
            objectName: "branchesList"
            Layout.fillWidth: true
            // the whole page while there is no result; then about a third, the run below
            Layout.fillHeight: !page.hasResult
            Layout.preferredHeight: page.hasResult ? Math.min(contentHeight, Math.max(150, page.height * 0.36)) : -1
            clip: true
            model: page.vm.branches
            keyNavigationEnabled: true
            boundsBehavior: Flickable.StopAtBounds
            highlightMoveDuration: 0
            spacing: 1
            ScrollBar.vertical: ScrollBar { }
            onCountChanged: if (currentIndex >= count || currentIndex < 0) currentIndex = count > 0 ? 0 : -1
            Keys.onReturnPressed: page.vm.select(currentIndex)
            Keys.onEnterPressed: page.vm.select(currentIndex)
            Keys.onSpacePressed: page.vm.toggle(currentIndex)
            delegate: BranchRow {
                width: list.width - 8
                selected: page.vm.selected === index
                current: list.activeFocus && list.currentIndex === index
                onClicked: {
                    list.currentIndex = index;
                    list.forceActiveFocus();
                    page.vm.select(index);
                }
                onToggled: page.vm.toggle(index)
            }
        }

        TextArea {
            objectName: "branchesDetail"
            Layout.fillWidth: true
            visible: page.vm.detail !== ""
            text: page.vm.detail
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            font.family: Theme.mono
            font.pixelSize: Theme.small + 1
        }

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.hasResult
            contentWidth: availableWidth

            ColumnLayout {
                width: scroll.availableWidth - 12
                spacing: Theme.gap

                SectionHead {
                    Layout.fillWidth: true
                    text: page.vm.resultTitle
                    loading: page.vm.merging
                }
                BuildTracker {
                    objectName: "branchesBuild"
                    Layout.fillWidth: true
                    visible: page.vm.merging && (page.vm.buildHead !== "" || page.vm.stepNote !== "")
                    head: page.vm.buildHead
                    where: page.vm.buildPlace
                    note: page.vm.stepNote
                    rows: page.vm.build
                    tail: page.vm.tail
                }
                LogView {
                    id: runLog
                    objectName: "branchesLog"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 200
                    visible: page.vm.merging || count > 0
                    model: page.vm.log
                }
                FieldList {
                    objectName: "branchesResult"
                    Layout.fillWidth: true
                    model: page.vm.result
                    labelWidth: 70
                    note: page.vm.resultNote
                    noteStyle: page.vm.resultNoteStyle
                    copyable: true
                    onCopyRequested: page.vm.copyError()
                }
                Item { implicitHeight: 90 }  // room above the notice bar
            }
        }
    }
}
