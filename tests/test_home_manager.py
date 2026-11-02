import pytest

from nixin.cache import Cache
from nixin.discovery import DiscoveryError
from nixin.discovery import home_manager as hm
from nixin.http import NetworkError

KITTY_URL = "https://github.com/nix-community/home-manager/blob/master/modules/programs/kitty.nix"


@pytest.fixture
def cache(cache_dir):
    return Cache()


@pytest.fixture
def hm_transport(transport):
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    transport.add("GET", r"/data/options-master\.json$", fixture_file="hm/options-master.json")
    transport.add(
        "GET", r"/data/options-release-26\.05\.json$", fixture_file="hm/options-master.json"
    )
    transport.add("GET", r"/data/options-release-99\.99\.json$", None, status=404)
    return transport


@pytest.mark.parametrize(
    "channel, expected",
    [
        ("unstable", ("unstable", "master")),
        ("25.11", ("25.11", "release-25.11")),
        ("nixos-25.11", ("25.11", "release-25.11")),
        ("github:nixos/nixpkgs/nixos-25.11", ("25.11", "release-25.11")),
        ("master", ("unstable", "master")),
    ],
)
def test_release_for(hm_transport, cache, channel, expected):
    assert hm.release_for(cache, channel) == expected


def test_release_for_stable_asks_search_nixos_org(hm_transport, cache):
    assert hm.release_for(cache, "stable") == ("26.05", "release-26.05")
    assert hm_transport.count("_cat/aliases") == 1


def test_release_for_rev_is_bad_channel(hm_transport, cache):
    with pytest.raises(DiscoveryError) as e:
        hm.release_for(cache, "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504")
    assert e.value.code == "bad-channel"


def test_index_url():
    assert (
        hm.index_url("master")
        == "https://home-manager-options.extranix.com/data/options-master.json"
    )


def test_load_caches_index(hm_transport, cache):
    index, cached, name = hm.load(cache, "unstable")
    assert cached is False and name == "unstable"
    assert index["last_update"] and len(index["options"]) == 5
    _i, cached, _n = hm.load(cache, "unstable")
    assert cached is True and hm_transport.count("options-master.json") == 1


def test_load_missing_release_is_bad_channel(hm_transport, cache):
    with pytest.raises(DiscoveryError) as e:
        hm.load(cache, "99.99")
    assert e.value.code == "bad-channel" and "99.99" in e.value.message


def test_load_offline_is_network(transport, cache):
    with pytest.raises(NetworkError):
        hm.load(cache, "unstable")


def test_search_prefix_query(hm_transport, cache):
    part = hm.search(cache, "programs.kitty", "unstable", 10)
    assert part.source == "home-manager-options" and part.channel == "unstable" and part.rev is None
    assert [r["name"] for r in part.results] == [
        "programs.kitty.enable",
        "programs.kitty.settings",
        "programs.kitty.themeFile",
    ]
    assert part.extra["updated"] == hm.load(cache, "unstable")[0]["last_update"]


def test_search_segment_query_and_limit(hm_transport, cache):
    names = [r["name"] for r in hm.search(cache, "git", "unstable", 10).results]
    assert names[0] == "programs.git.enable"
    assert "programs.kitty.enable" not in names
    assert len(hm.search(cache, "programs", "unstable", 2).results) == 2
    assert hm.search(cache, "zzz-nothing", "unstable", 5).results == []


def test_record_fields(hm_transport, cache):
    (rec,) = hm.info(cache, "programs.kitty.enable", "unstable").results
    assert rec == {
        "kind": "hm",
        "source": "home-manager-options",
        "channel": "unstable",
        "rev": None,
        "name": "programs.kitty.enable",
        "type": "boolean",
        "default": "false",
        "example": "true",
        "description": "Whether to enable Kitty terminal emulator.",
        "declarations": ["modules/programs/kitty.nix"],
        "declaration_urls": [KITTY_URL],
        "read_only": False,
    }
    (settings,) = hm.info(cache, "programs.kitty.settings", "unstable").results
    assert settings["type"].startswith("attribute set of") and settings["default"] == "{ }"
    assert settings["example"].startswith("{") and "scrollback_lines" in settings["example"]


def test_info_miss(hm_transport, cache):
    assert hm.info(cache, "programs.kitty.nope", "unstable").results == []


@pytest.mark.parametrize(
    "name, desc, query, expected",
    [
        ("programs.git.enable", "", "programs.git.enable", 100),
        ("programs.git.enable", "", "PROGRAMS.GIT.ENABLE", 100),
        ("programs.git.enable", "", "programs.git", 80),
        ("programs.git.enable", "", "git", 69),
        ("programs.difftastic.git.enable", "", "git", 68),
        ("programs.bun.enableGitIntegration", "", "git", 60),
        ("programs.foo.enable", "Whether to enable Git integration", "git", 20),
        ("programs.foo.enable", "nothing", "git", 0),
        ("programs.kitty.settings", "Configuration written", "kitty settings", 68),
        ("programs.kitty.settings", "Configuration written", "kitty nope", 0),
        ("x", "y", "", 0),
    ],
)
def test_score(name, desc, query, expected):
    assert hm.score(name, desc, query) == expected
