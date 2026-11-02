import json

import pytest

from nixin import __version__, truth
from nixin.cli import VERBS, main
from nixin.discovery import VERBS as DISCOVERY_VERBS

STUB_VERBS = sorted(v for v in VERBS if v not in DISCOVERY_VERBS)


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"nixin {__version__}"


def test_no_verb_is_usage(capsys):
    assert main([]) == 2


@pytest.mark.parametrize("verb", STUB_VERBS)
def test_every_verb_speaks_json(verb, capsys):
    argv = ["--json", "--host", "testhost", verb]
    if verb in truth.VERBS:
        argv += ["--config", "/nonexistent/nixin-test"]  # never touch the real config in tests
    rc = main(argv)
    doc = json.loads(capsys.readouterr().out)
    assert doc["verb"] == verb and doc["host"] == "testhost"
    assert doc["ok"] is False and rc == 1
    expected = "config-not-found" if verb in truth.VERBS else "not-implemented"
    assert doc["error"]["code"] == expected
