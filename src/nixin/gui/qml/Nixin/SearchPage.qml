pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The query (typing is debounced, Enter searches at once and moves to the results), the
// kind filter, the channel, the results, the CLI's source footer; the details of the
// selected row on the right.
Item {
    id: page

    required property var backend
    readonly property var vm: backend.search

    objectName: "searchPage"

    function focusQuery() {
        query.forceActiveFocus();
        query.selectAll();
    }

    // A search started elsewhere (a script, a test) shows its query; never while typing,
    // where the stripped query would eat a trailing space.
    Connections {
        target: page.vm
        function onChanged() {
            if (!query.activeFocus && query.text !== page.vm.query)
                query.text = page.vm.query;
        }
    }

    Timer {
        id: debounce
        interval: 350
        onTriggered: page.vm.search(query.text)
    }

    function focusList() {
        results.forceActiveFocus();
    }

    // the two layouts (see above): one pane, reparented between the slot and the drawer
    readonly property bool stacked: width < Theme.stackBelow + 0
    property bool drawerOpen: false
    property int seenShows: -1

    Connections {
        target: page.backend.details
        function onChanged() {
            const d = page.backend.details;
            if (d.present && d.owner === "search" && d.shows !== page.seenShows) {
                page.seenShows = d.shows;
                page.drawerOpen = true;
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad

        ColumnLayout {
            Layout.fillHeight: true
            Layout.fillWidth: page.stacked
            Layout.preferredWidth: page.stacked ? -1
                : Math.max(330, Math.min(page.width * 0.46, page.width - 2 * Theme.pad - 29 - 400))
            spacing: Theme.gap

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                TextField {
                    id: query
                    objectName: "query"
                    Layout.fillWidth: true
                    placeholderText: "search packages, options, programs…"
                    onTextEdited: debounce.restart()
                    onAccepted: {
                        debounce.stop();
                        page.vm.search(text);
                        results.forceActiveFocus();
                    }
                    Keys.onDownPressed: results.forceActiveFocus()
                    Keys.onEscapePressed: results.forceActiveFocus()
                }
                ComboBox {
                    id: channel
                    objectName: "channel"
                    model: ["unstable", "stable"]
                    currentIndex: Math.max(0, model.indexOf(page.backend.channel))
                    implicitWidth: 120
                    onActivated: index => page.backend.setChannel(model[index])
                }
            }

            TabBar {
                id: kinds
                objectName: "kinds"
                Layout.fillWidth: true
                readonly property var names: page.vm.kinds()
                currentIndex: names.indexOf(page.vm.kind)
                Repeater {
                    model: kinds.names
                    TabButton {
                        required property string modelData
                        objectName: "kind-" + modelData
                        text: modelData
                        width: implicitWidth
                        focusPolicy: Qt.NoFocus
                        onClicked: page.vm.setKind(modelData)
                    }
                }
            }

            ListView {
                id: results
                objectName: "results"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.vm.results
                keyNavigationEnabled: true
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: 0
                ScrollBar.vertical: ScrollBar { }
                onCountChanged: currentIndex = count > 0 ? 0 : -1  // new results: the cursor on the first
                Keys.onReturnPressed: page.vm.select(currentIndex)
                Keys.onEnterPressed: page.vm.select(currentIndex)
                Keys.onSpacePressed: page.vm.select(currentIndex)
                delegate: HitRow {
                    width: results.width - 8
                    selected: page.vm.selected === index
                    current: results.activeFocus && results.currentIndex === index
                    onClicked: {
                        results.currentIndex = index;
                        results.forceActiveFocus();
                        page.vm.select(index);
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    visible: results.count === 0
                    text: page.vm.loading ? "searching…" : page.vm.message
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: Theme.dim2
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                }
            }

            Text {
                objectName: "source"
                Layout.fillWidth: true
                text: page.vm.source
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
            visible: !page.stacked
            color: Theme.line
        }

        Item {
            id: paneSlot
            visible: !page.stacked
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 0
        }
    }

    PaneDrawer {
        id: drawer
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        anchors.margins: Theme.pad
        open: page.stacked && page.drawerOpen
        backName: "searchBack"
        onCloseRequested: {
            page.drawerOpen = false;
            page.focusList();
        }
    }

    DetailsPane {
        parent: page.stacked ? drawer.slot : paneSlot
        anchors.fill: parent
        backend: page.backend
    }
}
