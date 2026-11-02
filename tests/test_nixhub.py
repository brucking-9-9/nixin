import json

import pytest

from nixin.cache import Cache
from nixin.cli import main
from nixin.discovery import DiscoveryError, nixhub
from nixin.http import NetworkError

FIREFOX_REV = "8edc0c72e3a38faf5434e40d1f19431125fe4b30"


@pytest.fixture
def cache(cache_dir):
    return Cache()


@pytest.fixture
def nixhub_transport(transport):
    transport.add("GET", r"/v1/pkg\?name=firefox$", fixture_file="nixhub/pkg_firefox.json")
    transport.add("GET", r"/v1/pkg\?name=gone$", None, status=404)
    transport.add("GET", r"/v1/pkg\?name=bad$", None, status=400)
    transport.add("GET", r"/v1/pkg\?name=empty$", [])
    transport.add("GET", r"/v1/pkg\?name=down$", None, status=503)
    return transport


def test_versions_records(nixhub_transport, cache):
    part = nixhub.versions(cache, "firefox", 10)
    assert (
        part.source == "nixhub.io"
        and part.channel is None
        and part.rev is None
        and part.cached is False
    )
    assert len(part.results) == 3 and part.extra["total"] == 3
    first = part.results[0]
    assert first == {
        "kind": "version",
        "source": "nixhub.io",
        "channel": None,
        "rev": FIREFOX_REV,
        "name": "firefox",
        "version": "157.0.1",
        "date": "2026-10-08",
        "attr": "firefox",
        "platforms": ["aarch64-darwin", "aarch64-linux", "x86_64-linux"],
        "programs": ["firefox"],
    }
    assert [r["version"] for r in part.results] == ["157.0.1", "157.0", "154.0.1"]
    assert part.extra["latest"] == "157.0.1" and part.extra["license"] == "MPL-2.0"
    assert part.extra["summary"].startswith("Web browser") and part.extra["homepage"].startswith(
        "http"
    )
    assert part.extra["programs"] == ["firefox"]


def test_versions_limit_and_cache(nixhub_transport, cache):
    assert len(nixhub.versions(cache, "firefox", 1).results) == 1
    assert nixhub.versions(cache, "firefox", 1).extra["total"] == 3
    assert nixhub.versions(cache, "firefox", 2).cached is True
    assert nixhub_transport.count("/v1/pkg") == 1


@pytest.mark.parametrize("name", ["gone", "bad", "empty"])
def test_versions_not_found(nixhub_transport, cache, name):
    with pytest.raises(DiscoveryError) as e:
        nixhub.versions(cache, name, 5)
    assert e.value.code == "not-found" and name in e.value.message


def test_versions_5xx_is_network(nixhub_transport, cache):
    with pytest.raises(NetworkError):
        nixhub.versions(cache, "down", 5)


def test_record_prefers_x86_64_linux_and_validates_hash():
    rel = {
        "name": "x",
        "version": "1",
        "commit_hash": "not-a-hash",
        "last_updated": "2026-10-08T12:00:00Z",
        "platforms": ["b", "a"],
        "systems": {
            "aarch64-darwin": {"attr_paths": ["x-darwin"], "programs": ["xd"]},
            "x86_64-linux": {"attr_paths": ["x-linux", "x2"], "programs": ["x"]},
        },
    }
    rec = nixhub.record(rel)
    assert rec["rev"] is None and rec["attr"] == "x-linux" and rec["programs"] == ["x"]
    assert rec["date"] == "2026-10-08" and rec["platforms"] == ["a", "b"]
    assert (
        nixhub.record({"version": "2", "systems": {"riscv64-linux": {"attr_paths": ["r"]}}})["attr"]
        == "r"
    )
    assert nixhub.record({"version": "3"})["attr"] is None


@pytest.mark.parametrize(
    "value, date",
    [
        (1791487292, "2026-10-08"),
        (1791487292.5, "2026-10-08"),
        ("2026-10-08T12:00:00Z", "2026-10-08"),
        ("yesterday", None),
        (None, None),
        (10**20, None),
    ],
)
def test_date(value, date):
    assert nixhub._date(value) == date


def test_cli_versions_json(nixhub_transport, cache_dir, capsys):
    rc = main(["--json", "versions", "firefox", "--limit", "2"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] is True and doc["verb"] == "versions"
    assert (
        doc["package"] == "firefox"
        and doc["limit"] == 2
        and doc["total"] == 3
        and doc["latest"] == "157.0.1"
    )
    assert (
        doc["source"] == "nixhub.io"
        and doc["channel"] is None
        and doc["rev"] is None
        and doc["cached"] is False
    )
    assert doc["count"] == 2 and doc["results"][0]["rev"] == FIREFOX_REV
    assert (
        doc["sources"]["nixhub.io"]["kinds"] == ["version"]
        and doc["sources"]["nixhub.io"]["total"] == 3
    )


def test_cli_versions_not_found(nixhub_transport, cache_dir, capsys):
    rc = main(["--json", "versions", "gone"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["error"]["code"] == "not-found"


def test_cli_versions_human(nixhub_transport, cache_dir, capsys, monkeypatch):
    monkeypatch.setenv("NIXIN_WIDTH", "120")
    assert main(["versions", "firefox", "--limit", "2"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("firefox · Web browser") and lines[0].endswith("2 of 3 versions")
    assert lines[1].startswith("VERSION") and lines[2].startswith(
        "157.0.1  2026-10-08  8edc0c72e3a3  firefox"
    )
    assert lines[-1] == "# 2 result(s) · nixhub.io"
