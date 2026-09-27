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
