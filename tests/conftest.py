import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return read


@pytest.fixture
def rules():
    from ulsan_jobs.config import load_rules

    return load_rules(ROOT / "config")


class _NoS2B:
    """테스트 중에는 S2B 에 실제로 접속하지 않는다 (목록 0건)."""

    def __init__(self, *args, **kwargs):
        self.calls = 0

    def list_page(self, region, start, end, page):
        return [], 1

    def detail(self, code, kind):
        raise AssertionError("상세 조회가 일어나면 안 됨")


@pytest.fixture(autouse=True)
def _offline_s2b(monkeypatch):
    from ulsan_jobs import s2b

    monkeypatch.setattr(s2b, "S2BClient", _NoS2B)


@pytest.fixture(autouse=True)
def _no_neis(monkeypatch):
    """시험은 나이스(학교 주소)에 실제로 접속하지 않는다. 필요한 시험은 school_district.neis_addresses 를 다시 바꾼다."""
    from ulsan_jobs import school_district

    monkeypatch.setattr(school_district, "neis_addresses", lambda http, school: [])


class _NoAgency:
    """테스트 중에는 기관 게시판에 실제로 접속하지 않는다 (관계없는 글 한 줄만 있는 목록)."""

    EMPTY = ("<table><tr><th>번호</th><th>제목</th><th>등록일</th></tr>"
             "<tr><td>1</td><td><a href='/view?no=1'>홈페이지 점검 안내</a></td><td>2020-01-01</td></tr></table>")

    def __init__(self, *args, **kwargs):
        self.calls = 0

    EMPTY_JSON = ('{"status":"success","data":{"result":[{"rtitle":"청사 시설 점검","pname":"시험공사",'
                  '"bidInfoEndDt":"2020.01.10","bdate":"2020.01.01","seq":1}],"page":{"totalPage":1}}}')

    def get(self, url, encoding=""):
        return (self.EMPTY_JSON if ".json" in url else self.EMPTY), url


@pytest.fixture(autouse=True)
def _offline_agency(monkeypatch):
    from ulsan_jobs import agency

    monkeypatch.setattr(agency, "AgencyClient", _NoAgency)
