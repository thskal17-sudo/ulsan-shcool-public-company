from http.client import RemoteDisconnected

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from ulsan_jobs.http import Http, HostUnreachable


class _Resp:
    status_code = 200
    url = ""

    def iter_content(self, chunk_size):
        yield b"ok"

    def close(self):
        pass

    def raise_for_status(self):
        pass


@pytest.fixture
def http(monkeypatch):
    client = Http(min_interval=0)
    calls = []
    errors = {}

    def fake_request(method, url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        if url in errors:
            raise errors[url]
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    return client, calls, errors


def test_connect_timeout_marks_host_and_skips_it(http):
    client, calls, errors = http
    errors["https://down.example/a"] = requests.exceptions.ConnectTimeout("timed out")

    with pytest.raises(requests.exceptions.ConnectTimeout):
        client.get("https://down.example/a")
    # 같은 서버의 다른 게시판은 기다리지 않고 바로 실패, 다른 서버는 그대로
    with pytest.raises(HostUnreachable):
        client.get("https://down.example/b")
    client.get("https://up.example/")
    assert [url for url, _ in calls] == ["https://down.example/a", "https://up.example/"]

    client.forget_unreachable()
    client.get("https://down.example/b")
    assert calls[-1][0] == "https://down.example/b"


def test_refused_connection_marks_host(http):
    client, _, errors = http
    reason = NewConnectionError(None, "Connection refused")
    errors["https://down.example/"] = requests.exceptions.ConnectionError(MaxRetryError(None, "/", reason))
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("https://down.example/")
    with pytest.raises(HostUnreachable):
        client.get("https://down.example/next")


def test_dropped_response_does_not_mark_host(http):
    # 연결은 됐는데 응답 도중 끊긴 경우는 서버가 살아 있으므로 다음 요청은 그대로 보낸다
    client, calls, errors = http
    errors["https://flaky.example/a"] = requests.exceptions.ConnectionError(
        ProtocolError("Connection aborted.", RemoteDisconnected("closed"))
    )
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("https://flaky.example/a")
    client.get("https://flaky.example/b")
    assert calls[-1][0] == "https://flaky.example/b"


def test_connect_wait_is_bounded(http):
    # 연결 대기 10초, 연결 실패는 한 번만 다시 시도 (사이트당 최대 약 20초)
    client, calls, _ = http
    client.get("https://up.example/")
    assert calls[0][1] == (10.0, 20.0)
    retry = client.session.get_adapter("https://up.example/").max_retries
    assert retry.connect == 1


def test_kr_proxy_routes_korean_sites_and_falls_back(monkeypatch):
    """KR_PROXY_URL 이 있으면 .kr 사이트만 중계로 보내고, 중계로 안 닿으면 그 사이트만 바로 접속한다."""
    monkeypatch.setenv("KR_PROXY_URL", "http://u:p@1.2.3.4:8888")
    client = Http(min_interval=0)
    calls = []

    def fake_request(method, url, **kwargs):
        proxied = "proxies" in kwargs
        calls.append((url, proxied))
        if proxied and "blocked.kr" in url:
            raise requests.exceptions.ProxyError("Tunnel connection failed: 502 Bad Gateway")
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    client.get("https://www.ulsan.go.kr/a")
    client.get("https://api.github.com/b")
    client.get("https://blocked.kr/c")
    client.get("https://blocked.kr/d")
    assert calls == [
        ("https://www.ulsan.go.kr/a", True),
        ("https://api.github.com/b", False),  # 한국 사이트가 아니면 중계하지 않는다
        ("https://blocked.kr/c", True),
        ("https://blocked.kr/c", False),  # 중계 실패 → 바로 접속
        ("https://blocked.kr/d", False),  # 같은 실행에서는 그 사이트를 바로 접속
    ]


def test_kr_proxy_down_disables_proxy_for_run(monkeypatch):
    client = Http(min_interval=0, kr_proxy="http://u:p@1.2.3.4:8888")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append("proxies" in kwargs)
        if "proxies" in kwargs:
            raise requests.exceptions.ProxyError("Unable to connect to proxy")
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    client.get("https://a.go.kr/1")
    client.get("https://b.or.kr/2")
    assert calls == [True, False, False] and client.kr_proxy is None


def test_no_proxy_without_env(monkeypatch):
    monkeypatch.delenv("KR_PROXY_URL", raising=False)
    assert Http(min_interval=0).kr_proxy is None
