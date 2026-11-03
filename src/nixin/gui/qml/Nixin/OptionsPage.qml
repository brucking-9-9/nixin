pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// NixOS or Home Manager options: the query (typing is debounced, Enter searches at once
// and moves to the results), which options (search --kind opt | hm), the results (name,
// type, the description under it), the CLI's source footer; the selected option in full
// on the right, with Set… and Unset.
Item {
    id: page

    required property var backend
    readonly property var vm: backend.options

    objectName: "optionsPage"

    function focusQuery() {
        query.forceActiveFocus();
        query.selectAll();
    }

    // a search or a selection made elsewhere (a script, a test) shows here; never the query
    // while typing, where the stripped query would eat a trailing space
    Connections {
        target: page.vm
        function onChanged() {
            if (!query.activeFocus && query.text !== page.vm.query)
                query.text = page.vm.query;
            if (page.vm.selected >= 0 && results.currentIndex !== page.vm.selected)
                results.currentIndex = page.vm.selected;
        }
    }

    Timer {
        id: debounce
        interval: 350
        onTriggered: page.vm.search(query.text)
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad

        ColumnLayout {
            Layout.fillHeight: true
            Layout.fillWidth: false
            Layout.preferredWidth: Math.max(330, page.width * 0.46)
            spacing: Theme.gap

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                TextField {
                    id: query
                    objectName: "opQuery"
                    Layout.fillWidth: true
                    placeholderText: page.vm.kind === "hm" ? "search Home Manager options…" : "search NixOS options…"
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
                    id: kind
                    objectName: "opKind"
                    implicitWidth: 180
                    model: ["opt", "hm"]
                    currentIndex: Math.max(0, model.indexOf(page.vm.kind))
                    displayText: currentText === "hm" ? "Home Manager" : "NixOS"
                    ToolTip.visible: hovered
                    ToolTip.text: "search --kind opt (NixOS options) or --kind hm (Home Manager options)"
                    onActivated: index => {
                        page.vm.setKind(model[index]);
                        query.forceActiveFocus();
                    }
                    delegate: ItemDelegate {
                        required property string modelData
                        required property int index
                        width: kind.width
                        text: modelData === "hm" ? "Home Manager" : "NixOS"
                        highlighted: kind.highlightedIndex === index
                    }
                }
            }

            ListView {
                id: results
                objectName: "opResults"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.vm.results
                keyNavigationEnabled: true
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: 0
                ScrollBar.vertical: ScrollBar { }
                onCountChanged: currentIndex = count > 0 ? 0 : -1
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
                objectName: "opSource"
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
            color: Theme.line
        }

        OptionPane {
            Layout.fillWidth: true
            Layout.fillHeight: true
            backend: page.backend
        }
    }
}
