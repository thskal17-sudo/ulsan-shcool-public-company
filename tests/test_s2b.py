from datetime import date, datetime

import pytest

from ulsan_jobs import camp, s2b
from ulsan_jobs.camp import (
    TIER_GO,
    TIER_REF,
    TIER_REVIEW,
    TOPIC_CORE,
    TOPIC_EDU,
    G2BError,
    load_camp_config,
    run_camp,
    topic_of,
)
from ulsan_jobs.models import KST
from ulsan_jobs.s2b import DESIGNATED, S2BDetail, S2BRow, detail_url, parse_detail, parse_list

from conftest import FIXTURES, ROOT

NOW = datetime(2026, 9, 30, 7, 30, tzinfo=KST)


@pytest.fixture
def cfg():
    return load_camp_config(ROOT / "config")


# ---------------------------------------------------------------- 키워드


def test_camp_word_needs_a_career_word(cfg):
    assert topic_of("반송중 3학년 진로비전스쿨 캠프", cfg) == TOPIC_CORE
    assert topic_of("2026학년도 특성화고 취업역량 강화를 위한 인터뷰 클래스 진로 캠프", cfg) == TOPIC_CORE
    assert topic_of("인성 리더십 캠프 운영", cfg) == TOPIC_CORE
    assert topic_of("고창 미식문화테마상권 주니어 미식학 캠프 기획 및 운영 용역", cfg) is None
    assert topic_of("[앵커] 생성형 AI 융합 K-pop 송캠프 운영 용역", cfg) is None


def test_education_topic(cfg):
    assert topic_of("2026 공무원 청렴 교육 위탁 운영", cfg) == TOPIC_EDU
    assert topic_of("내성초 AI 메이커 WITH 로보틱스 캠프 운영 견적요청", cfg) is None  # 캠프만 → 아님
    assert topic_of("가천대학교 AI부트캠프 사업 생성형 AI 심화 교육 기획·운영 위탁 용역", cfg) == TOPIC_EDU
    assert topic_of("부산광역시교육청 진로교육 프로그램 운영", cfg) == TOPIC_CORE  # 진로가 먼저
    assert topic_of("부산광역시교육청 학생 코딩 교육 운영", cfg) == TOPIC_EDU  # 교육청을 빼도 '교육'이 남음


def test_education_word_inside_names_is_not_education(cfg):
    for title in [
        "부산광역시교육청학생예술문화회관 누림관 타일 및 체육관 문 교체",
        "2026. 구례글로컬교육센터 STEP 에듀캠프 현장체험학습 위탁 용역",
        "토성초등학교 교육복지 쿠킹 프로그램 운영 견적 요청",
        "교육시설 냉난방기 세척",
    ]:
        assert topic_of(title, cfg) is None, title


def test_s2b_noise_is_excluded(cfg):
    for title in [
        "대사초 2학기 진로현장체험학습 차량임차용역 견적요청",
        "2026년 하반기 진로교육원 전열교환기 필터 교체 용역",
        "장산중 3학년 진로체험학습 공연 관람",
        "모전초 11월 문화체험활동 1학년 진로체험 인형극 견적요청",
        "중현초등학교 4학년 생존수영 실기 교육 견적 요청",
        "경성전자고등학교 교직원 심폐소생술 교육",
        "2026년 토성초등학교 정기 위험성평가 견적 요청",
    ]:
        assert topic_of(title, cfg) is None, title


# ---------------------------------------------------------------- 화면 읽기


def test_parse_list():
    rows, last = parse_list((FIXTURES / "s2b_list.html").read_text(encoding="utf-8"))
    assert last == 481 and len(rows) == 10
    first = rows[0]
    assert first == S2BRow(code="202609291539568", kind="1", title="2026년 토성초등학교 정기 위험성평가 견적 요청",
                           category="용역", school="토성초등학교", posted=date(2026, 9, 29),
                           close=datetime(2026, 9, 30, 18, 0))
    assert {r.category for r in rows} == {"용역", "물품", "공사"}


def test_parse_detail_scopes():
    assert parse_detail((FIXTURES / "s2b_detail_designated.html").read_text(encoding="utf-8")) == S2BDetail(
        regions=[DESIGNATED], price=None)
    assert parse_detail((FIXTURES / "s2b_detail_region.html").read_text(encoding="utf-8")) == S2BDetail(
        regions=["부산"], price=3_166_000)
    open_html = ('<input id="oestimateCompany1" type="radio" name="tcmu100VO.estimateCompany" value="1" checked/>'
                 '<input id="oestimateCompany3" type="radio" name="tcmu100VO.estimateCompany" value="3" />')
    assert parse_detail(open_html).regions == []
    assert parse_detail("<html>점검 중</html>").regions is None


def test_detail_url_opens_without_session():
    assert detail_url("202609281532446", "2") == (
        "https://www.s2b.kr/S2BNCustomer/tcmo001.do?forwardName=openViewTcmo12&estimateCode=202609281532446"
        "&tender_step_code=A&page_flag=2")


# ---------------------------------------------------------------- 수집 → 등급


class FakeS2B:
    def __init__(self, pages, details, fail_detail=()):
        self.pages = pages  # {지역: [행, ...]}
        self.details = details
        self.fail_detail = set(fail_detail)
        self.calls = 0
        self.detail_calls = []
        self.starts = []

    def list_page(self, region, start, end, page):
        self.calls += 1
        self.starts.append(start)
        rows = self.pages.get(region, [])
        chunk = rows[(page - 1) * 10: page * 10]
        return chunk, max(1, (len(rows) + 9) // 10)

    def detail(self, code, kind):
        self.calls += 1
        self.detail_calls.append(code)
        if code in self.fail_detail:
            raise RuntimeError("접속 끊김")
        return self.details.get(code, S2BDetail(regions=[]))


class NoG2B:
    calls = 0

    def list_services(self, begin, end):
        return []

    def regions(self, bid_no, bid_ord):
        return []


def row(code, title, *, kind="1", category="용역", school="부산공업고등학교", close=datetime(2026, 10, 2, 18, 0)):
    return S2BRow(code=code, kind=kind, title=title, category=category, school=school, posted=date(2026, 9, 29),
                  close=close)


def test_s2b_rows_are_tiered(tmp_path):
    fake = FakeS2B(
        {"부산": [
            row("1", "진로비전스쿨 캠프"),  # 학교가 업체를 정해 둠 → 참고
            row("2", "2026학년도 2학기 창업 체험 프로그램 안내 공고", kind="2"),  # 부산 업체 → 바로지원
            row("3", "교육복지 아닌 학부모 교육 운영"),  # 교육 + 지정 업체 → 알리지 않음
            row("4", "교직원 청렴 교육 운영"),  # 교육 + 전체공개 → 바로지원(교육)
            row("5", "취업 캠프 운영", close=datetime(2026, 9, 29, 18, 0)),  # 이미 마감
            row("6", "진로 특강 운영"),  # 상세 조회 실패 → 검토
            row("7", "진로 도서 구입", category="물품"),  # 물품은 보지 않음
            row("8", "학교 급식실 위험성평가"),  # 키워드 아님
        ]},
        {"1": S2BDetail(regions=[DESIGNATED]), "2": S2BDetail(regions=["부산"], price=3_166_000),
         "3": S2BDetail(regions=[DESIGNATED]), "4": S2BDetail(regions=[])},
        fail_detail={"6"},
    )
    out = run_camp(db_path=tmp_path / "p.db", send_mail=False, config_dir=ROOT / "config", now=NOW,
                   client=NoG2B(), s2b_client=fake)
    got = {b.bid_no: (b.tier, b.topic, b.source) for b in out.new}
    assert got == {
        "S2B-2": (TIER_GO, TOPIC_CORE, "S2B"),
        "S2B-4": (TIER_GO, TOPIC_EDU, "S2B"),
        "S2B-6": (TIER_REVIEW, TOPIC_CORE, "S2B"),
        "S2B-1": (TIER_REF, TOPIC_CORE, "S2B"),
    }
    assert [b.bid_no for b in out.new][:2] == ["S2B-2", "S2B-4"]  # 같은 등급에서는 진로·취업·창업 먼저
    assert sorted(fake.detail_calls) == ["1", "2", "3", "4", "6"]
    assert out.s2b_scanned == 7 and out.s2b_matched == 6
    by_no = {b.bid_no: b for b in out.new}
    assert by_no["S2B-2"].price == 3_166_000 and by_no["S2B-2"].method == "2인 수의 견적"
    assert by_no["S2B-1"].region_text == DESIGNATED
    assert by_no["S2B-1"].url.startswith("https://www.s2b.kr/") and by_no["S2B-1"].demand_org == "부산공업고등학교"

    html = camp.camp_html(out, load_camp_config(ROOT / "config"))
    assert "[교육]" in html and "S2B · 1인 수의 견적" in html and "지정 업체" in html
    assert camp.camp_subject(out).startswith("[캠프·교육 수주]")


def test_s2b_first_run_looks_back_further_and_caches_details(tmp_path):
    db = tmp_path / "p.db"
    # 나라장터 기록만 있는 DB 에서도 S2B 첫 실행은 10일 전부터 읽는다
    run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW,
             client=NoG2B(), s2b_client=FakeS2B({}, {}))
    first = FakeS2B({"부산": [row("1", "진로 캠프 운영")]}, {"1": S2BDetail(regions=[])})
    run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW, client=NoG2B(), s2b_client=first)
    assert first.starts[0] == date(2026, 9, 20)
    again = FakeS2B({"부산": [row("1", "진로 캠프 운영")]}, {})
    out = run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW, client=NoG2B(),
                   s2b_client=again)
    assert again.starts[0] == date(2026, 9, 28)  # 기록이 생기면 최근 2일
    assert again.detail_calls == []  # 상세는 한 번만
    assert [(b.bid_no, b.tier) for b in out.new] == [("S2B-1", TIER_GO)]


def test_s2b_reads_every_page_and_region(tmp_path):
    many = [row(str(i), f"진로 캠프 {i}") for i in range(23)]
    fake = FakeS2B({"부산": many, "울산": [row("u1", "취업 캠프", school="울산공업고등학교")]}, {})
    out = run_camp(db_path=tmp_path / "p.db", send_mail=False, config_dir=ROOT / "config", now=NOW,
                   client=NoG2B(), s2b_client=fake)
    assert len(out.new) == 24
    assert {b.org for b in out.new} == {"S2B 부산", "S2B 울산"}


class BrokenG2B(NoG2B):
    def list_services(self, begin, end):
        raise G2BError("인증키 오류")


class BrokenS2B(FakeS2B):
    def list_page(self, region, start, end, page):
        raise RuntimeError("S2B 점검 중")


def test_one_source_failing_does_not_stop_the_other(tmp_path):
    out = run_camp(db_path=tmp_path / "p.db", send_mail=False, config_dir=ROOT / "config", now=NOW,
                   client=BrokenG2B(), s2b_client=FakeS2B({"부산": [row("1", "진로 캠프")]}, {}))
    assert [b.bid_no for b in out.new] == ["S2B-1"]
    assert out.errors == ["나라장터: 인증키 오류"]
    assert "읽지 못한 곳" in camp.camp_html(out, load_camp_config(ROOT / "config"))
    with pytest.raises(G2BError):
        run_camp(db_path=tmp_path / "q.db", send_mail=False, config_dir=ROOT / "config", now=NOW,
                 client=BrokenG2B(), s2b_client=BrokenS2B({}, {}))


def test_old_db_without_new_columns_still_works(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(camp.SCHEMA)
    conn.execute("INSERT INTO camp_bids (bid_no, title, tier, regions, close_at, first_seen_at) "
                 "VALUES ('R1', '진로캠프 운영', '바로지원', '[]', '2026-10-06 10:00', '2026-09-29T10:00:00')")
    conn.commit()
    conn.close()
    out = run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW, client=NoG2B())
    assert [(b.bid_no, b.source, b.topic) for b in out.new] == [("R1", "나라장터", TOPIC_CORE)]
