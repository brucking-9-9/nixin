import json
import os
import stat

import pytest

from nixin.cache import Cache, default_root


def test_default_root_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_root() == tmp_path / "nixin"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert default_root() == tmp_path / "h" / ".cache" / "nixin"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/dir")  # never cache into the cwd
    assert default_root() == tmp_path / "h" / ".cache" / "nixin"


def test_roundtrip_and_layout(tmp_path):
    c = Cache(tmp_path)
    assert c.get("es", "k", ttl=10) is None
    c.put("es", "k", {"a": 1})
    assert c.get("es", "k", ttl=10) == {"a": 1}
    files = list((tmp_path / "es").iterdir())
    assert len(files) == 1 and files[0].suffix == ".json"
    entry = json.loads(files[0].read_text())
    assert entry["key"] == "k" and "stored" in entry and entry["payload"] == {"a": 1}


def test_ttl_expiry(tmp_path, monkeypatch):
    c = Cache(tmp_path)
    now = [1000.0]
    monkeypatch.setattr("nixin.cache.time.time", lambda: now[0])
    c.put("s", "k", "v")
    now[0] += 5
    assert c.get("s", "k", ttl=10) == "v"
    now[0] += 10
    assert c.get("s", "k", ttl=10) is None


def test_entry_from_the_future_is_stale(tmp_path, monkeypatch):
    c = Cache(tmp_path)
    now = [10_000.0]
    monkeypatch.setattr("nixin.cache.time.time", lambda: now[0])
    c.put("s", "k", "v")
    now[0] -= 3600  # the clock went back an hour after the write
    assert c.get("s", "k", ttl=10 * 3600) is None


def test_write_uses_a_per_process_temp_file(tmp_path, monkeypatch):
    c = Cache(tmp_path)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr("nixin.cache.os.replace", spy)
    c.put("s", "k", 1)
    assert seen == [f"{c.path('s', 'k').stem}.{os.getpid()}.tmp"]
    assert [p.name for p in (tmp_path / "s").iterdir()] == [c.path("s", "k").name]


def test_key_collision_is_detected(tmp_path, monkeypatch):
    c = Cache(tmp_path)
    monkeypatch.setattr(
        "nixin.cache.hashlib.sha1", lambda b: type("H", (), {"hexdigest": lambda s: "same"})()
    )
    c.put("s", "one", 1)
    assert c.get("s", "two", ttl=100) is None  # same file, different key -> miss


def test_modes(tmp_path):
    off = Cache(tmp_path, mode="off")
    off.put("s", "k", 1)
    assert not (tmp_path / "s").exists()
    assert off.get("s", "k", ttl=100) is None

    normal = Cache(tmp_path)
    normal.put("s", "k", 1)
    refresh = Cache(tmp_path, mode="refresh")
    assert refresh.get("s", "k", ttl=100) is None  # never reads
    refresh.put("s", "k", 2)  # but writes
    assert normal.get("s", "k", ttl=100) == 2

    with pytest.raises(ValueError):
        Cache(tmp_path, mode="bogus")


def test_from_flags():
    assert Cache.from_flags().mode == "normal"
    assert Cache.from_flags(refresh=True).mode == "refresh"
    assert Cache.from_flags(no_cache=True).mode == "off"
    assert Cache.from_flags(no_cache=True, refresh=True).mode == "off"


def test_fetch_reports_cached_flag(tmp_path):
    c = Cache(tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}

    assert c.fetch("s", "k", 100, fetch) == ({"n": 1}, False)
    assert c.fetch("s", "k", 100, fetch) == ({"n": 1}, True)
    assert len(calls) == 1


def test_unwritable_root_is_not_an_error(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("running as root, cannot make a read-only dir")
        c = Cache(ro)
        c.put("s", "k", 1)  # must not raise
        assert c.get("s", "k", ttl=100) is None
        assert c.fetch("s", "k", 100, lambda: "live") == ("live", False)
    finally:
        os.chmod(ro, stat.S_IRWXU)


def test_corrupt_entry_is_a_miss(tmp_path):
    c = Cache(tmp_path)
    p = c.path("s", "k")
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert c.get("s", "k", ttl=100) is None
    p.write_text(json.dumps({"key": "k", "stored": "yesterday", "payload": 1}))
    assert c.get("s", "k", ttl=100) is None
