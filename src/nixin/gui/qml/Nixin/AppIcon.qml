pragma ComponentBehavior: Bound

import QtQuick

// An application's icon: the image when there is a file for it (an installed app's own,
// or one the details pane fetched), else a tile with the name's first letter.
Item {
    id: icon

    property string source: ""
    property string name: ""

    implicitWidth: 40
    implicitHeight: 40

    Rectangle {
        anchors.fill: parent
        visible: img.status !== Image.Ready
        radius: Theme.radius * 2
        color: Theme.raised
        border.width: 1
        border.color: Theme.line
        Text {
            anchors.centerIn: parent
            text: icon.name.length > 0 ? icon.name.charAt(0).toUpperCase() : "?"
            textFormat: Text.PlainText
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Math.round(icon.height * 0.45)
            font.bold: true
        }
    }
    Image {
        id: img
        objectName: "appIconImage"
        anchors.fill: parent
        source: icon.source
        sourceSize.width: icon.width * 2
        sourceSize.height: icon.height * 2
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
        visible: status === Image.Ready
    }
}
