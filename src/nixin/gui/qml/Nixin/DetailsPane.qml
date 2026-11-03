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

                // the package as an application (`appstream`): icon, name, summary, chips
                RowLayout {
                    objectName: "appBlock"
                    Layout.fillWidth: true
                    visible: pane.d.hasApp
                    spacing: 12
                    AppIcon {
                        objectName: "appIcon"
                        Layout.preferredWidth: 56
                        Layout.preferredHeight: 56
                        Layout.alignment: Qt.AlignTop
                        source: pane.d.iconUrl
                        name: pane.d.appName !== "" ? pane.d.appName : pane.d.name
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Text {
                            objectName: "appName"
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: pane.d.appName
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.fg
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize + 2
                            font.bold: true
                        }
                        Text {
                            objectName: "appSummary"
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: pane.d.appSummary
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.fg
                            opacity: 0.85
                            font.family: Theme.font
                            font.pixelSize: Theme.fontSize
                        }
                        Flow {
                            objectName: "chips"
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: pane.d.chips
                                delegate: Rectangle {
                                    id: chip
                                    required property string modelData
                                    implicitWidth: chipText.implicitWidth + 14
                                    implicitHeight: chipText.implicitHeight + 6
                                    radius: height / 2
                                    color: Qt.alpha(Theme.purple, 0.12)
                                    border.width: 1
                                    border.color: Qt.alpha(Theme.purple, 0.45)
                                    Text {
                                        id: chipText
                                        anchors.centerIn: parent
                                        text: chip.modelData
                                        textFormat: Text.PlainText
                                        color: Theme.purple
                                        font.family: Theme.font
                                        font.pixelSize: Theme.small
                                    }
                                }
                            }
                        }
                        Text {
                            objectName: "appSource"
                            Layout.fillWidth: true
                            text: pane.d.appSource
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.dim2
                            font.family: Theme.font
                            font.pixelSize: Theme.small
                        }
                    }
                }

                // screenshots: fetched when this pane opened (search.nixos.org's small copies
                // when it has them), cached, at most three
                ListView {
                    id: shots
                    objectName: "shots"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 170
                    visible: pane.d.hasApp && (count > 0 || pane.d.shotsLoading)
                    orientation: ListView.Horizontal
                    spacing: 8
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: pane.d.shots
                    delegate: Column {
                        id: shot
                        required property string url
                        required property string caption
                        width: 240
                        spacing: 2
                        Rectangle {
                            width: 240
                            height: 148
                            radius: Theme.radius
                            color: Theme.sunken
                            border.width: 1
                            border.color: Theme.line
                            Image {
                                objectName: "shotImage"
                                anchors.fill: parent
                                anchors.margins: 1
                                source: shot.url
                                sourceSize.width: 480
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                smooth: true
                            }
                        }
                        Text {
                            width: 240
                            text: shot.caption
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.dim2
                            font.family: Theme.font
                            font.pixelSize: Theme.small
                        }
                    }
                    BusyIndicator {
                        anchors.left: parent.left
                        anchors.leftMargin: shots.count * 248 + 8
                        anchors.verticalCenter: parent.verticalCenter
                        running: pane.d.shotsLoading
                        visible: pane.d.shotsLoading
                    }
                }
                Text {
                    objectName: "shotsNote"
                    Layout.fillWidth: true
                    visible: pane.d.hasApp && text !== ""
                    text: pane.d.shotsNote
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Theme.dim2
                    font.family: Theme.font
                    font.pixelSize: Theme.small
                }

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
