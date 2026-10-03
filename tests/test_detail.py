from datetime import date, datetime

from ulsan_jobs.collectors import Collector
from ulsan_jobs.config import Source
from ulsan_jobs.detail import block_text, extends, full_title, looks_truncated, page_soup
from ulsan_jobs.jobinfo import INFO_VERSION, extract_info
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


def test_full_title_mixed_with_post_info():
    # 제목이 작성자·작성일과 한 칸에 섞여 있어도 제목만 꺼낸다
    html = """<div class="view_tit">[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고
      <ul><li>작성자 관리자</li><li>작성일 2026-09-16</li><li>조회수 133</li></ul></div>"""
    soup = page_soup(html.encode())
    assert full_title(soup, "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수") == (
        "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"
    )
    one_line = "<p>[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고 작성자 관리자 작성일 2026-09-16 조회수 133</p>"
    assert full_title(page_soup(one_line.encode()), "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수") == (
        "[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고"
    )


def test_truncation_helpers():
    assert looks_truncated("서류적격 대상자 발표 및 면접심사 시행 공...")
    assert looks_truncated("기간제 근로자 재채용 공…")
    assert not looks_truncated("수영 강사 모집 공고")
    assert extends("면접심사 시행 공고", "면접심사 시행 공...")
    assert not extends("다른 공고", "면접심사 시행 공...")



def _spans(*parts: str) -> str:
    return "<p>" + "".join(f'<span style="font-family:굴림체">{t}</span>' for t in parts) + "</p>"


def test_block_text_joins_inline_spans():
    # 천상고 공고: 글자 조각마다 <span> 이 씌워져 있다. 태그마다 줄을 나누면 '가' 만 한 줄이 돼서
    # 지원 자격·제출 서류가 '가' 로 들어갔다
    html = (
        '<div class="view">'
        + _spans("2. ", "응시자격 ")
        + _spans(" ", "가", ". ", "해당과목 교원자격증 소지자")
        + _spans(" ", "나", ". ", "국가공무원법 제", "33", "조 및 기타 관계법령에 의하여 임용에 결격사유가 없는 자")
        + _spans("3. ", "제출서류")
        + _spans(" ", "가", ". ", "응시원서")
        + _spans(" ", "나", ". ", "교원자격증 사본 ", "1", "부")
        + _spans("4. ", "제출")
        + "</div>"
    )
    soup = page_soup(html.encode())
    assert extract_info(None, soup.get_text("\n")) == {"qualification": "가", "documents": "가"}  # 예전 방식
    assert extract_info(None, block_text(soup)) == {
        "qualification": "해당과목 교원자격증 소지자\n국가공무원법 제33조 및 기타 관계법령에 의하여 임용에 결격사유가 없는 자",
        "documents": "응시원서\n교원자격증 사본 1부",
    }


def test_block_text_splits_blocks_cells_and_breaks():
    html = """<div><table><tr><th>모집분야</th><td>수영<br>(초급반)</td></tr></table>
      <div><p>접수기간</p>2026. 10. 6.
      ~ 10. 12.<!-- 주석 --></div><ul><li><b>문의</b> : 052-000-0000</li></ul></div>"""
    lines = [" ".join(ln.split()) for ln in block_text(page_soup(html.encode())).splitlines() if ln.strip()]
    # 표 칸·<br>·문단은 줄을 나누고, 소스의 줄바꿈은 빈칸, 주석은 뺀다
    assert lines == ["모집분야", "수영", "(초급반)", "접수기간", "2026. 10. 6. ~ 10. 12.", "문의 : 052-000-0000"]


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


INFO_PAGE = """<div class="view"><h3>방과후 로봇과학 강사 모집 공고</h3><p>○ 교육기간: 2026.10.6.~12.15.</p><p>○ 교육대상: 초등 3~4학년</p>
<p>○ 모집인원: 1명</p><p>접수기간: 2026. 9. 21. ~ 2026. 10. 2.</p></div>"""


class InfoBoard(Collector):
    fetched: list[str] = []

    def collect(self):
        return [
            Posting("info", "방과후 로봇과학 강사 모...", "https://example.org/r", "r",
                    posted_date=date(2026, 9, 20), detail_url="https://example.org/r"),
            # 이미 마감된 공고는 공고문을 읽지 않는다
            Posting("info", "수영 강사 모집", "https://example.org/old", "old",
                    posted_date=date(2026, 9, 1), deadline=date(2026, 9, 10), detail_url="https://example.org/old"),
        ]

    def fetch_detail(self, posting):
        self.fetched.append(posting.detail_url)
        return page_soup(INFO_PAGE.encode())


def test_pipeline_reads_notice_info_once(tmp_path, rules, monkeypatch):
    from ulsan_jobs import pipeline

    monkeypatch.setitem(pipeline.COLLECTORS, "info", InfoBoard)
    InfoBoard.fetched = []
    src = Source("info", "정보 게시판", "info", url="https://example.org/list", keyword_filter=False)
    store = Store(tmp_path / "db.sqlite")
    now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)

    collect_all([src], rules, store, NoHttp(), now)
    posting = next(p for p in store.unreported() if p.post_key == "r")
    assert posting.deadline == date(2026, 10, 2)
    assert posting.title == "방과후 로봇과학 강사 모집 공고"  # 목록에서 잘린 제목은 전체로
    assert posting.info == {"schedule": "2026.10.6.~12.15.", "target": "초등 3~4학년", "headcount": 1, "v": INFO_VERSION}
    assert InfoBoard.fetched == ["https://example.org/r"]

    # 다음 날: 이미 읽은 공고문은 다시 열지 않는다
    InfoBoard.fetched = []
    collect_all([src], rules, store, NoHttp(), now)
    assert InfoBoard.fetched == []
    store.close()


def test_notice_is_read_again_when_extraction_improves(tmp_path, rules, monkeypatch):
    from ulsan_jobs import pipeline

    monkeypatch.setitem(pipeline.COLLECTORS, "info", InfoBoard)
    src = Source("info", "정보 게시판", "info", url="https://example.org/list", keyword_filter=False)
    store = Store(tmp_path / "db.sqlite")
    now = datetime(2026, 9, 26, 7, 0, tzinfo=KST)
    collect_all([src], rules, store, NoHttp(), now)
    uid = next(p.uid for p in store.unreported() if p.post_key == "r")
    store.set_info(uid, {"qualification": "예전 방식으로 찾은 값"})  # 버전 표시 없는 옛 정보

    InfoBoard.fetched = []
    collect_all([src], rules, store, NoHttp(), now)
    assert InfoBoard.fetched == ["https://example.org/r"]
    assert store.detail_state(uid)[1]["schedule"] == "2026.10.6.~12.15."
    store.close()
