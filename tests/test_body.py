"""상세 페이지 본문 뽑기 (body.py) — 강사잇다 '상세 내용'용."""
from bs4 import BeautifulSoup

from ulsan_jobs.body import body_text, notice_excerpt

PAGE = """<html><head><title>채용공고</title></head><body>
<div id="gnb"><ul><li><a href="/a">채용정보 모집공고 안내</a></li><li><a href="/b">강사 모집 게시판 바로가기</a></li></ul></div>
<div class="sub_wrap"><div class="bbs_view">
 <h3>2026학년도 한얼고등학교 영어과 시간강사 채용공고</h3>
 <ul class="info"><li>작성자 관리자</li><li>작성일 2026-11-02</li><li>조회수 21</li></ul>
 <div class="view_con">
  <p>2026학년도 한얼고등학교 영어과 시간강사 채용 공고입니다.</p>
  <p>1. 채용과목 및 인원: 영어 1명</p>
  <p>2. 계약기간: 2026.11.23(월)~2026.12.04(금) (2주간, 주당시수 14시수)</p>
  <p>3. 접수기간: 2026.11.02.(월)~2026.11.4.(수) 12:00까지</p>
  <p>4. 접수방법: 방문접수, 우편접수</p>
 </div>
 <div class="file"><a href="/f/1">채용공고문.hwp</a></div>
</div></div>
<div id="footer">주소: 부산광역시 … 대표전화 채용 문의는 각 부서로 연락 바랍니다.</div>
</body></html>"""


def test_body_is_the_posting_text_only():
    text = body_text(PAGE, "한얼고등학교 영어과 시간강사 채용공고")
    assert text.startswith("2026학년도 한얼고등학교 영어과 시간강사 채용 공고입니다.")
    assert "3. 접수기간: 2026.11.02.(월)~2026.11.4.(수) 12:00까지" in text
    for noise in ("채용정보 모집공고 안내", "작성일", "조회수", ".hwp", "대표전화"):
        assert noise not in text


def test_soup_is_not_changed():
    soup = BeautifulSoup(PAGE, "lxml")
    before = str(soup)
    assert body_text(soup, "x")
    assert str(soup) == before


def test_attachment_only_post_has_no_body():
    page = """<html><body><div class="bbsView"><p class="tit">장선종합사회복지관 강사 채용 공고(웰빙댄스)</p>
    <span>작성일 2026-11-04</span><div class="file"><a href="/f">채용공고.hwp</a></div></div></body></html>"""
    assert body_text(page, "장선종합사회복지관 강사 채용 공고(웰빙댄스)") == ""


def test_notice_excerpt():
    assert notice_excerpt(None) == ""
    assert notice_excerpt("짧은 글") == ""
    long = "1. 모집 분야: 방과후 영어\n\n   2. 접수 기간: 11. 2. ~ 11. 6.\n" * 5
    out = notice_excerpt(long)
    assert "\n\n" not in out and out.startswith("1. 모집 분야: 방과후 영어")
    assert len(notice_excerpt("가나다라 마바사 아자차\n" * 400)) <= 1500


def test_board_button_words_are_dropped():
    page = """<html><body><div class="bbs_ViewA"><div class="bbsV_cont">
    <p>1. 선발 예정 인원: 1명</p><p>2. 채용과목 : 조리</p><p>3. 근무 기간: 2026.11.23.~ 2026.12.18.</p>
    <p>4. 접수 기간: 2026. 11. 3.(화) ~ 2026. 11. 6.(금) 16:00</p></div>
    <div><span>다운로드 정보</span><span>( 2회 )</span><span>바로듣기</span><span>주소 복사</span></div></div></body></html>"""
    text = body_text(page, "세연고등학교 시간강사(조리) 채용 공고")
    assert text.endswith("4. 접수 기간: 2026. 11. 3.(화) ~ 2026. 11. 6.(금) 16:00")
