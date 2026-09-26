"""하루 1회 실행 흐름: 수집 → 판별·분류 → 저장(신규 판정) → 엑셀 → 메일."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .attachments import extract_text, find_attachments
from .classify import categorize, judge
from .collectors import COLLECTORS, NotConfigured
from .config import DEFAULT_CONFIG_DIR, Rules, Source, load_rules, load_settings
from .dates import extract_deadline
from .detail import extends, full_title, looks_truncated, page_text
from .http import Http
from .matching import finished_by
from .mailer import MailConfig, build_message, html_body, send, subject_line, text_body
from .models import Posting, SourceResult, now_kst
from .report_excel import build_report, sort_key
from .storage import Store

log = logging.getLogger(__name__)


@dataclass
class RunOutcome:
    today: date
    results: list[SourceResult]
    new: list[Posting]
    closing_soon: list[Posting]
    active: list[Posting]
    report_path: Path
    mailed: bool = False
    source_names: dict[str, str] = field(default_factory=dict)


def select_sources(sources: list[Source], only: list[str] | None, max_phase: int) -> list[Source]:
    if only:
        unknown = set(only) - {s.id for s in sources}
        if unknown:
            raise ValueError(f"알 수 없는 소스 id: {sorted(unknown)}")
        return [s for s in sources if s.id in only]
    return [s for s in sources if s.enabled and s.phase <= max_phase]


def collect_all(
    sources: list[Source],
    rules: Rules,
    store: Store,
    http: Http,
    now: datetime,
    detail_limit: int = 100,
) -> list[SourceResult]:
    """모든 소스를 수집한다. 실패(오류·0건)한 소스는 끝에서 한 번 더 시도한다 (관공서 서버의 일시 장애 대비)."""
    budget = {"details": detail_limit}
    results = [_collect_source(src, rules, store, http, now, budget) for src in sources]
    for i, src in enumerate(sources):
        if results[i].state in ("오류", "0건"):
            log.info("다시 시도: %s (%s)", src.name, results[i].error)
            results[i] = _collect_source(src, rules, store, http, now, budget)
    return results


def _collect_source(
    src: Source, rules: Rules, store: Store, http: Http, now: datetime, budget: dict[str, int]
) -> SourceResult:
    today = now.date()
    result = SourceResult(src.id, src.name, src.org_type, src.url or "")
    collector_cls = COLLECTORS.get(src.collector)
    if collector_cls is None:
        result.state, result.error = "미구현", f"수집기 '{src.collector}' 는 다음 단계에서 구현 예정"
        return result
    if not src.url and not src.collector.endswith("_api"):
        result.state, result.error = "설정필요", "url 없음"
        return result
    if src.options.get("legacy_tls") and src.url:
        http.allow_legacy_tls(urlsplit(src.url).hostname or "")
    collector = collector_cls(src, http, today)
    log.info("수집: %s", src.name)
    try:
        items = collector.collect()
    except NotConfigured as exc:
        result.state, result.error = "설정필요", str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 - 한 소스 실패가 전체를 멈추지 않게 한다
        log.warning("수집 실패 %s: %s", src.id, exc)
        log.debug("상세", exc_info=True)
        result.state, result.error = "오류", f"{type(exc).__name__}: {exc}"[:300]
        return result

    result.fetched = len(items)
    result.samples = [p.title for p in items[:3]]
    if not items:
        result.state, result.error = "0건", "목록에서 글을 하나도 읽지 못함 (서버 일시 장애 또는 사이트 구조 변경. 며칠 계속되면 확인 필요)"
        return result

    truncated_board = bool(src.options.get("truncated_titles"))
    result_notices: list[Posting] = []
    for p in items:
        soup, tried = None, False
        if p.detail_url and (truncated_board or looks_truncated(p.title)):
            known = store.title_of(p.uid)
            if known and extends(known, p.title):
                p.title = known  # 전에 상세 페이지에서 받아 둔 전체 제목
            elif (
                not known
                and judge(p.title, rules, src.keyword_filter, p.label) is not None
                and worth_detail(p, today, rules)
                and budget["details"] > 0
            ):
                budget["details"] -= 1
                soup, tried = _fetch_detail(collector, p), True
                if soup is not None:
                    p.title = full_title(soup, p.title) or p.title

        status = judge(p.title, rules, src.keyword_filter, p.label, keep_results=True)
        if status is None:
            continue
        if status == "결과공고":
            result_notices.append(p)
            if not rules.keep_result_notices:
                continue
        p.status = status
        p.category = categorize(f"{p.title} {p.label}", p.org_name, rules)
        result.matched += 1
        if not store.upsert(p, now):
            continue
        result.new += 1
        if p.deadline is None and p.detail_url and worth_detail(p, today, rules):
            if not tried and budget["details"] > 0:
                budget["details"] -= 1
                soup = _fetch_detail(collector, p)
            if soup is not None:
                deadline = extract_deadline(page_text(soup), today, anywhere=False)
                if deadline is None and budget["details"] > 0:
                    budget["details"] -= 1
                    deadline = _deadline_from_attachment(collector, soup, p, today)
                if deadline:
                    store.set_deadline(p.uid, deadline)

    if result_notices:
        close_finished(store, src.id, result_notices, today)
    return result


def close_finished(store: Store, source_id: str, result_notices: list[Posting], today: date) -> list[Posting]:
    """같은 게시판에 나중에 결과공고가 올라온 모집공고를 '모집 끝'으로 닫는다."""
    closed = []
    for recruit in store.open_postings(source_id, since=today - timedelta(days=120)):
        res = finished_by(recruit, result_notices)
        if res is not None:
            store.mark_closed(recruit.uid)
            closed.append(recruit)
            log.info("모집 끝(결과공고): %s ← %s", recruit.title, res.title)
    return closed


def _fetch_detail(collector, p: Posting):
    try:
        return collector.fetch_detail(p)
    except Exception as exc:  # noqa: BLE001 - 상세는 보조 정보라 실패해도 진행
        log.info("상세 페이지 실패 %s: %s", p.detail_url, exc)
        return None


def _deadline_from_attachment(collector, soup, p: Posting, today: date) -> date | None:
    """본문에 마감일이 없으면 첨부 공고문(HWP/HWPX/PDF)에서 찾는다."""
    found = find_attachments(soup, p.detail_url or "", collector.source.options.get("attachment_template"))
    if not found:
        return None
    att = found[0]
    try:
        text = extract_text(collector.http.get(att.url).content)
    except Exception as exc:  # noqa: BLE001 - 첨부는 보조 정보라 실패해도 진행
        log.info("첨부 내려받기 실패 %s: %s", att.url, exc)
        return None
    deadline = extract_deadline(text, today, anywhere=False) if text else None
    log.info("첨부 %s → %s", att.name, deadline or ("글자 없음" if not text else "마감일 못 찾음"))
    return deadline


def worth_detail(p: Posting, today: date, rules: Rules) -> bool:
    """상세 페이지를 열어 볼 만한가: 너무 오래돼서 어차피 신규로 안 보낼 글은 건너뛴다."""
    return p.posted_date is None or (today - p.posted_date).days <= rules.new_max_age_days


def is_fresh(p: Posting, today: date, rules: Rules) -> bool:
    """신규로 보낼 가치가 있는가: 마감 전이고 너무 오래된 글이 아님."""
    if p.deadline is not None and p.deadline < today:
        return False
    if p.posted_date is not None and (today - p.posted_date).days > rules.new_max_age_days:
        return False
    return True


def run(
    *,
    db_path: Path,
    out_dir: Path,
    send_mail: bool,
    config_dir: Path = DEFAULT_CONFIG_DIR,
    only: list[str] | None = None,
    max_phase: int | None = None,
    now: datetime | None = None,
    http: Http | None = None,
) -> RunOutcome:
    settings = load_settings(config_dir)
    rules = load_rules(config_dir)
    now = now or now_kst()
    today = now.date()
    sources = select_sources(settings.sources, only, max_phase or settings.max_phase)
    source_names = {s.id: s.name for s in settings.sources}

    store = Store(db_path)
    try:
        results = collect_all(sources, rules, store, http or Http(), now)
        store.log_runs(results, now)
        store.commit()

        pending = store.unreported()
        statuses = ("모집중", "결과공고") if rules.keep_result_notices else ("모집중",)
        new = sorted(
            (p for p in pending if p.status in statuses and is_fresh(p, today, rules)), key=lambda p: sort_key(p, today)
        )
        active = sorted(store.active(today, rules.active_max_age_days, statuses), key=lambda p: sort_key(p, today))
        closing_soon = [
            p for p in active if p.deadline is not None and 0 <= (p.deadline - today).days <= rules.closing_soon_days
        ]
        report_path = build_report(
            out_dir / f"울산_강사구인_{today.isoformat()}.xlsx",
            today, new, closing_soon, active, results, source_names,
        )
        outcome = RunOutcome(today, results, new, closing_soon, active, report_path, source_names=source_names)

        if send_mail:
            cfg = MailConfig.from_env()
            msg = build_message(
                cfg,
                subject_line(today, len(new), len(closing_soon)),
                html_body(today, new, closing_soon, len(active), results),
                text_body(today, new),
                report_path,
            )
            send(cfg, msg)
            # 오래돼서 신규에서 뺀 글도 함께 '보냄' 처리해서 다음 날 다시 거르지 않게 한다
            store.mark_reported([p.uid for p in pending], now_kst())
            store.commit()
            outcome.mailed = True
        return outcome
    finally:
        store.close()
