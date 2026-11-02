import pytest
import requests

from nixin import __version__, http
from nixin.http import NetworkError


class FakeResponse:
    def __init__(self, status=200, payload=None, text="not json"):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records requests and answers from a table of (method, url) -> response/exception."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        answer = self.answers[(method, url)]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def fake():
    def install(answers):
        s = FakeSession(answers)
        http.set_session(s)
        return s

    yield install
    http.set_session(None)


def test_real_session_has_user_agent():
    http.set_session(None)
    s = http.session()
    assert s.headers["User-Agent"] == f"nixin/{__version__}"
    assert s is http.session()  # memoized
    http.set_session(None)


def test_get_json_passes_params_and_default_timeout(fake):
    s = fake({("GET", "https://x/api"): FakeResponse(200, {"ok": 1})})
    assert http.get_json("https://x/api", params={"q": "a"}) == {"ok": 1}
    _m, _u, kw = s.calls[0]
    assert kw["params"] == {"q": "a"}
    assert kw["timeout"] == http.DEFAULT_TIMEOUT


def test_post_json_sends_body(fake):
    s = fake({("POST", "https://x/s"): FakeResponse(200, [1, 2])})
    assert http.post_json("https://x/s", {"query": 1}, timeout=3) == [1, 2]
    assert s.calls[0][2]["json"] == {"query": 1}
    assert s.calls[0][2]["timeout"] == 3


@pytest.mark.parametrize(
    "exc, fragment",
    [
        (requests.Timeout("slow"), "timed out"),
        (requests.ConnectionError("nope"), "cannot reach"),
    ],
)
def test_transport_errors_become_network(fake, exc, fragment):
    fake({("GET", "https://x/"): exc})
    with pytest.raises(NetworkError) as e:
        http.get_json("https://x/")
    assert e.value.code == "network"
    assert fragment in e.value.message


def test_5xx_is_network_4xx_keeps_status(fake):
    fake(
        {
            ("GET", "https://x/5"): FakeResponse(503),
            ("GET", "https://x/4"): FakeResponse(404),
        }
    )
    with pytest.raises(NetworkError) as e5:
        http.get_json("https://x/5")
    assert e5.value.code == "network" and e5.value.status == 503
    with pytest.raises(NetworkError) as e4:
        http.get_json("https://x/4")
    assert e4.value.code == "http-404" and e4.value.status == 404


def test_non_json_body_is_network(fake):
    fake({("GET", "https://x/"): FakeResponse(200, None)})
    with pytest.raises(NetworkError) as e:
        http.get_json("https://x/")
    assert "did not return JSON" in e.value.message
