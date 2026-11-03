pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The selected option in full: what it is (type, default, example), the description, its
// value on this host and the files that set it (where), where it is declared. Set… and
// Unset, for the system or (Home Manager options) a user.
Item {
    id: pane

    required property var backend
    readonly property var o: backend.options

    objectName: "optionPane"

    Text {
        anchors.centerIn: parent
        width: parent.width - 60
        visible: !pane.o.present
        text: "Select an option to see its type, default and example, the description, its value on " + pane.backend.host + " and where that is set; Set… and Unset change it (a diff first)."
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.fontSize
    }

    ColumnLayout {
        anchors.fill: parent
        visible: pane.o.present
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                text: pane.o.badge
                color: Theme.dim
                font.family: Theme.mono
                font.pixelSize: Theme.small
            }
            Text {
                objectName: "opName"
                Layout.fillWidth: true
                text: pane.o.name
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: Theme.purple
                font.family: Theme.font
                font.pixelSize: Theme.big
                font.bold: true
            }
            Text {
                objectName: "opCue"
                text: pane.o.cue
                textFormat: Text.PlainText
                color: Theme.style(pane.o.cueStyle)
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
                elide: Text.ElideRight
                Layout.maximumWidth: pane.width * 0.4
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Button {
                objectName: "opSet"
                text: "Set…"
                highlighted: true
                enabled: pane.backend.busy === ""
                onClicked: pane.o.editOption()
            }
            DangerButton {
                objectName: "opUnset"
                text: "Unset"
                enabled: pane.backend.busy === "" && pane.o.canUnset
                ToolTip.visible: hovered && !pane.o.canUnset
                ToolTip.text: "a template: unset takes the full name"
                onClicked: pane.o.unset()
            }
            Item { Layout.fillWidth: true }
            Text {
                visible: pane.o.badge === "hm"
                text: "for"
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            ComboBox {
                id: home
                objectName: "opHome"
                visible: pane.o.badge === "hm" && pane.o.homeChoices.length > 0
                model: pane.o.homeChoices
                currentIndex: Math.max(0, pane.o.homeChoices.indexOf(pane.o.homeUser))
                displayText: "home: " + pane.o.homeUser
                implicitWidth: 170
                onActivated: index => pane.o.setHome(pane.o.homeChoices[index])
                delegate: ItemDelegate {
                    required property string modelData
                    required property int index
                    width: ListView.view.width
                    text: "home: " + modelData
                    highlighted: home.highlightedIndex === index
                }
            }
            Text {
                objectName: "opNoUser"
                visible: pane.o.badge === "hm" && pane.o.homeChoices.length === 0 && !pane.o.whereLoading
                text: "no Home Manager user"
                color: Theme.dim
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
        }

        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                width: scroll.availableWidth - 12
                spacing: Theme.gap

                SectionHead {
                    Layout.fillWidth: true
                    text: "option"
                }
                FieldList {
                    objectName: "opOption"
                    Layout.fillWidth: true
                    model: pane.o.option
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    text: "about"
                }
                TextEdit {
                    objectName: "opAbout"
                    Layout.fillWidth: true
                    text: pane.o.about
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    color: Theme.fg
                    selectionColor: Theme.sel
                    selectedTextColor: Theme.fg
                    font.family: Theme.font
                    font.pixelSize: Theme.fontSize
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    SectionHead {
                        Layout.fillWidth: true
                        text: pane.o.hostTitle
                        loading: pane.o.whereLoading
                    }
                    Button {
                        text: "where again"
                        flat: true
                        visible: !pane.o.template
                        enabled: !pane.o.whereLoading
                        onClicked: pane.o.refreshWhere()
                    }
                }
                FieldList {
                    objectName: "opHost"
                    Layout.fillWidth: true
                    model: pane.o.host
                    note: pane.o.hostNote
                    noteStyle: pane.o.hostNoteStyle
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    text: "declared"
                }
                FieldList {
                    objectName: "opDeclared"
                    Layout.fillWidth: true
                    model: pane.o.declared
                }
                Item { implicitHeight: 90 }  // room above the notice bar
            }
        }
    }
}
