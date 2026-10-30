import logging

from ulsan_jobs import logsafe


def test_redacts_query_keys_and_encoded_env_value(monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "abc+def/ghi==")
    line = "Retrying ... /x?serviceKey=abc%2Bdef%2Fghi%3D%3D&type=xml and abc+def/ghi=="
    out = logsafe.redact(line)
    assert "abc" not in out and "serviceKey=***&type=xml" in out


def test_filter_rewrites_record(monkeypatch, caplog):
    monkeypatch.setenv("G2B_API_KEY", "abc+def/ghi==")
    rec = logging.LogRecord("urllib3", logging.WARNING, __file__, 1, "url %s", ("?serviceKey=abc%2Bdef",), None)
    logsafe.RedactFilter().filter(rec)
    assert rec.getMessage() == "url ?serviceKey=***"


def test_redacts_proxy_credentials(monkeypatch):
    monkeypatch.setenv("KR_PROXY_URL", "http://myid:verylongpassword123@1.2.3.4:8888")
    out = logsafe.redact("ProxyError http://myid:verylongpassword123@1.2.3.4:8888 and http://x:y@host/")
    assert "verylongpassword123" not in out and "myid" not in out and "http://***@host/" in out
