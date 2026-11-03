pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The Nix value for an option (prefilled with its value on this host, else its default),
// with the option's example (and default) under it, then the preview. For a template
// (`users.users.<name>.shell`) the option's name comes first, its `<name>` selected.
Dialog {
    id: dlg

    required property var writer
    readonly property bool want: writer.setOpen

    objectName: "setDialog"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(parent ? parent.width - 80 : 640, 640)
    title: "set " + writer.setOption
    closePolicy: Popup.CloseOnEscape

    onWantChanged: want ? open() : close()
    onOpened: {
        value.text = writer.setValue;
        option.text = writer.setOption;
        if (writer.setPath) {
            option.forceActiveFocus();
            if (writer.setSelect.length === 2)
                option.select(writer.setSelect[0], writer.setSelect[1]);
        } else {
            value.forceActiveFocus();
            value.selectAll();
        }
    }

    function preview() {
        dlg.writer.previewSetPath(option.text, value.text);
    }
    onClosed: if (writer.setOpen) writer.cancelSet()

    contentItem: ColumnLayout {
        spacing: Theme.gap

        FieldList {
            Layout.fillWidth: true
            model: [
                { label: "type", value: dlg.writer.setType || "?", style: "option" },
                { label: "for", value: dlg.writer.setUser === "" ? "the system" : "home: " + dlg.writer.setUser, style: "" }
            ]
            labelWidth: 60
        }
        TextField {
            id: option
            objectName: "setOptionPath"
            Layout.fillWidth: true
            visible: dlg.writer.setPath
            font.family: Theme.mono
            placeholderText: "the option, with the name filled in"
            onAccepted: {
                value.forceActiveFocus();
                value.selectAll();
            }
        }
        TextField {
            id: value
            objectName: "setValue"
            Layout.fillWidth: true
            font.family: Theme.mono
            placeholderText: "a Nix value"
            onAccepted: dlg.preview()
        }
        FieldList {
            objectName: "setHint"
            Layout.fillWidth: true
            visible: dlg.writer.setHint.count > 0
            model: dlg.writer.setHint
            labelWidth: 60
        }
        Text {
            Layout.fillWidth: true
            text: "Nix syntax: true · 42 · \"text\" · [ pkgs.foo ] · { a = 1; }. The preview shows the diff before anything is written."
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.dim2
            font.family: Theme.font
            font.pixelSize: Theme.small
        }
    }

    footer: DialogFooter {
        Button {
            objectName: "setCancel"
            text: "Cancel"
            onClicked: dlg.writer.cancelSet()
        }
        Button {
            objectName: "setPreview"
            text: "Preview"
            highlighted: true
            onClicked: dlg.preview()
        }
    }
}
