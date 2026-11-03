"""Documents → display rows: the view-model the TUI and the GUI share.

Pure functions over the --json documents (docs/CONTRACT.md). No toolkit here, no I/O:
every function takes a document (or a piece of one) and returns plain data: strings,
`Field`s, `Span`s, `Hit`s. Wording follows the CLI's human output, reusing its helpers
where they exist (discovery's source footer, truth's `nix_text`, apply's `human_size`).

Each piece of text carries a semantic `style` that the UI maps to its palette
(nixin.tui.theme.STYLES for the TUI, Theme.qml's `style()` for the GUI):

    ""        normal text
    "dim"     secondary text: paths, counts, hints
    "accent"  names and things to act on (cyan)
    "option"  option names and Nix values (purple)
    "add"     appears: diff `+`, closure added, a package that is installed (cyan)
    "del"     goes away: diff `-`, closure removed (pink)
    "warn"    look at this (purple)
    "error"   a failure (pink)
    "head"    a heading
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..apply.closure import human_size
from ..discovery.format import _HEADERS, _footer
from ..outdated import render as outdated_render
from ..truth.render import nix_text

ELLIPSIS = "…"

Doc = dict[str, Any]


@dataclass(frozen=True)
class Span:
    text: str
    style: str = ""


@dataclass(frozen=True)
class Field:
    label: str
    value: str
    style: str = ""


@dataclass
class Section:
    title: str
    fields: list[Field] = field(default_factory=list)
    note: str = ""  # shown under the fields (alone when there are none)
    style: str = ""  # the note's style


def clip(text: str | None, n: int) -> str:
    """One line of at most n cells, whitespace collapsed, an ellipsis when cut."""
    text = " ".join(str(text or "").split())
    if n <= 0:
        return ""
    return text if len(text) <= n else text[: n - 1] + ELLIPSIS


def short(sha: str | None, n: int = 7) -> str:
    return sha[:n] if sha else "?"


def store_name(path: str | None) -> str:
    """/nix/store/<hash>-nixos-system-mbp13-26.11… → <hash8>…-nixos-system-mbp13-26.11…"""
    if not path:
        return "—"
    base = path.rsplit("/", 1)[-1]
    h, _, rest = base.partition("-")
    return f"{h[:8]}…-{rest}" if rest else base


# --- errors -------------------------------------------------------------------------------

INFO_CODES = frozenset({"nothing-to-apply"})  # a refusal that only says "all done"
ACTION_CODES = frozenset({"needs-sudo"})  # the person has something to run


@dataclass
class Notice:
    """An error document as something to show: code + message, and whatever the error
    object carries to act on (commands to copy, candidates to pick, hints, files)."""

    code: str
    message: str
    severity: str  # "error" | "info" | "action"
    commands: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def title(self) -> str:
        return {"info": "nothing to do", "action": "root is needed"}.get(self.severity, "error")

    @property
    def brief(self) -> str:
        """The message, without the commands it lists when they are shown on their own."""
        if not self.commands:
            return self.message
        return self.message.splitlines()[0].removesuffix(" Run:") if self.message else ""


def notice(doc: Doc) -> Notice | None:
    """None for `ok: true`, else the error as a Notice."""
    if doc.get("ok"):
        return None
    err = doc.get("error") or {}
    code = str(err.get("code") or "internal")
    severity = "info" if code in INFO_CODES else "action" if code in ACTION_CODES else "error"
    items = [str(h) for h in err.get("hints") or []]
    cands = [c for c in err.get("candidates") or [] if isinstance(c, dict)]
    items += [candidate_label(c) for c in cands]
    items += [str(f) for f in err.get("files") or []]
    if err.get("file") and err.get("line") and code in ("import-site-missing", "invalid-nix"):
        items.append(f"{err['file']}:{err['line']}")
    return Notice(
        code=code,
        message=str(err.get("message") or ""),
        severity=severity,
        commands=[str(c) for c in err.get("commands") or []],
        items=items,
        candidates=cands,
    )


def candidate_label(c: dict[str, Any]) -> str:
    where = f"{c.get('file')}:{c.get('line')}" if c.get("line") else str(c.get("file"))
    text = clip(c.get("text"), 48)
    return f"{where}  {text}".rstrip()


def candidate_where(c: dict[str, Any]) -> str:
    """The `--where FILE:LINE` value that picks this candidate."""
    return f"{c.get('file')}:{c.get('line')}"


# --- search ----------------------------------------------------------------------------------

KIND_BADGE = {"pkg": "pkg", "opt": "opt", "hm": "hm", "program": "bin", "fn": "fn"}
SEARCH_KINDS = ("pkg", "opt", "hm", "program", "all")  # the filter, in order
CHANNELS = ("unstable", "stable")


@dataclass(frozen=True)
class Hit:
    """One search result row."""

    kind: str  # pkg | opt | hm | program | fn
    name: str  # what the row is called (the binary, for program)
    target: str  # what info/where/add/set take: the attribute path or the option name
    detail: str  # version (pkg), type (opt, hm), "package version" (program)
    description: str
    record: dict[str, Any] = field(compare=False, hash=False, repr=False)
    where_name: str = ""  # what `where` takes when it is not `target` (an installed row's pname)

    @property
    def where_target(self) -> str:
        return self.where_name or self.target

    @property
    def is_option(self) -> bool:
        return self.kind in ("opt", "hm")

    @property
    def is_package(self) -> bool:
        return self.kind in ("pkg", "program")


def hit(rec: dict[str, Any]) -> Hit:
    kind = str(rec.get("kind") or "")
    name = str(rec.get("name") or "")
    desc = str(rec.get("description") or "")
    if kind == "pkg":
        return Hit(kind, name, name, str(rec.get("version") or ""), desc, rec)
    if kind in ("opt", "hm"):
        return Hit(kind, name, name, str(rec.get("type") or ""), desc, rec)
    if kind == "program":
        pkg = str(rec.get("package") or name)
        detail = f"{pkg} {rec.get('version') or ''}".strip()
        return Hit(kind, name, pkg, detail, desc, rec)
    if kind == "fn":
        return Hit(kind, name, name, str(rec.get("signature") or ""), desc, rec)
    return Hit(kind, name, name, "", desc, rec)


def hits(doc: Doc) -> list[Hit]:
    return [hit(r) for r in doc.get("results") or [] if isinstance(r, dict)]


def source_lines(doc: Doc) -> list[str]:
    """The CLI's footer: `8 result(s) · search.nixos.org unstable@1c89083e (cached)`, then
    one `note: …` line per note."""
    return [ln.removeprefix("# ") for ln in _footer(doc).splitlines()]


@dataclass(frozen=True)
class Column:
    key: str  # kind | name | detail | description
    label: str
    width: int


NARROW = 60  # below this many cells the results table drops the description column


def hit_columns(kind: str, width: int) -> list[Column]:
    """The results table's columns for a table `width` cells wide.

    Every column costs its width plus 2 cells of padding; one cell is kept for the
    scrollbar. Narrow (phone, ~50 cells): name and version/type only, the description
    goes to the line under the table. Wide: the description takes what is left."""
    name_h, detail_h, desc_h = _HEADERS.get(kind, ["NAME", "VERSION / TYPE", "DESCRIPTION"])
    cols: list[Column] = []
    avail = width - 1
    if kind == "all":
        cols.append(Column("kind", "", 3))
        avail -= 3 + 2
    if width < NARROW:
        detail_w = max(7, min(14, avail // 3))
        name_w = max(8, avail - detail_w - 4)
        return [*cols, Column("name", name_h, name_w), Column("detail", detail_h, detail_w)]
    name_w = max(14, min(36, avail * 2 // 7))
    detail_w = max(8, min(20, avail // 6))
    desc_w = max(10, avail - name_w - detail_w - 6)
    return [
        *cols,
        Column("name", name_h, name_w),
        Column("detail", detail_h, detail_w),
        Column("description", desc_h, desc_w),
    ]


def hit_cells(h: Hit, columns: list[Column]) -> list[Span]:
    style = {"kind": "dim", "name": "option" if h.is_option else "accent", "detail": "dim"}
    values = {
        "kind": KIND_BADGE.get(h.kind, h.kind),
        "name": h.name,
        "detail": h.detail,
        "description": h.description,
    }
    return [Span(clip(values[c.key], c.width), style.get(c.key, "")) for c in columns]


# --- details: discovery -------------------------------------------------------------------


def _join(xs: Any, n: int = 6) -> str:
    xs = [str(x) for x in xs or []]
    more = f" (+{len(xs) - n})" if len(xs) > n else ""
    return ", ".join(xs[:n]) + more


def info_fields(rec: dict[str, Any]) -> list[Field]:
    """One discovery row (from search or info) as label/value lines."""
    kind = rec.get("kind")
    out: list[Field] = []

    def add(label: str, value: Any, style: str = "") -> None:
        if value not in (None, "", [], {}):
            out.append(Field(label, str(value), style))

    if kind in ("pkg", "program"):
        if kind == "program":
            add("package", rec.get("package"), "accent")
            add("binary", rec.get("name"))
        add("version", rec.get("version"), "accent")
        add("about", rec.get("description"))
        add("homepage", rec.get("homepage"), "dim")
        add("license", _join(rec.get("license")))
        if rec.get("unfree"):
            add("unfree", "yes", "warn")
        if rec.get("broken"):
            add("broken", "yes", "error")
        progs = rec.get("programs") or []
        add("programs", _join(progs, 8) if progs else rec.get("main_program"))
        add("outputs", _join(rec.get("outputs")), "dim")
        add("position", rec.get("position"), "dim")
        if rec.get("alternatives"):
            add("also", _join(rec.get("alternatives")), "dim")
    elif kind in ("opt", "hm"):
        add("type", rec.get("type"), "option")
        add("default", rec.get("default"), "option")
        add("example", rec.get("example"), "option")
        add("about", (rec.get("description") or "").strip())
        add("declared", _join(rec.get("declarations"), 3), "dim")
        if rec.get("read_only"):
            add("read-only", "yes", "warn")
    else:
        add("about", rec.get("description"))
    src = f"{rec.get('source') or ''} {rec.get('channel') or ''}".strip()
    if rec.get("rev"):
        src += f"@{short(rec['rev'], 8)}"
    add("source", src, "dim")
    return out


def version_rows(doc: Doc, limit: int = 8) -> list[Field]:
    """`versions`: one Field per release, newest first: version, date, nixpkgs rev."""
    rows = []
    for r in (doc.get("results") or [])[:limit]:
        rows.append(
            Field(str(r.get("version") or "?"), f"{r.get('date') or ''}  {short(r.get('rev'), 8)}")
        )
    return rows


# --- details: truth (`where`) --------------------------------------------------------------


def _tag(scope: str | None, user: str | None) -> str:
    return f"[{scope}{' ' + user if user else ''}]"


def place(loc: dict[str, Any]) -> str:
    """config files as path:line, other sources as `input path:line` (as truth prints)."""
    if loc.get("source") == "inline":
        return "inline in nixosSystem"
    line = loc.get("line")
    if not line and loc.get("lines"):
        line = loc["lines"][0].get("line")
    where = f"{loc.get('file')}:{line}" if line else str(loc.get("file"))
    return where if loc.get("source") == "config" else f"{loc.get('source')} {where}"


def installed(doc: Doc) -> bool:
    """`where PACKAGE` says the host (or one of its HM users) has it."""
    return bool(doc.get("ok") and (doc.get("installed") or doc.get("programs")))


def where_section(doc: Doc, host: str) -> Section:
    """`where` for this host: installed? where declared? (package) / value and files (option)."""
    if not doc.get("ok"):
        n = notice(doc)
        assert n is not None
        if n.code == "not-found":
            return Section(f"on {host}", [], n.message, "dim")
        return Section(f"on {host}", [], f"{n.code}: {n.message}", "error")
    if doc.get("kind") == "package":
        return _where_package(doc, host)
    return _where_option(doc, host)


def _where_package(doc: Doc, host: str) -> Section:
    fields: list[Field] = []
    inst = doc.get("installed") or []
    seen: set[tuple] = set()
    for p in inst:
        key = (p.get("name"), p.get("scope"), p.get("user"))
        if key in seen:  # the same derivation from two lists shows once
            continue
        seen.add(key)
        fields.append(
            Field("installed", f"{p.get('name')} {_tag(p.get('scope'), p.get('user'))}", "add")
        )
    for v in doc.get("programs") or []:
        for df in v.get("definitions") or []:
            text = f"{v.get('option')} = {nix_text(df.get('value'))} in {place(df)}"
            fields.append(
                Field("enabled", f"{text} {_tag(v.get('scope'), v.get('user'))}", "option")
            )
    for loc in doc.get("declared") or []:
        text = f"{loc.get('option')} in {place(loc)} {_tag(loc.get('scope'), loc.get('user'))}"
        fields.append(Field("listed", text, "" if loc.get("source") == "config" else "dim"))
    shown = {
        (loc.get("file"), m.get("line"))
        for loc in doc.get("declared") or []
        for m in loc.get("lines") or []
    }
    others = [m for m in doc.get("mentions") or [] if (m.get("file"), m.get("line")) not in shown]
    for m in [m for m in others if m.get("score", 0) >= 2][:4]:
        fields.append(
            Field("mention", f"{m.get('file')}:{m.get('line')}  {clip(m.get('text'), 50)}", "dim")
        )
    note = "" if inst else f"not installed on {host}"
    return Section(f"on {host}", fields, note, "dim")


def option_value(o: dict[str, Any]) -> str:
    if o.get("value_error"):
        return "<cannot be evaluated>"
    if o.get("value_found") is False:
        return "<not set>"
    return nix_text(o.get("value"))


def _where_option(doc: Doc, host: str) -> Section:
    fields: list[Field] = []
    for s in doc.get("scopes") or []:
        tag = _tag(s.get("scope"), s.get("user"))
        if s.get("mode") == "prefix":
            opts = s.get("options") or []
            fields.append(
                Field("under", f"{s.get('under_prefix')} options, {len(opts)} set {tag}", "dim")
            )
            for o in opts[:12]:
                rel = str(o.get("name", "")).removeprefix(str(doc.get("name")) + ".")
                fields.append(Field(rel, option_value(o), "option"))
            continue
        for o in s.get("options") or []:
            fields.append(Field("value", f"{option_value(o)} {tag}", "option"))
            if o.get("option") != o.get("name"):
                fields.append(Field("inside", f"{o.get('option')} ({o.get('type')})", "dim"))
            for df in o.get("definitions") or []:
                own = (
                    f" = {clip(nix_text(df.get('value')), 40)}" if len(o["definitions"]) > 1 else ""
                )
                fields.append(
                    Field("set in", place(df) + own, "" if df.get("source") == "config" else "dim")
                )
            if not o.get("definitions"):
                fields.append(Field("set in", "nothing (the default)", "dim"))
            for dec in (o.get("declarations") or [])[:2]:
                fields.append(Field("declared", place(dec), "dim"))
    return Section(f"on {host}", fields)


def current_value(doc: Doc | None) -> str | None:
    """The option's value on this host as Nix text (to prefill `set`), if where found one."""
    if not doc or not doc.get("ok") or doc.get("kind") != "option":
        return None
    for s in doc.get("scopes") or []:
        for o in s.get("options") or []:
            if o.get("value_found") is not False and not o.get("value_error") and "value" in o:
                text = nix_text(o.get("value"))
                return None if text.startswith("<") else text
    return None


# --- writer -------------------------------------------------------------------------------


def diff_spans(doc: Doc) -> list[Span]:
    """Every `changes[]` entry: a header line, then its unified diff, one Span per line."""
    out: list[Span] = []
    for ch in doc.get("changes") or []:
        out.append(Span(f"{ch.get('file')}  ({ch.get('kind')})", "head"))
        for line in str(ch.get("diff") or "").splitlines():
            if line.startswith(("+++", "---")):
                continue
            if line.startswith("@@"):
                out.append(Span(line, "dim"))
            elif line.startswith("+"):
                out.append(Span(line, "add"))
            elif line.startswith("-"):
                out.append(Span(line, "del"))
            else:
                out.append(Span(line))
    return out


ACTION_WORDS = {
    "added": "add",
    "removed": "del",
    "set": "option",
    "unset": "del",
    "already-added": "dim",
    "already-declared": "dim",
    "unchanged": "dim",
}


def writer_fields(doc: Doc) -> list[Field]:
    """What a writer verb did (or, with --dry-run, would do): actions, imports, the eval
    proof, warnings."""
    out: list[Field] = []
    for a in doc.get("actions") or []:
        where = f"{a.get('file')}:{a.get('line')}" if a.get("line") else str(a.get("file") or "")
        text = f"{a.get('name')} → {where}" if where else str(a.get("name"))
        if a.get("action") == "set" and a.get("old") is not None:
            text += f" (was {clip(a['old'], 30)})"
        out.append(Field(str(a.get("action")), text, ACTION_WORDS.get(str(a.get("action")), "")))
    for i in doc.get("imports_added") or []:
        out.append(Field("import", f"{i.get('import')} in {i.get('file')}", "dim"))
    for i in doc.get("imports_removed") or []:
        out.append(Field("unimport", f"{i.get('file')}:{i.get('line')}", "dim"))
    ev = (doc.get("checks") or {}).get("eval")
    if ev is not None:
        if ev.get("ok"):
            out.append(Field("eval", "ok: the host evaluates with the change", "ok"))
        else:
            msg = ev.get("message") or ", ".join(ev.get("missing") or []) or "failed"
            out.append(Field("eval", f"FAILED: {clip(msg, 200)}", "error"))
    for p in (doc.get("checks") or {}).get("parse") or []:
        if not p.get("ok"):
            out.append(Field("parse", f"{p.get('file')}: {p.get('error')}", "error"))
    for w in doc.get("warnings") or []:
        out.append(Field("warning", str(w), "warn"))
    return out


def writer_changed(doc: Doc) -> bool:
    return bool(doc.get("changes"))


def writer_eval_failed(doc: Doc) -> bool:
    """`ok: true` but the eval proof failed: the text change stands (CONTRACT.md, writer)."""
    ev = (doc.get("checks") or {}).get("eval")
    return bool(doc.get("ok") and ev is not None and not ev.get("ok"))


def writer_summary(doc: Doc) -> str:
    """One line for the session log: `+cowsay (installer/mbp13.nix)`."""
    sign = {"add": "+", "rm": "-", "set": "", "unset": "-"}.get(str(doc.get("verb")), "")
    names = doc.get("packages") or [doc.get("option")]
    if doc.get("verb") == "set":
        names = [f"{doc.get('option')} = {clip(doc.get('value'), 30)}"]
    files = sorted({c.get("file") for c in doc.get("changes") or []})
    who = f" [home {doc['user']}]" if doc.get("user") else ""
    return f"{sign}{', '.join(str(n) for n in names)}{who} ({', '.join(files) or 'no change'})"


# --- plan / closure -------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureRow:
    sign: str  # + - ~
    name: str
    version: str
    size: str
    style: str


def closure_head(diff: dict[str, Any] | None) -> str:
    if diff is None:
        return "no closure diff (no running system to compare, or another host)"
    a, r, c = diff.get("added") or [], diff.get("removed") or [], diff.get("changed") or []
    if not (a or r or c):
        return "closure: no change"
    return (
        f"{len(a)} added, {len(r)} removed, {len(c)} changed, {human_size(diff.get('size_delta'))}"
    )


def closure_rows(diff: dict[str, Any] | None) -> list[ClosureRow]:
    if not diff:
        return []
    rows = [
        ClosureRow("+", x["name"], x.get("version") or "", human_size(x.get("size")), "add")
        for x in diff.get("added") or []
    ]
    rows += [
        ClosureRow("-", x["name"], x.get("version") or "", human_size(x.get("size")), "del")
        for x in diff.get("removed") or []
    ]
    for x in diff.get("changed") or []:
        ver = (
            f"{x.get('from')} → {x.get('to')}"
            if x.get("from") is not None or x.get("to") is not None
            else ""
        )
        rows.append(ClosureRow("~", x["name"], ver, human_size(x.get("size_delta")), "option"))
    return rows


def _gen(run: dict[str, Any] | None) -> str:
    g = (run or {}).get("generation")
    return f"generation {g}" if g is not None else "not a profile generation"


def plan_fields(doc: Doc) -> list[Field]:
    out = [
        Field(
            "result",
            "up to date: nothing to apply" if doc.get("up_to_date") else "a new system",
            "ok" if doc.get("up_to_date") else "accent",
        ),
        Field("out", store_name(doc.get("out")), "dim"),
    ]
    if doc.get("cached"):
        out.append(Field("built", f"cached ({doc.get('cache_key')})", "dim"))
    else:
        t = doc.get("timings") or {}
        out.append(
            Field("built", f"eval {t.get('eval_s', '?')} s, build {t.get('build_s', '?')} s", "dim")
        )
    w = doc.get("workspace") or {}
    dirty = f", {len(w.get('dirty') or [])} changed" if w.get("dirty") else ""
    out.append(Field("workspace", f"{w.get('branch')} @ {short(w.get('head'))}{dirty}", ""))
    a = doc.get("authoritative") or {}
    out.append(
        Field("authority", f"{a.get('path')} @ {short(a.get('head'))} ({a.get('relation')})", "dim")
    )
    pc = doc.get("pending_commits")
    if pc:
        out.append(
            Field(
                "commits",
                f"{len(pc)}: "
                + "; ".join(f"{short(c.get('commit'))} {c.get('subject')}" for c in pc[:3]),
                "",
            )
        )
    out.append(Field("running", _gen(doc.get("running")), "dim"))
    for wn in doc.get("warnings") or []:
        out.append(Field("warning", str(wn), "warn"))
    return out


# --- status.pending -------------------------------------------------------------------------


def pending_fields(p: dict[str, Any] | None) -> list[Field]:
    """`status.pending`: what an apply would bring in."""
    if not p:
        return [Field("pending", "not loaded", "dim")]
    if p.get("error"):
        e = p["error"]
        return [Field("pending", f"{e.get('code')}: {e.get('message')}", "error")]
    out = [Field("workspace", str(p.get("workspace") or "(the authoritative tree itself)"), "")]
    a = p.get("authoritative") or {}
    out.append(Field("authority", f"{a.get('path')} @ {short(a.get('head'))}", "dim"))
    commits = p.get("commits")
    if commits is None:
        out.append(
            Field("commits", "unknown (the authoritative HEAD is not in the workspace)", "warn")
        )
    elif commits:
        for c in commits[:6]:
            out.append(Field("commit", f"{short(c.get('commit'))} {c.get('subject')}", "accent"))
    else:
        out.append(Field("commits", "none to apply", "dim"))
    for f in p.get("dirty") or []:
        out.append(Field("changed", str(f), "accent"))
    for f in p.get("untracked") or []:
        out.append(Field("untracked", f"{f} (not part of a build)", "warn"))
    rm = p.get("running_matches")
    if rm is True:
        out.append(Field("running", "the authoritative HEAD's build", "ok"))
    elif rm is False:
        out.append(Field("running", "not the authoritative HEAD's build", "warn"))
    else:
        out.append(Field("running", "unknown (not built yet, or another host)", "dim"))
    return out


def has_pending(p: dict[str, Any] | None) -> bool:
    if not p or p.get("error"):
        return False
    from ..apply.system import HISTORY

    dirty = [f for f in p.get("dirty") or [] if f != HISTORY]
    return bool(dirty or p.get("commits") or p.get("running_matches") is False)


# --- apply ------------------------------------------------------------------------------------


def dry_run_fields(doc: Doc) -> list[Field]:
    """`apply --dry-run`, most important first (a phone shows a dozen lines): what would be
    committed, the closure diff, what root will run, whether sudo is ready, the build, then
    the unprivileged commands."""
    out: list[Field] = []
    commit = doc.get("commit")
    if commit:
        files = commit.get("files") or []
        out.append(Field("commit", f"{len(files)} files: " + ", ".join(files), "accent"))
    pc = doc.get("pending_commits")
    if pc:
        out.append(Field("commits", f"{len(pc)} to bring in", ""))
    if doc.get("diff") is not None:
        out.append(Field("closure", closure_head(doc.get("diff")), ""))
    cmds = doc.get("commands") or []
    for c in cmds:
        if c.get("privileged"):
            out.append(
                Field(f"{c.get('step')} (root)", str(c.get("by_hand") or c.get("command")), "warn")
            )
    ready = []
    for step, pv in (doc.get("privilege") or {}).items():
        ok = bool(pv.get("ready"))
        why = str(pv.get("why") or ("ready" if ok else "needs a password"))
        backend = str(pv.get("backend"))
        ready.append((step, ok, why if why.startswith(backend) else f"{backend}: {why}"))
    if ready and len({(ok, text) for _, ok, text in ready}) == 1:
        _, ok, text = ready[0]
        out.append(Field("root", text, "ok" if ok else "warn"))
    else:
        out += [Field(f"{step} via", text, "ok" if ok else "warn") for step, ok, text in ready]
    if doc.get("out"):
        out.append(Field("build", f"cached: {store_name(doc['out'])}", "dim"))
    else:
        out.append(
            Field("build", "not built yet: apply builds first (minutes; a kernel: longer)", "warn")
        )
    for c in cmds:
        if not c.get("privileged"):
            out.append(Field(str(c.get("step")), str(c.get("command")), "dim"))
    for w in doc.get("warnings") or []:
        out.append(Field("warning", str(w), "warn"))
    return out


def generated_message(doc: Doc) -> str:
    """The commit message apply would generate (dry run), first line."""
    commit = doc.get("commit") or {}
    return str(commit.get("message") or "").splitlines()[0] if commit.get("message") else ""


def apply_fields(doc: Doc) -> list[Field]:
    out = []
    if doc.get("committed"):
        out.append(
            Field(
                "committed",
                f"{short(doc['committed'])} {doc.get('commit_message') or ''}",
                "accent",
            )
        )
    else:
        out.append(Field("committed", "nothing new (already committed)", "dim"))
    mode = "switched to" if doc.get("switched") else "boot default:"
    out.append(Field("system", f"{mode} generation {doc.get('generation')}", "ok"))
    out.append(Field("out", store_name(doc.get("out")), "dim"))
    out.append(Field("authority", f"at {short(doc.get('authoritative_head'))}", "dim"))
    t = doc.get("timings") or {}
    if t:
        out.append(
            Field(
                "took",
                f"{t.get('total')} s (build {t.get('build')} s, switch {t.get('switch')} s)",
                "dim",
            )
        )
    for c in doc.get("privileged_commands") or []:
        out.append(Field("ran", str(c), "dim"))
    for w in doc.get("warnings") or []:
        out.append(Field("warning", str(w), "warn"))
    return out


def progress_fields(doc: Doc) -> list[Field]:
    """How far a failed apply got (the error object's partial-progress fields)."""
    err = doc.get("error") or {}
    out = []
    if err.get("committed"):
        out.append(Field("committed", short(err["committed"]), "accent"))
    if err.get("out"):
        out.append(Field("built", store_name(err["out"]), "dim"))
    if err.get("authoritative_head"):
        out.append(Field("authority", f"at {short(err['authoritative_head'])}", "dim"))
    for c in err.get("privileged_commands") or []:
        out.append(Field("ran", str(c), "dim"))
    return out


# --- sync ---------------------------------------------------------------------------------------


def sync_fields(doc: Doc) -> list[Field]:
    w = doc.get("workspace") or {}
    r = doc.get("remote") or {}
    a = doc.get("authoritative") or {}
    ahead, behind = w.get("ahead"), w.get("behind")
    rel = "no such branch there" if ahead is None else f"{ahead} ahead, {behind} behind"
    out = [
        Field("done", ", ".join(doc.get("done") or []), "accent"),
        Field("remote", f"{r.get('name')} {r.get('url') or ''}".strip(), "dim"),
        Field("workspace", f"{w.get('branch')} @ {short(w.get('head'))}: {rel}", ""),
        Field(
            "authority",
            f"{a.get('path')} ({a.get('relation')}; {a.get('behind_workspace')} to apply)",
            "dim",
        ),
    ]
    for wn in doc.get("warnings") or []:
        out.append(Field("warning", str(wn), "warn"))
    return out


# --- status -------------------------------------------------------------------------------------


def status_sections(doc: Doc, running: dict[str, Any] | None = None) -> list[Section]:
    """`status` as sections. `running` (a plan/apply `running` block, if the session has
    one) adds the generation, which status itself does not report."""
    c = doc.get("config") or {}
    lock = doc.get("lock") or {}
    run = doc.get("running") or {}
    config = Section(
        "config",
        [
            Field("path", str(c.get("path")), ""),
            Field(
                "HEAD",
                f"{short(c.get('head'))}{' (dirty)' if c.get('dirty') else ''}",
                "warn" if c.get("dirty") else "accent",
            ),
            Field("hosts", ", ".join(c.get("hosts") or []), "dim"),
        ],
    )
    lk = Section("lock", [], "no flake.lock" if not lock else "")
    if lock:
        lk.fields = [
            Field(
                str(lock.get("input") or "nixpkgs"),
                f"{short(lock.get('rev'), 12)} ({lock.get('ref') or lock.get('type')})",
                "accent",
            ),
            Field("date", str(lock.get("date")), ""),
        ]
    gen = (running or {}).get("generation")
    rn = Section(
        "running",
        [
            Field("version", str(run.get("version")), ""),
            Field(
                "nixpkgs",
                f"{run.get('nixpkgs_rev')} ({'matches the lock' if run.get('matches_lock') else 'not the lock'})",
                "ok" if run.get("matches_lock") else "warn",
            ),
            Field(
                "generation",
                str(gen) if gen is not None else "— (plan shows it)",
                "accent" if gen is not None else "dim",
            ),
            Field("system", store_name(run.get("system")), "dim"),
        ],
    )
    pk = doc.get("packages") or {}
    pg = doc.get("programs") or {}
    counts = Section("counts", [Field("packages", f"{pk.get('system')} system", "")])
    for user, n in (pk.get("home") or {}).items():
        counts.fields.append(Field(f"home {user}", f"{n} packages", ""))
    counts.fields.append(
        Field(
            "programs",
            f"{len(pg.get('system') or [])} enabled: {_join(pg.get('system'), 8)}",
            "dim",
        )
    )
    for scope, o in (doc.get("options") or {}).items():
        if isinstance(o, dict) and "total" in o:
            counts.fields.append(
                Field(
                    f"options {scope}",
                    f"{o.get('defined')} defined, {o.get('in_config')} in config, of {o.get('total')}",
                    "dim",
                )
            )
    if doc.get("hm_users"):
        counts.fields.append(Field("HM users", ", ".join(doc["hm_users"]), "option"))
    cache = doc.get("cache") or {}
    t = doc.get("timings") or {}
    caches = Section(
        "caches",
        [
            Field("dir", str(cache.get("dir")), "dim"),
            Field(
                "hits",
                ", ".join(cache.get("hits") or []) or "none",
                "ok" if cache.get("hits") else "dim",
            ),
            Field(
                "misses",
                ", ".join(cache.get("misses") or []) or "none",
                "warn" if cache.get("misses") else "dim",
            ),
            Field("took", f"{t.get('total')} s", "dim"),
        ],
    )
    pend = Section("pending", pending_fields(doc.get("pending")))
    return [config, lk, rn, pend, counts, caches]


# --- the session ----------------------------------------------------------------------------


def farewell(writes: list[str], applied: list[Doc], prog: str = "nixin tui") -> str:
    """The line `nixin tui` (or `nixin gui`, as `prog`) prints after the app closes."""
    if not writes and not applied:
        return f"{prog}: nothing changed"
    parts = []
    if writes:
        parts.append(f"wrote {len(writes)}: " + "; ".join(writes))
    if applied:
        parts.append(f"applied: generation {applied[-1].get('generation')}")
    elif writes:
        parts.append("not applied yet (nixin plan, nixin apply)")
    return f"{prog}: " + " · ".join(parts)


def where_cue(doc: Doc) -> Span | None:
    """A few words for the details title once `where` answered: installed or not (a package),
    the value (an option)."""
    if not doc.get("ok"):
        return None
    if doc.get("kind") == "package":
        inst = doc.get("installed") or []
        if not inst:
            return Span("○ not installed", "dim")
        tags = sorted({_tag(p.get("scope"), p.get("user")) for p in inst})
        return Span(f"● installed {' '.join(tags)}", "add")
    for s in doc.get("scopes") or []:
        for o in s.get("options") or []:
            if s.get("mode") != "prefix":
                return Span(f"= {clip(option_value(o), 30)}", "option")
    return None


# --- packages: `list` -----------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """One choice of the scope toggle: everything, the system, or one Home Manager user."""

    key: str  # "all" | "system" | "home:<user>"
    label: str

    @property
    def user(self) -> str | None:
        return self.key.removeprefix("home:") if self.key.startswith("home:") else None


def package_scopes(users: list[str]) -> list[Scope]:
    """all, system, then each HM user (the `list` document's `users[]`)."""
    return [
        Scope("all", "all"),
        Scope("system", "system"),
        *(Scope(f"home:{u}", f"home {u}") for u in users),
    ]


_ATTR = re.compile(r"[A-Za-z_][\w'-]*(?:\.[A-Za-z_][\w'-]*)*")


@dataclass(frozen=True)
class PackageRow:
    """One `list` row: what is installed, for whom, and the line that lists it."""

    scope: str  # system | home
    user: str | None
    name: str  # pname, else the derivation name (writeShellApplication scripts have no pname)
    drv: str  # the derivation name
    version: str
    where: str  # the first `declared` file:line (+N more), "" when a module adds it
    target: str  # the attribute info/versions/add/rm take: from `declared`, else the name

    @property
    def tag(self) -> str:
        return self.user or "system"

    @property
    def key(self) -> str:
        return "system" if self.user is None else f"home:{self.user}"


def attribute(declared: list[dict[str, Any]]) -> str | None:
    """The attribute path a `declared` element names (`pkgs.ripgrep` → `ripgrep`,
    `kdePackages.dolphin` as is); None for a call (`callPackage ./x {}`)."""
    for d in declared:
        text = str(d.get("text") or "").removeprefix("pkgs.")
        if _ATTR.fullmatch(text):
            return text
    return None


def package_rows(doc: Doc) -> list[PackageRow]:
    rows = []
    for p in doc.get("packages") or []:
        decl = [d for d in p.get("declared") or [] if isinstance(d, dict)]
        name = str(p.get("pname") or p.get("name") or "?")
        where = ""
        if decl:
            where = f"{decl[0].get('file')}:{decl[0].get('line')}"
            where += f" +{len(decl) - 1}" if len(decl) > 1 else ""
        rows.append(
            PackageRow(
                scope=str(p.get("scope") or "system"),
                user=p.get("user"),
                name=name,
                drv=str(p.get("name") or name),
                version=str(p.get("version") or ""),
                where=where,
                target=attribute(decl) or name,
            )
        )
    return rows


def filter_packages(rows: list[PackageRow], text: str = "", scope: str = "all") -> list[PackageRow]:
    """`list --filter TEXT` (name or pname contains it, any case) and `--scope`/`--home`,
    on rows already loaded: the toggles need no second eval."""
    needle = text.strip().lower()
    return [
        r
        for r in rows
        if (scope == "all" or r.key == scope)
        and (not needle or needle in r.name.lower() or needle in r.drv.lower())
    ]


def package_columns(width: int, tagged: bool) -> list[Column]:
    """name, version, the scope (`tagged`: several scopes shown), and from 80 cells the
    file:line that lists it. Same padding rules as `hit_columns`."""
    avail = width - 1
    tag_w = 9 if tagged else 0
    if tagged:
        avail -= tag_w + 2
    if width < 80:
        ver_w = max(7, min(12, avail // 4))
        cols = [
            Column("name", "NAME", max(8, avail - ver_w - 4)),
            Column("version", "VERSION", ver_w),
        ]
    else:
        ver_w = max(8, min(16, avail // 6))
        name_w = max(14, min(32, avail // 3))
        where_w = max(10, avail - name_w - ver_w - 6)
        cols = [
            Column("name", "NAME", name_w),
            Column("version", "VERSION", ver_w),
            Column("where", "LISTED IN", where_w),
        ]
    if tagged:
        cols.insert(2, Column("tag", "FOR", tag_w))
    return cols


def package_cells(r: PackageRow, columns: list[Column]) -> list[Span]:
    values = {"name": r.name, "version": r.version, "tag": r.tag, "where": r.where}
    style = {"name": "accent", "version": "", "tag": "dim" if r.user is None else "option"}
    out = []
    for c in columns:
        v = values[c.key]
        if c.key == "where" and len(v) > c.width:  # keep the file name and line
            v = ELLIPSIS + v[len(v) - c.width + 1 :]
        out.append(Span(clip(v, c.width), style.get(c.key, "dim")))
    return out


def package_detail(r: PackageRow) -> str:
    """The line under the table for the highlighted row."""
    who = "the system" if r.user is None else f"home {r.user}"
    where = f"listed in {r.where}" if r.where else "added by a module (no list line found)"
    return f"{r.drv} · {who} · {where}"


def list_footer(doc: Doc, shown: int, text: str = "", scope: Scope | None = None) -> str:
    """`328 packages: 138 system, 190 home brucking · 67 repeats folded · cached`, or what the
    filter and the scope leave: `3 of 328 packages matching 'fire' (system)`."""
    total = int(doc.get("count") or 0)
    rows = doc.get("packages") or []
    narrowed = bool(text.strip()) or (scope is not None and scope.key != "all")
    head = f"{shown} of {total} packages" if narrowed else f"{total} packages"
    if text.strip():
        head += f" matching {text.strip()!r}"
    if scope is not None and scope.key != "all":
        head += f" ({scope.label})"
    elif not narrowed:
        per = [f"{sum(r.get('scope') == 'system' for r in rows)} system"]
        per += [f"{sum(r.get('user') == u for r in rows)} home {u}" for u in doc.get("users") or []]
        head += ": " + ", ".join(per) if len(per) > 1 else ""
    parts = [head]
    if doc.get("folded"):
        parts.append(f"{doc['folded']} repeats folded")
    hit = "packages" in ((doc.get("cache") or {}).get("hits") or [])
    took = (doc.get("timings") or {}).get("packages")
    parts.append("cached" if hit else f"evaluated in {took} s")
    return " · ".join(parts)


def package_hit(r: PackageRow) -> Hit:
    """An installed row as a search hit, for the Details view: info/versions/rm take the
    attribute, `where` the pname (truth matches pname or derivation name)."""
    rec = {"kind": "pkg", "name": r.target, "version": r.version, "description": ""}
    where = r.name if r.name != r.target else ""
    return Hit("pkg", r.name, r.target, r.version, "", rec, where_name=where)


# --- updates: `outdated` ------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateRow:
    """One `outdated` row: a package that changes, appears or goes away."""

    pname: str
    old: str  # from (lock) / installed (published); "added" for an added one
    new: str  # to / published; "removed" for a removed one
    scope: str
    user: str | None
    note: str  # (older), (same order), as kdePackages.x, (added), (removed)
    style: str  # accent (newer), warn (older or same order), add, del

    @property
    def tag(self) -> str:
        return self.user or "system"


_DIR = {"down": ("(older)", "warn"), "same": ("(same order)", "warn")}


def update_rows(doc: Doc) -> list[UpdateRow]:
    """Per scope (system first, as `scopes[]` orders them), by pname within: the CLI's tables."""
    rows: list[UpdateRow] = []
    order = {(s.get("scope"), s.get("user")): i for i, s in enumerate(doc.get("scopes") or [])}
    lock = doc.get("mode") != "published"
    for r in doc.get("changed") or []:
        note, style = _DIR.get(str(r.get("direction")), ("", "accent"))
        if lock:
            old, new = str(r.get("from") or ""), str(r.get("to") or "")
        else:
            old, new = str(r.get("installed") or ""), str(r.get("published") or "")
            attr = str(r.get("attr") or "")
            if attr and attr.rsplit(".", 1)[-1] != r.get("pname"):
                note = f"{note}  as {attr}".strip()
        rows.append(UpdateRow(str(r["pname"]), old, new, r["scope"], r.get("user"), note, style))
    for r in doc.get("removed") or []:
        v = str(r.get("version") or "")
        rows.append(UpdateRow(str(r["pname"]), v, "removed", r["scope"], r.get("user"), "", "del"))
    for r in doc.get("added") or []:
        v = str(r.get("version") or "")
        rows.append(UpdateRow(str(r["pname"]), "added", v, r["scope"], r.get("user"), "", "add"))
    rows.sort(key=lambda r: (order.get((r.scope, r.user), 99), r.pname.lower()))
    return rows


def update_columns(width: int, tagged: bool) -> list[Column]:
    """name, from, to, the scope when several are shown, and from 80 cells the note."""
    avail = width - 1
    tag_w = 8 if tagged else 0
    if tagged:
        avail -= tag_w + 2
    if width < 80:
        ver_w = max(7, min(12, (avail - 4) // 4))
        cols = [
            Column("name", "PACKAGE", max(8, avail - 2 * ver_w - 6)),
            Column("old", "FROM", ver_w),
            Column("new", "TO", ver_w),
        ]
    else:
        ver_w = max(10, min(18, avail // 6))
        name_w = max(14, min(32, avail // 3))
        cols = [
            Column("name", "PACKAGE", name_w),
            Column("old", "FROM", ver_w),
            Column("new", "TO", ver_w),
            Column("note", "", max(8, avail - name_w - 2 * ver_w - 8)),
        ]
    if tagged:
        cols.insert(3, Column("tag", "FOR", tag_w))
    return cols


def update_cells(r: UpdateRow, columns: list[Column]) -> list[Span]:
    values = {"name": r.pname, "old": r.old, "new": r.new, "tag": r.tag, "note": r.note}
    style = {
        "name": "accent" if r.style == "accent" else r.style,
        "old": "del" if r.style == "del" else "dim",
        "new": r.style if r.style != "accent" else "",
        "tag": "dim" if r.user is None else "option",
        "note": "dim",
    }
    if r.style == "add":
        style["old"] = "add"
    return [Span(clip(values[c.key], c.width), style[c.key]) for c in columns]


def outdated_head(doc: Doc, host: str) -> str:
    """`19BK: nixpkgs (github:nixos/nixpkgs/nixos-unstable) 3ec6363 (2026-10-23) → 0bb2eed
    (2026-11-01), 8.9 days newer` (or the --published comparison)."""
    return outdated_render.header(doc, host)


def kernel_line(doc: Doc) -> Span:
    k = doc.get("kernel") or {}
    text = outdated_render.kernel(k, str(doc.get("mode") or "lock"))
    moves = bool(k.get("changed")) or bool(k.get("to") and k.get("to") != k.get("from"))
    return Span(text, "warn" if moves else "dim")


def outdated_summary(doc: Doc) -> str:
    """`13 of 325 packages would change; kernel 7.2.12 → 7.2.14`."""
    return outdated_render.summary(doc)


def outdated_scopes(doc: Doc) -> list[str]:
    """`system: 4 of 135 would change (1 removed)`, one per scope."""
    return [outdated_render.scope_line(doc, s) for s in doc.get("scopes") or []]


def outdated_notes(doc: Doc) -> list[str]:
    """notes[] and, with --published, the names search.nixos.org has no package for."""
    out = [str(n) for n in doc.get("notes") or []]
    unpub = doc.get("unpublished") or []
    if unpub:
        out.append(f"not on {(doc.get('target') or {}).get('source')}: {_join(unpub, 8)}")
    return out


def update_args(doc: Doc | None) -> list[str] | None:
    """What `update` takes to bring in what this `outdated` document compared: nothing for
    the lock's own ref (`nix flake update`), `--to REV` for another target (the commit
    compared, as `outdated`'s `next:` pins it). None when there is nothing to update."""
    if not doc or not doc.get("ok") or doc.get("up_to_date"):
        return None
    target = doc.get("target") or {}
    if doc.get("mode") == "lock" and target.get("from") != "lock" and target.get("rev"):
        return ["--to", str(target["rev"])]
    return []


def _side(side: dict[str, Any] | None) -> str:
    if not side or not side.get("rev"):
        return "?"
    return short(side["rev"]) + (f" ({side['date']})" if side.get("date") else "")


def update_confirm_fields(dry: Doc, outdated: Doc | None = None) -> list[Field]:
    """`update --dry-run` (and the `outdated` it follows) as the confirmation: what runs,
    where, from which commit to which, what it does not do, what it costs."""
    out = [
        Field("runs", str(dry.get("command")), "accent"),
        Field(str(dry.get("input") or "input"), f"now {_side(dry.get('from'))}", ""),
    ]
    target = (outdated or {}).get("target") or {}
    if outdated and outdated.get("mode") == "lock" and target.get("rev"):
        out.append(
            Field(
                "to",
                f"{target.get('short_rev')} ({target.get('date')}), as compared: "
                + outdated_summary(outdated),
                "add",
            )
        )
    else:
        out.append(Field("to", f"{dry.get('to_ref')}, as it resolves now", "add"))
    out += [
        Field("writes", f"flake.lock in {dry.get('config')}; nothing is built or switched", "dim"),
        Field("then", "Pending: Plan builds it, Apply… switches (root)", "dim"),
        Field("takes", "nix fetches the new nixpkgs: about a minute the first time", "dim"),
    ]
    return out


def update_fields(doc: Doc) -> list[Field]:
    """What `update` did."""
    name = str(doc.get("input") or "input")
    if doc.get("changed"):
        out = [Field(name, f"{_side(doc.get('from'))} → {_side(doc.get('to'))}", "add")]
    else:
        out = [Field(name, f"already at {_side(doc.get('to'))}; flake.lock unchanged", "dim")]
    out.append(Field("ran", str(doc.get("command")), "dim"))
    out += [Field("next", str(c), "dim") for c in doc.get("commands") or []]
    return out


def update_summary(doc: Doc) -> str:
    """One line for the session log: `nixpkgs 3ec6363 → 0bb2eed (flake.lock)`."""
    fr, to = doc.get("from") or {}, doc.get("to") or {}
    return f"{doc.get('input')} {short(fr.get('rev'))} → {short(to.get('rev'))} (flake.lock)"


# --- options: the Options page ----------------------------------------------------------------

OPTION_KINDS = ("opt", "hm")  # the page's toggle: NixOS options, Home Manager options
OPTION_KIND_LABEL = {"opt": "NixOS", "hm": "Home Manager"}
OPTION_LIMIT = 50  # rows per search: a browser shows more than the CLI's 20

# where a definition's priority comes from (lib/modules.nix: mkOptionDefault, mkDefault,
# a plain definition, mkForce)
_PRIO = {1500: "the option's default", 1000: "lib.mkDefault", 50: "lib.mkForce"}
_PLACEHOLDER = re.compile(r"^<(derivation .*|function|package set|….*|mkIf .*|cannot .*)>$")


def option_search_args(query: str, kind: str, channel: str) -> list[str]:
    """`search` for the page: NixOS (`opt`) or Home Manager (`hm`) options only."""
    return ["--kind", kind, "--channel", channel, "--limit", str(OPTION_LIMIT), "--", query]


def option_template(name: str) -> bool:
    """`users.users.<name>.shell`, `programs.git.includes.*.path`: a path with a placeholder
    for an attribute name or a list element, which `set` cannot take as it is."""
    return "<" in name or ".*." in name or name.endswith(".*")


def option_placeholder(name: str) -> tuple[int, int] | None:
    """Where the first `<name>` (or `*`) of a template is, to select it for typing over."""
    m = re.search(r"<[^>]*>|(?:^|(?<=\.))\*(?=\.|$)", name)
    return (m.start(), m.end()) if m else None


def option_where_scopes(where: Doc | None, kind: str) -> list[dict[str, Any]]:
    """The `where` scopes this page shows: the NixOS tree for `opt`, every Home Manager user's
    for `hm` (`where` answers every tree that has the name: `programs.git.lfs.enable` is
    both a NixOS and a Home Manager option)."""
    if not where or not where.get("ok"):
        return []
    want = "home" if kind == "hm" else "nixos"
    return [s for s in where.get("scopes") or [] if s.get("scope") == want]


def option_users(where: Doc | None) -> list[str]:
    """The Home Manager users whose tree has the option (from `where`)."""
    return [str(s["user"]) for s in option_where_scopes(where, "hm") if s.get("user")]


def _scope_for(where: Doc | None, kind: str, user: str | None) -> dict[str, Any] | None:
    scopes = option_where_scopes(where, kind)
    if kind == "hm":
        return next((s for s in scopes if s.get("user") == user), None)
    return scopes[0] if scopes else None


def _first(scope: dict[str, Any] | None) -> dict[str, Any] | None:
    opts = (scope or {}).get("options") or []
    return opts[0] if opts else None


def _lossy(v: Any) -> bool:
    """A `where` value nix_text cannot give back exactly: a placeholder (a derivation, a
    function, a capped list), a long string options.nix cut (`…<…>`) or a multi-line one
    (nix_text keeps its first line)."""
    if isinstance(v, str):
        return "\n" in v or v.endswith("<…>") or bool(_PLACEHOLDER.match(v))
    if isinstance(v, list):
        return any(_lossy(x) for x in v)
    if isinstance(v, dict):
        return "<…>" in v or any(_lossy(x) for x in v.values())
    return False


def option_value_text(where: Doc | None, kind: str, user: str | None) -> str | None:
    """The value on this host (the user's for `hm`) as Nix text, None when `where` has none."""
    o = _first(_scope_for(where, kind, user))
    if o is None or o.get("value_error") or o.get("value_found") is False or "value" not in o:
        return None
    return nix_text(o.get("value"))


def option_prefill(rec: dict[str, Any], where: Doc | None, kind: str, user: str | None) -> str:
    """What the Set dialog starts with: the value on this host when it can be written back as
    it is, else the documented default, else nothing."""
    o = _first(_scope_for(where, kind, user))
    if (
        o is not None
        and "value" in o
        and not o.get("value_error")
        and o.get("value_found") is not False
        and not _lossy(o.get("value"))
    ):
        return nix_text(o.get("value"))
    default = rec.get("default")
    return str(default) if default not in (None, "") and "\n" not in str(default) else ""


def option_cue(where: Doc | None, kind: str, user: str | None) -> Span | None:
    """`= true` beside the title once `where` answered."""
    text = option_value_text(where, kind, user)
    return None if text is None else Span(f"= {clip(text, 30)}", "option")


def option_fields(rec: dict[str, Any], where: Doc | None = None) -> list[Field]:
    """What the option is: type, default, example (Nix text, an attrset keeps its lines),
    read-only. Upstream's record first; the type from `where` (the locked nixpkgs) when
    upstream has none."""
    out: list[Field] = []
    o = next((_first(s) for s in (where or {}).get("scopes") or [] if _first(s)), None)
    typ = rec.get("type") or (o or {}).get("type")
    for label, value in (("type", typ), ("default", rec.get("default"))):
        if value not in (None, ""):
            out.append(Field(label, str(value), "option"))
    ex = rec.get("example")
    out.append(
        Field("example", str(ex), "option")
        if ex not in (None, "")
        else Field("example", "—", "dim")
    )
    if rec.get("read_only"):
        out.append(Field("read-only", "yes: set by the module itself", "warn"))
    if option_template(str(rec.get("name") or "")):
        out.append(
            Field("path", "a template: Set asks for the name in place of <name> / *", "warn")
        )
    return out


def option_about(rec: dict[str, Any], where: Doc | None = None) -> str:
    """The full description: upstream's, else the one `where` evaluated."""
    text = str(rec.get("description") or "").strip()
    if not text:
        o = next((_first(s) for s in (where or {}).get("scopes") or [] if _first(s)), None)
        text = str((o or {}).get("description") or "").strip()
    return text or "(no description)"


def _definition(df: dict[str, Any], prio: Any, several: bool) -> Field:
    if df.get("source") == "config":
        text = place(df)
        if df.get("text"):
            text += f"  {clip(df['text'], 60)}"
        elif several:
            text += f" = {clip(nix_text(df.get('value')), 40)}"
        return Field("set in", text, "")
    why = _PRIO.get(prio) if isinstance(prio, int) else None
    return Field("set in", place(df) + (f" ({why})" if why else ""), "dim")


def option_host(
    where: Doc | None, host: str, kind: str, user: str | None = None, *, template: bool = False
) -> Section:
    """`where` for the page's tree: the value on this host and the files that set it (a config
    file with its line, or the module that gives the default). For `hm`, one block per Home
    Manager user, the selected `user` first."""
    title = f"on {host}"
    if template:
        return Section(title, [], "a template: where needs the full name (Set asks for it)", "dim")
    if where is None:
        return Section(title, [], "evaluating…", "dim")
    if not where.get("ok"):
        n = notice(where)
        assert n is not None
        if n.code == "not-found":
            what = "Home Manager option" if kind == "hm" else "option"
            return Section(title, [], f"not an {what} of {host}'s config: {n.message}", "dim")
        return Section(title, [], f"{n.code}: {n.message}", "error")
    scopes = option_where_scopes(where, kind)
    if not scopes:
        if kind == "hm":
            other = "no Home Manager user of this host has it"
        else:
            other = "only a Home Manager option here"
        return Section(title, [], other, "dim")
    scopes.sort(key=lambda s: s.get("user") != user)
    fields: list[Field] = []
    for s in scopes:
        tag = f"  [home {s.get('user')}]" if kind == "hm" else ""
        for o in s.get("options") or []:
            fields.append(Field("value", option_value(o) + tag, "option"))
            if o.get("option") != o.get("name"):
                fields.append(Field("inside", f"{o.get('option')} ({o.get('type')})", "dim"))
            defs = o.get("definitions") or []
            for df in defs:
                fields.append(_definition(df, o.get("highestPrio"), len(defs) > 1))
            if not defs:
                fields.append(Field("set in", "nothing (the default)", "dim"))
    return Section(title, fields)


def option_declared(rec: dict[str, Any], where: Doc | None, kind: str) -> list[Field]:
    """Where the option is declared upstream: the module file and line on the locked nixpkgs
    or home-manager (from `where`), and upstream's link to it (search.nixos.org, the HM index)."""
    out: list[Field] = []
    seen: set[str] = set()
    for s in option_where_scopes(where, kind)[:1]:
        o = _first(s) or {}
        for dec in o.get("declarations") or []:
            text = place(dec)
            if text not in seen:
                seen.add(text)
                out.append(Field("module", text, ""))
    urls = [str(u) for u in rec.get("declaration_urls") or []]
    for u in urls[:2]:
        out.append(Field("upstream", u, "dim"))
    if not out:
        for d in (rec.get("declarations") or [])[:2]:
            out.append(Field("module", str(d), "dim"))
    src = f"{rec.get('source') or ''} {rec.get('channel') or ''}".strip()
    if rec.get("rev"):
        src += f"@{short(rec['rev'], 8)}"
    if src:
        out.append(Field("docs", src, "dim"))
    return out


def option_sections(
    rec: dict[str, Any], where: Doc | None, host: str, kind: str, user: str | None = None
) -> list[Section]:
    """The details of one option, in display order: what it is, the description, its value
    and definitions on this host, where it is declared. `where` None: still evaluating.

    The seam for `explain` (branch typed-set-explain): it joins the same two sides in one
    document; a front end can call it instead of `where` and map it onto these sections."""
    template = option_template(str(rec.get("name") or ""))
    return [
        Section("option", option_fields(rec, where)),
        Section("about", [], option_about(rec, where), ""),
        option_host(where, host, kind, user, template=template),
        Section("declared", option_declared(rec, where, kind)),
    ]


def option_set_hint(rec: dict[str, Any]) -> list[Field]:
    """Under the Set dialog's value: the type, and the example (as upstream writes it)."""
    out = [Field("type", str(rec.get("type") or "?"), "option")]
    ex = rec.get("example")
    if ex not in (None, ""):
        out.append(Field("example", str(ex), "option"))
    if rec.get("default") not in (None, ""):
        out.append(Field("default", str(rec.get("default")), "dim"))
    return out
