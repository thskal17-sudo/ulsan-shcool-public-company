from datetime import date, datetime

from ulsan_jobs.briefing import (
    Briefing,
    CampSummary,
    Section,
    gather,
    html_body,
    load_config,
    parse_md_report,
    run_briefing,
    should_send,
    subject_line,
)
from ulsan_jobs.camp import Bid, CampStore
from ulsan_jobs.models import KST, Posting, SourceResult
from ulsan_jobs.storage import Store

from conftest import ROOT

TODAY = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 7, 0, tzinfo=KST)

REPORT = """# 경남 강사 구인공고 일일 요약 — 2026-09-30 (수)

신규 **2건** · 마감 임박 **1건** · 변경 **0건** · 수집 소스 21/39 정상
## ⏰ 마감 임박 (3일 이내)

1. **[학교·방과후] 주동초 · 2026. 방과후학교 프로그램(뉴스포츠) 개인위탁 외부강사 모집** — D-1
   마감 10.01(목) 16:00 · 경남 · 용역 · 재공고   → https://example.com/a (출처: 경상남도교육청 구인구직포털)

## 🆕 신규 공고

1. **[체육] 창원시설공단(시민생활체육관팀) · 탁구 프로그램 도급강사 공개모집 · 2차**
   마감 10.13(화) 18:00 · 창원 · 기타   → https://example.com/b (출처: 창원시)
2. **[학교·방과후] 경운초 · 돌봄교실 특기적성 외부강사 공고**
   마감 10.05(월) · 경남 · 프리랜서 · ⚠ 강사 공고 여부 확인 필요   → https://example.com/c (출처: 교육청)

## 🔄 변경 공고

없음

## 📎 부록: 수집 상태

| 구분 | 값 |
|---|---|
| 실패 소스 | `gojobs` (목록 실패: HTTP 403), `hadong` (timed out) |
"""


def test_parse_md_report():
    new, closing, problems = parse_md_report(REPORT, TODAY)
    assert [i.org for i in new] == ["창원시설공단(시민생활체육관팀)", "경운초"]
    assert new[0].title == "탁구 프로그램 도급강사 공개모집 · 2차"  # 제목 안의 ' · ' 는 유지
    assert new[0].deadline == date(2026, 10, 13) and new[0].deadline_text == "10/13 18:00"
    assert new[0].url == "https://example.com/b" and new[0].region == "창원"
    assert new[1].note == "강사 공고 여부 확인 필요"
    assert [i.deadline for i in closing] == [date(2026, 10, 1)]
    assert problems == ["gojobs", "hadong"]


def test_parse_empty_report():
    text = "## ⏰ 마감 임박\n\n없음\n\n## 🆕 신규 공고\n\n없음\n"
    assert parse_md_report(text, TODAY) == ([], [], [])


def _db(tmp_path):
    db = tmp_path / "postings.db"
    store = Store(db)
    p1 = Posting(source_id="s1", title="방과후 강사 모집", url="https://u/1", post_key="1", org_name="울산초",
                 district="남구", deadline=date(2026, 10, 2))
    p2 = Posting(source_id="s1", title="어제 알린 공고", url="https://u/2", post_key="2", org_name="울산중",
                 district="중구", deadline=date(2026, 10, 20))
    for p in (p1, p2):
        store.upsert(p, NOW)
    store.mark_reported([p1.uid], NOW)
    store.mark_reported([p2.uid], datetime(2026, 9, 29, 7, 0, tzinfo=KST))
    store.log_runs([SourceResult("s1", "울산초 게시판", state="정상"), SourceResult("s2", "구청", state="오류")], NOW)
    store.close()
    camp = CampStore(db)
    b = Bid("R1", "000", "진로캠프 운영", demand_org="부산고", price=12_000_000, price_label="추정가격",
            close_at=datetime(2026, 10, 6, 10, 0), url="https://g2b/1", regions=[], tier="바로지원")
    camp.save(b, NOW)
    camp.mark_reported(["R1"], NOW)
    camp.conn.execute("INSERT INTO camp_runs VALUES (?, 1, 1, 1, 1, 0, '{}')", (NOW.isoformat(),))
    camp.close()
    return db


def _fetch(url):
    return REPORT.encode() if "Gyeongnam" in url else None  # 평생교육원은 아직 없음


def test_gather_reads_all_sources(tmp_path):
    b = gather(load_config(ROOT / "config"), TODAY, _db(tmp_path), _fetch)
    by = {s.id: s for s in b.sections}
    assert by["ulsan"].ready and [i.title for i in by["ulsan"].new] == ["방과후 강사 모집"]
    assert [i.title for i in by["ulsan"].closing] == ["방과후 강사 모집"]
    assert by["ulsan"].problems == ["s2"]
    assert by["gyeongnam"].ready and len(by["gyeongnam"].new) == 2
    assert not by["lifelong"].ready
    assert "busan" not in by  # 아직 꺼 둠
    assert b.camp.ready and [i.title for i in b.camp.go] == ["진로캠프 운영"]
    assert b.missing == ["대학평생교육원"]
    assert "강사 신규 3건" in subject_line(b) and "캠프 바로지원 1건" in subject_line(b)
    html = html_body(b)
    assert "아직 오늘 자료가 도착하지 않은 곳" in html and "진로캠프 운영" in html


def test_should_send_rules():
    cfg = {"send_anyway_after": "09:00"}
    ready = Briefing(TODAY, [Section("a", "울산", ready=True)], CampSummary(ready=True))
    waiting = Briefing(TODAY, [Section("a", "울산", ready=False)], CampSummary(ready=True))
    early, late = NOW, datetime(2026, 9, 30, 9, 5, tzinfo=KST)
    assert should_send(ready, early, cfg, False, False)[0]
    assert not should_send(ready, early, cfg, True, False)[0]  # 오늘 이미 보냄
    assert not should_send(waiting, early, cfg, False, False)[0]  # 기다림
    assert should_send(waiting, late, cfg, False, False)[0]  # 09:00 이후엔 도착한 것만
    assert should_send(ready, early, cfg, True, True)[0]  # 강제


def test_run_briefing_sends_once(tmp_path, monkeypatch):
    from ulsan_jobs.mailer import MailConfig

    sent = []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append((cfg.to, msg)))
    monkeypatch.setenv("BRIEFING_TO", "a@x.com, b@y.com")
    db, state = _db(tmp_path), tmp_path / "last.txt"
    fetch_all = lambda url: REPORT.encode()  # noqa: E731
    out = run_briefing(db_path=db, state_path=state, send_mail=True, config_dir=ROOT / "config", now=NOW,
                       fetch=fetch_all)
    assert out.sent and sent[0][0] == ["a@x.com", "b@y.com"]
    assert state.read_text() == "2026-09-30"
    again = run_briefing(db_path=db, state_path=state, send_mail=True, config_dir=ROOT / "config", now=NOW,
                         fetch=fetch_all)
    assert not again.sent and len(sent) == 1


def test_run_briefing_requires_recipients(tmp_path, monkeypatch):
    import pytest

    monkeypatch.delenv("BRIEFING_TO", raising=False)
    monkeypatch.setenv("SMTP_USER", "me@x.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "pw")
    with pytest.raises(RuntimeError, match="BRIEFING_TO"):
        run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s.txt", send_mail=True,
                     config_dir=ROOT / "config", now=NOW, fetch=lambda url: REPORT.encode())


# ---------------------------------------------------------------- 강사잇다 합본 엑셀 · 강사방 공유 글


def _gyeongnam_upload(tmp_path) -> bytes:
    """경남 저장소가 reports/gangsaitda/latest.xlsx 로 커밋하는 강사잇다 양식 (13칸)."""
    from ulsan_jobs.gangsaitda import write_rows

    rows = [
        {"제목": "탁구 프로그램 도급강사 공개모집", "기관명": "창원시설공단", "지역": "경남 창원", "마감일": "2026-10-13",
         "수업 일정": "원문 공고 참고", "상세 내용": "탁구", "원문 링크": "https://example.com/b"},
        {"제목": "방과후 강사 모집", "기관명": "울산초", "지역": "울산", "마감일": "2026-10-02"},  # 울산과 겹침
        {"제목": "지난 공고", "기관명": "옛학교", "지역": "경남", "마감일": "2026-09-01"},  # 캐시된 지난 파일 대비
    ]
    return write_rows(tmp_path / "gn.xlsx", rows, ROOT / "config" / "gangsaitda_template.xlsx").read_bytes()


def test_briefing_attaches_merged_upload_and_share_text(tmp_path, monkeypatch):
    from ulsan_jobs.gangsaitda import read_rows
    from ulsan_jobs.mailer import MailConfig

    upload = _gyeongnam_upload(tmp_path)

    def fetch(url):
        if "Gyeongnam" in url:
            return upload if url.endswith(".xlsx") else REPORT.encode()
        return None  # 평생교육원은 아직

    sent = []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    monkeypatch.setenv("BRIEFING_TO", "a@x.com")
    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s.txt", send_mail=True, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_html=tmp_path / "out" / "b.html")
    up = out.briefing.upload
    assert up.path.name == "강사잇다_부울경_2026-09-30.xlsx"
    assert up.by_region == {"울산": 2, "경남": 2} and up.missing == ["대학평생교육원"]
    rows = read_rows(up.path.read_bytes())
    assert [r["제목"] for r in rows] == ["방과후 강사 모집", "탁구 프로그램 도급강사 공개모집", "어제 알린 공고"]
    assert rows[1]["메모"] == "경남 수집" and up.rows == 3
    msg = sent[0]
    names = [part.get_filename() for part in msg.iter_attachments()]
    assert names == ["강사잇다_부울경_2026-09-30.xlsx"]
    html = msg.get_body(("html",)).get_content()
    assert "강사방에 붙여 넣을 글" in html and "합본에 빠진 곳: 대학평생교육원" in html
    assert out.briefing.share_text.splitlines()[0] == "[오늘의 부울경 강사 공고] 9/30(수) 새 공고 3건"


def test_share_text_format():
    from ulsan_jobs.briefing import Item, share_text

    ulsan = Section("ulsan", "울산", ready=True,
                    new=[Item("2026학년도 방과후 로봇 강사 모집", region="남구", deadline=date(2026, 10, 2))],
                    closing=[Item("수영 강사 모집", deadline=TODAY)])
    gn = Section("gyeongnam", "경남", ready=True, new=[Item("탁구 강사 모집", region="창원", deadline=date(2026, 10, 8))])
    later = Section("lifelong", "대학평생교육원", ready=False, new=[Item("안 보여야 함")])
    b = Briefing(TODAY, [ulsan, gn, later], CampSummary())
    cfg = {"gangsaitda": {"site_url": "https://gangsaitda.kr", "share_max": 1}}
    assert share_text(b, cfg) == (
        "[오늘의 부울경 강사 공고] 9/30(수) 새 공고 2건 · 오늘 마감 1건\n"
        "- 방과후 로봇 강사 모집 (울산 · D-2)\n"
        "외 1건\n"
        "전체 보기: https://gangsaitda.kr"
    )
    quiet = Briefing(TODAY, [Section("ulsan", "울산", ready=True, closing=[Item("수영 강사 모집", deadline=TODAY)])],
                     CampSummary())
    assert share_text(quiet, {}) == (
        "[오늘의 부울경 강사 공고] 9/30(수) 새 공고는 없고 마감 임박 1건\n"
        "- 수영 강사 모집 (울산 · D-day)\n"
        "전체 보기: [사이트 주소]"
    )
