from datetime import date, datetime

from ulsan_jobs.config import Source
from ulsan_jobs.matching import core, finished_by, same_program
from ulsan_jobs.models import KST, Posting
from ulsan_jobs.pipeline import collect_all
from ulsan_jobs.collectors import Collector
from ulsan_jobs.storage import CLOSED, Store


def test_core_strips_year_round_and_notice_words():
    assert core("[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고") == "남부청소년수련관주말수영강사"
    assert core("[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과") == "남부청소년수련관주말수영강사"
    # 목록에서 잘린 제목은 잘린 낱말까지 버린다
    assert core("중구수영장 시간강사(프리랜서) 서류적격 대상자 발표 및 면접심사 시행 공...") == "중구수영장시간강사"


def test_same_program():
    assert same_program("중구수영장 시간강사(수영) 위촉 공고", "중구수영장 시간강사(프리랜서) 최종합격자 공고")
    assert same_program(
        "[중부청소년수련관]2026년 문화강좌 4분기 교육강사 긴급 위·수탁 공고",
        "[중부청소년수련관]2026년 문화강좌 4분기 교육강사 긴급 위·수탁 서류심사 결과",
    )
    # 과목·시설이 다르면 다른 모집
    assert not same_program("울산동천고등학교 시간강사 채용 공고(일본어)", "울산동천고등학교 시간강사 최종합격자 공고(미술)")
    assert not same_program("[남부청소년수련관] 주말수영 강사 모집", "[서부청소년수련관] 주말수영 강사 서류심사 결과")
    # 핵심이 너무 짧으면 판단하지 않음
    assert not same_program("시간강사 채용 공고", "시간강사 최종합격자 공고")


def _p(title, key, posted, org="울주군시설관리공단"):
    return Posting("b", title, f"https://example.org/{key}", key, org, posted_date=posted)


def test_finished_by_requires_later_result_and_same_org():
    recruit = _p("[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고", "8979", date(2026, 9, 16))
    result = _p("[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과", "9001", date(2026, 9, 24))
    assert finished_by(recruit, [result]) is result
    # 결과가 먼저 올라왔으면 (예: 결과 뒤의 '추가 모집') 닫지 않음
    earlier = _p("[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과", "8900", date(2026, 9, 1))
    assert finished_by(recruit, [earlier]) is None
    # 같은 날이면 글 번호가 큰 쪽이 나중
    same_day = _p("[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과", "8980", date(2026, 9, 16))
    assert finished_by(recruit, [same_day]) is same_day
    # 다른 기관(예: 다른 학교)의 결과공고로는 닫지 않음
    other = _p("[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과", "9002", date(2026, 9, 24), "다른기관")
    assert finished_by(recruit, [other]) is None


LIST = [
    Posting("board", "[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과", "https://example.org/2", "2",
            "울주군시설관리공단", posted_date=date(2026, 9, 24)),
    Posting("board", "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고", "https://example.org/1", "1",
            "울주군시설관리공단", posted_date=date(2026, 9, 16)),
    Posting("board", "[서부청소년수련관] 2026년 스포츠강사 위·수탁 모집 공고", "https://example.org/3", "3",
            "울주군시설관리공단", posted_date=date(2026, 9, 20)),
]


class Board(Collector):
    def collect(self):
        return [Posting(**{**p.__dict__}) for p in LIST]


class NoHttp:
    def allow_legacy_tls(self, host):
        raise AssertionError


def test_pipeline_closes_recruitment_with_later_result(tmp_path, rules, monkeypatch):
    from ulsan_jobs import pipeline

    monkeypatch.setitem(pipeline.COLLECTORS, "board", Board)
    src = Source("board", "강습위탁", "board", url="https://example.org/list", keyword_filter=False)
    store = Store(tmp_path / "db.sqlite")
    now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)

    collect_all([src], rules, store, NoHttp(), now)
    status = {p.post_key: p.status for p in store.unreported()}
    assert status == {"1": CLOSED, "3": "모집중"}  # 결과공고 자체는 저장하지 않음
    assert [p.post_key for p in store.active(now.date(), 30)] == ["3"]

    # 다음 날 목록에 모집공고가 그대로 있어도 다시 '모집중'으로 돌아가지 않는다
    collect_all([src], rules, store, NoHttp(), now)
    assert {p.post_key: p.status for p in store.unreported()}["1"] == CLOSED
    store.close()
