import json

import pytest

from nixin import __version__
from nixin.cli import VERBS, main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"nixin {__version__}"


def test_no_verb_is_usage(capsys):
    assert main([]) == 2


@pytest.mark.parametrize("verb", sorted(VERBS))
def test_every_verb_speaks_json(verb, capsys):
    rc = main(["--json", "--host", "testhost", verb])
    doc = json.loads(capsys.readouterr().out)
    assert doc["verb"] == verb and doc["host"] == "testhost"
    assert doc["ok"] is False and rc == 1  # stubs: the contract, not the feature yet
    assert doc["error"]["code"] == "not-implemented"
