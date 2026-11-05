"""The view-model's rows as Qt list models, and the converters from view.py's dataclasses.

`Rows` is one generic QAbstractListModel: a list of dicts with fixed keys, one role per key
(QML delegates declare `required property string label` etc.). `LogModel` is the streamed
log of plan/apply: chunks of text in, whole lines out, ANSI escapes dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)

from ..ui import view

Row = dict[str, Any]
Index = QModelIndex | QPersistentModelIndex

FIELD = ("label", "value", "style")
SPAN = ("text", "style")
HIT = ("kind", "badge", "name", "target", "detail", "description", "isOption")
CLOSURE = ("sign", "name", "version", "size", "style")
PACKAGE = ("name", "drv", "version", "tag", "where", "isHome")
UPDATE = ("name", "before", "after", "tag", "note", "style", "isHome", "held")
PIN = ("name", "attrs", "version", "heldAt", "mode", "stale", "why", "note", "drop")
LINE = ("text",)
APP = ("name", "target", "version", "summary", "icon", "installed", "curated", "marks")
CATEGORY = ("key", "label", "count", "installed")
SHOT = ("url", "caption")


class Rows(QAbstractListModel):
    countChanged = Signal()

    def __init__(self, keys: Sequence[str], parent=None) -> None:
        super().__init__(parent)
        self.keys = tuple(keys)
        self.rows: list[Row] = []
        self._roles = {Qt.ItemDataRole.UserRole + i: k for i, k in enumerate(self.keys)}

    def roleNames(self) -> dict[int, QByteArray]:  # type: ignore[override]
        return {r: QByteArray(k.encode()) for r, k in self._roles.items()}

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008 -- Qt's signature
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        key = self._roles.get(role) or (self.keys[0] if role == Qt.ItemDataRole.DisplayRole else "")
        return self.rows[index.row()].get(key)

    def reset(self, rows: Iterable[Row]) -> None:
        self.beginResetModel()
        self.rows = [{k: r.get(k, "") for k in self.keys} for r in rows]
        self.endResetModel()
        self.countChanged.emit()

    def append(self, rows: Iterable[Row]) -> None:
        new = [{k: r.get(k, "") for k in self.keys} for r in rows]
        if not new:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(new) - 1)
        self.rows.extend(new)
        self.endInsertRows()
        self.countChanged.emit()

    def column(self, key: str) -> list[Any]:
        return [r[key] for r in self.rows]

    @Slot(int, result="QVariantMap")
    def get(self, i: int) -> Row:
        return dict(self.rows[i]) if 0 <= i < len(self.rows) else {}

    def _count(self) -> int:
        return len(self.rows)

    count = Property(int, _count, notify=countChanged)


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)")
MAX_LOG = 5000


class LogModel(Rows):
    """Lines of a streamed log. `incoming` may be emitted from any thread: Qt queues it to
    the thread the model lives in, so lines are added on the UI thread, in order."""

    incoming = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(LINE, parent)
        self._partial = ""
        self.incoming.connect(self.feed)

    @Slot(str)
    def feed(self, text: str) -> None:
        buf = self._partial + text
        *whole, self._partial = buf.split("\n")
        lines = []
        for line in whole:
            line = line.rstrip("\r").rsplit("\r", 1)[-1]  # the last state of a \r line
            lines.append({"text": ANSI.sub("", line)})
        self.append(lines)
        if len(self.rows) > MAX_LOG:
            self.reset(self.rows[-MAX_LOG:])

    def flush_partial(self) -> None:
        if self._partial:
            self.feed("\n")

    def clear(self) -> None:
        self._partial = ""
        self.reset([])

    @property
    def lines(self) -> list[str]:
        return self.column("text")


# --- view.py → rows -------------------------------------------------------------------------


def fields(xs: Iterable[view.Field]) -> list[Row]:
    return [{"label": f.label, "value": f.value, "style": f.style} for f in xs]


def spans(xs: Iterable[view.Span]) -> list[Row]:
    return [{"text": s.text, "style": s.style} for s in xs]


def hits(xs: Iterable[view.Hit]) -> list[Row]:
    return [
        {
            "kind": h.kind,
            "badge": view.KIND_BADGE.get(h.kind, h.kind),
            "name": h.name,
            "target": h.target,
            "detail": h.detail,
            "description": " ".join(h.description.split()),
            "isOption": h.is_option,
        }
        for h in xs
    ]


def packages(xs: Iterable[view.PackageRow]) -> list[Row]:
    return [
        {
            "name": r.name,
            "drv": r.drv,
            "version": r.version,
            "tag": r.tag,
            "where": r.where,
            "isHome": r.user is not None,
        }
        for r in xs
    ]


def updates(xs: Iterable[view.UpdateRow]) -> list[Row]:
    return [
        {
            "name": r.pname,
            "before": r.old,
            "after": r.new,
            "tag": r.tag,
            "note": r.note,
            "style": r.style,
            "isHome": r.user is not None,
            "held": r.held,
        }
        for r in xs
    ]


def pins(xs: Iterable[view.PinRow]) -> list[Row]:
    return [
        {
            "name": r.name,
            "attrs": r.attrs,
            "version": r.version,
            "heldAt": r.held_at,
            "mode": r.mode,
            "stale": r.stale,
            "why": r.why,
            "note": r.note,
            "drop": r.drop,
        }
        for r in xs
    ]


def file_url(path: str) -> str:
    """A local file as a URL for QML's Image (empty when there is no such file)."""
    import os

    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(path).toString() if path and os.path.isfile(path) else ""


def apps(xs: Iterable[view.AppRow]) -> list[Row]:
    return [
        {
            "name": r.name,
            "target": r.target,
            "version": r.version,
            "summary": " ".join(r.summary.split()),
            "icon": file_url(r.icon),
            "installed": r.installed,
            "curated": r.curated,
            "marks": r.marks,
        }
        for r in xs
    ]


def categories(xs: Iterable[view.BrowseCategory]) -> list[Row]:
    return [
        {"key": c.key, "label": c.label, "count": c.count, "installed": c.installed} for c in xs
    ]


def closure(xs: Iterable[view.ClosureRow]) -> list[Row]:
    return [
        {"sign": r.sign, "name": r.name, "version": r.version, "size": r.size, "style": r.style}
        for r in xs
    ]


def section(s: view.Section) -> Row:
    return {"title": s.title, "note": s.note, "style": s.style, "fields": fields(s.fields)}
