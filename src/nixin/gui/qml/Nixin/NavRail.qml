pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Search, Browse, Pending, Status, Packages, Options, with their keys; under them what runs.
Rectangle {
    id: rail

    required property var backend

    color: Theme.bg

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.gap
        spacing: 2

        Repeater {
            model: [
                { page: "search", text: "Search", key: "/" },
                { page: "browse", text: "Browse", key: "Ctrl+B" },
                { page: "pending", text: "Pending", key: "Ctrl+2" },
                { page: "status", text: "Status", key: "Ctrl+3" },
                { page: "packages", text: "Packages", key: "Ctrl+4" },
                { page: "options", text: "Options", key: "Ctrl+5" }
            ]
            delegate: ItemDelegate {
                id: item
                required property var modelData
                objectName: "nav-" + modelData.page
                Layout.fillWidth: true
                text: modelData.text
                highlighted: rail.backend.page === modelData.page
                focusPolicy: Qt.NoFocus
                onClicked: {
                    rail.backend.go(modelData.page);
                    if (modelData.page === "search")
                        rail.searchRequested();
                    else if (modelData.page === "options")
                        rail.optionsRequested();
                }
                contentItem: RowLayout {
                    spacing: 6
                    Text {
                        text: item.text
                        color: item.highlighted ? Theme.cyan : Theme.fg
                        font.family: Theme.font
                        font.pixelSize: Theme.fontSize
                        Layout.fillWidth: true
                    }
                    Text {
                        text: item.modelData.key
                        color: Theme.dim
                        font.family: Theme.font
                        font.pixelSize: Theme.small
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }

        Text {
            Layout.fillWidth: true
            Layout.margins: Theme.pad
            visible: rail.backend.busy !== ""
            text: rail.backend.busy
            color: Theme.purple
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
    }

    signal searchRequested()
    signal optionsRequested()
}
