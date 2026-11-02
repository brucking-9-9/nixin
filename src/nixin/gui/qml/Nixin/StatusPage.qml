pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// status as cards: config (path, HEAD, dirty), the lock, the running system, pending,
// counts, caches.
Item {
    id: page

    required property var backend
    readonly property var st: backend.status

    objectName: "statusPage"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Text {
                text: "Status"
                color: Theme.fg
                font.family: Theme.font
                font.pixelSize: Theme.big
                font.bold: true
            }
            BusyIndicator {
                running: page.st.loading
                visible: page.st.loading
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
            }
            Text {
                Layout.fillWidth: true
                text: page.st.loading ? "evaluating " + page.backend.configLabel + " (about a minute cold, under a second warm)"
                                      : page.backend.host + " · " + page.backend.configLabel
                textFormat: Text.PlainText
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
            }
            Button {
                objectName: "statusRefresh"
                text: "Refresh"
                enabled: !page.st.loading
                onClicked: page.st.refresh()
            }
            Button {
                objectName: "statusFull"
                text: "Full"
                flat: true
                enabled: !page.st.loading
                ToolTip.visible: hovered
                ToolTip.text: "status --full: the option index too (one to three minutes cold)"
                onClicked: page.st.refreshFull()
            }
        }

        Text {
            Layout.fillWidth: true
            visible: page.st.note !== ""
            text: page.st.note
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.style(page.st.noteStyle)
            font.family: Theme.font
            font.pixelSize: Theme.fontSize
        }

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            GridLayout {
                id: grid
                width: scroll.availableWidth - 12
                columns: width > 820 ? 2 : 1
                columnSpacing: Theme.pad
                rowSpacing: Theme.pad

                Repeater {
                    model: page.st.sections
                    Card {
                        id: card
                        required property var modelData
                        objectName: "section-" + modelData.title
                        title: modelData.title
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: grid.width / grid.columns
                        FieldList {
                            width: parent.width
                            model: card.modelData.fields
                            note: card.modelData.note
                            noteStyle: card.modelData.style || "dim"
                        }
                    }
                }
            }
        }
    }
}
