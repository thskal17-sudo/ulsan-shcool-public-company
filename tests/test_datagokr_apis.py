"""공공데이터포털 Open API 수집기: 나라일터(인사혁신처)·잡알리오(재정경제부).

fixtures/mpm_list.xml 은 부산 수집 서버에서 받은 실제 응답(2026-10-12) 형식에 울산 공고를 넣은 것,
alio_list.json 은 그 실제 응답에서 몇 건만 남긴 것.
"""
import dataclasses
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


def test_sources_use_the_apis():
    assert SOURCES["gojobs_list"].collector == "mpm_api" and SOURCES["gojobs_list"].auth_env == "G2B_API_KEY"
    assert SOURCES["alio_recruit"].collector == "alio_api" and SOURCES["alio_recruit"].auth_env == "G2B_API_KEY"


def test_ymd():
    assert ymd("20261018") == date(2026, 10, 18)
    assert ymd(" ") is None and ymd("19000101") is None and ymd("20261340") is None and ymd(None) is None


def test_mpm_parse_and_ulsan_filter(fixture_bytes):
    items, total = parse_mpm_xml(fixture_bytes("mpm_list.xml"))
    assert total == 6 and len(items) == 6
    ulsan = [i["idx"] for i in items if in_area(i)]
    # 31… 지역코드, 여러 지역 중 울산 포함, 00000 이면서 기관명에 울산 → 울산.
    # 부산(26230) 학교·00000 인 서울 학교는 뺀다
    assert ulsan == ["305163", "305075", "305417", "304579"]
    # 다른 지역에 쓸 때는 지역코드·지역명을 바꾼다
    assert [i["idx"] for i in items if in_area(i, "26", "부산")] == ["305417", "305301"]


def test_mpm_collect(fixture_bytes, monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "abc%2Bdef")
    http = _Http(fixture_bytes("mpm_list.xml"))
    posts = MpmJobCollector(SOURCES["gojobs_list"], http, TODAY).collect()
    url, params = http.calls[0]
    assert url.endswith("/1760000/PblJobService/getList") and len(http.calls) == 1  # 6건 < 100건이라 한 쪽
    assert params["serviceKey"] == "abc+def"  # Encoding 키는 풀어서 보낸다
    assert params["Begin_de"] == "2026-09-29" and params["End_de"] == "2026-10-13"  # 최근 14일
    assert params["Pblanc_ty"] == "" and params["Instt_se"] == ""
    p = posts[0]
    assert p.title == "2026학년도 삼호초등학교 방과후 컴퓨터 시간강사 채용 공고"
    # 게시판을 읽던 때와 같은 글번호·주소라 이미 저장한 공고와 이어진다
    assert p.post_key == "305163"
    assert p.url == p.detail_url == "https://www.gojobs.go.kr/apmView.do?empmnsn=305163&searchJobsecode=020&menuNo=401&selMenuNo=400"
    assert p.district == "남구" and p.org_type == "교육청·학교"
    assert p.posted_date == date(2026, 10, 7) and p.deadline == date(2026, 10, 14)
    assert posts[1].district == "울주군" and posts[1].org_type == "지자체"
    assert posts[2].district == "동구"  # 31170,26200 → 울산 쪽 코드만 본다
    assert posts[3].district == "울산전체"  # 00000
    assert len(posts) == 4


def test_mpm_pages_until_total(fixture_bytes, monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "k")
    xml = fixture_bytes("mpm_list.xml").replace(b"<totalCount>6</totalCount>", b"<totalCount>250</totalCount>")
    http = _Http(xml, xml, xml, xml)
    MpmJobCollector(SOURCES["gojobs_list"], http, TODAY).collect()
    assert [c[1]["pageNo"] for c in http.calls] == [1, 2, 3]


def test_mpm_reads_newest_pages_when_too_many(fixture_bytes, monkeypatch):
    """전체 1000건(10쪽), max_pages 3 → 1쪽으로 건수를 알고 끝쪽 8·9·10쪽(새 글)을 읽는다."""
    monkeypatch.setenv("G2B_API_KEY", "k")
    xml = fixture_bytes("mpm_list.xml").replace(b"<totalCount>6</totalCount>", b"<totalCount>1000</totalCount>")
    src = dataclasses.replace(SOURCES["gojobs_list"], options={"days": 14, "max_pages": 3})
    http = _Http(xml)
    MpmJobCollector(src, http, TODAY).collect()
    assert [c[1]["pageNo"] for c in http.calls] == [1, 8, 9, 10]


def test_mpm_api_error(fixture_bytes):
    with pytest.raises(RuntimeError, match="30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        parse_mpm_xml(fixture_bytes("datagokr_key_error.xml"))


def test_alio_collect(fixture_bytes, monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "k")
    http = _Http(fixture_bytes("alio_list.json"))
    posts = AlioJobCollector(SOURCES["alio_recruit"], http, TODAY).collect()
    url, params = http.calls[0]
    assert url.endswith("/1051000/recruitment/list")
    assert params["workRgnLst"] == "R3016" and params["ongoingYn"] == "Y" and params["resultType"] == "json"
    assert [p.post_key for p in posts] == ["305916", "305902", "305830"]
    p = posts[1]
    assert p.title == "제2026-6호 한국청소년상담복지개발원 직원 모집"
    assert p.url == p.detail_url == "https://job.alio.go.kr/recruitview.do?idx=305902"
    assert p.org_name == "한국청소년상담복지개발원"
    assert p.posted_date == date(2026, 10, 12) and p.deadline == date(2026, 10, 27)


def test_alio_errors():
    with pytest.raises(RuntimeError, match="잡알리오 API 오류: 500"):
        parse_alio_json(b'{"resultCode":500,"resultMsg":"fail"}')
    with pytest.raises(RuntimeError, match="SERVICE_KEY_IS_NOT_REGISTERED"):  # 포털 게이트웨이 오류는 XML
        parse_alio_json(b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")


@pytest.mark.parametrize("cls,sid", [(MpmJobCollector, "gojobs_list"), (AlioJobCollector, "alio_recruit")])
def test_missing_key_and_hidden_key(cls, sid, monkeypatch):
    monkeypatch.delenv("G2B_API_KEY", raising=False)
    with pytest.raises(NotConfigured):
        cls(SOURCES[sid], _Http(b""), TODAY).collect()

    monkeypatch.setenv("G2B_API_KEY", "SECRET+KEY/123")

    class _Boom:
        def get(self, url, params=None, **kw):
            raise requests.exceptions.HTTPError(
                "403 Client Error for url: https://apis.data.go.kr/x?serviceKey=SECRET%2BKEY%2F123&pageNo=1")

    with pytest.raises(requests.exceptions.HTTPError) as info:
        cls(SOURCES[sid], _Boom(), TODAY).collect()
    assert "SECRET" not in str(info.value) and "serviceKey=***" in str(info.value)
