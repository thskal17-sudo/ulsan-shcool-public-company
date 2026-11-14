import json
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
    assert "busan" in by and not by["busan"].ready  # 부산 DB 가 아직 없으면 '미도착'
    assert "gyeongbuk" in by and not by["gyeongbuk"].ready  # 경북도 같은 방식
    assert b.camp.ready and [i.title for i in b.camp.go] == ["진로캠프 운영"]
    assert b.missing == ["대학평생교육원", "부산", "경북"]
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
        {"제목": "한국어강사 신규 채용", "기관명": "김해대", "지역": "경남 김해", "마감일": "2026-10-20",
         "상세 내용": "한국어", "모집 인원": 8046},  # 수업 일정 비어 있음 → 채운다. 인원은 전화번호 조각 → 비운다
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
    assert up.by_region == {"울산": 2, "경남": 3} and up.missing == ["대학평생교육원", "부산", "경북"]
    rows = read_rows(up.path.read_bytes())
    assert [r["제목"] for r in rows] == ["방과후 강사 모집", "탁구 프로그램 도급강사 공개모집", "한국어강사 신규 채용", "어제 알린 공고"]
    assert rows[1]["메모"] == "경남 수집" and up.rows == 4
    assert rows[2]["수업 일정"] == "원문 공고 참고"  # 비어 있던 필수 칸을 채웠다
    assert "모집 인원" not in rows[2] and rows[2]["메모"] == "모집 인원 확인 필요 · 경남 수집"
    assert [r["수업 일정"] for r in up.data][2] == "원문 공고 참고"  # 사이트로 보내는 줄도 같다
    msg = sent[0]
    names = [part.get_filename() for part in msg.iter_attachments()]
    assert names == ["강사잇다_부울경_2026-09-30.xlsx"]
    html = msg.get_body(("html",)).get_content()
    assert "강사방에 붙여 넣을 글" in html and "합본에 빠진 곳: 대학평생교육원" in html
    assert out.briefing.share_text.splitlines()[0] == "[오늘의 부울경 강사 공고] 9/30(수) 새 공고 3건"


def test_upload_merges_busan_state_file(tmp_path):
    """부산: state 브랜치의 DB 로 '오늘 수집했는가'를 보고, 같은 브랜치의 양식 엑셀을 합본에 넣는다."""
    from ulsan_jobs.briefing import build_upload
    from ulsan_jobs.gangsaitda import read_rows, write_rows

    db = _db(tmp_path)  # 오늘 수집 기록이 있는 DB (부산 저장소도 같은 구조)
    busan_xlsx = write_rows(tmp_path / "bs.xlsx", [
        {"제목": "부산 돌봄 강사 모집", "기관명": "부산초", "지역": "부산 북구", "마감일": "2026-10-10",
         "수업 일정": "원문 공고 참고", "상세 내용": "돌봄", "원문 링크": "https://example.com/bs"},
    ], ROOT / "config" / "gangsaitda_template.xlsx").read_bytes()

    def fetch(url):
        if "busan-school-company" in url:
            return busan_xlsx if url.endswith(".xlsx") else db.read_bytes()
        return None

    cfg = load_config(ROOT / "config")
    b = gather(cfg, TODAY, db, fetch)
    assert {s.id: s.ready for s in b.sections} == {"ulsan": True, "gyeongnam": False, "lifelong": False, "busan": True, "gyeongbuk": False}
    up = build_upload(cfg, b, db, fetch, tmp_path / "out", ROOT / "config")
    assert up.by_region == {"울산": 2, "부산": 1} and up.missing == ["경남", "대학평생교육원", "경북"]
    rows = read_rows(up.path.read_bytes())
    assert [(r["제목"], r["메모"]) for r in rows if "부산" in r["지역"]] == [("부산 돌봄 강사 모집", "부산 수집")]
    assert len(up.data) == 3  # 사이트로 보낼 줄에도 부산이 들어간다


def test_upload_merges_gyeongbuk_state_file(tmp_path):
    """경북: 부산판과 같은 구조 (kyeongbuk 저장소 state 브랜치의 DB + gangsaitda_latest.xlsx)."""
    from ulsan_jobs.briefing import build_upload
    from ulsan_jobs.gangsaitda import read_rows, write_rows

    db = _db(tmp_path)
    gb_xlsx = write_rows(tmp_path / "gb.xlsx", [
        {"제목": "늘봄 외부강사 모집", "기관명": "포항제철초", "지역": "경북 포항시", "마감일": "2026-10-10",
         "수업 일정": "원문 공고 참고", "상세 내용": "늘봄", "원문 링크": "https://example.com/gb"},
    ], ROOT / "config" / "gangsaitda_template.xlsx").read_bytes()

    def fetch(url):
        if "thskal17-sudo/kyeongbuk/" in url:
            return gb_xlsx if url.endswith(".xlsx") else db.read_bytes()
        return None

    cfg = load_config(ROOT / "config")
    b = gather(cfg, TODAY, db, fetch)
    assert {s.id: s.ready for s in b.sections} == {"ulsan": True, "gyeongnam": False, "lifelong": False, "busan": False, "gyeongbuk": True}
    up = build_upload(cfg, b, db, fetch, tmp_path / "out", ROOT / "config")
    assert up.by_region == {"울산": 2, "경북": 1} and up.missing == ["경남", "대학평생교육원", "부산"]
    rows = read_rows(up.path.read_bytes())
    assert [(r["제목"], r["지역"], r["메모"]) for r in rows if "경북" in r["지역"]] == [("늘봄 외부강사 모집", "경북 포항시", "경북 수집")]
    assert len(up.data) == 3  # 사이트로 보낼 줄에도 경북이 들어간다


def test_clean_headcount():
    from ulsan_jobs.briefing import _clean_headcount

    assert [_clean_headcount(v) for v in (2, 2.0, "2", " 3명 ", "999")] == [2, 2, 2, 3, 999]
    assert [_clean_headcount(v) for v in (None, "", 0, 1000, 8046, "6448", "약 2명", "2~3", True)] == [None] * 9


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


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_push_upload_batches_and_sums(monkeypatch):
    from ulsan_jobs.briefing import push_upload

    calls = []

    def post(url, json, headers, timeout):
        calls.append((url, headers, json["rows"]))
        n = len(json["rows"])
        return _Resp(200, {"received": n, "created": n - 1,
                           "skipped": [{"title": "x", "reason": "같은 제목"}],
                           "failed": [{"title": "y", "reason": "마감"}] if len(calls) == 1 else []})

    rows = [{"제목": f"공고 {i}", "마감일": date(2026, 10, 15), "모집 인원": 2, "처리": None} for i in range(250)]
    r = push_upload(rows, "https://www.gangsaitda.com/api/jobs/import", "secret-token", post=post)
    assert len(calls) == 2 and [len(c[2]) for c in calls] == [200, 50]
    assert calls[0][1] == {"Authorization": "Bearer secret-token"}
    assert calls[0][2][0] == {"제목": "공고 0", "마감일": "2026-10-15", "모집 인원": 2, "처리": None}  # 날짜는 글자로
    assert (r.sent, r.created, r.skipped, r.failed, r.error) == (250, 248, 2, [("y", "마감")], "")


def test_push_upload_sends_same_columns_in_every_batch():
    """엑셀에서 읽은 '보류' 줄은 마감일 칸이 아예 없다. 묶음마다 칸 이름이 같아야 사이트가 묶음을 거절하지 않는다."""
    from ulsan_jobs.briefing import push_upload

    calls = []

    def post(url, json, headers, timeout):
        calls.append(json["rows"])
        return _Resp(200, {"received": len(json["rows"]), "created": 0, "skipped": [], "failed": []})

    rows = [{"제목": f"공고 {i}", "마감일": date(2026, 10, 15), "메모": "x"} for i in range(200)]
    rows += [{"제목": f"보류 {i}", "처리": "보류"} for i in range(30)]  # 마감일 칸 없음 (2번째 묶음 전부)
    r = push_upload(rows, "https://x", "t", post=post)
    assert r.error == "" and [len(c) for c in calls] == [200, 30]
    assert calls[1][0] == {"제목": "보류 0", "마감일": None, "메모": None, "처리": "보류"}
    assert calls[0][0] == {"제목": "공고 0", "마감일": "2026-10-15", "메모": "x", "처리": None}


def test_push_upload_reports_rejection_and_connection_error():
    import requests

    from ulsan_jobs.briefing import push_upload

    rows = [{"제목": "a"}]
    r = push_upload(rows, "https://x", "t", post=lambda *a, **k: _Resp(401, {"error": "열쇠가 맞지 않아요."}))
    assert r.error == "사이트가 거절함 (HTTP 401) 열쇠가 맞지 않아요." and r.created == 0

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    r = push_upload(rows, "https://x", "t", post=boom)
    assert r.error == "사이트 접속 실패: ConnectionError"


def test_run_briefing_pushes_upload_when_token_set(tmp_path, monkeypatch):
    from ulsan_jobs.mailer import MailConfig

    upload = _gyeongnam_upload(tmp_path)
    fetch = lambda url: (upload if url.endswith(".xlsx") else REPORT.encode()) if "Gyeongnam" in url else None  # noqa: E731
    sent, posted = [], []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    monkeypatch.setenv("BRIEFING_TO", "a@x.com")

    def post(url, json, headers, timeout):
        posted.append((url, headers["Authorization"], [r["제목"] for r in json["rows"]]))
        return _Resp(200, {"received": len(json["rows"]), "created": 2, "failed": [],
                           "skipped": [{"title": "어제 알린 공고", "reason": "이미"}]})

    # 열쇠가 없으면 보내지 않는다
    monkeypatch.delenv("GANGSAITDA_IMPORT_TOKEN", raising=False)
    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s1.txt", send_mail=True, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_dir=tmp_path / "o1", post=post)
    assert out.briefing.imported is None and posted == []
    assert "/admin/jobs/upload" in sent[0].get_body(("html",)).get_content()

    # 열쇠가 있으면 합본 줄을 그대로 보내고, 결과가 메일에 적힌다
    monkeypatch.setenv("GANGSAITDA_IMPORT_TOKEN", "secret-token")
    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s2.txt", send_mail=True, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_dir=tmp_path / "o2", post=post)
    imp = out.briefing.imported
    assert posted == [("https://www.gangsaitda.com/api/jobs/import", "Bearer secret-token",
                       ["방과후 강사 모집", "탁구 프로그램 도급강사 공개모집", "한국어강사 신규 채용", "어제 알린 공고"])]
    assert (imp.sent, imp.created, imp.skipped, imp.error) == (4, 2, 1, "")
    html = sent[1].get_body(("html",)).get_content()
    assert "사이트 자동 등록: 새로 <b>2</b>건" in html and "다시 올리지 않아도 됩니다" in html

    # 메일을 안 보내는 시험 실행에서는 사이트도 건드리지 않는다
    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s3.txt", send_mail=False, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_dir=tmp_path / "o3", post=post)
    assert out.briefing.imported is None and len(posted) == 1


def test_run_briefing_mail_still_goes_when_site_rejects(tmp_path, monkeypatch):
    from ulsan_jobs.mailer import MailConfig

    upload = _gyeongnam_upload(tmp_path)
    fetch = lambda url: (upload if url.endswith(".xlsx") else REPORT.encode()) if "Gyeongnam" in url else None  # noqa: E731
    sent = []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    monkeypatch.setenv("BRIEFING_TO", "a@x.com")
    monkeypatch.setenv("GANGSAITDA_IMPORT_TOKEN", "wrong")
    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s.txt", send_mail=True, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_dir=tmp_path / "o",
                       post=lambda *a, **k: _Resp(401, {"error": "열쇠가 맞지 않아요."}))
    assert out.sent and out.briefing.imported.error.startswith("사이트가 거절함 (HTTP 401)")
    html = sent[0].get_body(("html",)).get_content()
    assert "사이트 자동 등록 실패" in html and "/admin/jobs/upload" in html


# ---- 원문에서 내려간 공고 (경남 removed.json → 사이트 받는 문 removed) ----

REMOVED_JSON = json.dumps({"generated_at": "2026-09-30T05:00:00+09:00", "removed": [
    {"url": "https://www.gne.go.kr/works/user/recruitment/BD_recruitmentDetail.do?regSn=1815262",
     "title": "11월 전일제 강사 모집 공고", "org": "동포초", "removed_at": "2026-09-30T04:51:00+09:00"},
    {"url": "javascript:void(0)", "title": "주소 이상", "org": "x"},  # https 가 아니면 버린다
]}, ensure_ascii=False).encode()


def _fetch_with_removed(upload, removed=REMOVED_JSON):
    def fetch(url):
        if "Gyeongnam" not in url:
            return None
        if url.endswith(".xlsx"):
            return upload
        if url.endswith("removed.json"):
            return removed
        return REPORT.encode()
    return fetch


def test_upload_reads_gyeongnam_removed_list(tmp_path):
    from ulsan_jobs.briefing import build_upload

    upload = _gyeongnam_upload(tmp_path)
    cfg = load_config(ROOT / "config")
    fetch = _fetch_with_removed(upload)
    b = gather(cfg, TODAY, _db(tmp_path), fetch)
    up = build_upload(cfg, b, _db(tmp_path), fetch, tmp_path / "out", ROOT / "config")
    assert up.removed == [{"url": "https://www.gne.go.kr/works/user/recruitment/BD_recruitmentDetail.do?regSn=1815262",
                           "title": "11월 전일제 강사 모집 공고", "org": "동포초", "region": "경남"}]
    assert up.by_region["경남"] == 3  # 합본은 그대로

    # removed.json 이 없거나 깨져 있어도 합본은 그대로 만든다
    for broken in (None, b"not json", b'{"removed": "x"}'):
        fetch = _fetch_with_removed(upload, broken)
        up = build_upload(cfg, gather(cfg, TODAY, _db(tmp_path), fetch), _db(tmp_path), fetch, tmp_path / "o2", ROOT / "config")
        assert up.removed == [] and up.by_region["경남"] == 3 and "경남" not in up.missing


def test_push_upload_sends_removed_once_and_collects_hidden():
    from ulsan_jobs.briefing import push_upload

    calls = []

    def post(url, json, headers, timeout):
        calls.append(json)
        return _Resp(200, {"received": len(json["rows"]), "created": 0, "skipped": [], "failed": [],
                           "hidden": [{"id": 9, "title": "11월 전일제 강사 모집 공고"}] if "removed" in json else []})

    rows = [{"제목": f"공고 {i}"} for i in range(250)]
    r = push_upload(rows, "https://x", "t", post=post, removed=["https://a/1", "https://a/1", "https://a/2"])
    assert [("removed" in c) for c in calls] == [True, False]  # 첫 묶음에만
    assert calls[0]["removed"] == ["https://a/1", "https://a/2"]  # 겹친 주소는 한 번
    assert r.hidden == ["11월 전일제 강사 모집 공고"] and r.error == ""

    # 올릴 줄이 없어도 내려간 공고가 있으면 한 번 보낸다. 둘 다 없으면 보내지 않는다
    calls.clear()
    r = push_upload([], "https://x", "t", post=post, removed=["https://a/1"])
    assert calls == [{"rows": [], "removed": ["https://a/1"]}] and r.hidden == ["11월 전일제 강사 모집 공고"]
    calls.clear()
    push_upload([], "https://x", "t", post=post)
    assert calls == []


def test_run_briefing_reports_hidden_postings(tmp_path, monkeypatch):
    from ulsan_jobs.mailer import MailConfig

    fetch = _fetch_with_removed(_gyeongnam_upload(tmp_path))
    sent, posted = [], []
    monkeypatch.setattr("ulsan_jobs.mailer.MailConfig.from_env",
                        classmethod(lambda cls: MailConfig("me@x.com", "pw", ["me@x.com"])))
    monkeypatch.setattr("ulsan_jobs.mailer.send", lambda cfg, msg: sent.append(msg))
    monkeypatch.setenv("BRIEFING_TO", "a@x.com")
    monkeypatch.setenv("GANGSAITDA_IMPORT_TOKEN", "secret-token")

    def post(url, json, headers, timeout):
        posted.append(json.get("removed"))
        return _Resp(200, {"received": len(json["rows"]), "created": 1, "skipped": [], "failed": [],
                           "hidden": [{"id": 9, "title": "11월 전일제 강사 모집 공고"}]})

    out = run_briefing(db_path=_db(tmp_path), state_path=tmp_path / "s.txt", send_mail=True, force=True,
                       config_dir=ROOT / "config", now=NOW, fetch=fetch, out_dir=tmp_path / "o", post=post)
    assert posted == [["https://www.gne.go.kr/works/user/recruitment/BD_recruitmentDetail.do?regSn=1815262"]]
    assert out.briefing.imported.hidden == ["11월 전일제 강사 모집 공고"]
    html = sent[0].get_body(("html",)).get_content()
    assert "원문에서 내려간 공고 <b>1</b>건을 사이트에서 숨김" in html and "11월 전일제 강사 모집 공고" in html


def test_ulsan_rows_fill_districts_from_saved_lookups(tmp_path, monkeypatch):
    """사이트로 보내는 울산 줄에도 구·군이 들어간다: 수집 때 찾아 둔 학교 주소와 시설 이름표 (나이스에는 묻지 않음)."""
    from ulsan_jobs import school_district
    from ulsan_jobs.briefing import _ulsan_rows

    def no_network(http, school):
        raise AssertionError("브리핑에서는 나이스에 묻지 않는다")

    monkeypatch.setattr(school_district, "neis_addresses", no_network)
    db = tmp_path / "postings.db"
    store = Store(db)
    posts = [
        Posting(source_id="s1", title="중산초병설유치원 겨울방학 중 방과후 강사 채용", url="https://u/1", post_key="1",
                org_name="중산초등학교병설유치원", district="울산전체", deadline=date(2026, 10, 20)),
        Posting(source_id="s1", title="2027년 상반기 문수체육관 교육강사(프리랜서) 위촉 공고", url="https://u/2",
                post_key="2", org_name="문수경기장", district="울산전체", deadline=date(2026, 10, 20)),
        Posting(source_id="s1", title="옥현유치원 방과후 강사 채용", url="https://u/3", post_key="3",
                org_name="옥현유치원", district="울산전체", deadline=date(2026, 10, 20)),  # 아직 찾아 둔 기록 없음
    ]
    for p in posts:
        store.upsert(p, NOW)
    store.set_school_district("중산초등학교", "북구")
    store.commit()
    store.close()
    rows = _ulsan_rows(db, date(2026, 10, 1), ROOT / "config")
    assert {r["기관명"]: r["지역"] for r in rows} == {
        "중산초등학교병설유치원": "울산 북구", "문수경기장": "울산 남구", "옥현유치원": "울산",
    }
