"""One HTTP session for every remote source.

Every discovery backend goes through `get_json` / `post_json` here, so the
user agent (`nixin/<version>`), the timeouts, the retry policy and the error
mapping live in one place. Tests swap the transport with `set_session()`.

Failures become `NetworkError`, whose `.code` is the `--json` error code:
`network` for anything transport-level (DNS, refused, timeout, 5xx after the
retries, non-JSON body) and `http-<status>` for a 4xx the caller may want to
interpret (`404` on a retired channel alias, for instance).
"""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

USER_AGENT = f"nixin/{__version__}"
DEFAULT_TIMEOUT = 15.0

_session: requests.Session | None = None


class NetworkError(Exception):
    """A remote source could not be reached or answered badly."""

    def __init__(self, message: str, *, code: str = "network", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def message(self) -> str:
        return str(self)


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept"] = "application/json"
    retry = Retry(
        total=2,
        connect=2,
        read=1,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def session() -> requests.Session:
    """The process-wide session (created on first use)."""
    global _session
    if _session is None:
        _session = _new_session()
    return _session


def set_session(s: requests.Session | None) -> None:
    """Replace the session; tests pass a fake, `None` resets to a real one."""
    global _session
    _session = s


def _request(method: str, url: str, **kw: Any) -> Any:
    kw.setdefault("timeout", DEFAULT_TIMEOUT)
    try:
        resp = session().request(method, url, **kw)
    except requests.Timeout as exc:
        raise NetworkError(f"timed out talking to {url}") from exc
    except requests.RequestException as exc:
        raise NetworkError(f"cannot reach {url}: {exc}") from exc
    if resp.status_code >= 500:
        raise NetworkError(f"{url} answered HTTP {resp.status_code}", status=resp.status_code)
    if resp.status_code >= 400:
        raise NetworkError(
            f"{url} answered HTTP {resp.status_code}",
            code=f"http-{resp.status_code}",
            status=resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise NetworkError(f"{url} did not return JSON") from exc


def get_json(url: str, **kw: Any) -> Any:
    """GET `url` and decode the JSON body. `params`, `headers`, `auth`, `timeout` pass through."""
    return _request("GET", url, **kw)


def post_json(url: str, body: Any, **kw: Any) -> Any:
    """POST `body` as JSON to `url` and decode the JSON answer."""
    return _request("POST", url, json=body, **kw)
