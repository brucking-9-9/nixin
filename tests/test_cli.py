import json

import pytest

from nixin import __version__, apply, outdated, truth, writer
from nixin.cli import VERBS, main
from nixin.discovery import VERBS as DISCOVERY_VERBS

STUB_VERBS = sorted(
    v for v in VERBS if v not in DISCOVERY_VERBS and v not in ("tui", "gui", "explain")
)  # own tests


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"nixin {__version__}"


def test_no_verb_is_usage(capsys):
    assert main([]) == 2


@pytest.mark.parametrize("after", [False, True], ids=["flags-before-verb", "flags-after-verb"])
@pytest.mark.parametrize("verb", STUB_VERBS)
def test_every_verb_speaks_json(verb, after, capsys):
    flags = ["--json", "--host", "testhost"]
    argv = [verb, *flags] if after else [*flags, verb]
    configured = verb in truth.VERBS or verb in writer.VERBS or verb in apply.VERBS
    configured = configured or verb in outdated.VERBS
    if configured:
        argv += ["--config", "/nonexistent/nixin-test"]  # never touch the real config in tests
    rc = main(argv)
    doc = json.loads(capsys.readouterr().out)
    assert doc["verb"] == verb and doc["host"] == "testhost"
    assert doc["ok"] is False and rc == 1
    expected = "config-not-found" if configured else "not-implemented"
    assert doc["error"]["code"] == expected


def _one_doc(capsys) -> tuple[dict, str]:
    out, err = capsys.readouterr()
    return json.loads(out), err


@pytest.mark.parametrize(
    "argv, verb",
    [
        ([], None),  # no verb
        (["frobnicate"], None),  # unknown verb
        (["search"], "search"),  # missing positional
        (["search", "x", "--kind", "package"], "search"),  # bad choice
        (["search", "x", "--limit", "many"], "search"),  # bad type
        (["where", "x", "--bogus"], "where"),  # unknown flag
        (["search", ""], "search"),  # the verb's own usage check
    ],
)
def test_usage_errors_are_one_json_document(argv, verb, capsys):
    rc = main(["--json", "--host", "testhost", *argv])
    doc, err = _one_doc(capsys)
    assert rc == 2 and err == ""
    assert doc["ok"] is False and doc["error"]["code"] == "usage"
    assert doc["verb"] == verb and doc["host"] == "testhost"


def test_usage_error_without_json_is_argparse_text(capsys):
    assert main(["search"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("usage: nixin search") and "nixin search: error:" in err


def test_unexpected_exception_is_internal(monkeypatch, capsys):
    def boom(ns):
        raise KeyError("surprise")

    monkeypatch.setattr(truth, "status", boom)
    rc = main(["--json", "--host", "testhost", "status"])
    doc, err = _one_doc(capsys)
    assert rc == 1 and err == ""
    assert doc["error"]["code"] == "internal" and "KeyError" in doc["error"]["message"]


@pytest.mark.parametrize("verb", ["status", "add", "plan"])
def test_config_before_the_verb(verb, capsys):
    """`nixin --config DIR VERB` = `nixin VERB --config DIR`; the verb's own flag wins."""
    argv = ["--json", "--config", "/nonexistent/nixin-a", verb]
    if verb == "add":
        argv.append("x")
    main(argv)
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "config-not-found" and "nixin-a" in doc["error"]["message"]
    main([*argv, "--config", "/nonexistent/nixin-b"])
    doc = json.loads(capsys.readouterr().out)
    assert "nixin-b" in doc["error"]["message"]


def test_config_before_a_verb_without_one_is_ignored(capsys, monkeypatch):
    monkeypatch.setattr(
        "nixin.discovery.run_search", lambda ns: {"ok": True, "seen": "config" in ns}
    )
    assert main(["--json", "--config", "/x", "search", "q"]) == 0
    assert json.loads(capsys.readouterr().out)["seen"] is False
