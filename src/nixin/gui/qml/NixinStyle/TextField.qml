import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Nixin

T.TextField {
    id: control

    implicitWidth: implicitBackgroundWidth + leftInset + rightInset
                   || Math.max(contentWidth, placeholder.implicitWidth) + leftPadding + rightPadding
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             contentHeight + topPadding + bottomPadding,
                             placeholder.implicitHeight + topPadding + bottomPadding)
    padding: 6
    leftPadding: 10
    rightPadding: 10
    color: Theme.fg
    selectionColor: Theme.sel
    selectedTextColor: Theme.fg
    placeholderTextColor: Theme.dim2
    font.family: Theme.font
    font.pixelSize: Theme.fontSize
    verticalAlignment: TextInput.AlignVCenter

    PlaceholderText {
        id: placeholder
        x: control.leftPadding
        y: control.topPadding
        width: control.width - (control.leftPadding + control.rightPadding)
        height: control.height - (control.topPadding + control.bottomPadding)
        text: control.placeholderText
        font: control.font
        color: control.placeholderTextColor
        verticalAlignment: control.verticalAlignment
        visible: !control.length && !control.preeditText && (!control.activeFocus || control.horizontalAlignment !== Qt.AlignHCenter)
        elide: Text.ElideRight
        renderType: control.renderType
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: Theme.controlH + 4
        radius: Theme.radius
        color: control.hovered && !control.activeFocus ? Theme.raised : Theme.panel
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.cyan : Theme.line
    }
}
