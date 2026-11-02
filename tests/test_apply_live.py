"""Live checks of the apply verbs on this machine (`NIXIN_LIVE=1`, `NIXIN_CONFIG=<workspace>`).

None of them switches anything or writes to the workspace:

- `plan` builds the workspace host's toplevel with the host's nix (minutes when cold,
  under a second when plan's cache has the tree) and diffs it against the running system.
- `apply --dry-run` reads both trees and the caches, probes `sudo -n true`, prints the
  commands; the workspace HEAD must not move. With nothing to do it is `nothing-to-apply`.
- `sync` runs on a throwaway clone of the workspace against a throwaway bare remote.

The real `apply` is not here on purpose: it switches the machine. docs/HANDOFF-apply.md
has the by-hand acceptance run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import live

from nixin.cli import main

CONFIG = os.environ.get("NIXIN_CONFIG")
needs_config = pytest.mark.skipif(not CONFIG, reason="set NIXIN_CONFIG to the workspace")


def nixin(capsys, *argv: str) -> dict:
    rc = main(["--json", *argv])
    out, err = capsys.readouterr()
    assert err == ""
    doc = json.loads(out)
    assert rc == (0 if doc["ok"] else 1), doc
    return doc


def head(path: str) -> str:
    return subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


@live
@needs_config
def test_live_plan(capsys):
    d = nixin(capsys, "plan", "--config", CONFIG)
    assert d["ok"], d
    assert d["out"].startswith("/nix/store/") and os.path.exists(d["out"])
    assert d["running"]["store_path"] and d["diff"] is not None
    assert d["up_to_date"] == (d["out"] == os.path.realpath("/run/current-system"))
    again = nixin(capsys, "plan", "--config", CONFIG)
    assert again["cached"] is True and again["out"] == d["out"]


@live
@needs_config
def test_live_apply_dry_run(capsys):
    before = head(CONFIG)
    d = nixin(capsys, "apply", "--dry-run", "--config", CONFIG)
    if not d["ok"]:
        assert d["error"]["code"] == "nothing-to-apply", d
    else:
        assert [c["step"] for c in d["commands"]][-2:] == ["pull", "switch"]
        assert all(c["command"] for c in d["commands"])
    assert head(CONFIG) == before


@live
@needs_config
def test_live_sync_on_a_clone(capsys, tmp_path):
    ws, hub = tmp_path / "ws", tmp_path / "hub.git"
    subprocess.run(["git", "clone", "-q", CONFIG, str(ws)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(hub)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "hub", str(hub)], check=True)
    d = nixin(capsys, "sync", "--remote", "hub", "--push", "--config", str(ws))
    assert d["ok"] and d["done"] == ["fetch", "push"], d
    assert d["remote"]["head"] == head(str(ws))
    assert (d["workspace"]["ahead"], d["workspace"]["behind"]) == (0, 0)
    assert Path(hub, "refs", "heads").exists()
