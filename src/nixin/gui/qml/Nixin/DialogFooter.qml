pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts

// A dialog's button row, right-aligned, with the dialog's padding around it.
Item {
    id: footer

    default property alias content: row.data

    implicitHeight: row.implicitHeight + 2 * Theme.pad
    implicitWidth: row.implicitWidth + 2 * Theme.pad

    RowLayout {
        id: row
        anchors.right: parent.right
        anchors.rightMargin: Theme.pad + 4
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.gap
    }
}
