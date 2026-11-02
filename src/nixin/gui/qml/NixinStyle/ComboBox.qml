import QtQuick
import QtQuick.Templates as T
import Nixin

T.ComboBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)
    leftPadding: 10
    rightPadding: 10 + (indicator ? indicator.width + spacing : 0)
    spacing: 6
    font.family: Theme.font
    font.pixelSize: Theme.fontSize

    delegate: ItemDelegate {
        required property var model
        required property var modelData
        required property int index
        width: ListView.view.width
        text: control.textRole ? (Array.isArray(control.model) ? modelData[control.textRole] : model[control.textRole])
                               : modelData
        highlighted: control.highlightedIndex === index
        hoverEnabled: control.hoverEnabled
    }

    indicator: Text {
        x: control.width - width - 10
        y: (control.height - height) / 2
        text: "▾"
        font.pixelSize: Theme.fontSize
        color: control.visualFocus || control.popup.visible ? Theme.cyan : Theme.dim2
    }

    contentItem: Text {
        text: control.displayText
        font: control.font
        textFormat: Text.PlainText
        color: control.enabled ? Theme.fg : Theme.dim
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        implicitWidth: 120
        implicitHeight: Theme.controlH
        radius: Theme.radius
        color: control.hovered ? Theme.raised : Theme.panel
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus || control.popup.visible ? Theme.cyan : Theme.line
    }

    popup: Popup {
        y: control.height + 2
        width: control.width
        height: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, 320)
        padding: 4

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.delegateModel
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0
            ScrollBar.vertical: ScrollBar { }
        }
    }
}
