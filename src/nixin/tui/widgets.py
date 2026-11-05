"""Small widgets that render view-model data (view.py) with the palette (theme.STYLES).

Document text is never parsed as markup (a diff line may end in `[`): everything is built
as Rich Text with the theme's colours resolved per app theme (`rich_style`).
"""

from __future__ import annotations

import time

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import events
from textual.widgets import RichLog, Static

from ..ui import view
from .theme import STYLES

_RICH: dict[str, dict[str, str]] = {}


def rich_style(app, style: str) -> str:
    """A view-model style name as a Rich style for the app's current theme (cached)."""
    styles = _RICH.get(app.theme)
    if styles is None:
        css = app.get_css_variables()
        styles = {}
        for name, spec in STYLES.items():
            toks = []
            for tok in spec.split():
                if tok.startswith("$"):
                    value = css.get(tok[1:], "")
                    tok = value if value.startswith("#") and len(value) == 7 else "dim"
                toks.append(tok)
            styles[name] = " ".join(toks)
        _RICH[app.theme] = styles
    return styles.get(style, "")


def cell(app, text: str, style: str) -> Text:
    """One line (a table cell)."""
    return Text(text, style=rich_style(app, style), no_wrap=True, overflow="ellipsis")


def spans_text(app, spans: list[view.Span]) -> Text:
    """Several pieces in a row; a Span whose text ends in "\\n" ends the line."""
    out = Text()
    for s in spans:
        out.append(s.text, style=rich_style(app, s.style))
    return out


def fields_table(app, fields: list[view.Field], label_width: int) -> Table:
    """`label  value` rows; long values wrap under themselves, not under the label."""
    lw = min(max((len(f.label) for f in fields), default=0), label_width)
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(width=lw, no_wrap=True, overflow="ellipsis")
    grid.add_column(ratio=1, overflow="fold")
    muted = rich_style(app, "dim")
    for f in fields:
        grid.add_row(Text(f.label, style=muted), Text(f.value, style=rich_style(app, f.style)))
    return grid


class Fields(Static):
    """A list of view.Field, or a note when there are none."""

    DEFAULT_CSS = "Fields { height: auto; }"

    def __init__(self, *, label_width: int = 12, **kw) -> None:
        super().__init__("", **kw)
        self.label_width = label_width
        self.fields: list[view.Field] = []
        self.note = ""

    def show(self, fields: list[view.Field], note: str = "", note_style: str = "dim") -> None:
        self.fields, self.note = fields, note
        table = fields_table(self.app, fields, self.label_width) if fields else None
        if note:
            line = Text(note, style=rich_style(self.app, note_style))
            if table is None:
                self.update(line)
                return
            table.add_row(Text(""), line)  # under the values
        self.update(table if table is not None else "")

    def section(self, s: view.Section) -> None:
        self.show(s.fields, s.note, s.style or "dim")

    @property
    def text(self) -> str:
        """Plain text (tests): `label value` per line, then the note."""
        lines = [f"{f.label} {f.value}" for f in self.fields]
        return "\n".join([*lines, self.note] if self.note else lines)


class Spans(Static):
    """Lines of view.Span (a unified diff)."""

    DEFAULT_CSS = "Spans { height: auto; }"

    def show(self, spans: list[view.Span]) -> None:
        self.spans = spans
        lines = [view.Span(s.text + "\n", s.style) for s in spans]
        text = spans_text(self.app, lines)
        text.rstrip()  # in place
        self.update(text)


class Title(Static):
    """A section heading in the accent colour."""

    DEFAULT_CSS = "Title { height: 1; margin-top: 1; color: $accent; text-style: bold; }"


class Line(Static):
    """One styled line of view-model text (hints, source footers, headings)."""

    DEFAULT_CSS = "Line { height: auto; }"

    def show(self, text: str, style: str = "") -> None:
        self.value = text
        self.update(Text(text, style=rich_style(self.app, style)) if text else "")


class NoticeBox(Static):
    """An error document: `code` and message, plus what the error carries to act on."""

    DEFAULT_CSS = "NoticeBox { height: auto; display: none; }"

    def show(self, n: view.Notice | None) -> None:
        self.notice = n
        self.remove_class("-error", "-info", "-action")
        if n is None:
            self.display = False
            return
        self.add_class(f"-{n.severity}")
        style = {"error": "error", "info": "accent", "action": "warn"}[n.severity]
        pieces = [
            view.Span(n.code, style),
            view.Span(f"  {n.title}\n", "dim"),
            view.Span(n.brief),
        ]
        pieces += [view.Span(f"\n• {i}", "dim") for i in n.items]
        self.update(spans_text(self.app, pieces))
        self.display = True


SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner(Static):
    """`⠋ label 12s` while something runs; hidden otherwise. Slow verbs say how slow."""

    DEFAULT_CSS = "Spinner { height: auto; display: none; color: $accent; }"

    def on_mount(self) -> None:
        self._label = ""
        self._hint = ""
        self._t0 = 0.0
        self._i = 0
        self._timer = self.set_interval(0.12, self._tick, pause=True)

    def start(self, label: str, hint: str = "") -> None:
        self._label, self._hint, self._t0, self._i = label, hint, time.monotonic(), 0
        self.display = True
        self._tick()
        self._timer.resume()

    def stop(self) -> None:
        self._timer.pause()
        self.display = False

    def relabel(self, label: str) -> None:
        """A new label while it runs (the build tracker names the step)."""
        self._label = label

    @property
    def running(self) -> bool:
        return bool(self.display)

    def _tick(self) -> None:
        self._i += 1
        secs = int(time.monotonic() - self._t0)
        pieces = [view.Span(f"{SPIN[self._i % len(SPIN)]} {self._label} {secs}s", "accent")]
        if self._hint:
            pieces.append(view.Span(f"\n{self._hint}", "dim"))
        self.update(spans_text(self.app, pieces))


class BuildTracker(Static):
    """The build while plan or apply runs it: the status line (built/total, fetched, the
    current derivation, elapsed, ETA), the derivations building and queued, and the
    current build's last log lines. Fed with the tracker's snapshot (apply.progress)."""

    DEFAULT_CSS = "BuildTracker { height: auto; display: none; }"

    def __init__(self, **kw) -> None:
        super().__init__("", **kw)
        self.snap: dict | None = None

    def show(self, snap: dict | None) -> None:
        self.snap = snap
        if not snap or not (snap.get("total") or (snap.get("downloads") or {}).get("expected")):
            self.display = False
            return
        accent = rich_style(self.app, "accent")
        parts = [Text(view.build_head(snap), style=accent, no_wrap=True, overflow="ellipsis")]
        where = view.build_where(snap)
        if where:
            purple = rich_style(self.app, "option")  # secondary: the purple
            parts.append(Text(where, style=purple, no_wrap=True, overflow="ellipsis"))
        rows = view.build_rows(snap)
        if rows:
            # one line per derivation, clipped, the name column a third of a narrow screen
            width = self.size.width or self.app.size.width
            grid = Table.grid(padding=(0, 2), expand=True)
            grid.add_column(width=min(32, max(12, width // 3)), no_wrap=True, overflow="ellipsis")
            grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
            for f in rows:
                grid.add_row(cell(self.app, f.label, ""), cell(self.app, f.value, f.style))
            parts.append(grid)
        muted = rich_style(self.app, "dim")
        for ln in view.build_tail(snap):
            parts.append(Text(f"  {ln}", style=muted, no_wrap=True, overflow="ellipsis"))
        self.update(Group(*parts))
        self.display = True

    def hide(self) -> None:
        self.snap = None
        self.display = False

    @property
    def text(self) -> str:
        """Plain text (tests): the head, `label value` per row, the tail."""
        if not self.snap:
            return ""
        rows = [f"{f.label} {f.value}".strip() for f in view.build_rows(self.snap)]
        where = [view.build_where(self.snap)] if view.build_where(self.snap) else []
        return "\n".join([view.build_head(self.snap), *where, *rows, *view.build_tail(self.snap)])


class LogView(RichLog):
    """nix's build output and nixos-rebuild's log, as the backend tees them to stderr.

    The app routes captured stderr here (App.begin_capture_print) while plan or apply runs.
    Writes arrive in pieces; whole lines are shown, ANSI colours kept."""

    DEFAULT_CSS = "LogView { height: auto; max-height: 12; display: none; }"

    def __init__(self, **kw) -> None:
        super().__init__(wrap=False, markup=False, highlight=False, max_lines=2000, **kw)
        self._partial = ""
        self.lines_seen: list[str] = []

    def on_print(self, event: events.Print) -> None:
        event.stop()
        self.feed(event.text)

    def feed(self, text: str) -> None:
        self.display = True
        buf = self._partial + text
        *whole, self._partial = buf.split("\n")
        for line in whole:
            line = line.rstrip("\r").rsplit("\r", 1)[-1]  # keep the last state of a \r line
            self.lines_seen.append(line)
            self.write(Text.from_ansi(line))

    def flush_partial(self) -> None:
        if self._partial:
            self.feed("\n")

    def reset(self) -> None:
        self.clear()
        self._partial = ""
        self.lines_seen = []
