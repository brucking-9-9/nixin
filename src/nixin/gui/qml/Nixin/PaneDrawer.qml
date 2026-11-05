pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The details pane on a narrow window (a page below Theme.stackBelow): a panel over the
// right part of the list, opened by a selection, closed by its back button or Escape. The
// page reparents its one DetailsPane/OptionPane into `slot` while it is stacked, so the
// same pane (and its objectNames) serves both layouts.
Item {
    id: drawer

    required property bool open
    property string backName: "drawerBack"
    readonly property alias slot: slot

    signal closeRequested()

    visible: open
    z: 2
    width: Math.min(parent ? parent.width - 24 : Theme.drawerW, Theme.drawerW)

    // a click on the panel never reaches the list under it
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onWheel: wheel => wheel.accepted = true
    }
    Rectangle {
        anchors.fill: parent
        color: Theme.panel
        radius: Theme.radius
        border.width: 1
        border.color: Theme.line
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        Button {
            objectName: drawer.backName
            text: "‹ back to the list"
            flat: true
            Layout.alignment: Qt.AlignLeft
            onClicked: drawer.closeRequested()
        }
        Item {
            id: slot
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
    }
    Keys.onEscapePressed: drawer.closeRequested()
}
