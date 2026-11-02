import QtQuick
import QtQuick.Templates as T
import Nixin

// A row in a list or a menu: `highlighted` is the current one (cyan text, a cyan bar on the
// left, a faint cyan fill), as the results and the navigation draw it.
T.ItemDelegate {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    padding: 8
    horizontalPadding: 14
    spacing: 8
    font.family: Theme.font
    font.pixelSize: Theme.fontSize

    contentItem: Text {
        text: control.text
        font: control.font
        textFormat: Text.PlainText
        color: !control.enabled ? Theme.dim : control.highlighted ? Theme.cyan : Theme.fg
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        implicitWidth: 100
        implicitHeight: 34
        color: control.down ? Qt.alpha(Theme.cyan, 0.2)
             : control.highlighted ? Qt.alpha(Theme.cyan, 0.09)
             : control.hovered ? Qt.alpha(Theme.fg, 0.05) : "transparent"
        border.width: control.visualFocus ? 1 : 0
        border.color: Theme.cyan
        Rectangle {
            width: 3
            height: parent.height
            color: Theme.cyan
            visible: control.highlighted
        }
    }
}
