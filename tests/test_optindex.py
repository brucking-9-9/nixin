"""The option indexes nixin's modules build (optindex), offline: the recorded indexes under
tests/fixtures/optindex/ (scripts/record-index-fixtures.sh made them from the module
tests' configs), installed into a fake machine in a tmp dir (a store, the system profile
and /run/current-system, Home Manager's gcroot and profile), then read, checked against
the running generation, searched, and used by search/info/where/status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_truth import FakeNix, repo  # noqa: F401 -- `repo` is a fixture

from nixin import configrepo, optindex
from nixin.apply import system
from nixin.cli import main

FIX = Path(__file__).parent / "fixtures" / "optindex"


def compact(doc) -> str:
    """As builtins.toJSON writes it: no spaces, keys sorted."""
    return json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


class Machine:
    """A tmp dir laid out like this machine: a store, system generations 6 and 7 (7 in
    the profile), /run/current-system, a home with Home Manager generations 2 and 3."""

    def __init__(self, tmp: Path, monkeypatch) -> None:
        self.tmp, self.mp = tmp, monkeypatch
        self.store = tmp / "store"
        self.store.mkdir()
        self.profiles = tmp / "profiles"
        self.profiles.mkdir()
        self.home = tmp / "home"
        self.state = self.home / ".local" / "state"
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(self.home / ".config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(self.state))
        monkeypatch.setattr(system, "CURRENT", str(tmp / "current-system"))
        monkeypatch.setattr(system, "PROFILE", str(self.profiles / "system"))
        monkeypatch.setattr(optindex, "hostname", lambda: "mbp13")  # not the build sandbox's
        optindex._LOADED.clear()

    def blob(self, scope: str, name: str, **header) -> Path:
        doc = json.loads((FIX / f"options-{scope}.json").read_text())
        doc.update(header)
        p = self.store / f"{name}-options-{scope}.json"
        p.write_text(compact(doc))
        return p

    def nixos(self, *, host="mbp13", running=7, with_index=(7,)) -> Path:
        """System generations 6 and 7; the ones in `with_index` carry an index in etc."""
        blob = self.blob("nixos", "sys", host=host)
        for n in (6, 7):
            gen = self.store / f"nixos-system-{n}"
            (gen / "etc" / "nixin").mkdir(parents=True)
            if n in with_index:
                (gen / "etc" / "nixin" / "options-nixos.json").symlink_to(blob)
            (self.profiles / f"system-{n}-link").symlink_to(gen)
        (self.profiles / "system").symlink_to("system-7-link")
        (self.tmp / "current-system").symlink_to(self.store / f"nixos-system-{running}")
        etc = self.tmp / "etc" / "nixin" / "options-nixos.json"  # /etc → /etc/static → gen
        etc.parent.mkdir(parents=True)
        etc.symlink_to(blob)
        self.mp.setenv(optindex.ENV_NIXOS, str(etc))
        return etc

    def hm(self, *, user="alice", host=None, active=3) -> Path:
        """Home Manager generations 2 and 3, both with the same index; `active` is the
        gcroot's (None: no activation on record)."""
        blob = self.blob("home", f"hm-{user}", user=user, host=host)
        gens = {}
        for n in (2, 3):
            gen = self.store / f"home-manager-generation-{n}"
            f = gen / "home-files" / ".config" / "nixin" / "options-home.json"
            f.parent.mkdir(parents=True)
            if n == 3:
                f.symlink_to(blob)
            gens[n] = gen
            prof = self.state / "nix" / "profiles"
            prof.mkdir(parents=True, exist_ok=True)
            (prof / f"home-manager-{n}-link").symlink_to(gen)
        if active is not None:
            root = self.state / "home-manager" / "gcroots"
            root.mkdir(parents=True)
            (root / "current-home").symlink_to(gens[active])
        link = self.home / ".config" / "nixin" / "options-home.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(gens[3] / "home-files" / ".config" / "nixin" / "options-home.json")
        self.mp.delenv(optindex.ENV_HOME, raising=False)
        return link


@pytest.fixture
def machine(tmp_path, monkeypatch, cache_dir):
    return Machine(tmp_path, monkeypatch)


# --- reading ------------------------------------------------------------------------------


def test_fixtures_are_what_the_modules_write():
    for scope in optindex.SCOPES:
        text = (FIX / f"options-{scope}.json").read_text()
        doc = json.loads(text)
        assert text.rstrip("\n") == compact(doc)  # builtins.toJSON's form: peek() reads it
        assert doc["format"] == optindex.FORMAT and doc["scope"] == scope
        assert doc["count"] == len(doc["options"]) > 50
        for o in doc["options"]:
            assert {"name", "loc", "type", "declarations", "readOnly"} <= set(o)
            assert "_module" not in o["loc"]


def test_peek_reads_the_header_without_the_list(tmp_path):
    doc = json.loads((FIX / "options-nixos.json").read_text())
    want = {k: v for k, v in doc.items() if k != "options"}
    assert optindex.peek(FIX / "options-nixos.json") == want
    pretty = tmp_path / "pretty.json"  # another writer's layout: read whole instead
    pretty.write_text(json.dumps(doc, indent=2))
    assert optindex.peek(pretty) == want
    other = tmp_path / "other.json"
    other.write_text('{"options": []}')
    with pytest.raises(ValueError):
        optindex.peek(other)


def test_nixos_index_of_the_running_generation(machine):
    machine.nixos()
    idx = optindex.load("nixos")
    assert idx is not None and idx.scope == "nixos" and idx.host == "mbp13"
    assert idx.generation["kind"] == "system" and idx.generation["number"] == 7
    assert idx.generation["fresh"] is True and idx.generation["date"]
    assert idx.generation["path"].endswith("nixos-system-7")
    assert idx.rev == "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
    assert idx.applies("mbp13") and not idx.applies("19BK")
    assert optindex.load("nixos") is idx  # parsed once per process
    assert optindex.load("home") is None


def test_nixos_index_of_another_generation_is_not_fresh(machine):
    machine.nixos(running=6)  # booted into 6, whose etc has no index; /etc still has 7's
    idx = optindex.load("nixos")
    assert idx.generation["fresh"] is False and idx.generation["number"] == 7


def test_home_index_and_its_generation(machine):
    machine.hm()
    idx = optindex.load("home")
    assert idx.scope == "home" and idx.user == "alice" and idx.host is None
    g = idx.generation
    assert g["kind"] == "home-manager" and g["number"] == 3 and g["fresh"] is True
    assert idx.applies(optindex.hostname(), "alice") and not idx.applies("elsewhere", "alice")
    assert not idx.applies(optindex.hostname(), "bob")


def test_home_index_states(machine):
    machine.hm(active=2)  # the active generation's home-files has no index
    assert optindex.load("home").generation["fresh"] is False


def test_home_index_without_an_activation_record(machine):
    machine.hm(active=None)
    g = optindex.load("home").generation
    assert g["fresh"] is None and g["number"] is None


def test_off_switch_and_missing_files(machine, monkeypatch):
    machine.nixos()
    monkeypatch.setenv(optindex.ENV_OFF, "1")
    assert optindex.load("nixos") is None
    monkeypatch.setenv(optindex.ENV_OFF, "0")
    assert optindex.load("nixos") is not None
    monkeypatch.setenv(optindex.ENV_NIXOS, str(machine.tmp / "none.json"))
    assert optindex.load("nixos") is None


def test_lookup_covers_and_search(machine):
    machine.nixos()
    idx = optindex.load("nixos")
    assert idx.lookup("services.openssh.enable")["type"] == "boolean"
    assert idx.covers(["services", "openssh", "enable"])
    assert idx.covers(["services", "openssh"])  # a prefix
    assert idx.covers(["systemd", "services", "foo", "enable"])  # inside an attrsOf option
    assert not idx.covers(["programs", "kitty", "enable"])
    hits = idx.search("openssh", 5)
    assert hits[0]["name"].startswith("services.openssh.") and len(hits) == 5
    assert idx.search("services.openssh.enable", 3)[0]["name"] == "services.openssh.enable"
    depth = [len(o["loc"]) for o in idx.search("openssh", 80)]
    assert depth == sorted(depth)  # one score for all: the shallower first
    assert not idx.search("bootMount", 5)  # hidden (an alias): left out, as upstream does
    assert idx.lookup("boot.bootMount") is not None  # but there for info/where


def test_records_and_links(machine):
    machine.nixos()
    idx = optindex.load("nixos")
    r = idx.record(idx.lookup("services.openssh.enable"))
    assert r["kind"] == "opt" and r["source"] == "index" and r["channel"] is None
    assert r["rev"] == idx.rev and r["default"] == "false" and r["read_only"] is False
    assert r["declarations"] == ["nixos/modules/services/networking/ssh/sshd.nix"]
    assert r["declaration_urls"] == [
        (
            "https://github.com/NixOS/nixpkgs/blob/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504/"
            "nixos/modules/services/networking/ssh/sshd.nix#L247"
        )
    ]
    assert r["in_config"] is False and r["generation"]["number"] == 7
    own = idx.record(idx.lookup("nixinTest.greeting"))
    assert own["in_config"] is True and own["declaration_urls"] == []
    assert own["locations"] == [{"source": "config", "file": "nixos-config.nix", "line": 5}]
    assert own["example"] == '"hej"'
    mine = idx.record(idx.lookup("programs.nixin.enable"))
    assert mine["locations"][0]["source"] == "nixin"


def test_home_records_link_home_manager(machine):
    machine.hm()
    idx = optindex.load("home")
    r = idx.record(idx.lookup("programs.git.enable"))
    assert r["kind"] == "hm" and r["user"] == "alice"
    assert r["declarations"] == ["modules/programs/git.nix"]
    assert r["declaration_urls"][0].startswith(
        "https://github.com/nix-community/home-manager/blob/master/modules/programs/git.nix#L"
    )


# --- search / info ------------------------------------------------------------------------


def run(capsys, *argv):
    rc = main(["--json", *argv])
    out, err = capsys.readouterr()
    assert err == ""
    return rc, json.loads(out)


def test_search_opt_answers_from_the_index(machine, transport, capsys):
    machine.nixos()
    rc, doc = run(capsys, "--host", "mbp13", "search", "--kind", "opt", "openssh", "--limit", "3")
    assert rc == 0 and doc["source"] == "index" and doc["channel"] is None
    assert doc["count"] == 3 and all(r["source"] == "index" for r in doc["results"])
    src = doc["sources"]["index"]
    assert src["kinds"] == ["opt"] and src["indexes"]["nixos"]["generation"]["number"] == 7
    assert not transport.calls  # no network


def test_search_falls_back_upstream(machine, es_transport, capsys):
    machine.nixos()
    for extra in (["--no-index"], ["--channel", "unstable"]):
        rc, doc = run(
            capsys, "--host", "mbp13", "search", "--kind", "opt", "services.openssh", *extra
        )
        assert rc == 0 and doc["source"] == "search.nixos.org", extra
    rc, doc = run(capsys, "--host", "19BK", "search", "--kind", "opt", "services.openssh")
    assert doc["source"] == "search.nixos.org"  # mbp13's index says nothing about 19BK
    rc, doc = run(capsys, "--host", "mbp13", "search", "--kind", "opt", "zzzunknownzzz")
    assert doc["source"] == "search.nixos.org"
    assert "nothing in this machine's option index" in " ".join(doc["notes"])


def test_search_hm_and_all(machine, transport, capsys, monkeypatch):
    machine.nixos()
    machine.hm()
    monkeypatch.setattr(optindex, "current_user", lambda: "alice")
    rc, doc = run(capsys, "--host", "mbp13", "search", "--kind", "hm", "git", "--limit", "2")
    assert doc["source"] == "index" and doc["results"][0]["name"].startswith("programs.git")
    assert doc["results"][0]["kind"] == "hm"
    rc, doc = run(capsys, "--host", "mbp13", "info", "--kind", "opt", "services.openssh.enable")
    assert rc == 0 and doc["results"][0]["source"] == "index" and not transport.calls
    monkeypatch.setattr(optindex, "current_user", lambda: "bob")  # alice's index, not bob's
    rc, doc = run(capsys, "--host", "mbp13", "search", "--kind", "hm", "git")
    assert rc == 1 and doc["error"]["code"] == "network"


def test_index_info_for_other_verbs(machine):
    import argparse

    from nixin import discovery

    machine.nixos()
    ns = argparse.Namespace(host="mbp13", channel=None)  # explain's namespace has no more
    part = discovery.index_info(ns, "opt", "services.openssh.enable")
    assert part.source == "index" and part.results[0]["generation"]["number"] == 7
    assert discovery.index_info(ns, "opt", "no.such.option").results == []
    assert discovery.index_info(ns, "hm", "programs.git.enable") is None  # no HM index
    ns.channel = "25.05"  # asked for a channel: upstream's answer, not this generation's
    assert discovery.index_info(ns, "opt", "services.openssh.enable") is None


def test_info_all_mixes_index_and_upstream(machine, es_transport, capsys):
    machine.nixos()
    rc, doc = run(capsys, "--host", "mbp13", "info", "services.openssh.enable")
    assert rc == 0
    opt = [r for r in doc["results"] if r["kind"] == "opt"]
    assert opt and opt[0]["source"] == "index"
    assert "index" in doc["sources"] and doc["sources"]["index"]["kinds"] == ["opt"]


def test_human_search_from_the_index(machine, transport, capsys, monkeypatch):
    machine.nixos()
    monkeypatch.setenv("NIXIN_WIDTH", "100")
    assert main(["--host", "mbp13", "search", "--kind", "opt", "openssh", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "services.openssh" in out and "# 2 result(s) · index (nixos generation 7)" in out
    assert main(["--host", "mbp13", "info", "--kind", "opt", "services.openssh.enable"]) == 0
    out = capsys.readouterr().out
    assert "nixpkgs nixos/modules/services/networking/ssh/sshd.nix:247" in out
    assert "system 7 (" in out and "running" in out


# --- where / status (truth, on the fake nix of test_truth) ---------------------------------


@pytest.fixture
def truth(repo, monkeypatch):  # noqa: F811 -- the imported fixture
    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: repo)
    )
    return repo


def test_where_takes_docs_from_the_index_and_skips_scopes(machine, truth, capsys, monkeypatch):
    machine.nixos(host="19BK")
    machine.hm(user="brucking", host="19BK")
    monkeypatch.setattr(optindex, "current_user", lambda: "brucking")
    name = "services.openssh.settings.PermitRootLogin"
    rc, doc = run(capsys, "--host", "19BK", "where", name)
    assert rc == 0 and [s["scope"] for s in doc["scopes"]] == ["nixos"]
    o = doc["scopes"][0]["options"][0]
    assert o["value"] == "no"  # values still from the eval
    assert o["docs"]["source"] == "index" and o["docs"]["type"] == o["type"]
    assert o["docs"]["declarations"][0]["url"].endswith(
        "#L" + str(o["docs"]["declarations"][0]["line"])
    )
    assert [i["scope"] for i in doc["index"]] == ["nixos"]
    assert doc["skipped_scopes"] == [
        {
            "scope": "home",
            "user": "brucking",
            "reason": doc["skipped_scopes"][0]["reason"],
        }
    ]
    assert "generation 3" in doc["skipped_scopes"][0]["reason"]
    hm_calls = [c for c in truth.nix.calls if 'hm = "brucking"' in " ".join(c)]
    assert not hm_calls  # the Home Manager prefix eval did not run


def test_where_evaluates_every_scope_without_fresh_indexes(machine, truth, capsys, monkeypatch):
    machine.nixos(host="19BK", running=6)  # an index, but not the running generation's
    machine.hm(user="brucking", host="19BK")
    monkeypatch.setattr(optindex, "current_user", lambda: "brucking")
    rc, doc = run(capsys, "--host", "19BK", "where", "services.openssh.settings.PermitRootLogin")
    assert rc == 0 and doc["skipped_scopes"] == []
    assert [c for c in truth.nix.calls if 'hm = "brucking"' in " ".join(c)]
    assert doc["scopes"][0]["options"][0]["docs"]["source"] == "index"  # docs still apply


def test_where_without_indexes_is_unchanged(truth, capsys):
    rc, doc = run(capsys, "--host", "19BK", "where", "services.openssh.settings.PermitRootLogin")
    assert rc == 0 and doc["index"] == [] and doc["skipped_scopes"] == []
    assert "docs" not in doc["scopes"][0]["options"][0]


def test_where_human_shows_default(machine, truth, capsys, monkeypatch):
    machine.nixos(host="19BK")
    assert main(["--host", "19BK", "where", "services.openssh.enable"]) == 0
    out = capsys.readouterr().out
    assert "default" in out and "false" in out


def test_status_reports_the_indexes(machine, truth, capsys, monkeypatch):
    machine.nixos(host="19BK")
    machine.hm(user="brucking", host="19BK")
    monkeypatch.setattr(optindex, "current_user", lambda: "brucking")
    monkeypatch.setattr(configrepo, "run_nix", truth.nix)
    rc, doc = run(capsys, "--host", "19BK", "status")
    assert rc == 0
    ix = doc["indexes"]
    assert ix["nixos"]["present"] and ix["nixos"]["fresh"] and ix["nixos"]["applies"]
    assert ix["nixos"]["count"] == 145 and ix["nixos"]["generation"]["number"] == 7
    assert ix["home"]["present"] and ix["home"]["user"] == "brucking" and ix["home"]["applies"]
    rc, doc = run(capsys, "--host", "19BK", "status")
    assert main(["--host", "19BK", "status"]) == 0
    out = capsys.readouterr().out
    assert "indexes" in out and "145 options (generation 7, running)" in out


def test_status_without_indexes(truth, capsys, monkeypatch):
    monkeypatch.setattr(configrepo, "run_nix", truth.nix)
    rc, doc = run(capsys, "--host", "19BK", "status")
    assert rc == 0 and doc["indexes"]["nixos"]["present"] is False
    assert doc["indexes"]["home"]["present"] is False and "user" in doc["indexes"]["home"]
    monkeypatch.setenv(optindex.ENV_OFF, "1")
    rc, doc = run(capsys, "--host", "19BK", "status")
    assert doc["indexes"]["nixos"]["disabled"] is True
