from datetime import date, datetime

from ulsan_jobs.collectors import Collector
from ulsan_jobs.config import Source
from ulsan_jobs.detail import extends, full_title, looks_truncated, page_soup
from ulsan_jobs.models import KST, Posting
from ulsan_jobs.pipeline import collect_all
from ulsan_jobs.storage import Store

DETAIL_HTML = """
<html><body>
<div class="board_view">
  <div class="tit">강습위탁공고</div>
  <table><tr><th colspan="4">[서부청소년수련관] 2026 청소년방과후아카데미 스포츠강사 (긴급) 위수탁 모집 공고</th></tr>
  <tr><td>작성자</td><td>관리자</td><td>작성일</td><td>2026-09-18</td></tr></table>
  <div class="prev">이전글 [서부청소년수련관] 2026 청소년방과후아카데미 다른 공고</div>
</div>
</body></html>
"""


def test_full_title_from_detail_page():
    soup = page_soup(DETAIL_HTML.encode())
    short = "[서부청소년수련관] 2026 청소년방과후아카데미 스포츠강사 (긴급) 위수"
    assert full_title(soup, short) == "[서부청소년수련관] 2026 청소년방과후아카데미 스포츠강사 (긴급) 위수탁 모집 공고"
    assert full_title(soup, "완전히 다른 제목의 공고입니다") is None
    # 목록 제목이 이미 전체면 바꿀 것이 없다
    assert full_title(soup, "[서부청소년수련관] 2026 청소년방과후아카데미 스포츠강사 (긴급) 위수탁 모집 공고") is None


def test_truncation_helpers():
    assert looks_truncated("서류적격 대상자 발표 및 면접심사 시행 공...")
    assert looks_truncated("기간제 근로자 재채용 공…")
    assert not looks_truncated("수영 강사 모집 공고")
    assert extends("면접심사 시행 공고", "면접심사 시행 공...")
    assert not extends("다른 공고", "면접심사 시행 공...")


PAGES = {
    "https://example.org/a": "<table><tr><th>[중부청소년수련관]2026년 문화강좌 4분기 교육강사 긴급 위·수탁 서류심사 결과</th></tr></table>",
    "https://example.org/b": "<table><tr><th>[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고</th></tr>"
    "<tr><td>접수기간: 2026. 9. 21. ~ 2026. 10. 2. 18:00</td></tr></table>",
}


class TruncatedBoard(Collector):
    fetched: list[str] = []

    def collect(self):
        return [
            Posting("cut", "[중부청소년수련관]2026년 문화강좌 4분기 교육강사 긴급 위·수탁 서류", "https://example.org/a", "a",
                    posted_date=date(2026, 9, 20), detail_url="https://example.org/a"),
            Posting("cut", "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수", "https://example.org/b", "b",
                    posted_date=date(2026, 9, 21), detail_url="https://example.org/b"),
            Posting("cut", "[남부청소년수련관] 2025년 오래된 강사 위·수", "https://example.org/c", "c",
                    posted_date=date(2025, 1, 5), detail_url="https://example.org/c"),
        ]

    def fetch_detail(self, posting):
        self.fetched.append(posting.detail_url)
        return page_soup(PAGES[posting.detail_url].encode())


class NoHttp:
    def allow_legacy_tls(self, host):
        raise AssertionError


def test_pipeline_recovers_truncated_titles(tmp_path, rules, monkeypatch):
    from ulsan_jobs import pipeline

    monkeypatch.setitem(pipeline.COLLECTORS, "cut", TruncatedBoard)
    TruncatedBoard.fetched = []
    src = Source("cut", "잘린 제목 게시판", "cut", url="https://example.org/list", keyword_filter=False,
                 options={"truncated_titles": True})
    store = Store(tmp_path / "db.sqlite")
    now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)

    [result] = collect_all([src], rules, store, NoHttp(), now)
    new = {p.post_key: p for p in store.unreported()}
    # 잘린 뒷부분이 '서류심사 결과' 였던 글은 결과공고로 빠지고, 모집 공고는 전체 제목·마감일로 저장
    assert set(new) == {"b", "c"}
    assert new["b"].title == "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"
    assert new["b"].deadline == date(2026, 10, 2)
    assert result.matched == 2
    # 상세는 글마다 한 번만, 오래된 글(c)은 열지 않음
    assert TruncatedBoard.fetched == ["https://example.org/a", "https://example.org/b"]

    # 다음 날: 저장해 둔 전체 제목을 그대로 쓰고 상세를 다시 열지 않는다
    TruncatedBoard.fetched = []
    collect_all([src], rules, store, NoHttp(), now)
    assert store.title_of(new["b"].uid) == "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"
    assert TruncatedBoard.fetched == ["https://example.org/a"]  # 결과공고라 저장 안 된 글만 다시 확인
    store.close()
