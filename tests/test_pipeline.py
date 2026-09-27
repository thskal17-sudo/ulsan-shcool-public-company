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


class FlakyCollector(Collector):
    calls = 0

    def collect(self):
        FlakyCollector.calls += 1
        if FlakyCollector.calls == 1:
            raise ConnectionError("일시 장애")
        return [Posting(**{**p.__dict__}) for p in POSTS]


def test_failed_source_is_retried_once(env, monkeypatch):
    # 관공서 서버의 일시 장애: 처음 실패한 소스는 끝에서 한 번 더 시도한다
    tmp_path, config, _ = env
    FlakyCollector.calls = 0
    monkeypatch.setitem(COLLECTORS, "fake", FlakyCollector)
    outcome = run(tmp_path, config, send_mail=False)
    states = {r.source_id: r.state for r in outcome.results}
    assert states == {"fake": "정상", "broken": "오류"}
    assert FlakyCollector.calls == 2
    assert len(outcome.new) == 2


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _RecordingHttp:
    def __init__(self):
        self.forgets = 0

    def forget_unreachable(self):
        self.forgets += 1


def _collect(tmp_path, config, clock, http):
    from ulsan_jobs.config import load_rules, load_settings
    from ulsan_jobs.storage import Store

    sources = pipeline.select_sources(load_settings(config).sources, None, 1)  # fake, broken
    store = Store(tmp_path / "db.sqlite")
    try:
        return pipeline.collect_all(sources, load_rules(config), store, http, NOW, time_limit=20 * 60, clock=clock)
    finally:
        store.close()


def test_retry_pass_forgets_unreachable_hosts(env):
    # 끝의 재시도에서는 앞에서 접속 실패한 서버도 한 번 더 기다려 본다
    tmp_path, config, _ = env
    http = _RecordingHttp()
    results = _collect(tmp_path, config, _Clock(), http)
    assert [r.state for r in results] == ["정상", "오류"]
    assert http.forgets == 1


def test_time_limit_skips_remaining_sources_and_retries(env, monkeypatch):
    # 접속 안 되는 서버가 많아 수집이 오래 걸리면 남은 소스·재시도를 건너뛰고 메일을 보낼 수 있게 한다
    tmp_path, config, _ = env
    clock = _Clock()
    broken_calls = []

    class SlowCollector(FakeCollector):
        def collect(self):
            clock.now += 25 * 60
            return super().collect()

    class CountingBroken(BrokenCollector):
        def collect(self):
            broken_calls.append(1)
            return super().collect()

    monkeypatch.setitem(COLLECTORS, "fake", SlowCollector)
    monkeypatch.setitem(COLLECTORS, "fake_broken", CountingBroken)
    http = _RecordingHttp()
    fake, broken = _collect(tmp_path, config, clock, http)

    assert fake.state == "정상"
    assert broken.state == "오류" and "20분을 넘겨 건너뜀" in broken.error
    assert broken_calls == [] and http.forgets == 0
