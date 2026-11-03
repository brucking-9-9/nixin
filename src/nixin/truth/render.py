"""Human (non --json) rendering of `status`, `where` and `list`: one fact per line, values
in Nix syntax, capped to stay on one terminal line. The --json document has everything in
full.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from ..configrepo import nix_attr

VALUE_CAP = 72
MENTIONS_SHOWN = 4


def nix_text(v: Any) -> str:
    """A serialised option value (options.nix placeholders included) as Nix source."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        if v.startswith("<") and v.endswith(">"):
            return v  # <derivation kitty-0.49.2>, <function>, <… 3 more>, <mkIf false>
        first, nl, _ = v.partition("\n")
        return json.dumps(first + ("…" if nl else ""), ensure_ascii=False)
    if isinstance(v, list):
        return "[ " + "".join(nix_text(x) + " " for x in v) + "]"
    if isinstance(v, dict):
        parts = [
            f"… {x}" if k == "<…>" else f"{nix_attr(k)} = {nix_text(x)};" for k, x in v.items()
        ]
        return "{ " + "".join(p + " " for p in parts) + "}"
    return str(v)


def row(label: str, text: str, indent: str = "") -> str:
    """`label     text`, labels padded to one column."""
    return f"{indent}{label:<9} {text}".rstrip()


def sub(label: str, text: str) -> str:
    return row(label, text, "  ")


def cap(s: str, n: int = VALUE_CAP) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def term_width() -> int:
    """Columns for the `list` table: $NIXIN_WIDTH (as discovery's tables), else the terminal."""
    w = os.environ.get("NIXIN_WIDTH")
    if w and w.isdigit():
        return int(w)
    return shutil.get_terminal_size((100, 24)).columns


def _tag(scope: str, user: str | None) -> str:
    return f"[{scope}{' ' + user if user else ''}]"


def place(loc: dict[str, Any]) -> str:
    """A location (where's declarations/definitions): config files as path:line, inputs as `input path:line`, inline as such."""
    if loc["source"] == "inline":
        return "inline in nixosSystem"
    line = loc.get("line")
    if not line and loc.get("lines"):
        line = loc["lines"][0]["line"]
    where = f"{loc['file']}:{line}" if line else str(loc["file"])
    return where if loc["source"] == "config" else f"{loc['source']} {where}"


# --- status -------------------------------------------------------------------------------


def status(d: dict[str, Any], host: str) -> str:
    c, lock, run, ev = d["config"], d["lock"] or {}, d["running"], d["eval"]
    cached = "toplevel" in d["cache"]["hits"]
    head = (c["head"] or "?")[:7] + (" + uncommitted changes" if c["dirty"] else "")
    pk, pr = d["packages"], d["programs"]
    rev = " ".join(
        x for x in (lock.get("input"), (lock.get("rev") or "?")[:7], lock.get("ref")) if x
    )
    lines = [
        f"{host}{' (this machine)' if c['host_is_local'] else ''} evaluates"
        + f"  ({ev['seconds']} s{', cached' if cached else ''})",
        row("config", f"{c['path']} @ {head}  · hosts {', '.join(c['hosts'])}"),
        row("lock", rev + (f" ({lock['date']})" if lock.get("date") else "")),
        row(
            "running",
            f"{run.get('version') or '?'}"
            + ("" if c["host_is_local"] else f" on {run['hostname']}, not {host}")
            + ("  · matches the lock" if run["matches_lock"] else "  · not the locked nixpkgs"),
        ),
        row(
            "packages",
            ", ".join(
                [f"{pk['system']} system"] + [f"{n} home {u}" for u, n in pk["home"].items()]
            ),
        ),
        row(
            "programs",
            ", ".join(
                [f"{len(pr['system'])} system"]
                + [f"{len(p)} home {u}" for u, p in pr["home"].items()]
            ),
        ),
    ]
    o = d["options"]
    if not o:
        lines.append(row("options", "not indexed (nixin status --full)"))
    else:
        parts = [("nixos", o["nixos"])] + [(f"home {u}", h) for u, h in o["home"].items()]
        lines.append(
            row(
                "options",
                ", ".join(
                    f"{name} {s['defined']} set"
                    + (f" ({s['in_config']} by the config)" if "in_config" in s else "")
                    + f" of {s['total']}"
                    for name, s in parts
                )
                + f"  · {sum(s['excluded'] for _, s in parts)} skipped",
            )
        )
    misses = d["cache"]["misses"]
    lines.append(
        f"({d['timings']['total']} s; "
        + (f"evaluated {', '.join(misses)}" if misses else "all from cache")
        + ")"
    )
    return "\n".join(lines)


# --- where --------------------------------------------------------------------------------


def where(d: dict[str, Any]) -> str:
    return _where_package(d) if d["kind"] == "package" else _where_option(d)


def _where_package(d: dict[str, Any]) -> str:
    inst = d["installed"]
    head = (
        ", ".join(f"{p['name']} {_tag(p['scope'], p['user'])}" for p in inst)
        if inst
        else "not installed on this host"
    )
    lines = [f"{d['name']}: {head}"]
    for v in d["programs"]:
        for df in v["definitions"]:
            text = f"{v['option']} = {nix_text(df.get('value'))} in {place(df)}"
            lines.append(sub("enabled", f"{text}  {_tag(v['scope'], v['user'])}"))
    for loc in d["declared"]:
        text = f"{loc['option']} in {place(loc)}"
        lines.append(sub("listed", f"{text}  {_tag(loc['scope'], loc['user'])}"))
    # the text grep, minus lines already shown, strongest first (pkgs.x, bare list items,
    # programs.x); comments and passing mentions only in --json
    shown = {(loc["file"], m["line"]) for loc in d["declared"] for m in loc.get("lines", [])}
    others = [m for m in d["mentions"] if (m["file"], m["line"]) not in shown]
    strong = [m for m in others if m["score"] >= 2][:MENTIONS_SHOWN]
    for i, m in enumerate(strong):
        text = f"{m['file']}:{m['line']}  {cap(m['text'], 60)}"
        lines.append(sub("mentions" if i == 0 else "", text))
    if len(others) > len(strong):
        lines.append(sub("", f"… {len(others) - len(strong)} more mentions (--json)"))
    return "\n".join(lines)


def _where_option(d: dict[str, Any]) -> str:
    lines: list[str] = []
    for s in d["scopes"]:
        tag = _tag(s["scope"], s["user"])
        if s["mode"] == "prefix":
            lines += _prefix(d["name"], s, tag)
        else:
            for o in s["options"]:
                lines += option_lines(o, tag)
        for name, msg in s["excluded"].items():
            lines.append(sub("skipped", f"{name}: {cap(msg.splitlines()[0], 80)}"))
    return "\n".join(lines)


def _value(o: dict[str, Any]) -> str:
    if o.get("value_error"):
        return "<cannot be evaluated>"
    if o.get("value_found") is False:
        return "<not set>"
    return nix_text(o.get("value"))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def option_lines(o: dict[str, Any], tag: str) -> list[str]:
    """An option entry (`where`, `set OPTION`, `explain`): the name and value, its type,
    default and example, where it is declared and set, and what it is about."""
    value = _value(o) if "value" in o or o.get("value_error") else None
    lines = [o["name"] + (f" = {cap(value)}" if value is not None else "") + f"  {tag}"]
    ti = o.get("type_info") or {}
    if o["option"] == o["name"]:
        lines.append(sub("type", cap(str(ti.get("description") or o.get("type")), 90)))
    else:  # a path inside an attrsOf / submodule option
        if ti.get("description"):
            lines.append(sub("type", cap(ti["description"], 90)))
        lines.append(sub("inside", cap(f"{o['option']} ({o.get('type')})", 90)))
    for label in ("default", "example"):
        if o.get(label) is not None:
            lines.append(sub(label, cap(_one_line(o[label]), 90)))
    for dec in o["declarations"]:
        lines.append(sub("declared", place(dec)))
    many = len(o["definitions"]) > 1
    for df in o["definitions"]:
        own = f"  = {cap(nix_text(df.get('value')), 60)}" if many else ""
        lines.append(sub("set in", place(df) + own))
        for c in df.get("candidates", [])[:3]:
            lines.append(sub("", f"maybe {c['file']}:{c['line']}  {cap(c['text'], 50)}"))
    if not o["definitions"]:
        lines.append(sub("set in", "nothing (the default)"))
    about = o.get("sub_description") or o.get("description")
    if about:
        lines.append(sub("about", cap(about.strip(), 90)))
    return lines


def _prefix(name: str, s: dict[str, Any], tag: str) -> list[str]:
    """One line per option set under the prefix: what the config writes (falling back to
    the merged value) and where; other modules' contributions as their input's name."""
    opts = s["options"]
    lines = [f"{name}  {tag}  {s['under_prefix']} options, {len(opts)} set beyond their defaults"]
    rels = [o["name"].removeprefix(name + ".") for o in opts]
    width = min(max(map(len, rels), default=0), 32)
    for rel, o in zip(rels, opts, strict=True):
        mine = [df for df in o["definitions"] if df["source"] in ("config", "inline")]
        value = nix_text(mine[0].get("value")) if len(mine) == 1 else _value(o)
        places = [place(df) for df in mine] + sorted(
            {f"+ {df['source']}" for df in o["definitions"] if df not in mine}
        )
        lines.append(f"  {rel:<{width}} = {cap(value, 44)}  {' '.join(places) or '?'}")
    return lines


# --- list ---------------------------------------------------------------------------------

NAME_CAP = 32
NAME_MIN = 16
VERSION_CAP = 14
WHERE_MIN = 12


def listing(d: dict[str, Any], host: str) -> str:
    """`scope  name  version  (user)  file:line`, one package a line, sorted by name; the
    last column only where the line has room. Then the programs (--programs), a footer."""
    total = term_width()
    rows = d["packages"]
    lines = _table(
        total,
        [
            (r["scope"], r["pname"] or r["name"] or "?", r["version"] or "", r["user"], _first(r))
            for r in rows
        ],
    )
    per = [f"{sum(r['scope'] == 'system' for r in rows)} system"] if d["scope"] != "home" else []
    per += [f"{sum(r['user'] == u for r in rows)} home {u}" for u in d["users"]]
    head = f"{d['count']} package{'s' if d['count'] != 1 else ''}"
    if d["filter"]:
        head += f" matching {d['filter']!r}"
    head += f" on {host}"
    if len(per) > 1:
        head += ": " + ", ".join(per)
    elif per:
        head += f" ({per[0].split(' ', 1)[1]})"
    foot = [head]
    if d["folded"]:
        foot.append(f"{d['folded']} repeated list entries folded")
    hits = "packages" in d["cache"]["hits"]
    foot.append("cached" if hits else f"evaluated in {d['timings']['packages']} s")
    joined = "  · ".join(foot)
    out = lines + ([joined] if len(joined) <= total else foot)
    if "programs" in d:
        progs = d["programs"]
        out += ["", "programs.<name>.enable = true:"]
        out += _table(total, [(p["scope"], p["name"], "", p["user"], "") for p in progs])
        out.append(f"{len(progs)} program{'s' if len(progs) != 1 else ''} enabled")
    out += [f"note: {w}" for w in d["warnings"]]
    return "\n".join(out)


def _first(r: dict[str, Any]) -> str:
    decl = r["declared"]
    if not decl:
        return ""
    more = f" +{len(decl) - 1}" if len(decl) > 1 else ""
    return f"{decl[0]['file']}:{decl[0]['line']}{more}"


def _table(total: int, rows: list[tuple[str, str, str, str | None, str]]) -> list[str]:
    """Columns sized to their content, name and version capped. On a narrow terminal the
    name column gives way to the last one (down to NAME_MIN), which is then cut from the
    left (file name and line stay) and dropped below WHERE_MIN."""
    if not rows:
        return []
    sw = max(len(r[0]) for r in rows)
    vw = min(max(len(r[2]) for r in rows), VERSION_CAP)
    uw = max((len(r[3]) + 2 for r in rows if r[3]), default=0)
    lw = max(len(r[4]) for r in rows)
    fixed = sw + 2 + (vw + 2 if vw else 0) + (uw + 2 if uw else 0) + (lw + 2 if lw else 0)
    nw = min(max(len(r[1]) for r in rows), NAME_CAP)
    nw = max(min(nw, total - fixed), min(nw, NAME_MIN))
    out = []
    for scope, name, version, user, last in rows:
        cells = [scope.ljust(sw), cap(name, nw).ljust(nw)]
        if vw:
            cells.append(cap(version, vw).ljust(vw))
        if uw:
            cells.append((f"({user})" if user else "").ljust(uw))
        line = "  ".join(cells)
        room = total - len(line) - 2
        if last and room >= WHERE_MIN:
            line += "  " + (last if len(last) <= room else "…" + last[len(last) - room + 1 :])
        out.append(line.rstrip())
    return out
