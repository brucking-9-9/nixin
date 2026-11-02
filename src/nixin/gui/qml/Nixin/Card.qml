import QtQuick

// A panel with a section head; children go under it.
Rectangle {
    id: card

    property string title: ""
    property bool loading: false
    default property alias content: body.data

    implicitHeight: body.implicitHeight + head.implicitHeight + 3 * Theme.gap + 8
    color: Theme.panel
    radius: Theme.radius
    border.width: 1
    border.color: Theme.line

    SectionHead {
        id: head
        x: Theme.pad
        y: Theme.gap + 4
        width: card.width - 2 * Theme.pad
        text: card.title
        loading: card.loading
    }
    Column {
        id: body
        x: Theme.pad
        y: head.y + head.height + Theme.gap
        width: card.width - 2 * Theme.pad
        spacing: Theme.gap
    }
}
