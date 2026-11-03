pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The selected row: what it is (info), its releases (versions, packages), and where it is
// on this host (where: installed? declared where? the value). Add / Remove for packages,
// Set / Unset for options, for the system or a Home Manager user. One details object
// serves Search and Packages: each page's pane shows the row picked on that page (`owner`).
Item {
    id: pane

    required property var backend
    readonly property var d: backend.details
    property string owner: "search"
    property string hint: "Select a result to see what it is, its versions, and where it is on " + backend.host + "."
    readonly property bool shown: d.present && d.owner === owner

    objectName: "details"

    Text {
        anchors.centerIn: parent
        width: parent.width - 60
        visible: !pane.shown
        text: pane.hint
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        color: Theme.dim2
        font.family: Theme.font
        font.pixelSize: Theme.fontSize
    }

    ColumnLayout {
        anchors.fill: parent
        visible: pane.shown
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                text: pane.d.badge
                color: Theme.dim
                font.family: Theme.mono
                font.pixelSize: Theme.small
            }
            Text {
                objectName: "detailsName"
                Layout.fillWidth: true
                text: pane.d.name + (pane.d.target !== pane.d.name ? "  (" + pane.d.target + ")" : "")
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: pane.d.isOption ? Theme.purple : Theme.cyan
                font.family: Theme.font
                font.pixelSize: Theme.big
                font.bold: true
            }
            Text {
                objectName: "cue"
                text: pane.d.cue
                textFormat: Text.PlainText
                color: Theme.style(pane.d.cueStyle)
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
                objectName: "addButton"
                visible: pane.d.isPackage
                text: "Add"
                highlighted: true
                enabled: pane.backend.busy === ""
                onClicked: pane.d.add()
            }
            Button {
                objectName: "tryButton"
                visible: pane.d.isPackage
                text: "Try"
                enabled: pane.backend.busy === ""
                ToolTip.visible: hovered
                ToolTip.text: "Build it into the store and run it, without installing it"
                onClicked: pane.backend.trying.start(pane.d.target)
            }
            DangerButton {
                objectName: "removeButton"
                visible: pane.d.isPackage
                text: "Remove"
                enabled: pane.backend.busy === ""
                onClicked: pane.d.remove()
            }
            Button {
                objectName: "setButton"
                visible: pane.d.isOption
                text: "Set…"
                highlighted: true
                enabled: pane.backend.busy === ""
                onClicked: pane.d.editOption()
            }
            DangerButton {
                objectName: "unsetButton"
                visible: pane.d.isOption
                text: "Unset"
                enabled: pane.backend.busy === ""
                onClicked: pane.d.unset()
            }
            Item { Layout.fillWidth: true }
            Text {
                visible: pane.d.canHome
                text: "for"
                color: Theme.dim2
                font.family: Theme.font
                font.pixelSize: Theme.fontSize
            }
            ComboBox {
                id: home
                objectName: "homeBox"
                visible: pane.d.canHome && pane.d.homeKnown && pane.d.homeChoices.length > 0
                model: pane.d.homeChoices
                currentIndex: Math.max(0, pane.d.homeChoices.indexOf(pane.d.homeUser))
                displayText: pane.d.homeUser === "" ? "the system" : "home: " + pane.d.homeUser
                implicitWidth: 170
                onActivated: index => pane.d.setHome(pane.d.homeChoices[index])
                delegate: ItemDelegate {
                    required property string modelData
                    required property int index
                    width: ListView.view.width
                    text: modelData === "" ? "the system" : "home: " + modelData
                    highlighted: home.highlightedIndex === index
                }
            }
            Button {
                objectName: "homeLoad"
                visible: pane.d.canHome && !pane.d.homeKnown
                text: pane.d.homeLoading ? "loading users…" : "the system ▾"
                flat: true
                enabled: !pane.d.homeLoading
                ToolTip.visible: hovered
                ToolTip.text: "Home Manager users come from status (a minute cold)"
                onClicked: pane.d.loadHomeUsers()
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
                    text: "info"
                    loading: pane.d.infoLoading
                }
                FieldList {
                    objectName: "info"
                    Layout.fillWidth: true
                    model: pane.d.info
                    note: pane.d.infoNote
                    noteStyle: pane.d.infoNoteStyle
                }

                SectionHead {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    visible: pane.d.isPackage
                    text: "versions"
                    loading: pane.d.versionsLoading
                }
                FieldList {
                    objectName: "versions"
                    Layout.fillWidth: true
                    visible: pane.d.isPackage
                    model: pane.d.versions
                    note: pane.d.versionsNote
                    labelWidth: 120
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    SectionHead {
                        Layout.fillWidth: true
                        text: pane.d.whereTitle
                        loading: pane.d.whereLoading
                    }
                    Button {
                        text: "where again"
                        flat: true
                        enabled: !pane.d.whereLoading
                        onClicked: pane.d.refreshWhere()
                    }
                }
                FieldList {
                    objectName: "where"
                    Layout.fillWidth: true
                    model: pane.d.where
                    note: pane.d.whereNote
                    noteStyle: pane.d.whereNoteStyle
                }
                Item { implicitHeight: 90 }  // room above the notice bar
            }
        }
    }
}
