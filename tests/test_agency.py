"""국가 산하기관 자체 입찰 게시판 수집 (agency.py) 시험."""
import shutil
import sqlite3
from datetime import date, datetime

import pytest
import yaml

from ulsan_jobs import agency
from ulsan_jobs.agency import AgencyClient, RobotsBlocked, agency_tier, detail_text, parse_detail_text, short_key
from ulsan_jobs.camp import TIER_GO, TIER_REVIEW, Bid, G2BError, load_camp_config, run_camp
from ulsan_jobs.mailer import MailConfig
from ulsan_jobs.models import KST

from conftest import FIXTURES, ROOT

NOW = datetime(2026, 11, 20, 8, 23, tzinfo=KST)
TODAY = NOW.date()
SITE = "https://www.test-agency.or.kr"
LIST_URL = f"{SITE}/notice/list.do?bbsId=BID"


def view(sn):
    return f"{SITE}/notice/view.do?bbsSn={sn}&pageIndex=1"


DETAILS = {
    # 큰 경쟁입찰 → 검토
    view(9105): """<div class="view">
        <h3>2026 청소년 AI 코딩 캠프 운영 용역 입찰 공고</h3>
        <p>1. 입찰에 부치는 사항</p>
        <p>가. 용역명: 2026 청소년 AI 코딩 캠프 운영 용역</p>
        <p>나. 추정가격: 50,000,000원(부가가치세 별도)</p>
        <p>다. 계약방법: 제한경쟁입찰(협상에 의한 계약)</p>
        <p>2. 입찰서 제출 마감일시: 2026. 12. 1.(화) 14:00</p></div>""",
    # 수의계약 + 2천만원 이하 → 바로지원
    view(9104): """<div class="view">
        <p>사업예산 : 금18,000,000원 (부가세 포함)</p>
        <p>계약방법 : 수의계약 (소액)</p>
        <p>견적서 제출기간 : 2026. 11. 18.(수) ~ 2026. 11. 25.(수) 오후 6시</p></div>""",
    # 금액을 적지 않음 → 검토
    view(9101): """<div class="view">
        <p>제안서 접수 마감 : 2026년 11월 30일 17:00</p>
        <p>사업 내용은 첨부파일을 참고하십시오.</p></div>""",
}


class FakeAgency:
    """게시판 목록·상세를 시험 HTML 로 돌려주는 가짜 접속기."""

    def __init__(self, pages=None, details=None, robots_block=(), fail=()):
        self.pages = pages if pages is not None else {
            LIST_URL: (FIXTURES / "agency_board.html").read_text(encoding="utf-8")
        }
        self.details = DETAILS if details is None else details
        self.robots_block = set(robots_block)
        self.fail = set(fail)
        self.calls = 0
        self.fetched = []

    def get(self, url, encoding=""):
        if any(url.startswith(p) for p in self.robots_block):
            raise RobotsBlocked(f"robots.txt 가 막음: {url}")
        if any(url.startswith(p) for p in self.fail):
            raise ConnectionError("접속 안 됨")
        self.calls += 1
        self.fetched.append(url)
        if url in self.pages:
            return self.pages[url], url
        if url in self.details:
            return self.details[url], url
        raise AssertionError(f"모르는 주소: {url}")


class NoG2B:
    calls = 0

    def list_services(self, begin, end):
        return []

    def regions(self, bid_no, bid_ord):
        return []


@pytest.fixture
def config_dir(tmp_path):
    """camp.yaml 을 복사해 기관 게시판을 시험 주소 하나로 바꾼다 (S2B 는 끔)."""
    d = tmp_path / "config"
    shutil.copytree(ROOT / "config", d)
    data = yaml.safe_load((d / "camp.yaml").read_text(encoding="utf-8"))
    data["s2b"]["enabled"] = False
    data["agencies"] = {
        "enabled": True,
        "boards": [{"name": "시험재단", "id": "TEST", "url": LIST_URL}],
    }
    (d / "camp.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return d


@pytest.fixture
def cfg():
    return load_camp_config(ROOT / "config")


def run(config_dir, db, client, **kw):
    return run_camp(db_path=db, send_mail=kw.pop("send_mail", False), config_dir=config_dir,
                    now=kw.pop("now", NOW), client=NoG2B(), agency_client=client, **kw)


# ---------------------------------------------------------------- 상세 화면 읽기


def test_detail_price_method_deadline():
    d = parse_detail_text(detail_text(DETAILS[view(9105)]), TODAY)
    assert (d.price, d.price_label, d.method) == (50_000_000, "추정가격", "제한경쟁입찰(협상에 의한 계약)")
    assert d.close_at == datetime(2026, 12, 1, 14, 0)

    d = parse_detail_text(detail_text(DETAILS[view(9104)]), TODAY)
    assert (d.price, d.price_label, d.method) == (18_000_000, "예산", "수의계약 (소액)")
    assert d.close_at == datetime(2026, 11, 25, 18, 0)  # 기간이면 '~' 뒤 날짜, '오후 6시'

    d = parse_detail_text(detail_text(DETAILS[view(9101)]), TODAY)
    assert (d.price, d.method) == (None, "")
    assert d.close_at == datetime(2026, 11, 30, 17, 0)


@pytest.mark.parametrize("text, price", [
    ("기초금액: 금 일천오백만원정(15,000,000원)", 15_000_000),
    ("배정예산 : 30,000천원(VAT 포함)", 30_000_000),
    ("사업비 1.2억원", 120_000_000),
    ("소요예산: 4,500만원", 45_000_000),
    ("예산: 0원", None),  # 금액을 가려 둔 값은 모름
])
def test_detail_amount_units(text, price):
    assert parse_detail_text(text, TODAY).price == price


def test_method_is_left_empty_when_ambiguous():
    # 본문에 '수의계약'과 '경쟁'이 함께 나오면 수의계약으로 보지 않는다 (잘못 '바로지원'이 되지 않게)
    text = "입찰 방식: 일반경쟁. 유찰 시 수의계약으로 전환할 수 있음."
    assert parse_detail_text(text, TODAY).method == ""
    assert parse_detail_text("계약방법: 수의계약", TODAY).method == "수의계약"


def test_short_key():
    assert short_key("https://x.or.kr/notice/view.do?bbsSn=9105") == "9105"
    assert short_key("https://www.kosac.re.kr/menus/275/boards/403/posts/61722") == "61722"
    assert short_key("https://www.kosac.re.kr/menus/274/bns/PBANC_00000000000519") == "PBANC_00000000000519"
    assert short_key("ARTICLE-77") == "ARTICLE-77"
    assert len(short_key("https://x.or.kr/view?id=abc")) == 12


def test_deadline_defaults_to_end_of_day():
    d = parse_detail_text("입찰마감일 : 2026-12-03", TODAY)
    assert d.close_at == datetime(2026, 12, 3, 23, 59)


# ---------------------------------------------------------------- 등급


def test_agency_tier_rules(cfg):
    close = datetime(2026, 12, 1, 14, 0)
    small = Bid("A", "", "진로캠프 운영", price=15_000_000, price_label="추정가격", close_at=close)
    assert agency_tier(small, cfg) == TIER_GO  # 2천만원 이하
    sole = Bid("B", "", "진로캠프 운영", price=60_000_000, price_label="예산", method="수의계약", close_at=close)
    assert agency_tier(sole, cfg) == TIER_GO  # 수의계약
    big = Bid("C", "", "진로캠프 운영", price=50_000_000, price_label="추정가격", method="제한경쟁", close_at=close)
    assert agency_tier(big, cfg) == TIER_REVIEW
    # 금액이나 마감을 모르면 검토 (수의계약이라도)
    assert agency_tier(Bid("D", "", "진로캠프 운영 수의계약", close_at=close), cfg) == TIER_REVIEW
    assert agency_tier(Bid("E", "", "진로캠프 운영", price=10_000_000, price_label="추정가격"), cfg) == TIER_REVIEW


# ---------------------------------------------------------------- 수집 → DB → 메일


def test_agency_bids_go_to_db_and_mail(config_dir, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    db = tmp_path / "p.db"
    client = FakeAgency()

    out = run(config_dir, db, client, send_mail=True)

    got = {b.bid_no: (b.tier, b.topic) for b in out.new}
    assert got == {
        "TEST-9104": (TIER_GO, "진로·취업·창업"),  # 수의계약 1,800만원
        "TEST-9105": (TIER_REVIEW, "교육"),  # 추정가격 5천만원 경쟁입찰 (AI·코딩 → 교육)
        "TEST-9101": (TIER_REVIEW, "진로·취업·창업"),  # 금액 모름
    }
    # 플랫폼 구축(교육 제외어)·시설관리(제외어)는 빠지고, 지난달 글(10/1)은 보지 않는다
    assert (out.agency_scanned, out.agency_matched) == (5, 3)
    # 키워드에 맞은 공고만 상세를 연다
    assert sorted(u for u in client.fetched if "view.do" in u) == sorted(DETAILS)

    # DB: 같은 camp_bids 표에 source='기관명' 으로 들어간다 (Cowork 예약 작업이 그대로 읽음)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {r["bid_no"]: r for r in conn.execute("SELECT * FROM camp_bids WHERE source = '시험재단'")}
    assert set(rows) == {"TEST-9104", "TEST-9105", "TEST-9101"}
    r = rows["TEST-9105"]
    assert (r["org"], r["price"], r["price_label"], r["close_at"]) == ("시험재단", 50_000_000, "추정가격",
                                                                      "2026-12-01 14:00")
    assert r["url"] == view(9105) and r["tier"] == TIER_REVIEW and r["topic"] == "교육"
    assert r["posted_at"] == "2026-11-19 00:00" and r["reported_at"] is not None
    picked = [x[0] for x in conn.execute("SELECT bid_no FROM camp_bids WHERE tier IN ('바로지원', '검토')")]
    assert sorted(picked) == ["TEST-9101", "TEST-9104", "TEST-9105"]
    diag = conn.execute("SELECT diag FROM camp_runs").fetchone()[0]
    assert '"시험재단"' in diag
    conn.close()

    # 메일: 바로지원 1 · 검토 2, 기관 이름이 보인다
    assert out.mailed and len(sent) == 1
    assert "바로지원 1건 · 검토 2건" in sent[0]["Subject"]
    html = sent[0].get_body(("html",)).get_content()
    assert "시험재단" in html and "기관 입찰" in html and "기관 게시판 글 5건 중 3건" in html

    # 다음 날: 이미 알린 공고는 다시 보내지 않고, 상세도 다시 열지 않는다
    client2 = FakeAgency()
    out2 = run(config_dir, db, client2, send_mail=True, now=datetime(2026, 11, 21, 8, 23, tzinfo=KST))
    assert out2.new == [] and not out2.mailed and len(sent) == 1
    assert [u for u in client2.fetched if "view.do" in u] == []


def test_closed_agency_bid_is_not_saved(config_dir, tmp_path):
    details = dict(DETAILS)
    details[view(9104)] = "계약방법: 수의계약 / 사업예산: 18,000,000원 / 견적서 제출 마감: 2026. 11. 19. 18:00"
    out = run(config_dir, tmp_path / "p.db", FakeAgency(details=details))
    assert "TEST-9104" not in {b.bid_no for b in out.new}


def test_detail_failure_still_reports_as_review(config_dir, tmp_path):
    client = FakeAgency(details={})  # 상세 화면이 모두 열리지 않음
    client.fail = {f"{SITE}/notice/view.do"}
    out = run(config_dir, tmp_path / "p.db", client)
    assert {b.bid_no: b.tier for b in out.new} == {
        "TEST-9105": TIER_REVIEW, "TEST-9104": TIER_REVIEW, "TEST-9101": TIER_REVIEW,
    }


def test_robots_block_is_reported_and_others_continue(config_dir, tmp_path):
    data = yaml.safe_load((config_dir / "camp.yaml").read_text(encoding="utf-8"))
    data["agencies"]["boards"].append({"name": "막힌재단", "id": "BLK", "url": "https://blocked.or.kr/bid/list"})
    (config_dir / "camp.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    out = run(config_dir, tmp_path / "p.db", FakeAgency(robots_block={"https://blocked.or.kr"}))
    assert any("막힌재단" in e and "robots.txt" in e for e in out.errors)
    assert len(out.new) == 3  # 다른 기관은 그대로 읽음


def test_agency_failure_does_not_stop_g2b(config_dir, tmp_path):
    out = run(config_dir, tmp_path / "p.db", FakeAgency(fail={SITE}))
    assert any(e.startswith("시험재단:") for e in out.errors)  # 나라장터는 읽었으니 실행은 성공


def test_all_sources_failing_raises(config_dir, tmp_path):
    class BrokenG2B(NoG2B):
        def list_services(self, begin, end):
            raise G2BError("인증 오류")

    with pytest.raises(G2BError):
        run_camp(db_path=tmp_path / "p.db", send_mail=False, config_dir=config_dir, now=NOW, client=BrokenG2B(),
                 agency_client=FakeAgency(fail={SITE}))


def test_empty_board_page_is_an_error(config_dir, tmp_path):
    client = FakeAgency(pages={LIST_URL: "<html><body>점검 중입니다</body></html>"})
    out = run(config_dir, tmp_path / "p.db", client)
    assert any("행 0건" in e for e in out.errors)


# ---------------------------------------------------------------- robots.txt


class FakeHttp:
    def __init__(self, robots: str | None, status: int = 200):
        self.robots, self.status = robots, status

    def get(self, url, **kw):
        import requests

        resp = requests.Response()
        resp.status_code = self.status
        resp.url = url
        resp._content = (self.robots or "").encode()
        resp.encoding = "utf-8"
        if self.status >= 400:
            raise requests.HTTPError(response=resp)
        return resp


@pytest.mark.parametrize("robots, status, ok", [
    ("User-agent: *\nDisallow: /notice/\n", 200, False),
    ("User-agent: *\nDisallow: /admin/\n", 200, True),
    ("User-agent: *\nDisallow: /\n", 200, False),
    ("", 404, True),  # robots.txt 없음 → 제한 없음
    ("", 503, False),  # 서버 오류 → 막힌 것으로 본다
])
def test_robots_txt(robots, status, ok):
    client = AgencyClient(http=FakeHttp(robots, status))
    assert client.allowed(LIST_URL) is ok


def test_real_config_boards_are_valid(cfg):
    """config/camp.yaml 의 기관 게시판 설정이 읽히는지 (이름·주소·공고번호 앞글자 겹침 없음)."""
    boards = agency.boards_of(cfg)
    assert len({b.id for b in boards}) == len(boards)
    for b in boards:
        assert b.name and b.url.startswith("https://")


def test_rows_linking_to_g2b_are_skipped(config_dir, tmp_path):
    """한국과학창의재단 입찰공고처럼 나라장터로 바로 가는 글은 나라장터 수집에서 이미 받으므로 건너뛴다."""
    html = (FIXTURES / "agency_board.html").read_text(encoding="utf-8").replace(
        "/notice/view.do?bbsSn=9105&amp;pageIndex=1",
        "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK01834304&amp;bidPbancOrd=000")
    data = yaml.safe_load((config_dir / "camp.yaml").read_text(encoding="utf-8"))
    data["agencies"]["boards"][0]["skip_url"] = r"g2b\.go\.kr"
    (config_dir / "camp.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    out = run(config_dir, tmp_path / "p.db", FakeAgency(pages={LIST_URL: html}))
    assert {b.bid_no for b in out.new} == {"TEST-9104", "TEST-9101"}


def test_crawl_delay_is_respected():
    waits = []
    clock = iter([0.0, 2.0, 10.0])
    client = AgencyClient(http=FakeHttp("User-agent: *\nCrawl-delay: 10\n"), sleep=waits.append,
                          clock=lambda: next(clock))
    client.get(LIST_URL)
    client.get(view(9105))
    assert waits == [8.0]  # 앞 요청 뒤 2초 지났으니 8초 더 기다림
