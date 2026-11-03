"""nixin-state (the modules' outdated and pending timers), offline: the nixin verbs and
notify-send are replaced, the state directory is a tmp dir."""

from __future__ import annotations

import json
import time

import pytest

from nixin import state

LOCK_DOC = {
    "ok": True,
    "verb": "outdated",
    "count": 3,
    "total": 340,
    "changed": [
        {
            "pname": "git",
            "scope": "system",
            "user": None,
            "from": "2.50",
            "to": "2.51",
            "direction": "up",
        },
        {
            "pname": "nano",
            "scope": "system",
            "user": None,
            "from": "8.6",
            "to": "8.7",
            "direction": "up",
        },
        {
            "pname": "foo",
            "scope": "system",
            "user": None,
            "from": "2",
            "to": "1",
            "direction": "down",
        },
    ],
    "kernel": {"changed": True, "from": "6.12.1", "to": "6.12.4"},
    "target": {"days_newer": 4.2},
    "scopes": [{"scope": "system", "user": None, "changed": 3, "total": 340}],
    "lock": {"input": "nixpkgs", "short_rev": "3ec6363", "date": "2026-10-23"},
}
PUBLISHED_DOC = {**LOCK_DOC, "changed": LOCK_DOC["changed"][:1], "count": 1}


@pytest.fixture
def sd(tmp_path, monkeypatch):
    monkeypatch.setenv("NIXIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NIXIN_AUTHORITATIVE", str(tmp_path / "etc-nixos"))
    return tmp_path / "state"


@pytest.fixture
def fake(monkeypatch):
    """Records nixin and notify-send calls; `docs` maps a verb's flags to its document."""

    class Fake:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.notes: list[tuple] = []
            self.docs: dict[str, dict] = {"--published": PUBLISHED_DOC, "lock": LOCK_DOC}
            self.notify_ok = True

    f = Fake()

    def nixin(*args):
        f.calls.append(args)
        if args[0] == "outdated":
            return f.docs["--published" if "--published" in args else "lock"]
        return f.docs.get(args[0], {"ok": False, "error": {"code": "x", "message": "y"}})

    def notify(summary, body, timeout):
        f.notes.append((summary, body, timeout))
        return f.notify_ok

    monkeypatch.setattr(state, "nixin", nixin)
    monkeypatch.setattr(state, "notify", notify)
    return f


def summary(sd):
    return json.loads((sd / "summary.json").read_text())


def test_outdated_writes_documents_and_notifies_on_a_change(sd, fake):
    assert state.main(["outdated", "--timeout", "30000"]) == 0
    s = summary(sd)
    assert s["outdated"]["source"] == "lock" and s["outdated"]["count"] == 2  # "up" only
    assert s["outdated"]["kernel"] == {"from": "6.12.1", "to": "6.12.4"}
    assert s["published"]["count"] == 1 and s["errors"] == {}
    assert json.loads((sd / "outdated-lock.json").read_text())["count"] == 3
    assert fake.calls[0][-1] == "--published" and "--config" in fake.calls[1]
    assert fake.notes == [
        ("2 packages have newer versions", "kernel 6.12.1 → 6.12.4 · nixpkgs 4 days newer", "30000")
    ]
    assert s["notified"]["count"] == 2
    state.main(["outdated"])  # same count: no second notification
    assert len(fake.notes) == 1


def test_outdated_no_notify_and_quiet_hours(sd, fake, monkeypatch):
    state.main(["outdated", "--no-notify"])
    assert fake.notes == [] and "notified" not in summary(sd)
    hour = time.localtime().tm_hour
    state.main(["outdated", "--quiet-hours", f"{hour}-{(hour + 1) % 24}"])
    assert fake.notes == []  # held back, the count stays unannounced
    state.main(["outdated"])
    assert len(fake.notes) == 1


def test_outdated_keeps_the_last_good_numbers(sd, fake):
    state.main(["outdated", "--no-notify"])
    fake.docs = {
        "--published": {"ok": False, "error": {"code": "network", "message": "down"}},
        "lock": {"ok": False, "error": {"code": "eval-failed", "message": "boom"}},
    }
    assert state.main(["outdated"]) == 0
    s = summary(sd)
    assert s["outdated"]["count"] == 2 and s["errors"]["lock"]["code"] == "eval-failed"
    assert s["errors"]["published"]["code"] == "network" and fake.notes == []


def test_status_pending_part_and_skip(sd, fake, monkeypatch):
    fake.docs = {
        "status": {
            "ok": True,
            "pending": {
                "commits": [{"sha": "a"}],
                "dirty": ["installer/history.jsonl", "hosts/x.nix"],
                "untracked": ["new.nix"],
                "running_matches": False,
                "workspace": "/home/a/nixos",
            },
        }
    }
    monkeypatch.setattr(state, "trees_key", lambda ws, auth: "k1")
    assert state.main(["status"]) == 0
    p = summary(sd)["pending"]
    assert p["count"] == 2 and p["commits"] == 1 and p["dirty"] == 1 and p["untracked"] == 1
    assert p["apply"] is True
    n = len(fake.calls)
    state.main(["status"])  # nothing changed, younger than an hour: skipped
    assert len(fake.calls) == n
    state.main(["status", "--force"])
    assert len(fake.calls) == n + 1


def test_in_quiet_wraps_midnight():
    t = time.mktime((2026, 11, 2, 23, 30, 0, 0, 0, -1))
    assert state.in_quiet("23-8", t) and not state.in_quiet("9-17", t)
    assert not state.in_quiet(None, t) and not state.in_quiet("x", t)


def test_message_wording():
    assert state.message({"count": 1, "source": "lock"})[0] == "1 package has a newer version"
    assert state.message({"count": 0})[0] == "all packages are up to date"
    _head, body = state.message({"count": 3, "source": "published"})
    assert "search.nixos.org" in body


def test_report_renders_both_documents(tmp_path, capsys):
    lock, pub = tmp_path / "lock.json", tmp_path / "pub.json"
    lock.write_text(json.dumps(LOCK_DOC))
    pub.write_text(
        json.dumps(
            {
                "ok": True,
                "count": 1,
                "total": 340,
                "target": {"channel": "unstable", "short_rev": "abc"},
                "changed": [
                    {
                        "pname": "git",
                        "scope": "system",
                        "user": None,
                        "installed": "2.50",
                        "published": "2.52",
                        "attr": "git",
                    }
                ],
            }
        )
    )
    assert state.main(["report", str(lock), str(pub)]) == 0
    out = capsys.readouterr().out
    assert "**3 of 340 packages change**; kernel 6.12.1 → 6.12.4." in out
    assert "| git | 2.50 | 2.51 |" in out and "1 of them differ" in out


def test_nixin_runs_the_verb_in_process(monkeypatch):
    from nixin import cli

    seen = {}

    def document(ns):
        seen["verb"], seen["json"] = ns.verb, ns.json
        return {"ok": True}

    monkeypatch.setattr(cli, "document", document)
    doc = state.nixin("status", "--config", "/tmp/x")
    assert doc["ok"] and doc["verb"] == "status" and seen == {"verb": "status", "json": True}
    assert state.nixin("frobnicate")["error"]["code"] == "usage"


@pytest.mark.parametrize(
    "argv", [["--help"], ["outdated", "--help"], ["status", "-h"], ["report", "--help"]]
)
def test_help_prints_usage_and_runs_nothing(sd, fake, capsys, argv):
    # a review lane's `nixin-state outdated --help` ran the job and notified at 01:53
    assert state.main(argv) == 0
    out = capsys.readouterr().out
    assert "nixin-state outdated [--timeout MS]" in out
    assert fake.calls == [] and fake.notes == [] and not (sd / "summary.json").exists()


def test_unknown_subcommand_is_usage(sd, fake, capsys):
    assert state.main(["frobnicate"]) == 2
    assert "nixin-state status" in capsys.readouterr().err and fake.calls == []
