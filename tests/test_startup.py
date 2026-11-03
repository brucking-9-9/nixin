"""The CLI starts light: building the parser and running a verb that needs neither never
imports the UI toolkits (Textual, PySide6) or the HTTP stack (requests is imported on
first use by discovery). Run in a fresh interpreter, since this test process has
imported all of them."""

from __future__ import annotations

import json
import os
import subprocess
import sys

HEAVY = ["PySide6", "shiboken6", "textual", "rich", "requests", "urllib3"]

CODE = """
import json, sys
from nixin import cli
for argv in json.loads(sys.argv[1]):
    cli.main(argv)
print(json.dumps(sorted({m.split(".")[0] for m in sys.modules} & set(json.loads(sys.argv[2])))))
"""


def test_cli_verbs_import_no_toolkit_and_no_http(tmp_path):
    runs = [
        ["--json", "--host", "testhost", "status", "--config", "/nonexistent/nixin-x"],
        ["--json", "--host", "testhost", "list", "--config", "/nonexistent/nixin-x"],
        ["--json", "--host", "testhost", "plan", "--config", "/nonexistent/nixin-x"],
        ["--json", "--host", "testhost", "generations", "--config", "/nonexistent/nixin-x"],
        ["--json", "--host", "testhost", "gc", "--dry-run"],
    ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "XDG_CACHE_HOME": str(tmp_path),
    }
    p = subprocess.run(
        [sys.executable, "-c", CODE, json.dumps(runs), json.dumps(HEAVY)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == []
