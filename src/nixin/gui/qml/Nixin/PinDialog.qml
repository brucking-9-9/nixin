pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Pin…: what to hold (kernel, or a pkgs attribute) and at which nixpkgs revision — the
// lock's own, the committed or the applied lock's, the running system's (pins' revisions[]),
// or a commit typed in — then `pin NAME --rev REV` previewed in the diff dialog.
Dialog {
    id: dlg

    required property var pins
    readonly property bool want: pins.pinOpen
    readonly property int freeIndex: pins.pinChoices.length

    objectName: "pinDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 640, 640)
    title: "pin at a nixpkgs revision"
    closePolicy: Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onOpened: {
        name.text = pins.pinName;
        rev.currentIndex = 0;
        free.text = "";
        name.forceActiveFocus();
        name.selectAll();
    }
    onClosed: if (pins.pinOpen) pins.cancelPin()

    function preview() {
        dlg.pins.confirmPin(name.text, rev.currentIndex, free.text);
    }

    contentItem: ColumnLayout {
        spacing: Theme.gap

        Text {
            Layout.fillWidth: true
            text: "what: kernel, or a pkgs attribute (firefox, ripgrep); nested ones (python3Packages.x) cannot be held by an overlay"
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        TextField {
            id: name
            objectName: "pinName"
            Layout.fillWidth: true
            font.family: Theme.mono
            placeholderText: "kernel"
            onAccepted: dlg.preview()
        }
        Text {
            Layout.fillWidth: true
            text: "at which nixpkgs:"
            textFormat: Text.PlainText
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
        ComboBox {
            id: rev
            objectName: "pinRev"
            Layout.fillWidth: true
            model: dlg.pins.pinChoices.concat(["another commit (type it)"])
            delegate: ItemDelegate {
                required property string modelData
                required property int index
                width: rev.width
                text: modelData
                highlighted: rev.highlightedIndex === index
            }
        }
        TextField {
            id: free
            objectName: "pinRevFree"
            Layout.fillWidth: true
            visible: rev.currentIndex === dlg.freeIndex
            font.family: Theme.mono
            placeholderText: "a commit of nixpkgs, 7-40 hex digits"
            onAccepted: dlg.preview()
        }
        Text {
            Layout.fillWidth: true
            text: "The committed and the applied lock's revisions need no network; another commit is prefetched from GitHub. The preview shows the diff before anything is written."
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "pinCancel"
            text: "Cancel"
            onClicked: dlg.pins.cancelPin()
        }
        Button {
            objectName: "pinPreview"
            text: "Preview"
            highlighted: true
            onClicked: dlg.preview()
        }
    }
}
