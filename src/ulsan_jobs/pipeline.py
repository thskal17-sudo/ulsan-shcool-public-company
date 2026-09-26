"""하루 1회 실행 흐름: 수집 → 판별·분류 → 저장(신규 판정) → 엑셀 → 메일."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .classify import categorize, judge
from .collectors import COLLECTORS, NotConfigured
from .config import DEFAULT_CONFIG_DIR, Rules, Source, load_rules, load_settings
from .dates import extract_deadline
from .http import Http
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
    detail_limit: int = 60,
) -> list[SourceResult]:
    today = now.date()
    results: list[SourceResult] = []
    details_fetched = 0
    for src in sources:
        result = SourceResult(src.id, src.name, src.org_type, src.url or "")
        results.append(result)
        collector_cls = COLLECTORS.get(src.collector)
        if collector_cls is None:
            result.state, result.error = "미구현", f"수집기 '{src.collector}' 는 다음 단계에서 구현 예정"
            continue
        if not src.url and not src.collector.endswith("_api"):
            result.state, result.error = "설정필요", "url 없음"
            continue
        collector = collector_cls(src, http, today)
        log.info("수집: %s", src.name)
        try:
            items = collector.collect()
        except NotConfigured as exc:
            result.state, result.error = "설정필요", str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 - 한 소스 실패가 전체를 멈추지 않게 한다
            log.warning("수집 실패 %s: %s", src.id, exc)
            log.debug("상세", exc_info=True)
            result.state, result.error = "오류", f"{type(exc).__name__}: {exc}"[:300]
            continue

        result.fetched = len(items)
        result.samples = [p.title for p in items[:3]]
        if not items:
            result.state, result.error = "0건", "목록에서 글을 하나도 읽지 못함 (사이트 구조 변경 의심)"
            continue

        for p in items:
            status = judge(p.title, rules, src.keyword_filter, p.label)
            if status is None:
                continue
            p.status = status
            p.category = categorize(f"{p.title} {p.label}", p.org_name, rules)
            result.matched += 1
            if not store.upsert(p, now):
                continue
            result.new += 1
            if p.deadline is None and p.detail_url and details_fetched < detail_limit:
                details_fetched += 1
                try:
                    deadline = extract_deadline(collector.fetch_detail_text(p), today, anywhere=False)
                except Exception as exc:  # noqa: BLE001 - 마감일은 보조 정보라 실패해도 진행
                    log.info("상세 페이지 실패 %s: %s", p.detail_url, exc)
                    deadline = None
                if deadline:
                    store.set_deadline(p.uid, deadline)
    return results


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
        new = sorted((p for p in pending if is_fresh(p, today, rules)), key=lambda p: sort_key(p, today))
        statuses = ("모집중", "결과공고") if rules.keep_result_notices else ("모집중",)
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
