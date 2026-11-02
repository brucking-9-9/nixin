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

from dataclasses import dataclass, field
from typing import Any

from ..apply.closure import human_size
from ..discovery.format import _HEADERS, _footer
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
