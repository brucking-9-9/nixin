pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Pins: what `nixin pin` holds back on this host (`pins`), each at its nixpkgs commit and
// whether it still holds anything (stale: the lock is at the pinned revision). Check runs
// `pins --check` (one eval: do the pins hold, the compatibility checks against the lock as
// it is now); Unpin drops the selected one through the writer's diff dialog; Pin… holds
// something at a chosen revision (PinDialog). Holding at the lock's own revision also
// starts on Packages › Updates (the row's hold button, or "hold kernel").
Item {
    id: page

    required property var backend
    readonly property var vm: backend.pins

    objectName: "pinsPage"

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
                text: "Pins"
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
                objectName: "pinsSummary"
                Layout.fillWidth: true
                text: page.vm.loading ? (page.vm.checking ? "pins --check: one eval of the host (seconds)…" : "pins…")
                                      : page.vm.summary
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
            Button {
                objectName: "pinsRefresh"
                text: "Refresh"
                flat: true
                enabled: !page.vm.loading
                onClicked: page.vm.load()
            }
            Button {
                objectName: "pinsCheck"
                text: "Check"
                highlighted: true
                enabled: !page.vm.loading && list.count > 0 && page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "pins --check: do the pins still hold, and the compatibility checks against the lock as it is now (one eval)"
                onClicked: page.vm.check()
            }
            Button {
                objectName: "pinsPin"
                text: "Pin…"
                enabled: !page.vm.loading && page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "hold the kernel or a package at a nixpkgs revision: the committed one, the running system's, or a commit you name (previewed first)"
                onClicked: page.vm.startPin()
            }
            Button {
                objectName: "pinsUnpin"
                text: "Unpin…"
                enabled: page.vm.selected >= 0 && page.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "unpin the selected pin (previewed first); the lock update then brings it along"
                onClicked: page.vm.unpin(page.vm.selected)
            }
        }

        Text {
            objectName: "pinsNote"
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
            objectName: "pinsList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.vm.pins
            keyNavigationEnabled: true
            boundsBehavior: Flickable.StopAtBounds
            highlightMoveDuration: 0
            ScrollBar.vertical: ScrollBar { }
            onCountChanged: currentIndex = count > 0 ? 0 : -1
            Keys.onReturnPressed: page.vm.select(currentIndex)
            Keys.onEnterPressed: page.vm.select(currentIndex)
            Keys.onSpacePressed: page.vm.select(currentIndex)
            delegate: PinRow {
                width: list.width - 8
                selected: page.vm.selected === index
                current: list.activeFocus && list.currentIndex === index
                onClicked: {
                    list.currentIndex = index;
                    list.forceActiveFocus();
                    page.vm.select(index);
                }
            }
        }

        Text {
            objectName: "pinsDetail"
            Layout.fillWidth: true
            visible: text !== ""
            text: page.vm.detail
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }

        SectionHead {
            visible: page.vm.checks.count > 0
            text: page.vm.checked ? "checks against the lock as it is now" : "checks"
        }
        FieldList {
            objectName: "pinsChecks"
            Layout.fillWidth: true
            visible: page.vm.checks.count > 0
            model: page.vm.checks
            labelWidth: 50
        }
    }
}
