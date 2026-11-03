"""Human output of `explain`: the `view` as a block wrapped to the terminal width.

    services.openssh.enable — NixOS option
      type      boolean
      default   false
      value     true
      set in    hosts/mbp13/default.nix:240
      declared  nixpkgs nixos/modules/services/networking/ssh/sshd.nix:247
                https://github.com/NixOS/nixpkgs/blob/…/sshd.nix#L247

      Whether to enable the OpenSSH secure shell daemon, which allows secure remote
      logins.

`NIXIN_WIDTH` overrides the width, as for the other tables.
"""

from __future__ import annotations

import textwrap
from typing import Any

from ..truth.render import term_width

LABEL = 9  # the label column, as in `where`
MIN_WIDTH = 40


def text(view: dict[str, Any], width: int | None = None) -> str:
    width = max(width or term_width(), MIN_WIDTH)
    head = view["title"] + (f" — {view['subtitle']}" if view.get("subtitle") else "")
    lines = textwrap.wrap(head, width, subsequent_indent="  ") or [head]
    indent = " " * (2 + LABEL + 1)
    room = width - len(indent)
    for r in view["rows"]:
        first = f"  {r['label']:<{LABEL}} "
        body = _block(r["text"], room)
        lines.append((first + body[0]).rstrip())
        lines += [(indent + b).rstrip() for b in body[1:]]
        if r.get("url") and r["url"] != r["text"]:
            lines.append(indent + r["url"])  # one line even when long: it stays clickable
    if view.get("about"):
        lines.append("")
        for para in _paragraphs(view["about"]):
            lines += textwrap.wrap(
                para,
                width,
                initial_indent="  ",
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
    for n in view.get("notes") or []:
        lines += textwrap.wrap(f"note: {n}", width, subsequent_indent="      ")
    return "\n".join(lines)


def _block(value: str, room: int) -> list[str]:
    """A value's own line breaks kept (an example attrset), long lines wrapped."""
    out: list[str] = []
    for line in value.splitlines() or [""]:
        lead = len(line) - len(line.lstrip(" "))
        wrapped = textwrap.wrap(  # a path or URL longer than the room stays whole
            line,
            max(room, 20),
            subsequent_indent=" " * (lead + 2),
            drop_whitespace=False,
            break_long_words=False,
            break_on_hyphens=False,
        )
        out += [w.rstrip() for w in wrapped] or [""]
    return out


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs, each joined into one line for wrapping."""
    paras, cur = [], []
    for line in text.strip().splitlines():
        if line.strip():
            cur.append(line.strip())
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return paras
