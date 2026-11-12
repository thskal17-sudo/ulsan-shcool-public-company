"""구·군 채우기 (school_district.py): 시설 이름표, 나이스 학교 주소, DB 에 남기기, 강사잇다 지역 칸."""
from datetime import date

from ulsan_jobs import school_district
from ulsan_jobs.gangsaitda import region
from ulsan_jobs.models import Posting
from ulsan_jobs.school_district import district_from_address, fill_districts, pick_district, school_names
from ulsan_jobs.storage import Store

ADDR = {
    "중산초등학교": ["울산광역시 북구 중산동 1"],
    "강동초등학교": ["울산광역시 북구 정자동 2"],
    "약사초등학교": ["울산광역시 중구 약사동 3"],
    "울산효문초등학교": ["울산광역시 북구 효문동 4"],
    "같은이름초등학교": ["울산광역시 남구 a", "울산광역시 울주군 b"],
}


def _post(org: str, title: str = "방과후 강사 모집", district: str = "울산전체") -> Posting:
    return Posting("use_job_general", title, f"https://x.org/{org}", org_name=org, district=district,
                   deadline=date(2026, 11, 20))


def test_names_and_addresses():
    assert school_names("중산초등학교병설유치원", "중산초병설유치원 겨울방학 중 특수방과후") == ["중산초등학교"]
    assert school_names("연암초등학교 병설유치원") == ["연암초등학교"]
    assert school_names("울산효문초등학교병설유치원") == ["울산효문초등학교"]
    assert school_names("강동유치원") == ["강동초등학교"]
    assert school_names("약사가온유치원") == ["약사가온초등학교", "약사가초등학교", "약사초등학교"]
    assert school_names("남외초등학교", "늘봄학교 프로그램 운영 용역") == ["남외초등학교"]
    assert school_names("울산광역시교육청") == [] and school_names("문수경기장") == []
    assert district_from_address("울산광역시 울주군 범서읍") == "울주군"
    assert district_from_address("부산광역시 북구") is None
    assert pick_district("남구,울주군", "같은이름초 울주 늘봄") == "울주군"
    assert pick_district("남구,울주군", "같은이름초 늘봄") == ""


def test_venues_schools_and_kindergartens(tmp_path, monkeypatch):
    calls = []

    def fake(http, school):
        calls.append(school)
        return ADDR.get(school, [])

    monkeypatch.setattr(school_district, "neis_addresses", fake)
    store = Store(tmp_path / "db.sqlite")
    posts = {
        "munsu": _post("문수경기장", "2027년 상반기 문수체육관, 문수테니스장 교육강사(프리랜서) 위촉 공고"),
        "park": _post("울산대공원", "2027년 상반기 울산대공원 아쿠아시스 교육강사(프리랜서) 위촉 공고"),
        "dongcheon": _post("동천국민체육센터", "2027년 상반기 동천국민체육센터 교육강사(프리랜서) 위촉 공고"),
        "welfare": _post("노동자종합복지회관", "2027년도 상반기 노동복지관 스포츠센터 교육강사(프리랜서) 위촉 공고"),
        "jungsan": _post("중산초등학교병설유치원", "중산초병설유치원 겨울방학 중 특수방과후 계약제교원-시간강사 채용 공고"),
        "gangdong": _post("강동유치원", "강동유치원 겨울방학 및 학년말방학 중 방과후과정"),
        "yaksa": _post("약사가온유치원", "약사가온유치원 겨울방학 중 특수방과후과정"),
        "hyomun": _post("울산효문초등학교병설유치원", "2026. 울산효문초병설유치원 특수방과후"),
        "twin": _post("같은이름초등학교"),
        "unknown": _post("옥현유치원"),
        "titled": _post("어딘가초등학교", "울주군 늘봄 강사"),
    }
    assert fill_districts(list(posts.values()), store, http=None) == 8
    got = {k: region(p) for k, p in posts.items()}
    assert got == {
        "munsu": "울산 남구", "park": "울산 남구", "dongcheon": "울산 중구", "welfare": "울산 남구", "jungsan": "울산 북구",
        "gangdong": "울산 북구", "yaksa": "울산 중구", "hyomun": "울산 북구", "twin": "울산", "unknown": "울산",
        "titled": "울산 울주군",
    }
    assert "어딘가초등학교" not in calls  # 제목에 구가 있으면 묻지 않는다
    store.commit()

    # 다음 실행: DB 에 남아 있어 다시 묻지 않는다 (찾지 못한 학교도)
    calls.clear()
    again = Store(tmp_path / "db.sqlite")
    p, q = _post("중산초등학교병설유치원"), _post("옥현유치원")
    fill_districts([p, q], again, http=None)
    assert region(p) == "울산 북구" and region(q) == "울산" and calls == []


def test_neis_down_stops_asking_and_remembers_nothing(tmp_path, monkeypatch):
    calls = []

    def down(http, school):
        calls.append(school)
        raise ConnectionError("down")

    monkeypatch.setattr(school_district, "neis_addresses", down)
    store = Store(tmp_path / "db.sqlite")
    posts = [_post("중산초등학교"), _post("강동유치원"), _post("문수경기장", "문수경기장 강사")]
    assert fill_districts(posts, store, http=None) == 1  # 시설은 나이스 없이 채운다
    assert calls == ["중산초등학교"]
    assert store.school_district("중산초등학교") is None


def test_neis_addresses_reads_api_shape(monkeypatch):
    class Resp:
        def json(self):
            return {"schoolInfo": [{"head": [{"list_total_count": 2}]}, {"row": [
                {"SCHUL_NM": "중산초등학교", "ORG_RDNMA": "울산광역시 북구 중산동 1"},
                {"SCHUL_NM": "중산초등학교분교", "ORG_RDNMA": "울산광역시 남구 x"},
            ]}]}

    class Http:
        def get(self, url, params=None, **kw):
            assert params["ATPT_OFCDC_SC_CODE"] == "H10" and params["SCHUL_NM"] == "중산초등학교"
            return Resp()

    monkeypatch.undo()
    assert school_district.neis_addresses(Http(), "중산초등학교") == ["울산광역시 북구 중산동 1"]
