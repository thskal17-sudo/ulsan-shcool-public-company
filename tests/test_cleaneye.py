from datetime import date

import pytest

from ulsan_jobs.collectors.base import NotConfigured
from ulsan_jobs.collectors.cleaneye_api import CleaneyeCollector, parse_cleaneye_xml
from ulsan_jobs.config import Source

TODAY = date(2026, 10, 12)


class _Resp:
    def __init__(self, content: bytes):
        self.content = content


class _Http:
    def __init__(self, content: bytes):
        self.content = content
        self.calls: list[dict] = []

    def get(self, url, params=None, **kw):
        self.calls.append(params)
        return _Resp(self.content)


def _source(**kw) -> Source:
    return Source(id="cleaneye_api", name="클린아이", collector="cleaneye_api", org_type="공공기관",
                  district="울산전체", auth_env="G2B_API_KEY", params={"sidoCd": "007007"}, **kw)


def test_parse_items_and_no_data(fixture_bytes):
    items = parse_cleaneye_xml(fixture_bytes("cleaneye_list.xml"))
    assert [i["ENT_NAME"] for i in items] == ["울산광역시남구도시관리공단", "재단법인 울주복지재단"]
    assert parse_cleaneye_xml(fixture_bytes("cleaneye_nodata.xml")) == []


def test_api_error_code_raises():
    xml = b"<response><openXmlEmployInfo-header><resultCode>11</resultCode><resultMsg>NOTEXISTPARAMETER_ERROR(type)</resultMsg></openXmlEmployInfo-header></response>"
    with pytest.raises(RuntimeError, match="NOTEXISTPARAMETER"):
        parse_cleaneye_xml(xml)


def test_collect_builds_postings(fixture_bytes, monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "abc%2Bdef")
    http = _Http(fixture_bytes("cleaneye_list.xml"))
    posts = CleaneyeCollector(_source(), http, TODAY).collect()
    assert http.calls[0]["serviceKey"] == "abc+def"  # Encoding 키는 풀어서 보낸다
    assert http.calls[0]["type"] == "xml" and http.calls[0]["sidoCd"] == "007007"
    p = posts[0]
    assert p.title == "2026년 남구국민체육센터 수영 강사 모집 공고"
    assert p.org_name == "울산광역시남구도시관리공단"
    assert p.district == "남구"
    assert p.posted_date == date(2026, 10, 12) and p.deadline == date(2026, 10, 20)
    assert p.url.endswith("idx=80300") and p.post_key == "80300"
    assert posts[1].label == "사무보조" and p.label == ""


def test_missing_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("G2B_API_KEY", raising=False)
    with pytest.raises(NotConfigured):
        CleaneyeCollector(_source(), _Http(b""), TODAY).collect()
