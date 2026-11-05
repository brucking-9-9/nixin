pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Installed: what `list` says the host has (system and each Home Manager user), narrowed by
// the filter and the scope without another eval. Updates: what `outdated` says a nixpkgs
// update would change (the lock's next commit, or --published against search.nixos.org),
// the kernel line (with its hold), the summary, and "Update nixpkgs lock" (update → Pending);
// a row's hold pins it. The selected row opens on the right, for its user, with Remove.
Item {
    id: page

    required property var backend
    readonly property var vm: backend.packages

    objectName: "packagesPage"

    function focusList() {
        (page.vm.tab === "installed" ? installed : updates).forceActiveFocus();
    }

    // a row selected from elsewhere (a script, a test) is the cursor, scrolled into view
    Connections {
        target: page.vm
        function onChanged() {
            if (page.vm.selected >= 0 && installed.currentIndex !== page.vm.selected)
                installed.currentIndex = page.vm.selected;
            if (page.vm.upSelected >= 0 && updates.currentIndex !== page.vm.upSelected)
                updates.currentIndex = page.vm.upSelected;
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad

        ColumnLayout {
            Layout.fillHeight: true
            Layout.fillWidth: false
            Layout.preferredWidth: Math.max(360, page.width * 0.5)
            spacing: Theme.gap

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                TabBar {
                    id: tabs
                    objectName: "pkTabs"
                    Layout.fillWidth: true
                    currentIndex: page.vm.tab === "updates" ? 1 : 0
                    Repeater {
                        model: ["installed", "updates"]
                        TabButton {
                            required property string modelData
                            objectName: "pk-tab-" + modelData
                            text: modelData
                            width: implicitWidth
                            focusPolicy: Qt.NoFocus
                            onClicked: {
                                page.vm.setTab(modelData);
                                page.focusList();
                            }
                        }
                    }
                }
                Button {
                    objectName: "pkRefresh"
                    text: "Refresh"
                    flat: true
                    enabled: page.vm.tab === "installed" ? !page.vm.listLoading : !page.vm.updatesLoading
                    ToolTip.visible: hovered
                    ToolTip.text: "evaluate again (--refresh)"
                    onClicked: page.vm.refresh()
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: tabs.currentIndex

                // --- installed
                ColumnLayout {
                    spacing: Theme.gap

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.gap
                        TextField {
                            id: filter
                            objectName: "pkFilter"
                            Layout.fillWidth: true
                            placeholderText: "filter by name…"
                            onTextEdited: page.vm.setFilter(text)
                            Keys.onDownPressed: installed.forceActiveFocus()
                            Keys.onEscapePressed: installed.forceActiveFocus()
                            onAccepted: installed.forceActiveFocus()
                        }
                        ComboBox {
                            id: scope
                            objectName: "pkScope"
                            implicitWidth: 170
                            model: page.vm.scopeLabels
                            currentIndex: Math.max(0, page.vm.scopeKeys.indexOf(page.vm.scope))
                            displayText: "for: " + currentText
                            onActivated: index => page.vm.setScope(page.vm.scopeKeys[index])
                        }
                    }

                    ListView {
                        id: installed
                        objectName: "pkList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.vm.packages
                        keyNavigationEnabled: true
                        boundsBehavior: Flickable.StopAtBounds
                        highlightMoveDuration: 0
                        ScrollBar.vertical: ScrollBar { }
                        onCountChanged: currentIndex = count > 0 ? 0 : -1
                        Keys.onReturnPressed: page.vm.select(currentIndex)
                        Keys.onEnterPressed: page.vm.select(currentIndex)
                        Keys.onSpacePressed: page.vm.select(currentIndex)
                        delegate: PackageRow {
                            width: installed.width - 8
                            selected: page.vm.selected === index
                            current: installed.activeFocus && installed.currentIndex === index
                            onClicked: {
                                installed.currentIndex = index;
                                installed.forceActiveFocus();
                                page.vm.select(index);
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            width: parent.width - 40
                            visible: installed.count === 0
                            text: page.vm.listLoading ? "list: evaluating " + page.backend.configLabel + " (a few seconds cold)…"
                                                      : page.vm.listNote
                            textFormat: Text.PlainText
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            color: page.vm.listLoading ? Theme.dim2 : Theme.style(page.vm.listNoteStyle)
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize
                        }
                    }

                    Text {
                        objectName: "pkFooter"
                        Layout.fillWidth: true
                        text: page.vm.footer
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.dim2
                        font.family: Theme.font
                        font.pixelSize: Theme.small
                    }
                }

                // --- updates
                ColumnLayout {
                    spacing: Theme.gap

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.gap
                        ComboBox {
                            id: mode
                            objectName: "upMode"
                            implicitWidth: 250
                            model: ["lock", "published"]
                            currentIndex: Math.max(0, model.indexOf(page.vm.mode))
                            displayText: currentText === "lock" ? "vs the lock's next commit" : "vs search.nixos.org"
                            ToolTip.visible: hovered
                            ToolTip.text: currentText === "lock" ? "outdated: what `nix flake update nixpkgs` would change (exact)"
                                                                 : "outdated --published: newer versions on the channel's search index"
                            onActivated: index => page.vm.setMode(model[index])
                            delegate: ItemDelegate {
                                required property string modelData
                                required property int index
                                width: mode.width
                                text: modelData === "lock" ? "vs the lock's next commit" : "vs search.nixos.org"
                                highlighted: mode.highlightedIndex === index
                            }
                        }
                        Item { Layout.fillWidth: true }
                        DangerButton {
                            objectName: "undoButton"
                            text: "Undo lock move…"
                            visible: page.vm.canUndo
                            enabled: page.backend.busy === "" && !page.vm.updatesLoading
                            ToolTip.visible: hovered
                            ToolTip.text: "update --undo: flake.lock back to the committed one (asks first); pins and other changes stay"
                            onClicked: page.vm.startUndo()
                        }
                        Button {
                            objectName: "updateButton"
                            text: "Update nixpkgs lock…"
                            highlighted: true
                            enabled: page.vm.canUpdate && page.backend.busy === "" && !page.vm.updatesLoading
                            ToolTip.visible: hovered
                            ToolTip.text: "nix flake update nixpkgs in the workspace (asks first); then Pending: Plan, Apply"
                            onClicked: page.vm.startUpdate()
                        }
                    }

                    Text {
                        objectName: "upHead"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: page.vm.head
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme.dim2
                        font.family: Theme.font
                        font.pixelSize: Theme.small + 1
                    }

                    ListView {
                        id: updates
                        objectName: "upList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.vm.updates
                        keyNavigationEnabled: true
                        boundsBehavior: Flickable.StopAtBounds
                        highlightMoveDuration: 0
                        ScrollBar.vertical: ScrollBar { }
                        onCountChanged: currentIndex = count > 0 ? 0 : -1
                        Keys.onReturnPressed: page.vm.selectUpdate(currentIndex)
                        Keys.onEnterPressed: page.vm.selectUpdate(currentIndex)
                        Keys.onSpacePressed: page.vm.selectUpdate(currentIndex)
                        delegate: UpdateRow {
                            width: updates.width - 8
                            selected: page.vm.upSelected === index
                            current: updates.activeFocus && updates.currentIndex === index
                            onClicked: {
                                updates.currentIndex = index;
                                updates.forceActiveFocus();
                                page.vm.selectUpdate(index);
                            }
                            onHoldClicked: page.vm.hold(index)
                        }

                        Text {
                            anchors.centerIn: parent
                            width: parent.width - 40
                            visible: updates.count === 0
                            text: page.vm.updatesLoading ? (page.vm.mode === "lock"
                                                            ? "outdated: two package-list evals (about a minute when nixpkgs has a new commit)…"
                                                            : "outdated --published: one eval and a few search.nixos.org queries…")
                                                         : page.vm.updatesNote !== "" ? page.vm.updatesNote : page.vm.summary
                            textFormat: Text.PlainText
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            color: page.vm.updatesNote !== "" && !page.vm.updatesLoading ? Theme.style(page.vm.updatesNoteStyle) : Theme.dim2
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.vm.kernel !== ""
                        spacing: Theme.gap
                        Text {
                            objectName: "upKernel"
                            Layout.fillWidth: true
                            text: page.vm.kernel
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.style(page.vm.kernelStyle)
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize
                        }
                        Button {
                            objectName: "holdKernel"
                            visible: page.vm.kernelHold !== ""
                            text: page.vm.kernelHold
                            flat: true
                            enabled: page.backend.busy === ""
                            ToolTip.visible: hovered
                            ToolTip.text: "pin kernel: keep the kernel while the rest of nixpkgs moves on (no kernel rebuild); unpin to let it go"
                            onClicked: page.vm.holdKernel()
                        }
                    }
                    Text {
                        objectName: "upSummary"
                        Layout.fillWidth: true
                        visible: text !== "" && updates.count > 0
                        text: page.vm.summary
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme.cyan
                        font.family: Theme.font
                        font.pixelSize: Theme.fontSize
                        font.bold: true
                    }
                    Text {
                        objectName: "upNotes"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: page.vm.notes
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        color: Theme.dim2
                        font.family: Theme.font
                        font.pixelSize: Theme.small
                    }
                }
            }
        }

        Rectangle {
            Layout.fillHeight: true
            implicitWidth: 1
            color: Theme.line
        }

        DetailsPane {
            Layout.fillWidth: true
            Layout.fillHeight: true
            backend: page.backend
            owner: "packages"
            hint: "Select a package to see what it is, its versions, and where it is listed on " + page.backend.host + "; Remove takes it out."
        }
    }
}
