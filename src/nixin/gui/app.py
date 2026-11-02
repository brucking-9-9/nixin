"""The Qt application: the style, the QML engine, the window; the self-test and the grabs.

The QML lives in two modules under qml/: `Nixin` (the window, pages, dialogs, and the
Theme singleton) and `NixinStyle` (the Qt Quick Controls style: Button, TextField, … drawn
in the palette, everything else from the Basic style). Main.qml is loaded through its
module URI, so Theme is one instance for the pages and the style alike.

Offscreen (QT_QPA_PLATFORM=offscreen: tests, screenshots, the sandbox's install check)
Qt Quick draws with its software renderer, which needs no GPU and can grab the window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle

from ..ui.backend import Backend
from .bridge import Gui

QML_DIR = Path(__file__).with_name("qml")
STYLE = "NixinStyle"
APP_ID = "nixin"  # the Wayland app_id (window rules match it)
SELF_TEST_TIMEOUT_MS = 20_000
_software = False  # the software renderer is chosen (graphicsApi() does not say so)


def offscreen() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")


def application() -> QGuiApplication:
    """The process's QGuiApplication (created once; the tests' qapp is reused)."""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([sys.argv[0] if sys.argv else "nixin-gui"])
    QGuiApplication.setApplicationName("nixin")
    QGuiApplication.setDesktopFileName(APP_ID)
    return app  # type: ignore[return-value]


class Window:
    """The engine and the main window around a Gui. `errors` collects the engine's
    warnings (a QML error is a bug: the tests and the self-test fail on any)."""

    def __init__(self, gui: Gui) -> None:
        self.gui = gui
        self.errors: list[str] = []
        global _software
        if offscreen() and not _software:  # before the first window, once per process
            QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
            _software = True
        if QQuickStyle.name() != STYLE:
            QQuickStyle.setStyle(STYLE)  # before QML imports QtQuick.Controls, once
        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(QML_DIR))
        self.engine.warnings.connect(self._warnings)
        self.engine.setInitialProperties({"backend": gui})
        self.engine.loadFromModule("Nixin", "Main")
        roots = self.engine.rootObjects()
        self.window: QQuickWindow | None = roots[0] if roots else None  # type: ignore[assignment]
        if self.window is None and not self.errors:
            self.errors.append("Main.qml did not load")

    def _warnings(self, warnings: list[Any]) -> None:
        self.errors += [w.toString() for w in warnings]

    def find(self, name: str) -> QObject | None:
        """A QML object by objectName: in the object tree (popups), else in the visual tree
        (the delegates of repeaters and views have no QObject parent). A name on two pages
        (the details pane of Search and of Packages) finds the one on screen."""
        assert self.window is not None
        found: list[QObject] = list(self.window.findChildren(QObject, name))
        if not found:
            todo: list[QQuickItem] = [self.window.contentItem()]
            while todo:
                it = todo.pop()
                if it.objectName() == name:
                    found.append(it)
                todo += it.childItems()
        return next((o for o in found if o.property("visible")), found[0] if found else None)

    def grab(self) -> QImage:
        assert self.window is not None
        return self.window.grabWindow()

    def close(self) -> None:
        """Close and delete the window and the engine now, while the Gui they bind to is
        still alive (deleteLater would leave bindings running into a collected object)."""
        if self.window is not None:
            self.window.close()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.window = None


def run_app(backend: Backend, *, channel: str = "unstable", self_test: bool = False) -> dict:
    """Show the window until it closes; the document `nixin gui` answers with."""
    app = application()
    gui = Gui(backend, channel=channel)
    try:
        win = Window(gui)
        if win.window is None or (self_test and win.errors):
            return _qml_failed(win.errors)
        if self_test:
            return _self_test(app, win)
        app.exec()
        if not gui.idle:
            print(
                f"nixin gui: waiting for {gui['activity'] or 'a verb'} to finish",
                file=gui.router.real,
            )
            gui.pool.waitForDone()
        return {"ok": True, "message": gui.farewell()}
    finally:
        gui.close()


def _self_test(app: QGuiApplication, win: Window) -> dict:
    """Load everything, draw one frame, quit: proves the Qt environment (plugins, QML
    modules, the style) without a display when run offscreen."""
    assert win.window is not None
    drawn: list[bool] = []

    def frame() -> None:
        drawn.append(True)
        QTimer.singleShot(0, app.quit)

    win.window.frameSwapped.connect(frame)
    QTimer.singleShot(SELF_TEST_TIMEOUT_MS, app.quit)
    win.window.update()
    app.exec()
    if win.errors:
        return _qml_failed(win.errors)
    if not drawn:
        return _qml_failed([f"no frame drawn in {SELF_TEST_TIMEOUT_MS // 1000} s"])
    return {
        "ok": True,
        "message": f"nixin gui: self-test ok ({QGuiApplication.platformName()}, "
        f"{win.window.width()}x{win.window.height()}, first frame drawn)",
    }


def _qml_failed(errors: list[str]) -> dict:
    text = "; ".join(errors[:5]) or "unknown error"
    return {"ok": False, "error": {"code": "internal", "message": f"QML: {text}"}}
