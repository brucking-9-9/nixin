"""Verbs off the UI thread, and their log into the UI.

`Job` runs one backend call on a QThreadPool thread and hands the document back through a
signal, which Qt delivers on the UI thread (the signal object lives there).

`StderrRouter` is `sys.stderr` while the GUI runs. In human mode plan and apply tee nix's
build output and nixos-rebuild's log to `sys.stderr` (from the job's thread and from the
pump threads `apply.proc` starts); while a streaming job runs, the router hands every
write to that job's log, and otherwise passes writes through to the real stderr.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import cli
from ..ui.backend import Backend


class JobSignals(QObject):
    done = Signal(object)


class Job(QRunnable):
    def __init__(self, backend: Backend, verb: str, args: list[str], stream: bool) -> None:
        super().__init__()
        self.backend, self.verb, self.args, self.stream = backend, verb, args, stream
        self.signals = JobSignals()

    def run(self) -> None:
        try:
            doc = self.backend.call(self.verb, *self.args, stream=self.stream)
        except Exception as exc:  # noqa: BLE001 -- a bug in the shim must not kill the UI
            doc = {"verb": self.verb, "host": self.backend.host, **cli._internal(exc)}
        self.signals.done.emit(doc)


class StderrRouter:
    """A file-like `sys.stderr`: writes go to `sink` while one is attached."""

    def __init__(self, real: TextIO) -> None:
        self.real = real
        self._sink: Callable[[str], Any] | None = None
        self._lock = threading.Lock()

    def attach(self, sink: Callable[[str], Any]) -> None:
        with self._lock:
            self._sink = sink

    def detach(self) -> None:
        with self._lock:
            self._sink = None

    def write(self, text: str) -> int:
        with self._lock:
            sink = self._sink
        if sink is None:
            return self.real.write(text)
        sink(text)
        return len(text)

    def flush(self) -> None:
        if self._sink is None:
            self.real.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:  # fileno, encoding, errors, …
        return getattr(self.real, name)


def install_router() -> StderrRouter:
    """Put a router in front of the current sys.stderr (once); `uninstall_router` undoes it."""
    if isinstance(sys.stderr, StderrRouter):
        return sys.stderr
    router = StderrRouter(sys.stderr)
    sys.stderr = router  # type: ignore[assignment]
    return router


def uninstall_router(router: StderrRouter) -> None:
    if sys.stderr is router:
        sys.stderr = router.real
