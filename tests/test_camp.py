import json
from datetime import datetime

import pytest

from ulsan_jobs import camp
from ulsan_jobs.camp import (
    TIER_GO,
    TIER_REF,
    TIER_REVIEW,
    Bid,
    G2BError,
    load_camp_config,
    matches,
    parse_response,
    run_camp,
    tier_of,
    to_bid,
)
from ulsan_jobs.mailer import MailConfig
from ulsan_jobs.models import KST

from conftest import ROOT

NOW = datetime(2026, 9, 30, 7, 30, tzinfo=KST)


@pytest.fixture
def cfg():
    return load_camp_config(ROOT / "config")


def item(no, title, *, ord_="000", price="15000000", close="2026-10-06 10:00:00", method="수의계약",
         bid_method="전자시담", kind="등록", demand="부산진로고등학교"):
    return {
        "bidNtceNo": no, "bidNtceOrd": ord_, "bidNtceNm": title, "ntceInsttNm": demand, "dminsttNm": demand,
        "bidNtceDt": "2026-09-29 10:00:00", "bidClseDt": close, "presmptPrce": price,
        "cntrctCnclsMthdNm": method, "bidMethdNm": bid_method, "ntceKindNm": kind,
        "bidNtceDtlUrl": f"https://www.g2b.go.kr/link/{no}",
    }


class FakeClient:
    def __init__(self, rows, regions=None, fail_region=()):
        self.rows = rows
        self.region_map = regions or {}
        self.fail_region = set(fail_region)
        self.calls = 0
        self.region_calls = []

    def list_services(self, begin, end):
        self.calls += 1
        self.begin = begin
        return self.rows

    def regions(self, bid_no, bid_ord):
        self.calls += 1
        self.region_calls.append(bid_no)
        if bid_no in self.fail_region:
            raise G2BError("오류")
        return self.region_map.get(bid_no, [])


def test_matches_keywords(cfg):
    assert matches("2026학년도 진로체험 캠프 운영 용역", cfg)
    assert matches("고3 취업 캠프 위탁 운영", cfg)
    assert matches("청소년 기업가 정신 교육 운영", cfg)  # 띄어쓰기 무시
    assert matches("중학생 진로·미래설계 캠프", cfg)
    assert matches("부산도시공사 청년 취업 멘토링", cfg)
    assert not matches("진로교육원 냉난방 유지보수 용역", cfg)
    assert not matches("진로체험관 실시설계 용역", cfg)
    assert not matches("체험학습 버스 임차 용역", cfg)
    assert not matches("학교 급식실 청소 용역", cfg)


def test_tiers(cfg):
    small = Bid("A", "000", "진로캠프", price=15_000_000, price_label="추정가격", method="제한경쟁", regions=[])
    big = Bid("B", "000", "진로캠프", price=80_000_000, price_label="추정가격", method="제한경쟁", regions=["부산광역시"])
    sole = Bid("C", "000", "진로캠프", price=40_000_000, price_label="추정가격", method="수의계약", regions=["부산광역시"])
    other = Bid("D", "000", "진로캠프", price=10_000_000, price_label="추정가격", method="수의계약", regions=["울산광역시"])
    unknown = Bid("E", "000", "진로캠프", price=10_000_000, price_label="추정가격", method="수의계약", regions=None)
    budget = Bid("F", "000", "진로캠프", price=22_000_000, price_label="예산", method="제한경쟁", regions=[])
    assert tier_of(small, cfg) == TIER_GO
    assert tier_of(big, cfg) == TIER_REVIEW
    assert tier_of(sole, cfg) == TIER_GO
    assert tier_of(other, cfg) == TIER_REF
    assert tier_of(unknown, cfg) == TIER_REVIEW
    assert tier_of(budget, cfg) == TIER_GO  # 예산 2,200만원(부가세 포함) = 추정 2,000만원


def test_parse_response_shapes():
    body = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 2, "items": [{"a": 1}, {"a": 2}]}}}
    assert parse_response(json.dumps(body).encode()) == ([{"a": 1}, {"a": 2}], 2)
    nested = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 1, "items": {"item": {"a": 1}}}}}
    assert parse_response(json.dumps(nested).encode()) == ([{"a": 1}], 1)
    empty = {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": ""}}}
    assert parse_response(json.dumps(empty).encode()) == ([], 0)


def test_parse_response_errors():
    xml = (
        b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
        b"<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    with pytest.raises(G2BError, match="1~2시간"):
        parse_response(xml)
    bad = {"response": {"header": {"resultCode": "07", "resultMsg": "입력범위값 초과"}}}
    with pytest.raises(G2BError, match="07"):
        parse_response(json.dumps(bad).encode())


def test_to_bid_uses_budget_when_no_estimate():
    b = to_bid({**item("X", "진로캠프", price=""), "asignBdgtAmt": "11000000"})
    assert (b.price, b.price_label) == (11_000_000, "예산")
    assert b.method == "수의계약 · 전자시담"
    assert b.close_at == datetime(2026, 10, 6, 10, 0)


def test_run_camp_classifies_and_reports_once(tmp_path, monkeypatch):
    rows = [
        item("R1", "2026 진로체험 캠프 운영 용역"),  # 부산 제한 → 바로지원
        item("R2", "대학생 취업캠프 위탁운영", price="90000000", method="제한경쟁", bid_method="전자입찰"),  # 검토
        item("R3", "청소년 창업캠프 운영", demand="울산강북교육지원청"),  # 울산 제한 → 참고
        item("R4", "청사 방역 용역"),  # 키워드 아님
        item("R5", "진로캠프 운영", close="2026-09-29 10:00:00"),  # 이미 마감
        item("R6", "창업 멘토링 프로그램 운영", kind="취소"),  # 취소 공고
        item("R1", "2026 진로체험 캠프 운영 용역(정정)", ord_="001"),  # R1 정정 차수
    ]
    client = FakeClient(rows, {"R1": ["부산광역시"], "R2": [], "R3": ["울산광역시"]})
    sent = []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env", classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    db = tmp_path / "p.db"

    out = run_camp(db_path=db, send_mail=True, config_dir=ROOT / "config", now=NOW, client=client)
    tiers = {b.bid_no: b.tier for b in out.new}
    assert tiers == {"R1": TIER_GO, "R2": TIER_REVIEW, "R3": TIER_REF}
    assert [b.bid_no for b in out.new][0] == "R1"  # 바로지원이 맨 앞
    assert next(b for b in out.new if b.bid_no == "R1").title.endswith("(정정)")
    assert out.mailed and len(sent) == 1
    assert "바로지원 1건 · 검토 1건 · 참고 1건" in sent[0]["Subject"]
    assert (client.begin.month, client.begin.day) == (9, 20)  # 처음 실행: 10일 전부터

    # 다음 날: 같은 공고는 다시 알리지 않고, 지역도 다시 조회하지 않는다
    client2 = FakeClient(rows, {})
    out2 = run_camp(db_path=db, send_mail=True, config_dir=ROOT / "config",
                    now=datetime(2026, 10, 3, 7, 30, tzinfo=KST), client=client2)
    assert out2.new == [] and not out2.mailed and len(sent) == 1
    assert client2.region_calls == []
    assert (client2.begin.month, client2.begin.day) == (10, 1)  # 평소: 2일 전부터
    assert [b.bid_no for b in out2.closing_soon] == ["R1", "R2"]  # 10/6 마감 = D-3


def test_region_failure_goes_to_review(tmp_path):
    client = FakeClient([item("R1", "진로캠프 운영")], fail_region={"R1"})
    out = run_camp(db_path=tmp_path / "p.db", send_mail=False, config_dir=ROOT / "config", now=NOW, client=client)
    assert [(b.bid_no, b.tier) for b in out.new] == [("R1", TIER_REVIEW)]
    assert out.region_errors == 1


def test_canceled_after_report_leaves_closing_soon(tmp_path, monkeypatch):
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env", classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: None)
    db = tmp_path / "p.db"
    run_camp(db_path=db, send_mail=True, config_dir=ROOT / "config", now=NOW,
             client=FakeClient([item("R1", "진로캠프 운영")]))
    out = run_camp(db_path=db, send_mail=True, config_dir=ROOT / "config",
                   now=datetime(2026, 10, 3, 7, 30, tzinfo=KST),
                   client=FakeClient([item("R1", "진로캠프 운영", ord_="001", kind="취소")]))
    assert out.closing_soon == [] and out.new == []


def test_service_key_accepts_encoded(monkeypatch):
    monkeypatch.setenv("G2B_API_KEY", "abc%2Bdef%3D%3D")
    assert camp.service_key() == "abc+def=="
    monkeypatch.setenv("G2B_API_KEY", "")
    with pytest.raises(G2BError):
        camp.service_key()


def test_html_renders(cfg):
    out = camp.CampOutcome(now=NOW, scanned=1200, matched=3)
    out.new = [Bid("R1", "000", "진로캠프 <운영>", demand_org="부산고", price=15_000_000, price_label="추정가격",
                   method="수의계약", regions=[], tier=TIER_GO, url="https://x/1",
                   close_at=datetime(2026, 10, 6, 10, 0))]
    html = camp.camp_html(out, cfg)
    assert "진로캠프 &lt;운영&gt;" in html and "1,500만원" in html and "D-6" in html and "제한없음" in html


def test_first_run_noise_is_excluded(cfg):
    for title in [
        "2027학년도 수시 및 정시 실기(면접)고사 채점 솔루션 렌탈업체 선정",
        "2026학년도 환일고등학교 2학기 숙박형 현장체험학습(스키캠프) 위탁",
        "「2026 중등영어 겨울방학캠프」 운영 용역",
        "2026. G-글로컬 다산 리더스 국외 캠프 위탁 용역",
        "4대 과학기술원 창업리그「GRAVITY 2026」창업활동비 회계정산 용역",
        "창업지원 성과분석 및 발전방안",
    ]:
        assert not matches(title, cfg), title
    assert matches("2026학년도 지피지기 취업불패 취업캠프(간호학과) 운영 용역", cfg)


def test_hidden_price_is_unknown():
    assert to_bid(item("X", "취업캠프", price="1000")).price is None


def test_recheck_looks_up_unreported_regions_again(tmp_path):
    db = tmp_path / "p.db"
    run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW,
             client=FakeClient([item("R1", "진로캠프 운영")], {"R1": []}))
    again = FakeClient([item("R1", "진로캠프 운영")], {"R1": ["울산광역시"]})
    out = run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW, client=again, recheck=True)
    assert again.region_calls == ["R1"]
    assert [(b.bid_no, b.tier) for b in out.new] == [("R1", TIER_REF)]


def test_keyword_change_applies_to_unreported(tmp_path):
    db = tmp_path / "p.db"
    run_camp(db_path=db, send_mail=False, config_dir=ROOT / "config", now=NOW,
             client=FakeClient([item("R1", "진로캠프 운영")]))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    text = (ROOT / "config" / "camp.yaml").read_text(encoding="utf-8")
    (cfg_dir / "camp.yaml").write_text(text.replace("exclude:\n", "exclude:\n  - 진로캠프\n"), encoding="utf-8")
    out = run_camp(db_path=db, send_mail=False, config_dir=cfg_dir, now=NOW, client=FakeClient([]))
    assert out.new == []
