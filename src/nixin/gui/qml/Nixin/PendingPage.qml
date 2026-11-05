pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// What an apply would bring in (status.pending), what this session wrote and applied, and
// the plan: nix's build output while it runs, then the plan and the closure diff.
Item {
    id: page

    required property var backend
    readonly property var p: backend.pending

    objectName: "pendingPage"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Text {
                text: "Pending"
                color: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.big
                font.bold: true
            }
            Text {
                Layout.fillWidth: true
                text: "on " + page.backend.host + " · " + page.backend.configLabel
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
            Button {
                objectName: "refreshButton"
                text: "Refresh"
                flat: true
                enabled: !page.p.loading
                onClicked: page.p.refresh()
            }
            Button {
                objectName: "syncButton"
                text: "Sync"
                enabled: !page.p.syncing
                ToolTip.visible: hovered
                ToolTip.text: "git fetch of the hub repository"
                onClicked: page.p.startSync()
            }
            DangerButton {
                objectName: "pendingUndo"
                text: "Undo lock move…"
                visible: page.p.canUndo
                enabled: page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "update --undo: flake.lock back to the committed one (asks first)"
                onClicked: page.p.startUndo()
            }
            Button {
                objectName: "planButton"
                text: "Plan"
                highlighted: true
                enabled: page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "build the workspace and diff it against the running system"
                onClicked: page.p.startPlan()
            }
            DangerButton {
                objectName: "stopButton"
                text: page.p.stopLabel
                visible: page.p.planning
                enabled: page.p.stopEnabled
                ToolTip.visible: hovered
                ToolTip.text: "stop the build (nothing is applied by a plan)"
                onClicked: page.p.stop()
            }
            DangerButton {
                objectName: "applyButton"
                text: "Apply…"
                enabled: page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "commit, pull into the authoritative tree, switch (asks first)"
                onClicked: page.p.startApply()
            }
        }

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                width: scroll.availableWidth - 12
                spacing: Theme.gap

                SectionHead {
                    Layout.fillWidth: true
                    text: "pending"
                    loading: page.p.loading
                }
                FieldList {
                    objectName: "pending"
                    Layout.fillWidth: true
                    model: page.p.pending
                    note: page.p.loading && page.p.pending.count === 0
                          ? "status: evaluating the config (about a minute cold, under a second warm)"
                          : page.p.statusNote
                    noteStyle: page.p.statusNote !== "" ? page.p.statusNoteStyle : "dim"
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    text: "this session"
                }
                FieldList {
                    objectName: "session"
                    Layout.fillWidth: true
                    model: page.p.session
                    note: page.p.sessionNote
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    text: "plan"
                    loading: page.p.planning
                }
                BuildTracker {
                    objectName: "planBuild"
                    Layout.fillWidth: true
                    visible: page.p.planning && (page.p.buildHead !== "" || page.p.stepNote !== "")
                    head: page.p.buildHead
                    note: page.p.stepNote
                    rows: page.p.build
                    tail: page.p.tail
                }
                LogView {
                    objectName: "planLog"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 220
                    visible: page.p.planning || count > 0
                    model: page.p.log
                }
                FieldList {
                    objectName: "plan"
                    Layout.fillWidth: true
                    model: page.p.plan
                    note: page.p.planNote
                    noteStyle: page.p.planNoteStyle
                }
                Text {
                    objectName: "closureHead"
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    visible: page.p.closureHead !== ""
                    text: page.p.closureHead
                    textFormat: Text.PlainText
                    color: Theme.cyan
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                    font.bold: true
                }
                ClosureTable {
                    objectName: "closure"
                    Layout.fillWidth: true
                    visible: count > 0
                    model: page.p.closure
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    visible: page.p.synced
                    text: "sync"
                    loading: page.p.syncing
                }
                FieldList {
                    objectName: "sync"
                    Layout.fillWidth: true
                    visible: page.p.synced
                    model: page.p.sync
                }
                Item { implicitHeight: 90 }  // room above the notice bar
            }
        }
    }
}
