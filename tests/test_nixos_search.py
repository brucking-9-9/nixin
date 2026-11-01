import pytest

from nixin.cache import Cache
from nixin.discovery import DiscoveryError
from nixin.discovery import nixos_search as es
from nixin.http import NetworkError

REV_UNSTABLE = "1c89083e451e891152365b799595cf7790a43325"


@pytest.fixture
def cache(cache_dir):
    return Cache()


@pytest.mark.parametrize(
    "ref, channel",
    [
        ("nixos-unstable", "unstable"),
        ("nixpkgs-unstable", "unstable"),
        ("nixos-unstable-small", "unstable"),
        ("master", "unstable"),
        ("unstable", "unstable"),
        ("stable", "stable"),
        ("25.11", "25.11"),
        ("nixos-25.11", "25.11"),
        ("release-25.11", "25.11"),
        ("nixpkgs-25.11-darwin", "25.11"),
        ("nixos-25.11-small", "25.11"),
        ("github:nixos/nixpkgs/nixos-25.11", "25.11"),
        ("github:NixOS/nixpkgs?ref=nixos-25.11", "25.11"),
        ("github:nixos/nixpkgs", "unstable"),
        ("nixpkgs", "unstable"),
        ("nixpkgs/nixos-26.05", "26.05"),
        ("flake:nixpkgs/nixos-26.05", "26.05"),
        ("https://github.com/nixos/nixpkgs/archive/nixos-25.11.tar.gz", None),
        ("github:nixos/nixpkgs/3ec636362e1816bc2f0888fa0fa2c69f1bc9e504", None),
        ("github:nixos/nixpkgs?rev=3ec636362e1816bc2f0888fa0fa2c69f1bc9e504", None),
        ("3ec636362e1816bc2f0888fa0fa2c69f1bc9e504", None),
        ("/etc/nixos", None),
        ("path:/etc/nixos", None),
        ("someone/else", None),
        ("", None),
        ("   ", None),
    ],
)
def test_channel_from_ref(ref, channel):
    assert es.channel_from_ref(ref) == channel


def test_channels_pick_newest_generation_and_rev(es_transport, cache):
    by_name, cached = es.channels(cache)
    assert cached is False
    assert set(by_name) == {"unstable", "25.11", "26.05"}
    unstable = by_name["unstable"]
    assert [c.alias for c in unstable] == ["latest-52-nixos-unstable", "latest-51-nixos-unstable"]
    assert unstable[0].rev == REV_UNSTABLE
    assert unstable[0].generation == 52
    # second call is served from the on-disk cache
    _, cached = es.channels(cache)
    assert cached is True
    assert es_transport.count("_cat/aliases") == 1


def test_resolve_channel_names(es_transport, cache):
    assert es.resolve_channel(cache, "unstable").alias == "latest-52-nixos-unstable"
    assert es.resolve_channel(cache, "stable").name == "26.05"
    assert es.resolve_channel(cache, "25.11").alias == "latest-48-nixos-25.11"
    assert es.resolve_channel(cache, "nixos-25.11").name == "25.11"
    assert es.resolve_channel(cache, "github:nixos/nixpkgs/nixos-unstable").name == "unstable"


def test_resolve_channel_errors(es_transport, cache):
    with pytest.raises(DiscoveryError) as e:
        es.resolve_channel(cache, "99.99")
    assert e.value.code == "bad-channel"
    assert e.value.message.endswith("known: unstable, stable, 25.11, 26.05")
    with pytest.raises(DiscoveryError) as e:
        es.resolve_channel(cache, "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504")
    assert e.value.code == "bad-channel" and "commit" in e.value.message


def test_resolve_channel_skips_empty_newest_generation(transport, cache):
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/nixos-52-unstable-[0-9a-f]{40}/_count$", {"count": 0})
    transport.add("POST", r"/nixos-51-unstable-[0-9a-f]{40}/_count$", {"count": 5})
    assert es.resolve_channel(cache, "unstable").alias == "latest-51-nixos-unstable"


def test_resolve_channel_404_count_means_empty(transport, cache):
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/nixos-52-unstable-[0-9a-f]{40}/_count$", None, status=404)
    transport.add("POST", r"/nixos-51-unstable-[0-9a-f]{40}/_count$", {"count": 5})
    assert es.resolve_channel(cache, "unstable").alias == "latest-51-nixos-unstable"


def test_no_aliases_is_a_network_error(transport, cache):
    transport.add("GET", r"/_cat/aliases", [])
    with pytest.raises(DiscoveryError) as e:
        es.channels(cache)
    assert e.value.code == "network"


def test_aliases_offline_is_network(transport, cache):
    with pytest.raises(NetworkError):
        es.channels(cache)


def test_search_packages(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    part = es.search(cache, "firefox", "pkg", ch, 4)
    assert (
        part.source == "search.nixos.org"
        and part.channel == "unstable"
        and part.rev == REV_UNSTABLE
    )
    assert [r["name"] for r in part.results] == [
        "firefox",
        "firefox-unwrapped",
        "firefox-esr-153-unwrapped",
        "firefox-esr",
    ]
    ff = part.results[0]
    assert ff["kind"] == "pkg" and ff["version"] == "159.0" and ff["pname"] == "firefox"
    assert ff["homepage"].startswith("http") and ff["license"] == ["MPL-2.0"]
    assert ff["programs"] == ["firefox"] and ff["main_program"] == "firefox"
    assert ff["outputs"] == ["out"] and isinstance(ff["platforms"], list)
    assert ff["position"].endswith(".nix:" + ff["position"].rsplit(":", 1)[1])
    assert ff["unfree"] is None and ff["source"] == "search.nixos.org"
    # the request body is the frontend-style query
    _m, _u, body = es_transport.calls[-1]
    assert body["size"] == 4 and body["_source"] == es.PKG_SOURCE_FIELDS and "rescore" in body
    assert body["query"]["bool"]["filter"] == [{"term": {"type": "package"}}]


def test_search_asks_the_concrete_index_not_the_alias(es_transport, cache):
    # the alias moves to a newer rev between alias-list refreshes; the index name is the rev
    ch = es.resolve_channel(cache, "unstable")
    part = es.search(cache, "firefox", "pkg", ch, 4)
    _m, url, _b = es_transport.calls[-1]
    assert url.endswith(f"/nixos-52-unstable-{REV_UNSTABLE}/_search")
    assert all(r["rev"] == REV_UNSTABLE for r in part.results)
    assert not any("latest-" in u for _m, u, _b in es_transport.calls)


def test_search_is_cached(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    assert es.search(cache, "firefox", "pkg", ch, 4).cached is False
    assert es.search(cache, "firefox", "pkg", ch, 4).cached is True
    assert es_transport.count("_search") == 1
    assert (
        es.search(cache, "firefox", "pkg", ch, 3).cached is False
    )  # different body, different key


def test_search_options(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    part = es.search(cache, "services.openssh", "opt", ch, 3)
    names = [r["name"] for r in part.results]
    assert names[0] == "services.openssh.allowSFTP" and len(names) == 3
    opt = part.results[0]
    assert opt["kind"] == "opt" and opt["type"] == "boolean" and opt["default"] == "true"
    assert "<" not in opt["description"] and opt["description"].startswith(
        "Whether to enable the SFTP"
    )
    assert opt["declarations"] == ["nixos/modules/services/networking/ssh/sshd.nix"]
    assert opt["declaration_urls"] == [
        f"https://github.com/NixOS/nixpkgs/blob/{REV_UNSTABLE}/nixos/modules/services/networking/ssh/sshd.nix"
    ]
    _m, _u, body = es_transport.calls[-1]
    assert (
        body["query"]["bool"]["filter"] == [{"term": {"type": "option"}}] and "rescore" not in body
    )


def test_search_programs(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    part = es.search(cache, "rg", "program", ch, 5)
    rows = [(r["name"], r["package"], r["exact"]) for r in part.results]
    assert rows[0] == ("rg", "ripgrep", True)
    assert ("rgx", "rgx", False) in rows and ("rgxg", "rgxg", False) in rows
    assert part.results[0]["version"] == "15.2.0" and part.results[0]["kind"] == "program"
    assert len(es.search(cache, "rg", "program", ch, 1).results) == 1


def test_search_rejects_empty_and_unknown_kind(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    with pytest.raises(DiscoveryError) as e:
        es.search(cache, "***", "pkg", ch, 5)
    assert e.value.code == "usage"
    with pytest.raises(DiscoveryError):
        es.search(cache, "x", "hm", ch, 5)


def test_search_404_index_is_bad_channel(transport, cache):
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    transport.add("POST", r"/_search$", None, status=404)
    ch = es.resolve_channel(cache, "unstable")
    with pytest.raises(DiscoveryError) as e:
        es.search(cache, "firefox", "pkg", ch, 5)
    assert e.value.code == "bad-channel" and "--refresh" in e.value.message


def test_info_package_by_attr(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    part = es.info_package(cache, "kitty", ch)
    (rec,) = part.results
    assert rec["name"] == "kitty" and rec["matched_by"] == "attr" and "alternatives" not in rec
    assert rec["outputs"] == ["kitten", "shell_integration", "out", "terminfo"]
    assert rec["programs"] == ["kitten", "kitty"]


def test_info_package_by_pname_lists_alternatives(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    part = es.info_package(cache, "gcc-wrapper", ch)
    (rec,) = part.results
    assert rec["matched_by"] == "pname" and rec["pname"] == "gcc-wrapper"
    # no attr equals the pname, so the alphabetically first attribute is chosen deterministically
    assert rec["name"] == "gccNGPackages.gccNoLibgcc"
    assert len(rec["alternatives"]) == 3 and rec["name"] not in rec["alternatives"]
    assert es_transport.count("_search") == 2  # attr miss, then pname


def test_info_package_miss(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    assert es.info_package(cache, "no-such-thing", ch).results == []


def test_info_option(es_transport, cache):
    ch = es.resolve_channel(cache, "unstable")
    (rec,) = es.info_option(cache, "services.openssh.enable", ch).results
    assert rec["type"] == "boolean" and rec["default"] == "false" and rec["example"] is None
    assert (
        rec["description"]
        == "Whether to enable the OpenSSH secure shell daemon, which allows secure remote logins."
    )
    assert es.info_option(cache, "services.nope", ch).results == []


@pytest.mark.parametrize(
    "html, text",
    [
        (None, None),
        ("", None),
        ("<rendered-html><p>Hello\nworld.</p></rendered-html>", "Hello world."),
        ("<p>one</p><p>two</p>", "one\ntwo"),
        ("a<br/>b &amp; c &lt;d&gt;", "a\nb & c <d>"),
        ("<ul><li>x</li><li>y</li></ul>", "x\ny"),
        ("  plain   text  ", "plain text"),
        ("<p>Use <code>foo</code>.</p>", "Use foo."),
    ],
)
def test_strip_html(html, text):
    assert es.strip_html(html) == text
