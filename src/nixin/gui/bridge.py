"""The GUI's state and flows, as Qt objects the QML binds to.

`Gui` is the root; Main.qml gets it as its required `backend` property. It owns one
object per area (search, packages, options, browse, details, writer, pending, apply, status,
trying, notice), each a `VM`: state in a dict that QML reads through properties notifying
on the object's own `changed` signal, and slots QML calls. Lists are `models.Rows`.

Every document comes from `Gui.fetch` (a Job on the thread pool running the backend's
verb handler in-process) and becomes rows through nixin.ui.view, as in the TUI. The flows
are the TUI's: a write is always `--dry-run` → the diff dialog → write → `where` again; an
apply is `apply --dry-run` → confirm (message) → apply; writer, plan and apply runs are
serialised by `busy` (two writer runs on one workspace race on installer/state.json);
`status` is stale after a write or an apply, and a plan after an apply. A nixpkgs update is
`update --dry-run` → confirm → `update` → Pending; it counts as a write (flake.lock).

A plan or an apply runs with a `Cancel` token (nixin.apply.proc): while it runs, the
Pending page and the apply dialog poll the build tracker's snapshot from it twice a second
(`_Tracking`: the status line, the derivations building, the log tail, the step) and their
Stop button asks it to stop; the verb refuses from the pull on and the notice says so.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from PySide6.QtCore import Property, QObject, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ..apply.proc import Cancel
from ..ui import view
from ..ui.backend import Backend, shell
from . import models
from .jobs import CallJob, Job, JobSignals, install_router, uninstall_router
from .models import LogModel, Rows

Doc = dict[str, Any]

WHERE_VERBS = ("rm", "set", "unset")  # accept --where FILE:LINE (pick a candidate)
INFO_KIND = {"pkg": "pkg", "program": "pkg", "opt": "opt", "hm": "hm", "fn": "fn"}
QUIT_AGAIN = 3.0  # seconds in which a second quit goes through while something runs


def state(kind: Any, name: str, signal: Signal) -> Property:
    """A read-only property over `self._s[name]` that notifies on `signal`."""

    def get(self: VM) -> Any:
        return self._s[name]

    return Property(kind, get, notify=signal)


def model(name: str) -> Property:
    def get(self: VM) -> QObject:
        return self._models[name]

    return Property(QObject, get, constant=True)


def area(name: str) -> Property:
    """One of the root's area objects (search, details, …), for QML."""

    def get(self: Gui) -> QObject:
        return self._areas[name]

    return Property(QObject, get, constant=True)


class VM(QObject):
    """State in a dict, and the list models. A subclass declares its own `changed =
    Signal()` (PySide drops a notify signal inherited from a base class), its properties
    with `state(type, name, changed)` and its models with `model(name)`."""

    defaults: ClassVar[dict[str, Any]] = {}
    lists: ClassVar[dict[str, tuple[str, ...]]] = {}  # model name → row keys
    logs: ClassVar[tuple[str, ...]] = ()
    changed: Signal

    def __init__(self, gui: Gui | None) -> None:
        super().__init__(gui)
        self._gui = gui
        self._s = copy.deepcopy(self.defaults)
        self._models: dict[str, Rows] = {k: Rows(keys, self) for k, keys in self.lists.items()}
        self._models |= {k: LogModel(self) for k in self.logs}

    @property
    def gui(self) -> Gui:
        assert self._gui is not None
        return self._gui

    def __getitem__(self, key: str) -> Any:
        return self._s[key]

    def set(self, **kw: Any) -> None:
        unknown = set(kw) - set(self._s)
        assert not unknown, f"{type(self).__name__}: no state {unknown}"
        new = {k: v for k, v in kw.items() if self._s[k] != v}
        if new:
            self._s.update(new)
            self.changed.emit()

    def rows(self, name: str) -> Rows:
        return self._models[name]

    def logmodel(self, name: str) -> LogModel:
        m = self._models[name]
        assert isinstance(m, LogModel)
        return m


# --- notice ---------------------------------------------------------------------------------


class NoticeVM(VM):
    """The non-modal notice: an error document (code + message, what to copy or pick), or a
    passing message (`say`, gone after a few seconds)."""

    changed = Signal()
    defaults: ClassVar = {
        "visible": False,
        "code": "",
        "title": "",
        "message": "",
        "severity": "",  # error | info | action (view.Notice), ok | warn (say)
        "context": "",  # the command it answers: `search cowsay`
        "items": [],
        "candidates": [],  # [{label, where}] when picking one re-runs the writer
        "commands": "",
        "transient": False,
        "serial": 0,  # bumps on every new notice (QML restarts its hide timer)
    }
    visible = state(bool, "visible", changed)
    code = state(str, "code", changed)
    title = state(str, "title", changed)
    message = state(str, "message", changed)
    severity = state(str, "severity", changed)
    context = state(str, "context", changed)
    items = state("QVariantList", "items", changed)
    candidates = state("QVariantList", "candidates", changed)
    commands = state(str, "commands", changed)
    transient = state(bool, "transient", changed)
    serial = state(int, "serial", changed)

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self._pick: Callable[[str], None] | None = None

    def show(self, doc: Doc, context: str = "", pick: Callable[[str], None] | None = None) -> None:
        n = view.notice(doc)
        if n is None:
            return
        self._pick = pick if n.candidates else None
        cands = []
        items = list(n.items)
        if self._pick is not None:
            cands = [
                {"label": view.candidate_label(c), "where": view.candidate_where(c)}
                for c in n.candidates
            ]
            labels = {c["label"] for c in cands}
            items = [i for i in items if i not in labels]
        self.set(
            visible=True,
            code=n.code,
            title=n.title,
            message=n.brief,
            severity=n.severity,
            context=context,
            items=items,
            candidates=cands,
            commands="\n".join(n.commands),
            transient=False,
            serial=self["serial"] + 1,
        )

    def say(self, text: str, severity: str = "ok") -> None:
        self._pick = None
        self.set(
            visible=True,
            code="",
            title="",
            message=text,
            severity=severity,
            context="",
            items=[],
            candidates=[],
            commands="",
            transient=True,
            serial=self["serial"] + 1,
        )

    @Slot()
    def dismiss(self) -> None:
        self.set(visible=False)

    @Slot(str)
    def pick(self, where: str) -> None:
        pick, self._pick = self._pick, None
        self.dismiss()
        if pick is not None:
            pick(where)

    @Slot()
    def copyCommands(self) -> None:
        self.gui.copy(self["commands"])


# --- search ---------------------------------------------------------------------------------


class SearchVM(VM):
    changed = Signal()
    defaults: ClassVar = {
        "query": "",
        "kind": "pkg",
        "count": 0,
        "source": "",
        "message": "type to search packages, options and programs",
        "loading": False,
        "selected": -1,
    }
    lists: ClassVar = {"results": models.HIT}
    query = state(str, "query", changed)
    kind = state(str, "kind", changed)
    count = state(int, "count", changed)
    source = state(str, "source", changed)
    message = state(str, "message", changed)
    loading = state(bool, "loading", changed)
    selected = state(int, "selected", changed)
    results = model("results")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.doc: Doc | None = None
        self.hits: list[view.Hit] = []
        self._last: tuple | None = None

    @Slot(result="QVariantList")
    def kinds(self) -> list[str]:
        return list(view.SEARCH_KINDS)

    @Slot(str)
    def search(self, query: str) -> None:
        self._run(query)

    @Slot(str)
    def setKind(self, kind: str) -> None:
        if kind in view.SEARCH_KINDS and kind != self["kind"]:
            self.set(kind=kind)
            self._run(self["query"])

    def rerun(self) -> None:
        self._run(self["query"], force=True)

    def _run(self, query: str, force: bool = False) -> None:
        query = query.strip()
        self.set(query=query)
        key = (query, self["kind"], self.gui["channel"])
        if not query:
            self._last = None
            self.show(None)
            return
        if key == self._last and not force:
            return
        self._last = key
        self.set(loading=True)
        kind, channel = self["kind"], self.gui["channel"]
        self.gui.fetch(
            "search",
            *("--kind", kind, "--channel", channel, "--", query),
            done=self.show,
            group="search",
            exclusive=True,
            label=f"searching {kind} for {query!r}",
        )

    def show(self, doc: Doc | None) -> None:
        self.doc = doc
        ok = bool(doc and doc.get("ok"))
        if doc and not ok:
            self.gui.notice.show(doc, f"search {self['query']}")
        self.hits = view.hits(doc) if doc and ok else []
        self.rows("results").reset(models.hits(self.hits))
        if not doc:
            message = self.defaults["message"]
        elif ok and not self.hits:
            message = "no results · try another kind"
        else:
            message = ""
        self.set(
            loading=False,
            count=len(self.hits),
            source=" · ".join(view.source_lines(doc)) if doc and ok else "",
            message=message,
            selected=-1,
        )

    @Slot(int)
    def select(self, i: int) -> None:
        if 0 <= i < len(self.hits):
            self.set(selected=i)
            self.gui.packages.set(selected=-1, upSelected=-1)
            self.gui.details.show(self.hits[i])


# --- details --------------------------------------------------------------------------------


_FROM_FILES = ("installed", "store")  # an `appstream` read from an output, not a hint


def _info_fields(rec: dict[str, Any]) -> list[view.Field]:
    """The info section without the AppStream lines: the pane draws those (icon, chips)."""
    return view.info_fields({k: v for k, v in rec.items() if k != "appstream"})


class DetailsVM(VM):
    """The selected row: discovery's record (refreshed by `info`), `versions`, and `where`
    on this host; Add / Remove / Set / Unset, and the Home Manager user to do them for."""

    changed = Signal()
    defaults: ClassVar = {
        "shows": 0,  # bumped by every show(): a page opens its drawer on a new one
        "present": False,
        "owner": "",  # the page whose row this is: search | packages | browse (each pane its own)
        "kind": "",
        "badge": "",
        "name": "",
        "target": "",
        "isOption": False,
        "isPackage": False,
        "cue": "",
        "cueStyle": "",
        "infoLoading": False,
        "infoNote": "",
        "infoNoteStyle": "",
        "versionsLoading": False,
        "versionsNote": "",
        "whereLoading": False,
        "whereTitle": "",
        "whereNote": "",
        "whereNoteStyle": "",
        "canHome": False,  # packages, and HM options (which always need a user)
        "homeUser": "",  # "" = the system
        "homeChoices": [],  # what the user picker offers ("" = system)
        "homeKnown": False,  # the host's HM users are known (status or where)
        "homeLoading": False,
        "currentValue": "",
        # the package as an application (its `appstream` object): icon, summary, chips,
        # screenshots; images are fetched when the details open, never before
        "hasApp": False,
        "appName": "",
        "appSummary": "",
        "appSource": "",
        "iconUrl": "",
        "chips": [],
        "shotsLoading": False,
        "shotsNote": "",
    }
    lists: ClassVar = {
        "info": models.FIELD,
        "versions": models.FIELD,
        "where": models.FIELD,
        "shots": models.SHOT,
    }
    shows = state(int, "shows", changed)
    present = state(bool, "present", changed)
    owner = state(str, "owner", changed)
    kind = state(str, "kind", changed)
    badge = state(str, "badge", changed)
    name = state(str, "name", changed)
    target = state(str, "target", changed)
    isOption = state(bool, "isOption", changed)
    isPackage = state(bool, "isPackage", changed)
    cue = state(str, "cue", changed)
    cueStyle = state(str, "cueStyle", changed)
    infoLoading = state(bool, "infoLoading", changed)
    infoNote = state(str, "infoNote", changed)
    infoNoteStyle = state(str, "infoNoteStyle", changed)
    versionsLoading = state(bool, "versionsLoading", changed)
    versionsNote = state(str, "versionsNote", changed)
    whereLoading = state(bool, "whereLoading", changed)
    whereTitle = state(str, "whereTitle", changed)
    whereNote = state(str, "whereNote", changed)
    whereNoteStyle = state(str, "whereNoteStyle", changed)
    canHome = state(bool, "canHome", changed)
    homeUser = state(str, "homeUser", changed)
    homeChoices = state("QVariantList", "homeChoices", changed)
    homeKnown = state(bool, "homeKnown", changed)
    homeLoading = state(bool, "homeLoading", changed)
    currentValue = state(str, "currentValue", changed)
    hasApp = state(bool, "hasApp", changed)
    appName = state(str, "appName", changed)
    appSummary = state(str, "appSummary", changed)
    appSource = state(str, "appSource", changed)
    iconUrl = state(str, "iconUrl", changed)
    chips = state("QVariantList", "chips", changed)
    shotsLoading = state(bool, "shotsLoading", changed)
    shotsNote = state(str, "shotsNote", changed)
    info = model("info")
    versions = model("versions")
    where = model("where")
    shots = model("shots")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.hit: view.Hit | None = None
        self.record: dict[str, Any] = {}
        self.where_doc: Doc | None = None
        self._images: set[str] = set()  # asked for this row already (icon, screenshots)
        self._shots: list[list[Any]] = []  # [url, caption, file or None, error], in order

    def show(self, hit: view.Hit, *, user: str = "", owner: str = "search") -> None:
        """`user`: the Home Manager user to start with (an installed row's own)."""
        g = self.gui
        self.hit, self.record, self.where_doc = hit, hit.record, None
        if hit.kind == "hm" and not user:
            user = (g.hm_users() or [""])[0]
        self.set(
            present=True,
            shows=self["shows"] + 1,
            owner=owner,
            kind=hit.kind,
            badge=view.KIND_BADGE.get(hit.kind, hit.kind),
            name=hit.name,
            target=hit.target,
            isOption=hit.is_option,
            isPackage=hit.is_package,
            cue="",
            cueStyle="",
            infoNote="",
            versionsNote="",
            whereTitle=f"on {g.backend.host}",
            whereNote="",
            canHome=hit.is_package or hit.kind == "hm",
            homeUser=user,
            currentValue="",
            hasApp=False,
            appName="",
            appSummary="",
            appSource="",
            iconUrl="",
            chips=[],
            shotsLoading=False,
            shotsNote="",
        )
        self._images, self._shots = set(), []
        self.rows("shots").reset([])
        self._home_choices()
        self.rows("info").reset(models.fields(_info_fields(self.record)))
        self._app(hit, self.record.get("appstream"))
        self.rows("versions").reset([])
        self.rows("where").reset([])
        if hit.kind in INFO_KIND:
            self.set(infoLoading=True)
            g.fetch(
                "info",
                *(hit.target, "--kind", INFO_KIND[hit.kind], "--channel", g["channel"]),
                done=lambda doc, h=hit: self._info(h, doc),
                group="info",
                exclusive=True,
                label=f"info {hit.target}",
                hint="a second; about 3 s more once when the package is not installed "
                "(AppStream: its out paths are evaluated)"
                if hit.is_package
                else "",
            )
        else:
            self.set(infoLoading=False)
        if hit.is_package:
            self.set(versionsLoading=True)
            g.fetch(
                "versions",
                *(hit.target, "--limit", "8"),
                done=lambda doc, h=hit: self._versions(h, doc),
                group="versions",
                exclusive=True,
                label=f"versions {hit.target} (NixHub)",
            )
        else:
            self.set(versionsLoading=False)
        self.refreshWhere()

    def _info(self, hit: view.Hit, doc: Doc) -> None:
        if hit is not self.hit:
            return
        self.set(infoLoading=False)
        n = view.notice(doc)
        if n is not None:
            self.set(infoNote=f"info: {n.code}: {n.message}", infoNoteStyle="error")
            return
        rec = next((r for r in doc.get("results") or [] if r.get("name") == hit.target), None)
        if rec is not None:
            if hit.kind == "program":  # keep the binary in view
                rec = {**rec, "kind": "program", "package": hit.target, "name": hit.name}
            had = self.record.get("appstream") or {}
            if (
                had.get("source") in _FROM_FILES
                and (rec.get("appstream") or {}).get("source") not in _FROM_FILES
            ):
                rec = {**rec, "appstream": had}  # Browse read the files; info only hints
            self.record = rec
        self.rows("info").reset(models.fields(_info_fields(self.record)))
        self._app(hit, self.record.get("appstream"))

    # --- the package as an application

    def _app(self, hit: view.Hit, a: dict[str, Any] | None) -> None:
        """Show what `appstream` says; fetch its icon (when not a local file) and its
        screenshots now that the details are open, each once per row."""
        if not a or not (a.get("gui") or a.get("name") or a.get("summary")):
            return
        icon = models.file_url(view.icon_path(a))
        self.set(
            hasApp=True,
            appName=str(a.get("name") or ""),
            appSummary=str(a.get("summary") or ""),
            appSource=view.app_source(a),
            chips=view.chips(a),
            iconUrl=icon or self["iconUrl"],
        )
        remote = str((a.get("icon") or {}).get("url") or "")
        if not icon and remote and remote not in self._images:
            self._images.add(remote)
            self.gui.fetch_image(remote, lambda path, err, h=hit: self._icon(h, path))
        for url, caption in view.screenshot_urls(a):
            if url in self._images:
                continue
            self._images.add(url)
            slot: list[Any] = [url, caption, None, ""]
            self._shots.append(slot)
            self.set(shotsLoading=True)
            self.gui.fetch_image(url, lambda path, err, h=hit, s=slot: self._shot(h, s, path, err))

    def _icon(self, hit: view.Hit, path: str | None) -> None:
        if hit is self.hit and path:
            self.set(iconUrl=models.file_url(path))

    def _shot(self, hit: view.Hit, slot: list[Any], path: str | None, err: str) -> None:
        """One screenshot arrived (or failed): the strip keeps the AppStream order, the
        default one first, whatever order the downloads finish in."""
        if hit is not self.hit:
            return
        slot[2], slot[3] = path, (err or "") if not path else ""
        if path:
            done = [s for s in self._shots if s[2]]
            self.rows("shots").reset({"url": models.file_url(s[2]), "caption": s[1]} for s in done)
        failed = [s[3] for s in self._shots if s[3]]
        note = f"{len(failed)} screenshot(s) not shown: {failed[0]}" if failed else ""
        pending = any(not s[2] and not s[3] for s in self._shots)
        self.set(shotsLoading=pending, shotsNote=note)

    def _versions(self, hit: view.Hit, doc: Doc) -> None:
        if hit is not self.hit:
            return
        n = view.notice(doc)
        if n is not None:
            self.set(versionsLoading=False, versionsNote=f"{n.code}: {n.message}")
            self.rows("versions").reset([])
            return
        self.rows("versions").reset(models.fields(view.version_rows(doc)))
        self.set(versionsLoading=False, versionsNote="" if doc.get("results") else "no history")

    @Slot()
    def refreshWhere(self) -> None:
        hit = self.hit
        if hit is None:
            return
        self.set(whereLoading=True)
        self.gui.fetch(
            "where",
            *(hit.where_target, "--kind", "option" if hit.is_option else "package"),
            done=lambda doc, h=hit: self._where(h, doc),
            group="where",
            exclusive=True,
            label=f"where: evaluating {self.gui.backend.config_label}",
            hint="a few seconds cold, under one warm",
        )

    def _where(self, hit: view.Hit, doc: Doc) -> None:
        if hit is not self.hit:
            return
        self.where_doc = doc
        cue = view.where_cue(doc)
        sec = view.where_section(doc, self.gui.backend.host)
        if hit.kind == "hm" and not self["homeUser"]:
            self.set(homeUser=(self.gui.hm_users(doc) or [""])[0])
        self.rows("where").reset(models.fields(sec.fields))
        self.set(
            whereLoading=False,
            cue=cue.text if cue else "",
            cueStyle=cue.style if cue else "",
            whereNote=sec.note,
            whereNoteStyle=sec.style,
            currentValue=view.current_value(doc) or "",
        )
        self._home_choices()

    # --- the Home Manager user

    def _home_choices(self) -> None:
        g = self.gui
        users = g.hm_users(self.where_doc)
        hm = self.hit is not None and self.hit.kind == "hm"
        self.set(
            homeChoices=users if hm else ["", *users],
            homeKnown=bool(users) or g.status_doc is not None,
        )

    @Slot()
    def loadHomeUsers(self) -> None:
        if self["homeLoading"]:
            return
        self.set(homeLoading=True)
        self.gui.load_status_for_users(self._users_loaded)

    def _users_loaded(self, users: list[str]) -> None:
        self.set(homeLoading=False)
        self._home_choices()
        if not users:
            self.gui.notice.say(f"no Home Manager users on {self.gui.backend.host}", "info")

    @Slot(str)
    def setHome(self, user: str) -> None:
        self.set(homeUser=user)

    def sync_home(self, target: str, user: str) -> None:
        """The diff dialog picked another user for this row: the picker here follows."""
        if self.hit is not None and self.hit.target == target:
            self.set(homeUser=user)

    def home_user(self) -> str | None:
        """The --home user for a write, "" for the system; None (and a notice) when an HM
        option has no user to go to."""
        hit = self.hit
        if hit is None:
            return None
        if hit.kind == "hm" and not self["homeUser"]:
            self.gui.notice.say(
                f"Home Manager options need a user; {self.gui.backend.host} has none", "error"
            )
            return None
        return self["homeUser"] if self["canHome"] else ""

    # --- actions

    def _write(self, verb: str) -> None:
        user = self.home_user()
        if user is None or self.hit is None:
            return
        self.gui.writer.start(
            verb,
            [self.hit.target],
            user=user,
            can_home=self["canHome"],
            system_ok=self.hit.kind != "hm",
            after=self.refreshWhere,
        )

    @Slot()
    def add(self) -> None:
        if self["isPackage"]:
            self._write("add")

    @Slot()
    def remove(self) -> None:
        if self["isPackage"]:
            self._write("rm")

    @Slot()
    def unset(self) -> None:
        if self["isOption"]:
            self._write("unset")

    @Slot()
    def editOption(self) -> None:
        hit = self.hit
        user = self.home_user()
        if hit is None or not hit.is_option or user is None:
            return
        value = view.current_value(self.where_doc) or str(self.record.get("default") or "")
        self.gui.writer.open_set(
            hit.target,
            value,
            type_=str(self.record.get("type") or hit.detail or ""),
            user=user,
            can_home=self["canHome"],
            after=self.refreshWhere,
        )


# --- writer ---------------------------------------------------------------------------------


@dataclass
class WriteRequest:
    verb: str
    args: list[str]
    user: str
    can_home: bool
    system_ok: bool
    after: Callable[[], None] | None

    def argv(self) -> list[str]:
        return [*self.args, *(["--home", self.user] if self.user else [])]


class WriterVM(VM):
    """The diff dialog (every write is previewed with --dry-run first, then written), and
    the Set dialog in front of it."""

    changed = Signal()
    defaults: ClassVar = {
        "open": False,
        "phase": "",  # previewing | preview | writing | failed (written, eval check failed)
        "title": "",
        "verb": "",
        "command": "",  # the CLI call the preview shows
        "nothing": False,  # the preview changes no file
        "canHome": False,
        "homeUser": "",
        "homeChoices": [],
        "setOpen": False,
        "setOption": "",
        "setValue": "",
        "setType": "",
        "setUser": "",
        "setPath": False,  # the option is a template: its name is edited in the dialog
        "setSelect": [],  # [start, end] of the template's `<name>`, selected to type over
    }
    lists: ClassVar = {"diff": models.SPAN, "fields": models.FIELD, "setHint": models.FIELD}
    open = state(bool, "open", changed)
    phase = state(str, "phase", changed)
    title = state(str, "title", changed)
    verb = state(str, "verb", changed)
    command = state(str, "command", changed)
    nothing = state(bool, "nothing", changed)
    canHome = state(bool, "canHome", changed)
    homeUser = state(str, "homeUser", changed)
    homeChoices = state("QVariantList", "homeChoices", changed)
    setOpen = state(bool, "setOpen", changed)
    setOption = state(str, "setOption", changed)
    setValue = state(str, "setValue", changed)
    setType = state(str, "setType", changed)
    setUser = state(str, "setUser", changed)
    setPath = state(bool, "setPath", changed)
    setSelect = state("QVariantList", "setSelect", changed)
    diff = model("diff")
    fields = model("fields")
    setHint = model("setHint")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.req: WriteRequest | None = None
        self._set_req: dict[str, Any] = {}
        self._users: list[str] | None = None  # the picker's users, as start() was given them

    def start(
        self,
        verb: str,
        args: list[str],
        *,
        user: str = "",
        can_home: bool = False,
        system_ok: bool = True,
        after: Callable[[], None] | None = None,
        users: list[str] | None = None,
    ) -> None:
        """`users`: the Home Manager users the dialog's picker offers (default: the host's,
        as the details pane knows them)."""
        g = self.gui
        if g["busy"]:
            g.notice.say(f"wait: {g['busy']} is still running", "warn")
            return
        self._users = users
        users = list(users) if users is not None else g.hm_users(g.details.where_doc)
        if user and user not in users:
            users = [user, *users]
        self.req = WriteRequest(verb, list(args), user, can_home, system_ok, after)
        self.set(
            open=True,
            verb=verb,
            canHome=can_home and bool(users),
            homeUser=user,
            homeChoices=(["", *users] if system_ok else users),
        )
        self._preview()

    def _preview(self) -> None:
        req = self.req
        assert req is not None
        self.rows("diff").reset([])
        self.rows("fields").reset([])
        argv = req.argv()
        self.set(
            phase="previewing",
            title=f"{req.verb} {' '.join(req.args)}",
            command=shell([req.verb, *argv, "--dry-run"]),
            nothing=False,
        )
        started = self.gui.fetch(
            req.verb,
            *argv,
            "--dry-run",
            done=lambda doc, r=req: self._previewed(r, doc),
            busy="writing",
            label=f"{req.verb} --dry-run",
            hint="one eval: a second or two",
        )
        if not started:
            self.set(open=False)

    def _previewed(self, req: WriteRequest, doc: Doc) -> None:
        if req is not self.req or not self["open"]:
            return
        if not doc.get("ok"):
            self._failed(req, doc)
            return
        self._render(doc)
        self.set(phase="preview", nothing=not view.writer_changed(doc))

    def _render(self, doc: Doc) -> None:
        self.rows("diff").reset(models.spans(view.diff_spans(doc)))
        self.rows("fields").reset(models.fields(view.writer_fields(doc)))

    def _failed(self, req: WriteRequest, doc: Doc) -> None:
        """A writer error closes the dialog and becomes the notice; `ambiguous` (and a
        --where that missed) lists the candidates, and picking one runs it again."""
        self.set(open=False)
        can_pick = req.verb in WHERE_VERBS and "--where" not in req.args

        def pick(where: str) -> None:
            self.start(
                req.verb,
                [*req.args, "--where", where],
                user=req.user,
                can_home=req.can_home,
                system_ok=req.system_ok,
                after=req.after,
                users=self._users,
            )

        context = f"{req.verb} {' '.join(req.argv())}"
        self.gui.notice.show(doc, context, pick if can_pick else None)

    @Slot(str)
    def setHome(self, user: str) -> None:
        req = self.req
        if req is None or self["phase"] != "preview" or user == req.user:
            return
        req.user = user
        self.set(homeUser=user)
        target = req.args[0] if req.args else ""
        self.gui.details.sync_home(target, user)
        self.gui.options.sync_home(target, user)
        self._preview()

    @Slot()
    def write(self) -> None:
        req = self.req
        if req is None or self["phase"] != "preview":
            return
        started = self.gui.fetch(
            req.verb,
            *req.argv(),
            done=lambda doc, r=req: self._written(r, doc),
            busy="writing",
            label=req.verb,
            hint="one or two evals: seconds",
        )
        if started:
            self.set(phase="writing", command=shell([req.verb, *req.argv()]))

    def _written(self, req: WriteRequest, doc: Doc) -> None:
        g = self.gui
        if not doc.get("ok"):
            self._failed(req, doc)
            return
        summary = view.writer_summary(doc)
        g.wrote(summary)
        if view.writer_eval_failed(doc):
            self._render(doc)
            self.set(phase="failed", title=f"{req.verb}: written, but the eval check FAILED")
        else:
            self.set(open=False)
            g.notice.say(f"written: {summary} · Pending: plan, apply", "ok")
        if req.after is not None:
            req.after()

    @Slot()
    def cancel(self) -> None:
        if self["phase"] == "writing":
            self.gui.notice.say("the write is running; it finishes on its own", "warn")
            return
        self.req = None
        self.set(open=False)

    # --- set

    def open_set(
        self,
        option: str,
        value: str,
        *,
        type_: str,
        user: str,
        can_home: bool,
        after: Callable[[], None] | None,
        hint: list[view.Field] | None = None,
        path: bool = False,
        users: list[str] | None = None,
    ) -> None:
        """`hint`: fields under the value (the example); `path`: the option is a template
        (`users.users.<name>.shell`) whose name is filled in here."""
        self._set_req = {"user": user, "can_home": can_home, "after": after, "users": users}
        self.rows("setHint").reset(models.fields(hint or []))
        self.set(
            setOpen=True,
            setOption=option,
            setValue=value,
            setType=type_,
            setUser=user,
            setPath=path,
            setSelect=list(view.option_placeholder(option) or ()) if path else [],
        )

    @Slot(str)
    def previewSet(self, value: str) -> None:
        self.previewSetPath(self["setOption"], value)

    @Slot(str, str)
    def previewSetPath(self, option: str, value: str) -> None:
        """The dialog's Preview: OPTION (as edited, for a template) and VALUE."""
        value, option = value.strip(), option.strip()
        if not self["setOpen"]:
            return
        if not value:
            self.gui.notice.say('a Nix value is needed: true, 42, "text", [ … ]', "warn")
            return
        if not option or view.option_template(option):
            self.gui.notice.say("fill in the name in place of <name> / * first", "warn")
            return
        r = self._set_req
        self.set(setOpen=False, setValue=value)
        self.start(
            "set",
            [option, value],
            user=r["user"],
            can_home=r["can_home"],
            system_ok=not r["user"],
            after=r["after"],
            users=r.get("users"),
        )

    @Slot()
    def cancelSet(self) -> None:
        self.set(setOpen=False)


# --- the build tracker ----------------------------------------------------------------------

TRACK_MS = 500  # how often a running plan/apply's tracker is read
STEP_NOTE = {
    "prepare": "reading the trees",
    "eval": "evaluating the config",
    "diff": "diffing the closures",
    "commit": "committing (cannot be stopped)",
    "pull": "pulling into the authoritative tree (cannot be stopped)",
    "switch": "switching (cannot be stopped)",
    "verify": "verifying (cannot be stopped)",
}
TRACKING_STATE = {
    "buildHead": "",
    "buildPlace": "",  # where the build runs: "building on mbp13 · big jobs" (empty: local)
    "stepNote": "",
    "stopEnabled": False,
    "stopLabel": "Stop",
}
TRACKING_LISTS = {"build": models.FIELD, "tail": models.LINE}


class _Tracking:
    """Mixed into a VM that runs a build: `track(token)` polls the token's snapshot every
    TRACK_MS into `buildHead`, `buildPlace`, `stepNote`, `stopEnabled`, `stopLabel` and
    the `build` and `tail` models; `untrack()` clears them. The VM declares
    TRACKING_STATE and TRACKING_LISTS and a `stop` slot."""

    _token: Cancel | None = None
    _timer: QTimer | None = None

    def track(self: Any, token: Cancel) -> None:
        self._token = token
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(TRACK_MS)
            self._timer.timeout.connect(self.tick)
        self._timer.start()
        self.tick()

    def untrack(self: Any) -> None:
        self._token = None
        if self._timer is not None:
            self._timer.stop()
        self.rows("build").reset([])
        self.rows("tail").reset([])
        self.set(**TRACKING_STATE)

    def tick(self: Any) -> None:
        token = self._token
        if token is None:
            return
        snapshot = token.snapshot
        snap = snapshot() if snapshot else None
        self.rows("build").reset(models.fields(view.build_rows(snap)))
        self.rows("tail").reset([{"text": ln} for ln in view.build_tail(snap)])
        note = STEP_NOTE.get(token.step, "")
        self.set(
            buildHead=view.build_head(snap),
            buildPlace=view.build_where(snap),
            stepNote=note if (snap is None or token.step != "build") else "",
            stopEnabled=token.stoppable,
            stopLabel="Stopping…" if token.requested else "Stop",
        )

    def stop_run(self: Any) -> None:
        self.gui.stop()
        self.tick()


# --- pending + plan -------------------------------------------------------------------------


class PendingVM(VM, _Tracking):
    changed = Signal()
    defaults: ClassVar = {
        "loading": False,
        "statusNote": "",
        "statusNoteStyle": "",
        "hasPending": False,
        "sessionNote": "no writes yet",
        "planning": False,
        "planned": False,
        "planNote": "Plan builds the workspace and diffs it against the running system",
        "planNoteStyle": "dim",
        "closureHead": "",
        "syncing": False,
        "synced": False,
        "canUndo": False,  # status.lock_change: an update that is not committed
        **TRACKING_STATE,
    }
    lists: ClassVar = {
        "pending": models.FIELD,
        "session": models.FIELD,
        "plan": models.FIELD,
        "closure": models.CLOSURE,
        "sync": models.FIELD,
        **TRACKING_LISTS,
    }
    logs: ClassVar = ("log",)
    loading = state(bool, "loading", changed)
    statusNote = state(str, "statusNote", changed)
    statusNoteStyle = state(str, "statusNoteStyle", changed)
    hasPending = state(bool, "hasPending", changed)
    sessionNote = state(str, "sessionNote", changed)
    planning = state(bool, "planning", changed)
    planned = state(bool, "planned", changed)
    planNote = state(str, "planNote", changed)
    planNoteStyle = state(str, "planNoteStyle", changed)
    closureHead = state(str, "closureHead", changed)
    syncing = state(bool, "syncing", changed)
    synced = state(bool, "synced", changed)
    canUndo = state(bool, "canUndo", changed)
    buildHead = state(str, "buildHead", changed)
    buildPlace = state(str, "buildPlace", changed)
    stepNote = state(str, "stepNote", changed)
    stopEnabled = state(bool, "stopEnabled", changed)
    stopLabel = state(str, "stopLabel", changed)
    pending = model("pending")
    session = model("session")
    plan = model("plan")
    closure = model("closure")
    sync = model("sync")
    log = model("log")
    build = model("build")
    tail = model("tail")

    def visit(self) -> None:
        self.render_session()
        self.render_plan()
        self._refresh(force=False)

    @Slot()
    def refresh(self) -> None:
        self._refresh(force=True)

    def _refresh(self, force: bool) -> None:
        g = self.gui
        if g.status_doc is not None and not force and not g.status_stale:
            self._status(g.status_doc)
            return
        self.set(loading=True)
        g.load_status(self._status)

    def _status(self, doc: Doc) -> None:
        self.set(loading=False)
        n = view.notice(doc)
        if n is not None:
            self.rows("pending").reset([])
            self.set(statusNote=f"status: {n.code}: {n.message}", statusNoteStyle="error")
            return
        p, change = doc.get("pending"), doc.get("lock_change")
        self.rows("pending").reset(models.fields(view.pending_fields(p, change)))
        self.set(statusNote="", hasPending=view.has_pending(p), canUndo=view.can_undo(change))

    @Slot()
    def startUndo(self) -> None:
        """`update --undo` through the Packages page's dialog."""
        self.gui.packages.startUndo()

    def render_session(self) -> None:
        g = self.gui
        rows = [view.Field("wrote", w, "accent") for w in g.writes]
        rows += [
            view.Field("applied", f"generation {d.get('generation')}", "ok") for d in g.applied
        ]
        self.rows("session").reset(models.fields(rows))
        self.set(sessionNote="" if rows else "no writes yet")

    @Slot()
    def startPlan(self) -> None:
        log = self.logmodel("log")
        token = Cancel(watched=True)
        started = self.gui.fetch(
            "plan",
            *self.gui.build_args(),
            done=self._planned,
            stream=True,
            log=log,
            busy="planning",
            label="plan: building the workspace",
            hint="38 s for nothing new, minutes for new packages, hours for a kernel; Stop stops",
            cancel=token,
        )
        if started:
            log.clear()
            self.gui.plan_doc = None
            self.set(planning=True)
            self.render_plan()
            self.track(token)

    @Slot()
    def stop(self) -> None:
        self.stop_run()

    def _planned(self, doc: Doc) -> None:
        self.untrack()
        self.gui.plan_doc = doc
        self.set(planning=False)
        if not doc.get("ok"):
            self.gui.notice.show(doc, "plan")
        self.render_plan()

    def render_plan(self) -> None:
        """The session's last plan, or the hint when there is none (a write or an apply
        since made it stale)."""
        doc = self.gui.plan_doc
        if doc is None:
            if not self["planning"]:
                self.logmodel("log").clear()
            self.rows("plan").reset([])
            self.rows("closure").reset([])
            note = "" if self["planning"] else self.defaults["planNote"]
            self.set(planned=False, closureHead="", planNote=note, planNoteStyle="dim")
            return
        n = view.notice(doc)
        if n is not None:
            self.rows("plan").reset([])
            self.rows("closure").reset([])
            self.set(
                planned=False,
                closureHead="",
                planNote=f"plan: {n.code}: {n.brief}",
                planNoteStyle="dim" if n.severity == "info" else "error",  # stopped: calm
            )
            return
        self.rows("plan").reset(models.fields(view.plan_fields(doc)))
        self.rows("closure").reset(models.closure(view.closure_rows(doc.get("diff"))))
        self.set(planned=True, closureHead=view.closure_head(doc.get("diff")), planNote="")

    @Slot()
    def startSync(self) -> None:
        if self.gui.fetch(
            "sync",
            done=self._synced,
            label="sync: git fetch of the hub",
            hint="a second or two",
        ):
            self.set(syncing=True)

    def _synced(self, doc: Doc) -> None:
        self.set(syncing=False)
        if not doc.get("ok"):
            self.gui.notice.show(doc, "sync")
            self.rows("sync").reset([])
            self.set(synced=False)
            return
        self.rows("sync").reset(models.fields(view.sync_fields(doc)))
        self.set(synced=True)

    @Slot()
    def startApply(self) -> None:
        self.gui.apply.start()


# --- apply ----------------------------------------------------------------------------------


class ApplyVM(VM, _Tracking):
    """The apply dialog: `apply --dry-run` → confirm (the commit message) → `apply` with its
    log streaming and the build tracker → the new generation; for `needs-sudo` the commands
    to copy and Retry. Stop stops the build (phase `stopped`, Retry); from the pull on the
    verb refuses it."""

    changed = Signal()
    defaults: ClassVar = {
        "open": False,
        "phase": "",  # checking | confirm | running | done | needs-sudo | failed | stopped
        "placeholder": "",
        "message": "",
        "closureHead": "",
        "noticeCode": "",
        "noticeTitle": "",
        "noticeMessage": "",
        "noticeSeverity": "",
        "commands": "",
        **TRACKING_STATE,
    }
    lists: ClassVar = {"dry": models.FIELD, "result": models.FIELD, **TRACKING_LISTS}
    logs: ClassVar = ("log",)
    open = state(bool, "open", changed)
    phase = state(str, "phase", changed)
    placeholder = state(str, "placeholder", changed)
    message = state(str, "message", changed)
    closureHead = state(str, "closureHead", changed)
    noticeCode = state(str, "noticeCode", changed)
    noticeTitle = state(str, "noticeTitle", changed)
    noticeMessage = state(str, "noticeMessage", changed)
    noticeSeverity = state(str, "noticeSeverity", changed)
    commands = state(str, "commands", changed)
    buildHead = state(str, "buildHead", changed)
    buildPlace = state(str, "buildPlace", changed)
    stepNote = state(str, "stepNote", changed)
    stopEnabled = state(bool, "stopEnabled", changed)
    stopLabel = state(str, "stopLabel", changed)
    dry = model("dry")
    result = model("result")
    log = model("log")
    build = model("build")
    tail = model("tail")

    @Slot()
    def start(self) -> None:
        if self["open"]:
            return
        started = self.gui.fetch(
            "apply",
            "--dry-run",
            *self.gui.build_args(),
            done=self._dry,
            busy="checking",
            label="apply --dry-run",
            hint="about a second: nix flake metadata, sudo -n true",
        )
        if not started:
            return
        self.rows("dry").reset([])
        self.rows("result").reset([])
        self.logmodel("log").clear()
        self.set(
            open=True,
            phase="checking",
            placeholder="",
            message="",
            closureHead="",
            noticeCode="",
            commands="",
        )

    def _dry(self, doc: Doc) -> None:
        if not self["open"] or self["phase"] != "checking":
            return
        if not doc.get("ok"):  # nothing-to-apply is a calm notice (severity info)
            self.set(open=False)
            self.gui.notice.show(doc, "apply")
            return
        self.rows("dry").reset(models.fields(view.dry_run_fields(doc)))
        self.set(phase="confirm", placeholder=view.generated_message(doc))

    @Slot(str)
    def confirm(self, message: str) -> None:
        if self["phase"] == "confirm":
            self.set(message=message.strip())
            self._run()

    def _run(self) -> None:
        log = self.logmodel("log")
        msg = self["message"]
        token = Cancel(watched=True)
        started = self.gui.fetch(
            "apply",
            *(["-m", msg] if msg else []),
            *self.gui.build_args(),
            done=self._done,
            stream=True,
            log=log,
            busy="applying",
            label="apply: commit, build, pull, switch",
            hint="about 30 s after a plan; nixos-rebuild evaluates again as root; "
            "Stop stops the build, not the switch",
            cancel=token,
        )
        if started:
            log.clear()
            self.rows("result").reset([])
            self.set(phase="running", noticeCode="", commands="", closureHead="")
            self.track(token)

    @Slot()
    def stop(self) -> None:
        self.stop_run()

    def _done(self, doc: Doc) -> None:
        g = self.gui
        self.untrack()
        g.applied_doc(doc)
        n = view.notice(doc)
        if n is not None:
            self.rows("result").reset(models.fields(view.progress_fields(doc)))
            self.set(
                phase="needs-sudo"
                if n.commands
                else "stopped"
                if n.code == "cancelled"
                else "failed",
                noticeCode=n.code,
                noticeTitle=n.title,
                noticeMessage=n.brief,
                noticeSeverity=n.severity,
                commands="\n".join(n.commands),
            )
            return
        self.rows("result").reset(models.fields(view.apply_fields(doc)))
        self.set(phase="done", closureHead=view.closure_head(doc.get("diff")))

    @Slot()
    def retry(self) -> None:
        if self["phase"] in ("needs-sudo", "failed", "stopped"):
            self._run()

    @Slot()
    def copyCommands(self) -> None:
        self.gui.copy(self["commands"])

    @Slot()
    def close(self) -> None:
        if self["phase"] == "running":
            self.gui.notice.say("apply is running; it finishes on its own", "warn")
            return
        self.set(open=False)


# --- packages: list, outdated, update -------------------------------------------------------

PACKAGE_TABS = ("installed", "updates")
OUTDATED_MODES = ("lock", "published")
OUTDATED_HINT = {
    "lock": "two package-list evals: seconds; about a minute when nixpkgs has a new commit",
    "published": "one eval and a few search.nixos.org queries: seconds",
}


class PackagesVM(VM):
    """What the host has installed (`list`, loaded once, narrowed here by the filter and the
    scope with `list --filter`/`--scope` semantics) and what a nixpkgs update would change
    (`outdated`, lock or --published), with the dialog that takes it: `update --dry-run` →
    confirm → `update` → Pending. A selected row opens in the details pane, for its user."""

    changed = Signal()
    defaults: ClassVar = {
        "tab": "installed",
        "listLoading": False,
        "listNote": "",
        "listNoteStyle": "",
        "footer": "",
        "filter": "",
        "scope": "all",
        "scopeKeys": ["all"],
        "scopeLabels": ["all"],
        "selected": -1,
        "mode": "lock",
        "updatesLoading": False,
        "updatesNote": "",
        "updatesNoteStyle": "",
        "head": "",
        "kernel": "",
        "kernelStyle": "dim",
        "summary": "",
        "notes": "",
        "canUpdate": False,
        "kernelHold": "",  # the kernel's hold toggle label ("" when the kernel does not move)
        "canUndo": False,  # the lock moved and the move is not committed (update --undo)
        "upSelected": -1,
        "updateOpen": False,
        "updatePhase": "",  # checking | confirm | running
        "updateTitle": "",
        "updateConfirmLabel": "Update",  # or Undo: the dialog serves `update --undo` too
    }
    lists: ClassVar = {
        "packages": models.PACKAGE,
        "updates": models.UPDATE,
        "updateFields": models.FIELD,
    }
    tab = state(str, "tab", changed)
    listLoading = state(bool, "listLoading", changed)
    listNote = state(str, "listNote", changed)
    listNoteStyle = state(str, "listNoteStyle", changed)
    footer = state(str, "footer", changed)
    filter = state(str, "filter", changed)
    scope = state(str, "scope", changed)
    scopeKeys = state("QVariantList", "scopeKeys", changed)
    scopeLabels = state("QVariantList", "scopeLabels", changed)
    selected = state(int, "selected", changed)
    mode = state(str, "mode", changed)
    updatesLoading = state(bool, "updatesLoading", changed)
    updatesNote = state(str, "updatesNote", changed)
    updatesNoteStyle = state(str, "updatesNoteStyle", changed)
    head = state(str, "head", changed)
    kernel = state(str, "kernel", changed)
    kernelStyle = state(str, "kernelStyle", changed)
    summary = state(str, "summary", changed)
    notes = state(str, "notes", changed)
    canUpdate = state(bool, "canUpdate", changed)
    kernelHold = state(str, "kernelHold", changed)
    canUndo = state(bool, "canUndo", changed)
    upSelected = state(int, "upSelected", changed)
    updateOpen = state(bool, "updateOpen", changed)
    updatePhase = state(str, "updatePhase", changed)
    updateTitle = state(str, "updateTitle", changed)
    updateConfirmLabel = state(str, "updateConfirmLabel", changed)
    packages = model("packages")
    updates = model("updates")
    updateFields = model("updateFields")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.list_doc: Doc | None = None
        self.list_stale = True
        self.outdated_docs: dict[str, Doc] = {}  # mode → its last ok document
        self.rows_all: list[view.PackageRow] = []
        self.shown: list[view.PackageRow] = []
        self.update_rows: list[view.UpdateRow] = []
        self._update_args: list[str] = []
        self._list_error = ""

    def visit(self) -> None:
        if self.list_doc is None or self.list_stale:
            self.loadList()
        if self["tab"] == "updates":
            self.loadUpdates()

    def stale(self) -> None:
        """A write or an update since: the next visit evaluates again; if the page is still
        open once the flow that wrote is done (an update goes on to Pending), now."""
        self.list_stale = True
        QTimer.singleShot(0, lambda: self.gui["page"] == "packages" and self.visit())

    @Slot(str)
    def setTab(self, tab: str) -> None:
        if tab in PACKAGE_TABS and tab != self["tab"]:
            self.set(tab=tab)
            if tab == "updates":
                self.loadUpdates()

    # --- installed

    @Slot()
    def loadList(self, refresh: bool = False) -> None:
        self.set(listLoading=True)
        self.gui.fetch(
            "list",
            *(["--refresh"] if refresh else []),
            done=self._listed,
            group="list",
            exclusive=True,
            label=f"list: evaluating {self.gui.backend.config_label}",
            hint="a few seconds cold, under a second warm",
        )

    def _listed(self, doc: Doc) -> None:
        self.set(listLoading=False)
        n = view.notice(doc)
        if n is not None:
            self.rows_all = []
            self._list_error = f"list: {n.code}: {n.message}"
        else:
            self.list_doc, self.list_stale = doc, False
            self.rows_all = view.package_rows(doc)
            self._list_error = ""
            scopes = view.package_scopes(list(doc.get("users") or []))
            keys = [sc.key for sc in scopes]
            self.set(
                scopeKeys=keys,
                scopeLabels=[sc.label for sc in scopes],
                scope=self["scope"] if self["scope"] in keys else "all",
            )
            self.gui.details._home_choices()  # the users are known now
        self.render_list()

    def _scope(self) -> view.Scope:
        keys, labels = self["scopeKeys"], self["scopeLabels"]
        i = keys.index(self["scope"]) if self["scope"] in keys else 0
        return view.Scope(keys[i], labels[i])

    def render_list(self) -> None:
        self.shown = view.filter_packages(self.rows_all, self["filter"], self["scope"])
        self.rows("packages").reset(models.packages(self.shown))
        doc = self.list_doc
        footer = ""
        if doc is not None and self.rows_all:
            footer = view.list_footer(doc, len(self.shown), self["filter"], self._scope())
        note = self._list_error
        if not note and doc is not None and not self.shown:
            note = "nothing matches · change the filter or the scope"
        self.set(
            footer=footer,
            selected=-1,
            listNote=note,
            listNoteStyle="error" if self._list_error else "dim",
        )

    @Slot(str)
    def setFilter(self, text: str) -> None:
        if text != self["filter"]:
            self.set(filter=text)
            self.render_list()

    @Slot(str)
    def setScope(self, key: str) -> None:
        if key in self["scopeKeys"] and key != self["scope"]:
            self.set(scope=key)
            self.render_list()

    @Slot(int)
    def select(self, i: int) -> None:
        if 0 <= i < len(self.shown):
            r = self.shown[i]
            self.set(selected=i, upSelected=-1)
            self.gui.search.set(selected=-1)
            self.gui.details.show(view.package_hit(r), user=r.user or "", owner="packages")

    # --- updates

    @Slot()
    def loadUpdates(self, refresh: bool = False) -> None:
        mode = self["mode"]
        doc = self.outdated_docs.get(mode)
        if doc is not None and not refresh:
            self.show_updates(doc)
            return
        self.set(updatesLoading=True, updatesNote="")
        self.gui.fetch(
            "outdated",
            *(["--published"] if mode == "published" else []),
            *(["--refresh"] if refresh else []),
            done=lambda d, m=mode: self._outdated(m, d),
            group="outdated",
            exclusive=True,
            label=f"outdated{' --published' if mode == 'published' else ''}: evaluating",
            hint=OUTDATED_HINT[mode],
        )

    def _outdated(self, mode: str, doc: Doc) -> None:
        if doc.get("ok"):
            self.outdated_docs[mode] = doc
        if mode == self["mode"]:
            self.show_updates(doc)

    def show_updates(self, doc: Doc) -> None:
        self.set(updatesLoading=False, upSelected=-1)
        n = view.notice(doc)
        if n is not None:
            self.update_rows = []
            self.rows("updates").reset([])
            self.set(
                updatesNote=f"outdated: {n.code}: {n.message}",
                updatesNoteStyle="error",
                head="",
                kernel="",
                summary="",
                notes="",
                canUpdate=True,  # the lock can move without a comparison
                canUndo=False,
            )
            return
        self.update_rows = view.update_rows(doc)
        self.rows("updates").reset(models.updates(self.update_rows))
        k = view.kernel_line(doc)
        hold = view.kernel_hold(doc)
        self.set(
            updatesNote="",
            head=view.outdated_head(doc, self.gui.backend.host),
            kernel=k.text,
            kernelStyle=k.style,
            summary=view.outdated_summary(doc),
            notes=" · ".join(view.outdated_notes(doc)),
            canUpdate=view.update_args(doc) is not None,
            kernelHold=hold[0] if hold else "",
            canUndo=view.can_undo(doc.get("lock_change")),
        )

    # --- holds: pin / unpin through the writer's diff dialog

    def _hold(self, args: list[str]) -> None:
        self.gui.writer.start(args[0], args[1:], after=self.gui.pins.stale)

    @Slot(int)
    def hold(self, i: int) -> None:
        """`h` on an update row: pin it (`pin PNAME`), or unpin the pin holding it."""
        if 0 <= i < len(self.update_rows):
            r = self.update_rows[i]
            if r.pname == "kernel":
                self.holdKernel()
            else:
                self._hold(r.hold_args)

    @Slot()
    def holdKernel(self) -> None:
        doc = self.outdated_docs.get(self["mode"])
        hold = view.kernel_hold(doc) if doc else None
        if hold is None:
            self.gui.notice.say("the kernel does not change with this update", "info")
            return
        self._hold(hold[1])

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in OUTDATED_MODES and mode != self["mode"]:
            self.set(mode=mode)
            self.loadUpdates()

    @Slot(int)
    def selectUpdate(self, i: int) -> None:
        if 0 <= i < len(self.update_rows):
            r = self.update_rows[i]
            if r.pname == "kernel":
                self.set(upSelected=i)
                return
            row = view.PackageRow(r.scope, r.user, r.pname, r.pname, r.old, "", r.pname)
            self.set(upSelected=i, selected=-1)
            self.gui.search.set(selected=-1)
            self.gui.details.show(view.package_hit(row), user=r.user or "", owner="packages")

    @Slot()
    def refresh(self) -> None:
        if self["tab"] == "installed":
            self.loadList(refresh=True)
        else:
            self.loadUpdates(refresh=True)

    # --- update

    @Slot()
    def startUpdate(self) -> None:
        """`update --dry-run` → the dialog (confirm) → `update` → Pending."""
        doc = self.outdated_docs.get(self["mode"])
        args = view.update_args(doc) if doc is not None else []
        if args is None:
            if doc is not None and view.can_undo(doc.get("lock_change")):
                self.gui.notice.say(
                    "flake.lock already has it: Pending's Plan/Apply, or Undo", "info"
                )
            else:
                self.gui.notice.say("nothing to update: the lock already has the target", "info")
            return
        self._update_args = args
        started = self.gui.fetch(
            "update",
            *args,
            "--dry-run",
            done=lambda d, o=doc: self._dry(d, o),
            busy="checking",
            label="update --dry-run",
            hint="reads flake.lock: instant",
        )
        if started:
            self.rows("updateFields").reset([])
            self.set(
                updateOpen=True,
                updatePhase="checking",
                updateTitle="update nixpkgs",
                updateConfirmLabel="Update",
            )

    @Slot()
    def startUndo(self) -> None:
        """`update --undo --dry-run` → the dialog (confirm) → `update --undo`: flake.lock back
        to the committed one; the Updates tab then compares the lock with the branch again."""
        self._update_args = ["--undo"]
        started = self.gui.fetch(
            "update",
            "--undo",
            "--dry-run",
            done=lambda d: self._dry(d, None),
            busy="checking",
            label="update --undo --dry-run",
            hint="reads flake.lock and git: instant",
        )
        if started:
            self.rows("updateFields").reset([])
            self.set(
                updateOpen=True,
                updatePhase="checking",
                updateTitle="undo the lock move",
                updateConfirmLabel="Undo",
            )

    def _dry(self, dry: Doc, outdated: Doc | None) -> None:
        if not self["updateOpen"]:
            return
        if not dry.get("ok"):
            self.set(updateOpen=False, updatePhase="")
            self.gui.notice.show(
                dry, "update --undo" if "--undo" in self._update_args else "update"
            )
            return
        if dry.get("undo"):
            self.rows("updateFields").reset(models.fields(view.undo_confirm_fields(dry)))
            self.set(
                updatePhase="confirm",
                updateTitle=f"undo the {dry.get('input')} lock move (flake.lock back to the committed one)",
            )
            return
        lock = outdated if outdated is not None and outdated.get("mode") == "lock" else None
        self.rows("updateFields").reset(models.fields(view.update_confirm_fields(dry, lock)))
        self.set(updatePhase="confirm", updateTitle=f"update {dry.get('input')} in flake.lock")

    @Slot()
    def confirmUpdate(self) -> None:
        if self["updatePhase"] != "confirm":
            return
        args = self._update_args
        undo = "--undo" in args
        if self.gui.fetch(
            "update",
            *args,
            done=self._updated,
            busy="updating",
            label="update --undo: git checkout HEAD -- flake.lock"
            if undo
            else "update: " + shell(["nix", "flake", "update", "nixpkgs", *args]),
            hint="instant"
            if undo
            else "nix fetches the new nixpkgs: about a minute the first time",
        ):
            self.set(updatePhase="running")

    @Slot()
    def cancelUpdate(self) -> None:
        if self["updatePhase"] == "running":
            self.gui.notice.say("the update is running; it finishes on its own", "warn")
            return
        self.set(updateOpen=False, updatePhase="")

    def _updated(self, doc: Doc) -> None:
        g = self.gui
        self.set(updateOpen=False, updatePhase="")
        if not doc.get("ok"):
            g.notice.show(doc, "update")
            return
        if not doc.get("changed"):
            g.notice.say(view.update_fields(doc)[0].value, "info")
            return
        if doc.get("undo"):
            self.outdated_docs.clear()
            g.pins.doc = None  # a pin at the old revision is stale again
            g.wrote(view.update_summary(doc))
            g.notice.say(f"undone: {view.update_summary(doc)}", "ok")
            if g["page"] == "pending":
                g.pending.refresh()
            return
        had_holds = any(d.get("holds") for d in self.outdated_docs.values())
        self.outdated_docs.clear()
        g.wrote(view.update_summary(doc))
        g.notice.say(f"updated: {view.update_summary(doc)} · Pending: plan, apply", "ok")
        g.go("pending")
        if had_holds:  # the lock moved: now the holds' checks mean something
            g.pins.check(after_update=True)


class PinsVM(VM):
    """The Pins page: the host's pins (`pins`: name, what it holds, at which nixpkgs, stale
    or not), Check (`pins --check`: one eval, the compatibility checks against the lock as it
    is now) and Unpin (through the writer's diff dialog)."""

    changed = Signal()
    defaults: ClassVar = {
        "loading": False,
        "checking": False,
        "note": "",
        "noteStyle": "",
        "summary": "",
        "checked": False,
        "selected": -1,
        "detail": "",
        "pinOpen": False,  # the Pin… dialog
        "pinChoices": [],  # the revision chooser's labels (pins' revisions[])
        "pinName": "",  # what the dialog starts with
    }
    lists: ClassVar = {"pins": models.PIN, "checks": models.FIELD}
    loading = state(bool, "loading", changed)
    checking = state(bool, "checking", changed)
    note = state(str, "note", changed)
    noteStyle = state(str, "noteStyle", changed)
    summary = state(str, "summary", changed)
    checked = state(bool, "checked", changed)
    selected = state(int, "selected", changed)
    detail = state(str, "detail", changed)
    pinOpen = state(bool, "pinOpen", changed)
    pinChoices = state("QVariantList", "pinChoices", changed)
    pinName = state(str, "pinName", changed)
    pins = model("pins")
    checks = model("checks")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.doc: Doc | None = None
        self.pin_rows: list[view.PinRow] = []
        self.rev_choices: list[view.RevChoice] = []

    def visit(self) -> None:
        if self.doc is None:
            self.load()
        else:
            self._show(self.doc)

    def stale(self) -> None:
        """A pin, unpin or update since: the next visit reads again; if the page is open, now."""
        self.doc = None
        self.gui.packages.outdated_docs.clear()  # a hold changes what the update brings
        QTimer.singleShot(0, lambda: self.gui["page"] == "pins" and self.visit())

    @Slot()
    def load(self) -> None:
        self.set(loading=True, note="")
        self.gui.fetch(
            "pins",
            done=self._loaded,
            group="pins",
            exclusive=True,
            label="pins",
            hint="reads installer/state.json: instant",
        )

    @Slot()
    def check(self, after_update: bool = False) -> None:
        self.set(loading=True, checking=True, note="")
        self.gui.fetch(
            "pins",
            "--check",
            done=lambda d: self._loaded(d, after_update),
            group="pins",
            exclusive=True,
            label="pins --check: evaluating",
            hint="one eval of the host: seconds",
        )

    def _loaded(self, doc: Doc, after_update: bool = False) -> None:
        self.set(loading=False, checking=False)
        if doc.get("ok"):
            self.doc = doc
        self._show(doc)
        counts = view.check_counts(doc.get("checks")) if doc.get("ok") else ""
        if after_update and counts:
            self.gui.notice.say(f"holds after the update: {counts} · Pins (Ctrl+6)", "warn")

    def _show(self, doc: Doc) -> None:
        n = view.notice(doc)
        if n is not None:
            self.pin_rows = []
            self.rows("pins").reset([])
            self.rows("checks").reset([])
            self.set(note=f"pins: {n.code}: {n.message}", noteStyle="error", summary="")
            return
        self.pin_rows = view.pin_rows(doc)
        self.rows("pins").reset(models.pins(self.pin_rows))
        self.rows("checks").reset(models.fields(view.check_fields(doc.get("checks"))))
        self.set(
            note=""
            if self.pin_rows
            else "no pins · Pin… holds something at a revision; Packages › Updates holds a row",
            noteStyle="dim",
            summary=f"{self.gui.backend.host}: {view.pins_summary(doc)}",
            checked=bool(doc.get("checked")),
            selected=-1,
            detail="",
        )

    @Slot(int)
    def select(self, i: int) -> None:
        if 0 <= i < len(self.pin_rows):
            self.set(selected=i, detail=view.pin_detail(self.pin_rows[i]))

    @Slot(int)
    def unpin(self, i: int) -> None:
        if 0 <= i < len(self.pin_rows):
            self.gui.writer.start("unpin", [self.pin_rows[i].name], after=self.stale)

    # --- Pin…: name + revision → pin NAME --rev REV through the diff dialog

    @Slot()
    @Slot(str)
    def startPin(self, name: str = "") -> None:
        if self.gui["busy"]:
            self.gui.notice.say(f"wait: {self.gui['busy']} is still running", "warn")
            return
        if self.doc is None:
            self.gui.notice.say("pins is still loading", "info")
            return
        self.rev_choices = view.pin_rev_choices(self.doc)
        self.set(pinOpen=True, pinChoices=[c.label for c in self.rev_choices], pinName=name)

    @Slot(str, int, str)
    def confirmPin(self, name: str, choice: int, free: str) -> None:
        """The dialog's Preview: NAME at the chosen revision (`choice` indexes pinChoices;
        past it: the typed one)."""
        if not self["pinOpen"]:
            return
        rev = self.rev_choices[choice].rev if 0 <= choice < len(self.rev_choices) else free
        args = view.pin_at_args(name, rev)
        if isinstance(args, str):
            self.gui.notice.say(args, "warn")
            return
        self.set(pinOpen=False)
        self.gui.writer.start(args[0], args[1:], after=self.stale)

    @Slot()
    def cancelPin(self) -> None:
        self.set(pinOpen=False)


# --- options --------------------------------------------------------------------------------


class OptionsVM(VM):
    """The Options page: `search --kind opt|hm` (NixOS or Home Manager options), and the
    selected option in full: what it is (upstream's record: type, default, example, the
    description), its value on this host and the files that set it (`where`, the Home
    Manager user's tree for `hm`), where it is declared. Set… (the value dialog: the current
    value, the type and example as a hint) and Unset go through the writer's diff dialog,
    for the system or a Home Manager user (`--home`)."""

    changed = Signal()
    defaults: ClassVar = {
        "query": "",
        "kind": "opt",
        "count": 0,
        "source": "",
        "message": "type to search NixOS options",
        "loading": False,
        "selected": -1,
        "shows": 0,
        "present": False,
        "badge": "",
        "name": "",
        "template": False,  # a <name> to fill in: no where, Set asks for the name
        "canUnset": False,  # not for a template or a list element
        "cue": "",
        "cueStyle": "",
        "about": "",
        "hostTitle": "",
        "hostNote": "",
        "hostNoteStyle": "",
        "whereLoading": False,
        "homeUser": "",
        "homeChoices": [],
    }
    lists: ClassVar = {
        "results": models.HIT,
        "option": models.FIELD,
        "host": models.FIELD,
        "declared": models.FIELD,
    }
    query = state(str, "query", changed)
    kind = state(str, "kind", changed)
    count = state(int, "count", changed)
    source = state(str, "source", changed)
    message = state(str, "message", changed)
    loading = state(bool, "loading", changed)
    selected = state(int, "selected", changed)
    shows = state(int, "shows", changed)
    present = state(bool, "present", changed)
    badge = state(str, "badge", changed)
    name = state(str, "name", changed)
    template = state(bool, "template", changed)
    canUnset = state(bool, "canUnset", changed)
    cue = state(str, "cue", changed)
    cueStyle = state(str, "cueStyle", changed)
    about = state(str, "about", changed)
    hostTitle = state(str, "hostTitle", changed)
    hostNote = state(str, "hostNote", changed)
    hostNoteStyle = state(str, "hostNoteStyle", changed)
    whereLoading = state(bool, "whereLoading", changed)
    homeUser = state(str, "homeUser", changed)
    homeChoices = state("QVariantList", "homeChoices", changed)
    results = model("results")
    option = model("option")
    host = model("host")
    declared = model("declared")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.doc: Doc | None = None
        self.hits: list[view.Hit] = []
        self.hit: view.Hit | None = None
        self.where_doc: Doc | None = None
        self._last: tuple | None = None

    # --- the list

    @Slot(str)
    def search(self, query: str) -> None:
        self._run(query)

    @Slot(str)
    def setKind(self, kind: str) -> None:
        if kind in view.OPTION_KINDS and kind != self["kind"]:
            self.set(kind=kind)
            self._run(self["query"])

    def rerun(self) -> None:
        self._run(self["query"], force=True)

    def _message(self) -> str:
        return f"type to search {view.OPTION_KIND_LABEL[self['kind']]} options"

    def _run(self, query: str, force: bool = False) -> None:
        query = query.strip()
        self.set(query=query)
        key = (query, self["kind"], self.gui["channel"])
        if not query:
            self._last = None
            self.show(None)
            return
        if key == self._last and not force:
            return
        self._last = key
        self.set(loading=True)
        kind = self["kind"]
        self.gui.fetch(
            "search",
            *view.option_search_args(query, kind, self.gui["channel"]),
            done=self.show,
            group="options-search",
            exclusive=True,
            label=f"searching {view.OPTION_KIND_LABEL[kind]} options for {query!r}",
        )

    def show(self, doc: Doc | None) -> None:
        self.doc = doc
        ok = bool(doc and doc.get("ok"))
        if doc and not ok:
            self.gui.notice.show(doc, f"search {self['query']}")
        self.hits = view.hits(doc) if doc and ok else []
        self.rows("results").reset(models.hits(self.hits))
        if not doc:
            message = self._message()
        elif ok and not self.hits:
            message = "no options match · try the other kind"
        else:
            message = ""
        self.set(
            loading=False,
            count=len(self.hits),
            source=" · ".join(view.source_lines(doc)) if doc and ok else "",
            message=message,
            selected=-1,
        )

    # --- the selected option

    @Slot(int)
    def select(self, i: int) -> None:
        if not 0 <= i < len(self.hits):
            return
        hit = self.hits[i]
        self.hit, self.where_doc = hit, None
        user = (self.gui.hm_users() or [""])[0] if hit.kind == "hm" else ""
        template = view.option_template(view.option_set_name(hit.target))
        self.set(
            selected=i,
            present=True,
            shows=self["shows"] + 1,
            badge=view.KIND_BADGE.get(hit.kind, hit.kind),
            name=hit.name,
            template=template,
            canUnset=not view.option_template(hit.target),
            homeUser=user,
            hostTitle=f"on {self.gui.backend.host}",
        )
        self.render()
        if not template:
            self.refreshWhere()

    def _user(self) -> str | None:
        return self["homeUser"] or None

    def render(self) -> None:
        hit = self.hit
        if hit is None:
            return
        host = self.gui.backend.host
        option, about, on_host, declared = view.option_sections(
            hit.record, self.where_doc, host, hit.kind, self._user()
        )
        self.rows("option").reset(models.fields(option.fields))
        self.rows("host").reset(models.fields(on_host.fields))
        self.rows("declared").reset(models.fields(declared.fields))
        cue = view.option_cue(self.where_doc, hit.kind, self._user())
        users = self.users() if hit.kind == "hm" else []
        self.set(
            about=about.note,
            hostNote=on_host.note,
            hostNoteStyle=on_host.style or "dim",
            cue=cue.text if cue else "",
            cueStyle=cue.style if cue else "",
            homeChoices=users,
        )

    def users(self) -> list[str]:
        """The Home Manager users whose tree has the option (where), else the host's."""
        return view.option_users(self.where_doc) or self.gui.hm_users(self.where_doc)

    @Slot()
    def refreshWhere(self) -> None:
        hit = self.hit
        if hit is None or self["template"]:
            return
        self.set(whereLoading=True)
        self.gui.fetch(
            "where",
            *(view.option_set_name(hit.target), "--kind", "option"),
            done=lambda doc, h=hit: self._where(h, doc),
            group="options-where",
            exclusive=True,
            label=f"where: evaluating {self.gui.backend.config_label}",
            hint="a few seconds cold, under one warm",
        )

    def _where(self, hit: view.Hit, doc: Doc) -> None:
        if hit is not self.hit:
            return
        self.where_doc = doc
        users = view.option_users(doc)
        if hit.kind == "hm" and self["homeUser"] not in users and users:
            self.set(homeUser=users[0])
        self.set(whereLoading=False)
        self.render()

    @Slot(str)
    def setHome(self, user: str) -> None:
        if self.hit is not None and self.hit.kind == "hm" and user != self["homeUser"]:
            self.set(homeUser=user)
            self.render()

    def sync_home(self, target: str, user: str) -> None:
        """The diff dialog picked another user for this option: the picker here follows."""
        if self.hit is not None and self.hit.target == target and self.hit.kind == "hm":
            self.setHome(user)

    def _home(self) -> str | None:
        """The --home user ("" for NixOS); None (and a notice) for an HM option without one."""
        hit = self.hit
        if hit is None:
            return None
        if hit.kind != "hm":
            return ""
        if self["homeUser"]:
            return self["homeUser"]
        if self["template"] and self.gui.status_doc is None:  # no where: the users from status
            self.gui.notice.say("loading the host's Home Manager users (status)…", "info")
            self.gui.load_status_for_users(self._users_loaded)
            return None
        self.gui.notice.say(
            f"Home Manager options need a user; {self.gui.backend.host} has none", "error"
        )
        return None

    def _users_loaded(self, users: list[str]) -> None:
        if users and not self["homeUser"]:
            self.set(homeUser=users[0])
            self.render()
            self.gui.notice.say(f"for home {users[0]}: Set… again", "ok")
        elif not users:
            self.gui.notice.say(f"no Home Manager users on {self.gui.backend.host}", "info")

    @Slot()
    def editOption(self) -> None:
        hit, user = self.hit, self._home()
        if hit is None or user is None:
            return
        rec = hit.record
        option = view.option_set_name(hit.target)
        prefill_rec = rec if option == hit.target else {}  # the list: no default of its own
        self.gui.writer.open_set(
            option,
            view.option_prefill(prefill_rec, self.where_doc, hit.kind, user or None),
            type_=str(rec.get("type") or hit.detail or ""),
            user=user,
            can_home=hit.kind == "hm",
            after=self.refreshWhere,
            hint=view.option_set_hint(rec)[1:],  # the dialog shows the type itself
            path=self["template"],
            users=self.users(),
        )

    @Slot()
    def unset(self) -> None:
        hit, user = self.hit, self._home()
        if hit is None or user is None:
            return
        if not self["canUnset"]:
            self.gui.notice.say("a template: unset takes the full name", "warn")
            return
        self.gui.writer.start(
            "unset",
            [hit.target],
            user=user,
            can_home=hit.kind == "hm",
            system_ok=hit.kind != "hm",
            after=self.refreshWhere,
            users=self.users(),
        )


# --- browse ---------------------------------------------------------------------------------


class BrowseVM(VM):
    """Applications by category (`browse`): the categories with their counts, then one
    category's apps (installed, well-known, search.nixos.org's most popular). A selected
    app opens in the details pane with its icon, chips and screenshots. The list draws only
    local icons (installed apps); nothing is fetched until a row's details open."""

    changed = Signal()
    defaults: ClassVar = {
        "loading": False,
        "note": "",
        "noteStyle": "",
        "category": "",
        "label": "",
        "appsLoading": False,
        "appsNote": "",
        "appsNoteStyle": "",
        "footer": "",
        "selected": -1,
    }
    lists: ClassVar = {"categories": models.CATEGORY, "apps": models.APP}
    loading = state(bool, "loading", changed)
    note = state(str, "note", changed)
    noteStyle = state(str, "noteStyle", changed)
    category = state(str, "category", changed)
    label = state(str, "label", changed)
    appsLoading = state(bool, "appsLoading", changed)
    appsNote = state(str, "appsNote", changed)
    appsNoteStyle = state(str, "appsNoteStyle", changed)
    footer = state(str, "footer", changed)
    selected = state(int, "selected", changed)
    categories = model("categories")
    apps = model("apps")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.overview: Doc | None = None
        self.cats: list[view.BrowseCategory] = []
        self.shown: list[view.AppRow] = []
        self._channel = ""

    def visit(self) -> None:
        if self.overview is None or self._channel != self.gui["channel"]:
            self.load()

    @Slot()
    def load(self, refresh: bool = False) -> None:
        self._channel = self.gui["channel"]
        self.set(loading=True, note="")
        self.gui.fetch(
            "browse",
            *("--channel", self._channel, *(["--refresh"] if refresh else [])),
            done=lambda doc, r=refresh: self._overview(doc, r),
            group="browse-overview",
            exclusive=True,
            label="browse: the categories",
            hint="the installed apps, one search.nixos.org request",
        )

    def _overview(self, doc: Doc, refresh: bool) -> None:
        self.set(loading=False)
        n = view.notice(doc)
        if n is not None:
            self.set(note=f"browse: {n.code}: {n.message}", noteStyle="error")
            return
        self.overview = doc
        self.cats = view.browse_categories(doc)
        self.rows("categories").reset(models.categories(self.cats))
        err = ((doc.get("sources") or {}).get("search.nixos.org") or {}).get("error")
        note = f"search.nixos.org: {err['code']}: {err['message']}" if err else ""
        self.set(note=" · ".join([note, *(doc.get("notes") or [])]).strip(" ·"), noteStyle="dim")
        key = self["category"] or (self.cats[0].key if self.cats else "")
        if key:
            self.setCategory(key, force=True, refresh=refresh)

    @Slot(str)
    def setCategory(self, key: str, force: bool = False, refresh: bool = False) -> None:
        cat = next((c for c in self.cats if c.key == key), None)
        if cat is None or (key == self["category"] and not force):
            return
        self.set(category=key, label=cat.label, appsLoading=True, appsNote="", selected=-1)
        self.gui.fetch(
            "browse",
            *(key, "--channel", self.gui["channel"], *(["--refresh"] if refresh else [])),
            done=lambda doc, k=key: self._apps(k, doc),
            group="browse",
            exclusive=True,
            label=f"browse {key}",
            hint="the installed apps, two search.nixos.org requests",
        )

    def _apps(self, key: str, doc: Doc) -> None:
        if key != self["category"]:
            return
        n = view.notice(doc)
        if n is not None:
            self.shown = []
            self.rows("apps").reset([])
            self.set(
                appsLoading=False,
                appsNote=f"browse: {n.code}: {n.message}",
                appsNoteStyle="error",
                footer="",
            )
            return
        self.shown = view.app_rows(doc)
        self.rows("apps").reset(models.apps(self.shown))
        notes = list(doc.get("notes") or [])
        self.set(
            appsLoading=False,
            appsNote=" · ".join(notes) if notes or self.shown else "no applications here",
            appsNoteStyle="dim",
            footer=view.browse_footer(doc),
        )

    @Slot(int)
    def select(self, i: int) -> None:
        if 0 <= i < len(self.shown):
            self.set(selected=i)
            self.gui.details.show(view.app_hit(self.shown[i]), owner="browse")

    @Slot()
    def refresh(self) -> None:
        self.load(refresh=True)


# --- status ---------------------------------------------------------------------------------


class StatusVM(VM):
    changed = Signal()
    defaults: ClassVar = {"loading": False, "sections": [], "note": "", "noteStyle": ""}
    loading = state(bool, "loading", changed)
    sections = state("QVariantList", "sections", changed)
    note = state(str, "note", changed)
    noteStyle = state(str, "noteStyle", changed)

    def visit(self) -> None:
        g = self.gui
        if g.status_doc is not None and not g.status_stale:
            self._show(g.status_doc)
        else:
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        self._load(full=False)

    @Slot()
    def refreshFull(self) -> None:
        self._load(full=True)

    def _load(self, full: bool) -> None:
        self.set(loading=True)
        self.gui.load_status(self._show, full=full)

    def _show(self, doc: Doc) -> None:
        g = self.gui
        self.set(loading=False)
        n = view.notice(doc)
        if n is not None:
            self.set(note=f"status: {n.code}: {n.message}", noteStyle="error")
            return
        running = (g.plan_doc or {}).get("running")
        if g.applied:
            running = g.applied[-1].get("running") or running
        sections = [models.section(s) for s in view.status_sections(doc, running)]
        self.set(sections=sections, note="", noteStyle="")


# --- the root -------------------------------------------------------------------------------


def _fetch_image(url: str) -> str:
    from ..appstream import images

    return str(images.fetch(url))


@dataclass
class Activity:
    label: str
    hint: str
    started: float


class Gui(VM):
    """The root object: the session (writes, applies, the last status and plan), the job
    runner, navigation, and the area objects."""

    changed = Signal()
    defaults: ClassVar = {
        "host": "",
        "configLabel": "",
        "channel": "unstable",
        "busy": "",  # writing | updating | planning | checking | applying | trying while one runs
        "running": False,  # any verb is running
        "activity": "",  # the newest running verb, as the busy indicator says it
        "activityHint": "",
        "activitySeconds": 0,
        "page": "search",
        "buildWhere": "local",  # the build-location selector (view.build_choices modes)
        "buildWhereIndex": 0,
        "buildWhereLabel": "local",
    }
    host = state(str, "host", changed)
    buildWhere = state(str, "buildWhere", changed)
    buildWhereIndex = state(int, "buildWhereIndex", changed)
    buildWhereLabel = state(str, "buildWhereLabel", changed)
    configLabel = state(str, "configLabel", changed)
    channel = state(str, "channel", changed)
    busy = state(str, "busy", changed)
    running = state(bool, "running", changed)
    activity = state(str, "activity", changed)
    activityHint = state(str, "activityHint", changed)
    activitySeconds = state(int, "activitySeconds", changed)
    page = state(str, "page", changed)

    def __init__(
        self, backend: Backend, *, channel: str = "unstable", pool: QThreadPool | None = None
    ) -> None:
        super().__init__(None)
        self.backend = backend
        self.pool = pool or QThreadPool.globalInstance()
        self.router = install_router()
        self.writes: list[str] = []
        self.applied: list[Doc] = []
        self.status_doc: Doc | None = None
        self.status_stale = True
        self.plan_doc: Doc | None = None
        self._edits = 0  # writes and applies so far: a status started before one is stale
        self._status_waiting: dict[bool, list[Callable[[Doc], None]]] = {}
        self._tokens: dict[str, int] = {}
        self._cancel: Cancel | None = None  # the busy run's stop token (plan, apply)
        self._jobs: set[JobSignals] = set()
        self._acts: list[Activity] = []
        self._refused = 0.0
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._activity)
        self._areas: dict[str, VM] = {
            "notice": NoticeVM(self),
            "search": SearchVM(self),
            "packages": PackagesVM(self),
            "options": OptionsVM(self),
            "details": DetailsVM(self),
            "writer": WriterVM(self),
            "pending": PendingVM(self),
            "apply": ApplyVM(self),
            "status": StatusVM(self),
            "browse": BrowseVM(self),
            "pins": PinsVM(self),
        }
        from .trying import TryVM  # it subclasses VM, from this module

        self._areas["trying"] = TryVM(self)
        self.image_fetcher: Callable[[str], Any] = _fetch_image  # tests swap it
        chan = channel if channel in view.CHANNELS else view.CHANNELS[0]
        self.set(host=backend.host, configLabel=backend.config_label, channel=chan)
        self.build_choices = view.build_choices(backend.config)

    # --- where plan and apply build (remembered for the session)

    @Slot(result="QVariantList")
    def buildChoices(self) -> list[str]:
        return [label for _mode, label in self.build_choices]

    @Slot(int)
    def setBuildWhere(self, index: int) -> None:
        if 0 <= index < len(self.build_choices):
            mode, label = self.build_choices[index]
            self.set(buildWhere=mode, buildWhereIndex=index, buildWhereLabel=label)

    def build_args(self) -> list[str]:
        """The plan/apply flags of the selected location (none for local)."""
        return view.build_flags(self["buildWhere"])

    notice = area("notice")
    search = area("search")
    packages = area("packages")
    options = area("options")
    details = area("details")
    writer = area("writer")
    pending = area("pending")
    apply = area("apply")
    status = area("status")
    trying = area("trying")
    browse = area("browse")
    pins = area("pins")

    # --- running verbs

    def fetch(
        self,
        verb: str,
        *args: str,
        done: Callable[[Doc], None],
        group: str | None = None,
        exclusive: bool = False,
        stream: bool = False,
        log: LogModel | None = None,
        busy: str = "",
        label: str = "",
        hint: str = "",
        cancel: Cancel | None = None,
    ) -> bool:
        """Run `verb args` on the thread pool; `done(doc)` on the UI thread unless a newer
        call in the same exclusive group superseded it. `stream`: the verb's log goes to
        `log` while it runs. `busy` marks a run that must not overlap another; False when
        one is already running (and the notice says so). `cancel`: the run's stop token
        (plan, apply), which `stop()` asks."""
        if busy:
            if self["busy"]:
                self.notice.say(f"wait: {self['busy']} is still running", "warn")
                return False
            self.set(busy=busy)
            self._cancel = cancel
        key = group or verb
        token = self._tokens.get(key, 0) + 1
        self._tokens[key] = token
        act = Activity(label or verb, hint, time.monotonic())
        self._acts.append(act)
        self._activity()
        job = Job(self.backend, verb, list(args), stream, cancel)
        signals = job.signals
        if stream and log is not None:
            self.router.attach(log.incoming.emit)

        def finished(doc: Doc) -> None:
            self._jobs.discard(signals)
            if stream and log is not None:
                self.router.detach()
                log.flush_partial()
            self._acts.remove(act)
            self._activity()
            if busy:
                self.set(busy="")
                self._cancel = None
            if exclusive and self._tokens.get(key) != token:
                return
            done(doc)

        signals.done.connect(finished)
        self._jobs.add(signals)
        self.pool.start(job)
        return True

    def fetch_image(self, url: str, done: Callable[[str | None, str], None]) -> None:
        """A screenshot or an icon, off the UI thread: `done(path, "")` or `done(None,
        "code: message")` on the UI thread. Not in the activity bar: it is not a verb."""
        job = CallJob(self.image_fetcher, url)
        signals = job.signals

        def finished(result: tuple[Any, str]) -> None:
            self._jobs.discard(signals)
            path, err = result
            done(str(path) if path else None, err)

        signals.done.connect(finished)
        self._jobs.add(signals)
        self.pool.start(job)

    @property
    def idle(self) -> bool:
        return not self._jobs

    @Slot()
    def stop(self) -> None:
        """Stop the running plan or apply (its build); the notice says why when refused."""
        token = self._cancel
        if token is None:
            self.notice.say("nothing is running that could be stopped", "info")
        elif token.request():
            self.notice.say("stopping: waiting for nix to clean up…", "warn")
        else:
            self.notice.say(token.refused, "warn")

    def _activity(self) -> None:
        if not self._acts:
            self._ticker.stop()
            self.set(running=False, activity="", activityHint="", activitySeconds=0)
            return
        act = self._acts[-1]
        self.set(
            running=True,
            activity=act.label,
            activityHint=act.hint,
            activitySeconds=int(time.monotonic() - act.started),
        )
        if not self._ticker.isActive():
            self._ticker.start()

    # --- the session

    def load_status(self, done: Callable[[Doc], None], *, full: bool = False) -> None:
        """`status` (or `--full`) for everyone who asks while one runs."""
        waiting = self._status_waiting.setdefault(full, [])
        waiting.append(done)
        if len(waiting) > 1:
            return
        edits = self._edits

        def got(doc: Doc) -> None:
            if doc.get("ok"):
                self.status_doc = doc
                self.status_stale = self._edits != edits
            for w in self._status_waiting.pop(full, []):
                w(doc)

        self.fetch(
            "status",
            *(["--full"] if full else []),
            done=got,
            group=f"status-{full}",
            label=f"status: evaluating {self.backend.config_label}",
            hint="about a minute cold (after an edit), under a second warm"
            + ("; --full adds one to three minutes" if full else ""),
        )

    def hm_users(self, where: Doc | None = None) -> list[str]:
        """Home Manager users of the host: from status or list, else from a `where`."""
        if self.status_doc and self.status_doc.get("hm_users"):
            return list(self.status_doc["hm_users"])
        listed = self.packages.list_doc
        if listed and listed.get("users"):
            return list(listed["users"])
        users: list[str] = []
        for s in (where or {}).get("scopes") or []:
            if s.get("scope") == "home" and s.get("user") and s["user"] not in users:
                users.append(s["user"])
        return users

    def load_status_for_users(self, done: Callable[[list[str]], None]) -> None:
        if self.status_doc is not None:
            done(self.hm_users())
            return
        self.load_status(lambda doc: done(self.hm_users()))

    def wrote(self, summary: str) -> None:
        self.writes.append(summary)
        self._edits += 1
        self.status_stale = True
        self.plan_doc = None
        self.pending.render_session()
        self.pending.render_plan()
        self.packages.stale()

    def applied_doc(self, doc: Doc) -> None:
        if doc.get("ok"):
            self.applied.append(doc)
            self._edits += 1
            self.status_stale = True
            self.plan_doc = None  # it compared against the system that ran before
            self.pending.render_session()
            self.pending.render_plan()

    def farewell(self) -> str:
        return view.farewell(self.writes, self.applied, prog="nixin gui")

    # --- slots for the window

    @Slot(str)
    def go(self, page: str) -> None:
        if page not in ("search", "packages", "options", "pending", "status", "browse", "pins"):
            return
        self.set(page=page)
        if page == "browse":
            self.browse.visit()
        elif page == "pins":
            self.pins.visit()
        elif page == "packages":
            self.packages.visit()
        elif page == "pending":
            self.pending.visit()
        elif page == "status":
            self.status.visit()

    @Slot(str)
    def setChannel(self, channel: str) -> None:
        if channel in view.CHANNELS and channel != self["channel"]:
            self.set(channel=channel)
            self.search.rerun()
            self.options.rerun()
            if self["page"] == "browse":
                self.browse.visit()

    @Slot(str)
    def copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.notice.say("copied to the clipboard", "ok")

    @Slot(result=bool)
    def mayQuit(self) -> bool:
        """Closing while a write, a plan or an apply runs takes a second try."""
        busy = self["busy"]
        if busy in ("writing", "updating", "planning", "applying"):
            now = time.monotonic()
            if now - self._refused > QUIT_AGAIN:
                self._refused = now
                self.notice.say(
                    f"{busy} is running; quit again to leave anyway (it finishes first)", "warn"
                )
                return False
        return True

    def close(self) -> None:
        uninstall_router(self.router)
