pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Applications by category (`browse`): the freedesktop main categories on the left with
// how many are installed and how many search.nixos.org lists, the category's apps in the
// middle (installed ●, well-known ★, then search.nixos.org's most popular), the selected
// app on the right in the shared details pane: icon, summary, chips, screenshots (fetched
// now, not before), then info, versions and where, with Add / Remove.
Item {
    id: page

    required property var backend
    readonly property var vm: backend.browse

    objectName: "browsePage"

    function focusList() {
        apps.forceActiveFocus();
    }

    Connections {
        target: page.vm
        function onChanged() {
            if (page.vm.selected >= 0 && apps.currentIndex !== page.vm.selected)
                apps.currentIndex = page.vm.selected;
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad

        ColumnLayout {
            Layout.fillHeight: true
            Layout.preferredWidth: 190
            Layout.fillWidth: false
            spacing: Theme.gap

            RowLayout {
                Layout.fillWidth: true
                SectionHead {
                    Layout.fillWidth: true
                    text: "categories"
                    loading: page.vm.loading
                }
                Button {
                    objectName: "browseRefresh"
                    text: "Refresh"
                    flat: true
                    enabled: !page.vm.loading && !page.vm.appsLoading
                    ToolTip.visible: hovered
                    ToolTip.text: "ask search.nixos.org again (--refresh)"
                    onClicked: page.vm.refresh()
                }
            }
            ListView {
                id: cats
                objectName: "browseCategories"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.vm.categories
                boundsBehavior: Flickable.StopAtBounds
                delegate: ItemDelegate {
                    id: cat
                    required property string key
                    required property string label
                    required property string count
                    objectName: "cat-" + key
                    width: cats.width
                    highlighted: page.vm.category === key
                    focusPolicy: Qt.NoFocus
                    onClicked: page.vm.setCategory(key)
                    contentItem: ColumnLayout {
                        spacing: 0
                        Text {
                            text: cat.label
                            textFormat: Text.PlainText
                            color: cat.highlighted ? Theme.cyan : Theme.fg
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize
                            font.bold: cat.highlighted
                        }
                        Text {
                            visible: text !== ""
                            text: cat.count
                            textFormat: Text.PlainText
                            color: Theme.dim2
                            font.family: Theme.font
                            font.pixelSize: Theme.small
                        }
                    }
                }
            }
            Text {
                objectName: "browseNote"
                Layout.fillWidth: true
                visible: text !== ""
                text: page.vm.note
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 4
                elide: Text.ElideRight
                color: Theme.style(page.vm.noteStyle)
                font.family: Theme.font
                font.pixelSize: Theme.small
            }
        }

        Rectangle {
            Layout.fillHeight: true
            implicitWidth: 1
            color: Theme.line
        }

        ColumnLayout {
            Layout.fillHeight: true
            Layout.fillWidth: false
            Layout.preferredWidth: Math.max(330, page.width * 0.36)
            spacing: Theme.gap

            SectionHead {
                Layout.fillWidth: true
                text: page.vm.label
                loading: page.vm.appsLoading
            }
            ListView {
                id: apps
                objectName: "browseApps"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.vm.apps
                keyNavigationEnabled: true
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: 0
                ScrollBar.vertical: ScrollBar { }
                onCountChanged: currentIndex = count > 0 ? 0 : -1
                Keys.onReturnPressed: page.vm.select(currentIndex)
                Keys.onEnterPressed: page.vm.select(currentIndex)
                Keys.onSpacePressed: page.vm.select(currentIndex)
                delegate: AppRow {
                    width: apps.width - 8
                    selected: page.vm.selected === index && page.backend.details.owner === "browse"
                    current: apps.activeFocus && apps.currentIndex === index
                    onClicked: {
                        apps.currentIndex = index;
                        apps.forceActiveFocus();
                        page.vm.select(index);
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: apps.count === 0
                    text: page.vm.appsLoading ? "browse " + page.vm.category + ": asking search.nixos.org…" : page.vm.appsNote
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: page.vm.appsLoading ? Theme.dim2 : Theme.style(page.vm.appsNoteStyle)
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                }
            }
            Text {
                objectName: "browseFooter"
                Layout.fillWidth: true
                text: page.vm.footer
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.small
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
            owner: "browse"
            hint: "Select an application to see its icon, screenshots and categories, what it is, and whether " + page.backend.host + " has it."
        }
    }
}
