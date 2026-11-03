"""The GUI's state and flows, as Qt objects the QML binds to.

`Gui` is the root; Main.qml gets it as its required `backend` property. It owns one
object per area (search, packages, options, details, writer, pending, apply, status,
notice), each a `VM`: state in a dict that QML reads through properties notifying on the
object's own `changed` signal, and slots QML calls. Lists are `models.Rows`.

Every document comes from `Gui.fetch` (a Job on the thread pool running the backend's
verb handler in-process) and becomes rows through nixin.ui.view, as in the TUI. The flows
are the TUI's: a write is always `--dry-run` → the diff dialog → write → `where` again; an
apply is `apply --dry-run` → confirm (message) → apply; writer, plan and apply runs are
serialised by `busy` (two writer runs on one workspace race on installer/state.json);
`status` is stale after a write or an apply, and a plan after an apply. A nixpkgs update is
`update --dry-run` → confirm → `update` → Pending; it counts as a write (flake.lock).
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from PySide6.QtCore import Property, QObject, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ..ui import view
from ..ui.backend import Backend, shell
from . import models
from .jobs import Job, JobSignals, install_router, uninstall_router
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


class DetailsVM(VM):
    """The selected row: discovery's record (refreshed by `info`), `versions`, and `where`
    on this host; Add / Remove / Set / Unset, and the Home Manager user to do them for."""

    changed = Signal()
    defaults: ClassVar = {
        "present": False,
        "owner": "",  # the page whose row this is: search | packages (each pane shows its own)
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
    }
    lists: ClassVar = {"info": models.FIELD, "versions": models.FIELD, "where": models.FIELD}
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
    info = model("info")
    versions = model("versions")
    where = model("where")

    def __init__(self, gui: Gui) -> None:
        super().__init__(gui)
        self.hit: view.Hit | None = None
        self.record: dict[str, Any] = {}
        self.where_doc: Doc | None = None

    def show(self, hit: view.Hit, *, user: str = "", owner: str = "search") -> None:
        """`user`: the Home Manager user to start with (an installed row's own)."""
        g = self.gui
        self.hit, self.record, self.where_doc = hit, hit.record, None
        if hit.kind == "hm" and not user:
            user = (g.hm_users() or [""])[0]
        self.set(
            present=True,
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
        )
        self._home_choices()
        self.rows("info").reset(models.fields(view.info_fields(self.record)))
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
            self.record = rec
        self.rows("info").reset(models.fields(view.info_fields(self.record)))

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


# --- pending + plan -------------------------------------------------------------------------


class PendingVM(VM):
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
    }
    lists: ClassVar = {
        "pending": models.FIELD,
        "session": models.FIELD,
        "plan": models.FIELD,
        "closure": models.CLOSURE,
        "sync": models.FIELD,
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
    pending = model("pending")
    session = model("session")
    plan = model("plan")
    closure = model("closure")
    sync = model("sync")
    log = model("log")

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
        p = doc.get("pending")
        self.rows("pending").reset(models.fields(view.pending_fields(p)))
        self.set(statusNote="", hasPending=view.has_pending(p))

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
        started = self.gui.fetch(
            "plan",
            done=self._planned,
            stream=True,
            log=log,
            busy="planning",
            label="plan: building the workspace",
            hint="38 s for nothing new, minutes for new packages, hours for a kernel",
        )
        if started:
            log.clear()
            self.gui.plan_doc = None
            self.set(planning=True)
            self.render_plan()

    def _planned(self, doc: Doc) -> None:
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
                planNoteStyle="error",
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


class ApplyVM(VM):
    """The apply dialog: `apply --dry-run` → confirm (the commit message) → `apply` with its
    log streaming → the new generation; for `needs-sudo` the commands to copy and Retry."""

    changed = Signal()
    defaults: ClassVar = {
        "open": False,
        "phase": "",  # checking | confirm | running | done | needs-sudo | failed
        "placeholder": "",
        "message": "",
        "closureHead": "",
        "noticeCode": "",
        "noticeTitle": "",
        "noticeMessage": "",
        "noticeSeverity": "",
        "commands": "",
    }
    lists: ClassVar = {"dry": models.FIELD, "result": models.FIELD}
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
    dry = model("dry")
    result = model("result")
    log = model("log")

    @Slot()
    def start(self) -> None:
        if self["open"]:
            return
        started = self.gui.fetch(
            "apply",
            "--dry-run",
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
        started = self.gui.fetch(
            "apply",
            *(["-m", msg] if msg else []),
            done=self._done,
            stream=True,
            log=log,
            busy="applying",
            label="apply: commit, build, pull, switch",
            hint="about 30 s after a plan; nixos-rebuild evaluates again as root",
        )
        if started:
            log.clear()
            self.rows("result").reset([])
            self.set(phase="running", noticeCode="", commands="", closureHead="")

    def _done(self, doc: Doc) -> None:
        g = self.gui
        g.applied_doc(doc)
        n = view.notice(doc)
        if n is not None:
            self.rows("result").reset(models.fields(view.progress_fields(doc)))
            self.set(
                phase="needs-sudo" if n.commands else "failed",
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
        if self["phase"] in ("needs-sudo", "failed"):
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
        "upSelected": -1,
        "updateOpen": False,
        "updatePhase": "",  # checking | confirm | running
        "updateTitle": "",
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
    upSelected = state(int, "upSelected", changed)
    updateOpen = state(bool, "updateOpen", changed)
    updatePhase = state(str, "updatePhase", changed)
    updateTitle = state(str, "updateTitle", changed)
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
            )
            return
        self.update_rows = view.update_rows(doc)
        self.rows("updates").reset(models.updates(self.update_rows))
        k = view.kernel_line(doc)
        self.set(
            updatesNote="",
            head=view.outdated_head(doc, self.gui.backend.host),
            kernel=k.text,
            kernelStyle=k.style,
            summary=view.outdated_summary(doc),
            notes=" · ".join(view.outdated_notes(doc)),
            canUpdate=view.update_args(doc) is not None,
        )

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in OUTDATED_MODES and mode != self["mode"]:
            self.set(mode=mode)
            self.loadUpdates()

    @Slot(int)
    def selectUpdate(self, i: int) -> None:
        if 0 <= i < len(self.update_rows):
            r = self.update_rows[i]
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
            self.set(updateOpen=True, updatePhase="checking", updateTitle="update nixpkgs")

    def _dry(self, dry: Doc, outdated: Doc | None) -> None:
        if not self["updateOpen"]:
            return
        if not dry.get("ok"):
            self.set(updateOpen=False, updatePhase="")
            self.gui.notice.show(dry, "update")
            return
        lock = outdated if outdated is not None and outdated.get("mode") == "lock" else None
        self.rows("updateFields").reset(models.fields(view.update_confirm_fields(dry, lock)))
        self.set(updatePhase="confirm", updateTitle=f"update {dry.get('input')} in flake.lock")

    @Slot()
    def confirmUpdate(self) -> None:
        if self["updatePhase"] != "confirm":
            return
        args = self._update_args
        if self.gui.fetch(
            "update",
            *args,
            done=self._updated,
            busy="updating",
            label="update: " + shell(["nix", "flake", "update", "nixpkgs", *args]),
            hint="nix fetches the new nixpkgs: about a minute the first time",
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
        self.outdated_docs.clear()
        g.wrote(view.update_summary(doc))
        g.notice.say(f"updated: {view.update_summary(doc)} · Pending: plan, apply", "ok")
        g.go("pending")


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
        "busy": "",  # writing | updating | planning | checking | applying while one runs
        "running": False,  # any verb is running
        "activity": "",  # the newest running verb, as the busy indicator says it
        "activityHint": "",
        "activitySeconds": 0,
        "page": "search",
    }
    host = state(str, "host", changed)
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
        }
        chan = channel if channel in view.CHANNELS else view.CHANNELS[0]
        self.set(host=backend.host, configLabel=backend.config_label, channel=chan)

    notice = area("notice")
    search = area("search")
    packages = area("packages")
    options = area("options")
    details = area("details")
    writer = area("writer")
    pending = area("pending")
    apply = area("apply")
    status = area("status")

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
    ) -> bool:
        """Run `verb args` on the thread pool; `done(doc)` on the UI thread unless a newer
        call in the same exclusive group superseded it. `stream`: the verb's log goes to
        `log` while it runs. `busy` marks a run that must not overlap another; False when
        one is already running (and the notice says so)."""
        if busy:
            if self["busy"]:
                self.notice.say(f"wait: {self['busy']} is still running", "warn")
                return False
            self.set(busy=busy)
        key = group or verb
        token = self._tokens.get(key, 0) + 1
        self._tokens[key] = token
        act = Activity(label or verb, hint, time.monotonic())
        self._acts.append(act)
        self._activity()
        job = Job(self.backend, verb, list(args), stream)
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
            if exclusive and self._tokens.get(key) != token:
                return
            done(doc)

        signals.done.connect(finished)
        self._jobs.add(signals)
        self.pool.start(job)
        return True

    @property
    def idle(self) -> bool:
        return not self._jobs

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
        if page not in ("search", "packages", "options", "pending", "status"):
            return
        self.set(page=page)
        if page == "packages":
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
