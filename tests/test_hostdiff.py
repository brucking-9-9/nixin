"""`list --diff A B` (truth/hostdiff.py), offline, on the two hosts' recorded package lists.

`tests/fixtures/hosts/<host>-packages.json` are truth/packages.nix's output for 19BK and
mbp13, evaluated from mbp13's `~/nixos` at a198757 (2026-11-02; same lock for both
hosts): 19BK has 203 system entries and the HM user brucking (192), mbp13 425 system
entries and no HM user. The fake nix answers the package-list eval per host from them
(`bump` changes a version, to have something that differs).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from nixin import configrepo
from nixin.cli import main
from nixin.configrepo import ConfigRepo
from nixin.truth import hostdiff

FIXTURES = Path(__file__).parent / "fixtures" / "hosts"
LISTS = {h: json.loads((FIXTURES / f"{h}-packages.json").read_text()) for h in ("19BK", "mbp13")}


class FakeNix:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.lists = copy.deepcopy(LISTS)

    def bump(self, host: str, pname: str, version: str) -> None:
        for p in self.lists[host]["system"]["packages"]:
            if p.get("pname") == pname:
                p["version"] = version
                p["name"] = f"{pname}-{version}"

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        if args[:2] == ["flake", "metadata"]:
            return json.dumps({"path": "/nix/store/aaaa-source", "fingerprint": "fp-1"})
        joined = " ".join(args)
        if joined.endswith("#nixosConfigurations --apply builtins.attrNames"):
            return '["19BK","mbp13"]'
        if "--apply" in args and "programsFromOptions" in args[-1]:
            attr = args[2].split("#", 1)[1]
            host = attr.removeprefix("nixosConfigurations.").strip('"')
            return json.dumps(self.lists[host])
        raise AssertionError(f"unexpected nix call: {args}")

    def package_evals(self) -> list[str]:
        return [
            c[2].split("#nixosConfigurations.", 1)[1]
            for c in self.calls
            if "--apply" in c and "programsFromOptions" in c[-1]
        ]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "flake.nix").write_text("{ }")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv(configrepo.ENV_CONFIG, raising=False)
    r = ConfigRepo(tmp_path, nix=FakeNix())
    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: r)
    )
    return r


def diff(capsys, *argv: str, host: str = "mbp13", json_: bool = True):
    rc = main([*(["--json"] if json_ else []), "--host", host, "list", "--diff", *argv])
    out, err = capsys.readouterr()
    if not json_:
        assert rc == 0, err
        return out
    assert err == ""
    doc = json.loads(out)
    assert doc["verb"] == "list" and doc["host"] == host
    assert rc == (0 if doc["ok"] else 2 if doc["error"]["code"] == "usage" else 1)
    return doc


def rows(d: dict, **match) -> list[dict]:
    return [r for r in d["packages"] if all(r[k] == v for k, v in match.items())]


def test_diff_contract_on_the_recorded_lists(repo, capsys):
    d = diff(capsys, "19BK", "mbp13")
    assert d["ok"] and (d["a"], d["b"]) == ("19BK", "mbp13")
    assert (d["scope"], d["users"], d["filter"]) == ("all", ["brucking"], None)
    assert d["scopes"] == [
        {
            "scope": "system",
            "user": None,
            "on": ["a", "b"],
            "only_a": 21,
            "only_b": 223,
            "differ": 0,  # one lock: no version differs
            "same": 114,
        },
        {
            "scope": "home",
            "user": "brucking",
            "on": ["a"],  # mbp13 has no Home Manager user
            "only_a": 190,  # 192 entries, two of them repeats
            "only_b": 0,
            "differ": 0,
            "same": 0,
        },
    ]
    assert d["counts"] == {"only_a": 211, "only_b": 223, "differ": 0, "same": 114}
    assert len(d["packages"]) == 211 + 223
    fd = rows(d, name="fd", scope="system")
    assert fd == [
        {"scope": "system", "user": None, "name": "fd", "state": "only_a", "a": ["10.5.0"], "b": []}
    ]
    assert rows(d, name="accountsservice")[0]["state"] == "only_b"
    # a package without pname is compared by its derivation name, version null
    assert rows(d, name="agent-run") == [
        {
            "scope": "system",
            "user": None,
            "name": "agent-run",
            "state": "only_b",
            "a": [],
            "b": [None],
        }
    ]
    # kitty: in mbp13's system packages, in brucking's home packages on 19BK
    assert [(r["scope"], r["user"], r["state"]) for r in rows(d, name="kitty")] == [
        ("system", None, "only_b"),
        ("home", "brucking", "only_a"),
    ]
    assert set(d["cache"]) == {"a", "b"} and d["cache"]["a"]["misses"] == ["packages"]
    assert "programs" not in d and d["warnings"] == []


def test_versions_that_differ_and_the_shared_cache(repo, capsys):
    repo.nix.bump("19BK", "git", "2.99.0")
    d = diff(capsys, "19BK", "mbp13")
    git = rows(d, name="git", scope="system")
    assert git == [
        {
            "scope": "system",
            "user": None,
            "name": "git",
            "state": "differ",
            "a": ["2.99.0"],
            "b": [rows(d, name="git")[0]["b"][0]],
        }
    ]
    assert git[0]["b"] != ["2.99.0"] and d["counts"]["differ"] == 1
    assert repo.nix.package_evals() == ['"19BK"', "mbp13"]
    # the same cache entry as `list`: no eval for either host now
    d = diff(capsys, "19BK", "mbp13")
    assert d["cache"]["a"]["hits"] == d["cache"]["b"]["hits"] == ["packages"]
    main(["--json", "--host", "mbp13", "list"])
    capsys.readouterr()
    assert len(repo.nix.package_evals()) == 2
    d = diff(capsys, "19BK", "mbp13", "--refresh")
    assert len(repo.nix.package_evals()) == 4 and d["cache"]["a"]["enabled"] is False


def test_one_host_defaults_to_host_and_the_order_flips(repo, capsys):
    d = diff(capsys, "19BK")  # a = --host (mbp13)
    assert (d["a"], d["b"]) == ("mbp13", "19BK")
    assert d["counts"] == {"only_a": 223, "only_b": 211, "differ": 0, "same": 114}
    assert d["scopes"][1]["on"] == ["b"]


def test_scope_home_filter_and_programs(repo, capsys):
    d = diff(capsys, "19BK", "mbp13", "--scope", "system")
    assert d["users"] == [] and [s["scope"] for s in d["scopes"]] == ["system"]
    d = diff(capsys, "19BK", "mbp13", "--home", "brucking")
    assert d["scope"] == "home" and [s["user"] for s in d["scopes"]] == ["brucking"]
    assert all(r["user"] == "brucking" for r in d["packages"])
    d = diff(capsys, "19BK", "mbp13", "--filter", "ZOX", "--programs")
    assert [(r["scope"], r["name"], r["state"]) for r in d["packages"]] == [
        ("system", "zoxide", "only_b"),
        ("home", "zoxide", "only_a"),
    ]
    assert [(p["scope"], p["user"], p["name"], p["state"]) for p in d["programs"]] == [
        ("system", None, "zoxide", "only_b"),
        ("home", "brucking", "zoxide", "only_a"),
    ]
    assert d["scopes"][0]["same"] == 0 and d["filter"] == "ZOX"


@pytest.mark.parametrize(
    "argv, code, text",
    [
        (["mbp13"], "usage", "both hosts are 'mbp13'"),
        (["19BK", "mbp13", "nas"], "usage", "--diff HOST_A HOST_B"),
        (["19BK", "nas"], "unknown-host", "'nas' is not in nixosConfigurations"),
        (["19BK", "mbp13", "--home", "bob"], "unknown-user", "neither 19BK nor mbp13"),
        (["19BK", "mbp13", "--home", "brucking", "--scope", "system"], "usage", "--home USER"),
    ],
)
def test_diff_errors(repo, capsys, argv, code, text):
    d = diff(capsys, *argv)
    assert d["error"]["code"] == code and text in d["error"]["message"]


def test_diff_human(repo, capsys, monkeypatch):
    repo.nix.bump("19BK", "git", "2.99.0")
    monkeypatch.setenv("NIXIN_WIDTH", "80")
    out = diff(capsys, "19BK", "mbp13", json_=False)
    lines = out.splitlines()
    assert lines[0].split() == ["scope", "name", "19BK", "mbp13"]
    assert "system  fd" in out and "10.5.0" in out
    git = next(ln for ln in lines if ln.startswith("system  git "))
    assert git.split()[2:4] == [
        "2.99.0",
        rows(diff(capsys, "19BK", "mbp13"), name="git")[0]["b"][0],
    ]
    assert "system  agent-run" in out and " yes" in next(ln for ln in lines if "agent-run" in ln)
    assert "(brucking)" not in out  # a user only one host has is summed up, not listed
    assert "system: 21 only on 19BK, 223 only on mbp13, 1 version differs, 113 the same" in out
    assert (
        "home brucking: only 19BK has this user (190 packages, none on mbp13; --home brucking "
        "lists them)" in out
    )
    assert re.fullmatch(
        r"19BK evaluated in \d+\.\d+ s · mbp13 evaluated in \d+\.\d+ s", lines[-1]
    ), lines[-1]  # cold
    assert all(len(ln) <= 80 for ln in lines if not ln.startswith("home brucking"))
    assert diff(capsys, "19BK", "mbp13", json_=False).splitlines()[-1] == (
        "19BK cached · mbp13 cached"
    )
    out = diff(capsys, "19BK", "mbp13", "--home", "brucking", "--filter", "kitty", json_=False)
    assert "home   kitty  0.49.2" in out.replace("  ", "  ") and "(brucking)" in out
    out = diff(capsys, "19BK", "mbp13", "--filter", "no-such-thing", json_=False)
    assert "no difference between 19BK and mbp13 matching 'no-such-thing'" in out
    monkeypatch.setenv("NIXIN_WIDTH", "40")
    out = diff(capsys, "19BK", "mbp13", "--scope", "system", "--filter", "akonadi", json_=False)
    assert "system  akonadi-import-w…  -     26.08.2" in out.splitlines()  # the name gives way
    assert max(len(ln) for ln in out.splitlines()[:6]) <= 40


def test_compare_versions_as_sets():
    a = [{"name": "py-3.12", "pname": "py", "version": "3.12"}]
    b = a + [{"name": "py-3.13", "pname": "py", "version": "3.13"}]
    found, same = hostdiff.compare(a, b, "")
    assert found == [{"name": "py", "state": "differ", "a": ["3.12"], "b": ["3.12", "3.13"]}]
    assert same == 0
    assert hostdiff.compare(a, a + a, "") == ([], 1)  # repeats fold
    assert hostdiff.compare(None, a, "")[0][0]["state"] == "only_b"
