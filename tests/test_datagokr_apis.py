"""공공데이터포털 Open API 수집기: 나라일터(인사혁신처)·잡알리오(재정경제부).

fixtures/mpm_list.xml·alio_list.json 은 실제 응답 모양에 맞춘 것 (지역코드는 2026-10-13 실제 응답에서 확인).
"""
from datetime import date

import pytest
import requests

from ulsan_jobs.collectors.alio_api import AlioJobCollector, parse_alio_json
from ulsan_jobs.collectors.base import NotConfigured
from ulsan_jobs.collectors.datagokr import ymd
from ulsan_jobs.collectors.mpm_api import MpmJobCollector, in_area, parse_mpm_xml
from ulsan_jobs.config import load_settings

TODAY = date(2026, 10, 13)
SOURCES = {s.id: s for s in load_settings().sources}
KEY_ENV = "G2B_API_KEY"  # 울산판은 나라장터·클린아이와 같은 공공데이터포털 키를 쓴다


class _Resp:
    def __init__(self, content: bytes):
        self.content = content


class _Http:
    def __init__(self, *pages: bytes):
        self.pages = list(pages)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        return _Resp(self.pages[min(len(self.calls), len(self.pages)) - 1])


def test_sources_use_the_api_collectors():
    assert SOURCES["gojobs_list"].collector == "mpm_api" and SOURCES["gojobs_list"].auth_env == KEY_ENV
    assert SOURCES["alio_recruit"].collector == "alio_api" and SOURCES["alio_recruit"].params["workRgnLst"] == "R3016"


def test_ymd():
    assert ymd("20261018") == date(2026, 10, 18)
    assert ymd(" ") is None and ymd("19000101") is None and ymd(None) is None


def test_mpm_ulsan_filter(fixture_bytes):
    items, total = parse_mpm_xml(fixture_bytes("mpm_list.xml"))
    assert total == 5
    # 31… 지역코드, 여러 지역 중 울산 포함, 00000 이면서 기관명에 울산 → 울산. 양산(48330)은 뺀다
    assert [i["idx"] for i in items if in_area(i)] == ["305101", "304960", "305190", "304702"]


def test_mpm_collect(fixture_bytes, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "abc%2Bdef")
    http = _Http(fixture_bytes("mpm_list.xml"))
    posts = MpmJobCollector(SOURCES["gojobs_list"], http, TODAY).collect()
    url, params = http.calls[0]
    assert url.endswith("/1760000/PblJobService/getList") and len(http.calls) == 1
    assert params["serviceKey"] == "abc+def"  # Encoding 키는 풀어서 보낸다
    assert params["Begin_de"] == "2026-09-29" and params["End_de"] == "2026-10-13"  # 최근 14일
    p = posts[0]
    assert p.title == "2026년 중구 평생학습관 시간강사 채용 공고"
    assert p.post_key == "305101"  # 게시판을 읽던 때의 empmnsn 과 같은 값
    assert p.url == p.detail_url == "https://www.gojobs.go.kr/apmView.do?empmnsn=305101&searchJobsecode=020&menuNo=401&selMenuNo=400"
    assert p.district == "중구"
    assert p.posted_date == date(2026, 10, 5) and p.deadline == date(2026, 10, 14)
    assert posts[1].district == "남구" and posts[2].district == "울주군"  # 31710,48330 → 울산 쪽 코드만 본다
    assert posts[3].district == "울산전체"  # 00000


def test_mpm_api_error():
    xml = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    with pytest.raises(RuntimeError, match="30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        parse_mpm_xml(xml)


def test_alio_collect(fixture_bytes, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k")
    http = _Http(fixture_bytes("alio_list.json"))
    posts = AlioJobCollector(SOURCES["alio_recruit"], http, TODAY).collect()
    url, params = http.calls[0]
    assert url.endswith("/1051000/recruitment/list")
    assert params["workRgnLst"] == "R3016" and params["ongoingYn"] == "Y"
    assert [p.post_key for p in posts] == ["305801", "305760"]
    assert posts[1].url == "https://job.alio.go.kr/recruitview.do?idx=305760"
    assert posts[1].deadline == date(2026, 10, 14)
    with pytest.raises(RuntimeError, match="잡알리오 API 오류: 500"):
        parse_alio_json(b'{"resultCode":500,"resultMsg":"fail"}')


@pytest.mark.parametrize("cls,sid", [(MpmJobCollector, "gojobs_list"), (AlioJobCollector, "alio_recruit")])
def test_missing_key_and_hidden_key(cls, sid, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(NotConfigured):
        cls(SOURCES[sid], _Http(b""), TODAY).collect()

    monkeypatch.setenv(KEY_ENV, "SECRET+KEY/123")

    class _Boom:
        def get(self, url, params=None, **kw):
            raise requests.exceptions.HTTPError(
                "403 Client Error for url: https://apis.data.go.kr/x?serviceKey=SECRET%2BKEY%2F123&pageNo=1")

    with pytest.raises(requests.exceptions.HTTPError) as info:
        cls(SOURCES[sid], _Boom(), TODAY).collect()
    assert "SECRET" not in str(info.value) and "serviceKey=***" in str(info.value)
