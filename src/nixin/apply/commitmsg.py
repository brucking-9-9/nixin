"""The commit message apply writes when no -m is given.

The subject says what installer/state.json gained or lost (`nixin: mbp13: +cowsay`,
`nixin: brucking (home): set programs.bat.enable = true`); a change only in hand-written
modules (the writer's in-place edits) names the files instead. The body lists every
item and every file.
"""

from __future__ import annotations

import json
from typing import Any

from ..writer.state import STATE_FILE
from .system import HISTORY

SUBJECT_MAX = 72


def _load(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _short(v: str, n: int = 40) -> str:
    v = " ".join(v.split())
    return v if len(v) <= n else v[: n - 1] + "…"


def state_changes(old_text: str | None, new_text: str | None) -> list[tuple[str, list[str]]]:
    """[(scope label, ["+cowsay", "-hello", "set a.b = 1", "unset c"])] in a stable order."""
    old, new = _load(old_text), _load(new_text)
    out: list[tuple[str, list[str]]] = []
    for kind in ("nixos", "home"):
        names = sorted(set(old.get(kind) or {}) | set(new.get(kind) or {}))
        for name in names:
            a = (old.get(kind) or {}).get(name) or {}
            b = (new.get(kind) or {}).get(name) or {}
            pa, pb = set(a.get("packages") or []), set(b.get("packages") or [])
            oa, ob = a.get("options") or {}, b.get("options") or {}
            items = [f"+{p}" for p in sorted(pb - pa)] + [f"-{p}" for p in sorted(pa - pb)]
            for k in sorted(set(oa) | set(ob)):
                if k not in ob:
                    items.append(f"unset {k}")
                elif oa.get(k) != ob[k]:
                    items.append(f"set {k} = {_short(str(ob[k]))}")
            if items:
                out.append((name if kind == "nixos" else f"{name} (home)", items))
    return out


def message(old_state: str | None, new_state: str | None, files: list[str]) -> str:
    changes = state_changes(old_state, new_state)
    hand = [f for f in files if not f.startswith("installer/")]
    if changes:
        subject = "nixin: " + "; ".join(f"{label}: {' '.join(items)}" for label, items in changes)
    elif hand:
        more = f" (+{len(hand) - 3} more)" if len(hand) > 3 else ""
        subject = "nixin: update " + ", ".join(hand[:3]) + more
    elif files and set(files) <= {HISTORY}:
        subject = "nixin: apply history"
    else:
        subject = "nixin: update " + ", ".join(files[:3])
    if len(subject) > SUBJECT_MAX:
        subject = subject[: SUBJECT_MAX - 1] + "…"
    body = [f"- {label}: {item}" for label, items in changes for item in items]
    if body:
        body.append("")
    body += ["Files:"] + [f"  {f}" for f in files]
    body.append("")
    body.append(f"Written by nixin apply; {STATE_FILE} is the record of what nixin added.")
    return subject + "\n\n" + "\n".join(body) + "\n"
