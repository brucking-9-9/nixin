"""Shared test plumbing: recorded fixtures and a fake HTTP transport.

Unit tests never touch the network (`nix build` runs them in the sandbox).
`transport` installs a fake requests session into `nixin.http`; a test adds
rules mapping (method, URL regex, optional body predicate) to a fixture file or
an inline payload, and anything unmatched raises ConnectionError, which the
code must turn into a `network` error.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
import requests

from nixin import http

FIXTURES = Path(__file__).parent / "fixtures"

live = pytest.mark.skipif(
    os.environ.get("NIXIN_LIVE") != "1", reason="set NIXIN_LIVE=1 for live tests"
)


def fixture(rel: str) -> Any:
    with (FIXTURES / rel).open(encoding="utf-8") as fh:
        return json.load(fh)


def has(obj: Any, key: str, value: Any) -> bool:
    """True if some dict anywhere inside `obj` maps `key` to `value`."""
    if isinstance(obj, dict):
        if key in obj and obj[key] == value:
            return True
        return any(has(v, key, value) for v in obj.values())
    if isinstance(obj, list):
        return any(has(v, key, value) for v in obj)
    return False


class FakeResponse:
    def __init__(self, status: int, payload: Any):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.rules: list[tuple[str, re.Pattern, Callable[[Any], bool] | None, int, Any]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.headers: dict[str, str] = {}

    def add(
        self,
        method: str,
        url: str,
        payload: Any = None,
        *,
        fixture_file: str | None = None,
        when: Callable[[Any], bool] | None = None,
        status: int = 200,
    ) -> None:
        if fixture_file is not None:
            payload = fixture(fixture_file)
        self.rules.append((method.upper(), re.compile(url), when, status, payload))

    def request(self, method: str, url: str, **kw: Any) -> FakeResponse:
        body = kw.get("json")
        if kw.get("params"):
            url = url + "?" + urlencode(kw["params"])  # so rules can match query strings
        self.calls.append((method.upper(), url, body))
        for m, pat, when, status, payload in self.rules:
            if m == method.upper() and pat.search(url) and (when is None or when(body)):
                return FakeResponse(status, payload)
        raise requests.ConnectionError(f"no fake rule for {method} {url}")

    def count(self, url_fragment: str) -> int:
        return sum(1 for _m, u, _b in self.calls if url_fragment in u)


@pytest.fixture(autouse=True)
def no_local_index(tmp_path, monkeypatch):
    """This machine's own option indexes (/etc/nixin, ~/.config/nixin) stay out of every
    test: the paths point into the test's tmp dir. tests/test_optindex.py points them at
    recorded indexes."""
    from nixin import optindex

    monkeypatch.setenv(optindex.ENV_NIXOS, str(tmp_path / "no-index" / "options-nixos.json"))
    monkeypatch.setenv(optindex.ENV_HOME, str(tmp_path / "no-index" / "options-home.json"))
    monkeypatch.delenv(optindex.ENV_OFF, raising=False)
    optindex._LOADED.clear()
    yield
    optindex._LOADED.clear()


@pytest.fixture
def transport():
    s = FakeSession()
    http.set_session(s)  # type: ignore[arg-type]
    yield s
    http.set_session(None)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path / "nixin"


@pytest.fixture
def es_transport(transport):
    """The search.nixos.org rules every ES test needs: aliases, counts, and the recorded searches."""
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/search_pkg_firefox.json",
        when=lambda b: has(b, "query", "firefox"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/search_opt_openssh.json",
        when=lambda b: has(b, "query", "services.openssh"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/search_program_rg.json",
        when=lambda b: has(b, "query", "rg"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/info_pkg_kitty.json",
        when=lambda b: has(b, "package_attr_name", "kitty"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/info_pkg_pname_gcc_wrapper.json",
        when=lambda b: has(b, "package_pname", "gcc-wrapper"),
    )
    transport.add(
        "POST",
        r"/_search$",
        fixture_file="es/info_opt_openssh_enable.json",
        when=lambda b: has(b, "option_name", "services.openssh.enable"),
    )
    transport.add("POST", r"/_search$", fixture_file="es/empty.json")  # anything else: no hits
    return transport
