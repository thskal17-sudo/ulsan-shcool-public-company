import shutil
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from ulsan_jobs import pipeline
from ulsan_jobs.collectors import COLLECTORS, Collector
from ulsan_jobs.models import KST, Posting

ROOT = Path(__file__).resolve().parent.parent

SOURCES_YAML = """
max_phase: 1
sources:
  - id: fake
    name: 가짜 게시판
    org_type: 교육청·학교
    district: 울산전체
    collector: fake
    url: https://example.org/list
    keyword_filter: true
    phase: 1
    enabled: true
  - id: broken
    name: 고장난 게시판
    org_type: 지자체
    collector: fake_broken
    url: https://example.org/broken
    phase: 1
    enabled: true
  - id: later
    name: 다음 단계 게시판
    collector: fake
    url: https://example.org/later
    phase: 2
    enabled: true
"""

POSTS = [
    # 신규·마감 전
    Posting("fake", "방과후 독서논술 강사 모집", "https://example.org/1", "1", "척과초등학교", deadline=date(2026, 10, 1)),
    # 마감임박
    Posting("fake", "수영 강사 모집", "https://example.org/2", "2", "울산시설공단", deadline=date(2026, 9, 28)),
    # 이미 마감 → 신규 아님
    Posting("fake", "늘봄 강사 모집", "https://example.org/3", "3", "남목초등학교", deadline=date(2026, 9, 20)),
    # 강사 공고 아님
    Posting("fake", "기간제 교원 채용", "https://example.org/4", "4", "대현중학교"),
]


class FakeCollector(Collector):
    def collect(self):
        return [Posting(**{**p.__dict__}) for p in POSTS]


class BrokenCollector(Collector):
    def collect(self):
        raise ConnectionError("접속 실패")


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
    shutil.copy(ROOT / "config" / "keywords.yaml", config / "keywords.yaml")
    monkeypatch.setitem(COLLECTORS, "fake", FakeCollector)
    monkeypatch.setitem(COLLECTORS, "fake_broken", BrokenCollector)
    sent = []
    monkeypatch.setattr(pipeline, "send", lambda cfg, msg: sent.append(msg))
    monkeypatch.setenv("SMTP_USER", "me@example.org")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "abcd efgh")
    return tmp_path, config, sent


NOW = datetime(2026, 9, 26, 7, 0, tzinfo=KST)


def run(tmp_path, config, send_mail, now=NOW):
    return pipeline.run(
        db_path=tmp_path / "db.sqlite", out_dir=tmp_path / "out", send_mail=send_mail, config_dir=config, now=now
    )


def test_run_builds_report_and_mails(env):
    tmp_path, config, sent = env
    outcome = run(tmp_path, config, send_mail=True)

    states = {r.source_id: r.state for r in outcome.results}
    assert states == {"fake": "정상", "broken": "오류"}  # phase 2 소스는 수집하지 않음
    assert [p.title for p in outcome.new] == ["수영 강사 모집", "방과후 독서논술 강사 모집"]  # 마감 빠른 순
    assert [p.title for p in outcome.closing_soon] == ["수영 강사 모집"]
    assert outcome.mailed and len(sent) == 1

    msg = sent[0]
    assert "신규 2건" in msg["Subject"] and "마감임박 1건" in msg["Subject"]
    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == "울산_강사구인_2026-09-26.xlsx"

    wb = load_workbook(outcome.report_path)
    assert wb.sheetnames == ["신규 (2)", "마감임박 (1)", "진행중 전체 (2)", "수집현황"]
    ws = wb["신규 (2)"]
    assert ws["F2"].value == "수영 강사 모집"
    assert ws["F2"].hyperlink.target == "https://example.org/2"
    assert ws["I2"].value == "D-2"
    status = wb["수집현황"]
    assert status["C3"].value == "오류"


def test_second_run_has_no_new_after_mail(env):
    tmp_path, config, sent = env
    run(tmp_path, config, send_mail=True)
    outcome = run(tmp_path, config, send_mail=True, now=datetime(2026, 9, 27, 7, 0, tzinfo=KST))
    assert outcome.new == []
    assert len(sent) == 2  # 신규가 없어도 확인용 메일은 보냄


def test_unsent_postings_stay_new(env):
    # 메일을 보내지 않은(또는 실패한) 실행 뒤에는 다음 실행에서 다시 신규로 잡혀야 한다
    tmp_path, config, sent = env
    run(tmp_path, config, send_mail=False)
    outcome = run(tmp_path, config, send_mail=True)
    assert len(outcome.new) == 2


def test_missing_mail_settings_fail_loudly(env, monkeypatch):
    tmp_path, config, _ = env
    monkeypatch.delenv("SMTP_APP_PASSWORD")
    with pytest.raises(RuntimeError, match="SMTP_APP_PASSWORD"):
        run(tmp_path, config, send_mail=True)
