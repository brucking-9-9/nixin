pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The window: the top bar, the navigation on the left (Search, Browse, Pending, Status,
// Packages, Options, Pins),
// the page, the non-modal notice over the bottom of the page, and the dialogs (diff, set,
// apply, update).
// `backend` is the Python root object (nixin/gui/bridge.py: Gui); everything shown comes
// from it, and every action is one of its slots.
ApplicationWindow {
    id: win

    required property var backend

    objectName: "main"
    width: 1200
    height: 780
    minimumWidth: 900
    minimumHeight: 600
    visible: true
    title: "nixin · " + backend.host
    color: Theme.bg
    font.family: Theme.font
    font.pixelSize: Theme.fontSize

    palette {
        window: Theme.bg
        windowText: Theme.fg
        base: Theme.panel
        alternateBase: Theme.raised
        text: Theme.fg
        button: Theme.panel
        buttonText: Theme.fg
        brightText: Theme.fg
        highlight: Theme.cyan
        highlightedText: Theme.bg
        accent: Theme.cyan
        placeholderText: Theme.dim2
        toolTipBase: Theme.panel
        toolTipText: Theme.fg
        link: Theme.cyan
        mid: Theme.line
        dark: Theme.sunken
        light: Theme.raised
        midlight: Theme.raised
        shadow: "#000000"
    }

    header: TopBar {
        backend: win.backend
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        NavRail {
            backend: win.backend
            onSearchRequested: searchPage.focusQuery()
            onOptionsRequested: optionsPage.focusQuery()
            Layout.fillHeight: true
            Layout.preferredWidth: Theme.navW
        }
        Rectangle {
            Layout.fillHeight: true
            implicitWidth: 1
            color: Theme.line
        }
        StackLayout {
            id: pages
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: ["search", "pending", "status", "packages", "options", "browse", "pins"].indexOf(win.backend.page)
            // the keys follow the page: the query on Search, the page itself elsewhere (a
            // hidden text field keeping the focus would take "/" as a letter)
            onCurrentIndexChanged: {
                if (currentIndex === 0)
                    searchPage.focusQuery();
                else if (currentIndex === 3)
                    packagesPage.focusList();
                else if (currentIndex === 4)
                    optionsPage.focusQuery();
                else if (currentIndex === 5)
                    browsePage.focusList();
                else if (currentIndex === 6)
                    pinsPage.focusList();
                else if (currentIndex > 0)
                    children[currentIndex].forceActiveFocus();
            }

            SearchPage {
                id: searchPage
                backend: win.backend
            }
            PendingPage {
                backend: win.backend
            }
            StatusPage {
                backend: win.backend
            }
            PackagesPage {
                id: packagesPage
                backend: win.backend
            }
            OptionsPage {
                id: optionsPage
                backend: win.backend
            }
            BrowsePage {
                id: browsePage
                backend: win.backend
            }
            PinsPage {
                id: pinsPage
                backend: win.backend
            }
        }
    }

    NoticeBar {
        notice: win.backend.notice
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.navW + 1 + Theme.pad
        anchors.rightMargin: Theme.pad
        anchors.bottomMargin: Theme.pad
    }

    DiffDialog {
        writer: win.backend.writer
    }
    SetDialog {
        writer: win.backend.writer
    }
    ApplyDialog {
        apply: win.backend.apply
        host: win.backend.host
    }
    UpdateDialog {
        packages: win.backend.packages
    }
    PinDialog {
        pins: win.backend.pins
    }
    TryDialog {
        trying: win.backend.trying
    }

    function showPage(name: string) {
        win.backend.go(name);
        if (name === "search")
            searchPage.focusQuery();  // also when Search is already the page
        else if (name === "options")
            optionsPage.focusQuery();
    }

    Shortcut {
        sequence: "/"
        onActivated: win.showPage("search")
    }
    Shortcut {
        sequence: "Ctrl+1"
        onActivated: win.showPage("search")
    }
    Shortcut {
        sequence: "Ctrl+2"
        onActivated: win.showPage("pending")
    }
    Shortcut {
        sequence: "Ctrl+3"
        onActivated: win.showPage("status")
    }
    Shortcut {
        sequence: "Ctrl+4"
        onActivated: win.showPage("packages")
    }
    Shortcut {
        sequence: "Ctrl+5"
        onActivated: win.showPage("options")
    }
    Shortcut {
        sequence: "Ctrl+B"
        onActivated: win.showPage("browse")
    }
    Shortcut {
        sequence: "Ctrl+6"
        onActivated: win.showPage("pins")
    }
    Shortcut {
        sequence: "Ctrl+Q"
        context: Qt.ApplicationShortcut
        onActivated: win.close()
    }

    onClosing: close => close.accepted = win.backend.mayQuit()

    Component.onCompleted: searchPage.focusQuery()
}
