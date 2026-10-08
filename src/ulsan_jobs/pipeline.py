"""하루 1회 실행 흐름: 수집 → 판별·분류 → 저장(신규 판정) → 엑셀 → 메일."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .attachments import extract_text, find_attachments
from .classify import categorize, judge
from .collectors import COLLECTORS, NotConfigured
from .config import DEFAULT_CONFIG_DIR, Rules, Source, load_rules, load_settings
from .dates import extract_deadline, first_period_end
from .detail import block_text, extends, full_title, looks_truncated, page_text
from .gangsaitda import TEMPLATE_NAME, build_gangsaitda
from .http import HostUnreachable, Http, is_connect_failure
from .jobinfo import INFO_VERSION, extract_info
from .matching import finished_by
from .mailer import MailConfig, build_message, html_body, send, subject_line, text_body
from .models import Posting, SourceResult, now_kst
from .report_excel import build_report, sort_key
from .storage import EXCLUDED, Store

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
    upload_path: Path | None = None  # 메일에 붙이는 강사잇다 양식 (마감 전 공고 전부)
    already_mailed: bool = False  # once_daily 실행에서 오늘 정기 메일을 이미 보낸 뒤였음


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
    time_limit: float = 20 * 60,
    clock: Callable[[], float] = time.monotonic,
) -> list[SourceResult]:
    """모든 소스를 수집한다. 실패(오류·0건)한 소스는 끝에서 한 번 더 시도한다 (관공서 서버의 일시 장애 대비).

    접속이 안 되는 서버가 많은 날에도 메일은 나가야 하므로(작업 제한 30분), 수집이 time_limit 초를
    넘으면 남은 소스는 건너뛰고 재시도도 하지 않는다.
    """
    started = clock()
    budget = {"details": detail_limit}

    def out_of_time() -> bool:
        return clock() - started > time_limit

    results = []
    for src in sources:
        if out_of_time():
            result = SourceResult(src.id, src.name, src.org_type, src.url or "")
            result.state, result.error = "시간초과", f"수집 시간 {time_limit / 60:.0f}분을 넘겨 건너뜀"
            results.append(result)
        else:
            results.append(_collect_source(src, rules, store, http, now, budget))

    retry = [i for i, r in enumerate(results) if r.state in ("오류", "0건", "접속불가")]
    if retry and not out_of_time():
        http.forget_unreachable()  # 앞에서 접속 실패한 서버도 한 번은 다시 기다려 본다
    for i in retry:
        if out_of_time():
            log.warning("수집 시간 %.0f분을 넘겨 나머지 재시도는 건너뜀", time_limit / 60)
            break
        log.info("다시 시도: %s (%s)", sources[i].name, results[i].error)
        results[i] = _collect_source(sources[i], rules, store, http, now, budget)
    return results


def describe_failure(exc: Exception) -> tuple[str, str]:
    """수집 실패를 메일·엑셀에 보일 상태와 짧은 설명으로 바꾼다 (자세한 오류는 실행 로그에 남음)."""
    if isinstance(exc, HostUnreachable):
        return "접속불가", "같은 서버가 앞서 응답하지 않아 건너뜀"
    if isinstance(exc, requests.exceptions.ConnectionError) and is_connect_failure(exc):
        return "접속불가", "서버가 응답하지 않음 (해외 접속 차단 또는 일시 장애)"
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return "오류", f"서버가 오류 응답을 보냄 (HTTP {exc.response.status_code})"
    if isinstance(exc, requests.exceptions.Timeout):
        return "오류", "서버 응답이 너무 느림"
    if isinstance(exc, requests.exceptions.SSLError):
        return "오류", "보안 연결(인증서) 실패"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "오류", "연결이 도중에 끊김"
    return "오류", f"목록을 읽다가 오류 ({type(exc).__name__}: {exc})"[:200]


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
        result.state, result.error = describe_failure(exc)
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
            # 제외 낱말이 늘기 전에 모집공고로 저장된 글이면 진행중에서 뺀다 ('늘봄학교 자원봉사자 모집', 2026-10-08)
            excluded = store.close_if_open(p.uid, EXCLUDED)
            if excluded:
                log.info("강사 공고 아님으로 다시 분류: %s", excluded)
            continue
        if status == "결과공고":
            result_notices.append(p)
            if not rules.keep_result_notices:
                # 결과공고 낱말이 늘기 전에 모집공고로 저장된 글이면 이제 닫는다 ('… 대상 결정 공고', 2026-09-30)
                reclassified = store.close_if_open(p.uid)
                if reclassified:
                    log.info("결과공고로 다시 분류: %s", reclassified)
                continue
        p.status = status
        p.category = categorize(f"{p.title} {p.label}", p.org_name, rules)
        result.matched += 1
        is_new = store.upsert(p, now)
        if is_new:
            result.new += 1
        if not (p.detail_url and worth_detail(p, today, rules)):
            continue
        deadline, info, stored_status = store.detail_state(p.uid)
        need_deadline = is_new and deadline is None
        # 강사잇다 양식용: 마감 전 모집공고는 공고문을 한 번 읽어 수업 일정·대상 등을 찾아 둔다
        stale = info is None or info.get("v") != INFO_VERSION
        need_info = stored_status == "모집중" and stale and (deadline is None or deadline >= today)
        if not (need_deadline or need_info):
            continue
        if not tried and budget["details"] > 0:
            budget["details"] -= 1
            soup, tried = _fetch_detail(collector, p), True
        if soup is None:
            continue  # 못 읽었으면 정보는 비워 두고 다음 실행 때 다시
        notice: str | None = None
        notice_read = False
        if need_deadline:
            deadline = extract_deadline(page_text(soup), today, anywhere=False)
            if deadline is None:
                notice, notice_read = _attachment_text(collector, soup, p, budget), True
                deadline = _deadline_in_notice(notice, today, p)
            if deadline:
                store.set_deadline(p.uid, deadline)
        if need_info and (deadline is None or deadline >= today):
            if looks_truncated(p.title):  # 전에 잘린 채 저장된 제목도 이참에 전체로
                full = full_title(soup, p.title)
                if full:
                    p.title = full
                    store.set_title(p.uid, full)
            if not notice_read:
                notice = _attachment_text(collector, soup, p, budget)
            info = extract_info(notice, block_text(soup))
            store.set_info(p.uid, {**info, "v": INFO_VERSION})
            log.info("공고문 정보 %s → %s", p.title, info or "못 찾음")

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


def _attachment_text(collector, soup, p: Posting, budget: dict[str, int]) -> str | None:
    """상세 페이지의 첨부 공고문(HWP/HWPX/PDF) 글자. 첨부가 없거나 읽지 못하면 None (내려받으면 budget 1 사용)."""
    found = find_attachments(soup, p.detail_url or "", collector.source.options.get("attachment_template"))
    if not found or budget["details"] <= 0:
        return None
    budget["details"] -= 1
    att = found[0]
    try:
        text = extract_text(collector.http.get(att.url).content)
    except Exception as exc:  # noqa: BLE001 - 첨부는 보조 정보라 실패해도 진행
        log.info("첨부 내려받기 실패 %s: %s", att.url, exc)
        return None
    log.info("첨부 %s → %s", att.name, f"글자 {len(text)}자" if text else "글자 없음")
    return text


def _deadline_in_notice(text: str | None, today: date, p: Posting) -> date | None:
    """본문에 마감일이 없을 때 첨부 공고문에서 찾는다 (표 모양 공고문은 첫 기간의 끝)."""
    if not text:
        return None
    deadline = extract_deadline(text, today, anywhere=False) or first_period_end(text, today, p.posted_date)
    log.info("첨부 공고문 마감일 %s → %s", p.title, deadline or "못 찾음")
    return deadline


def _template(config_dir: Path) -> Path:
    """강사잇다 양식 원본 (설정 폴더에 없으면 기본 설정 폴더의 것)."""
    path = config_dir / TEMPLATE_NAME
    return path if path.exists() else DEFAULT_CONFIG_DIR / TEMPLATE_NAME


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
    catch_up: bool = False,
    once_daily: bool = False,
) -> RunOutcome:
    """catch_up: 앞선 실행에서 접속 안 된 소스(only)를 다른 서버에서 다시 수집하는 보충 실행.
    새 공고가 있을 때만 '보충' 메일을 보낸다 (마감임박 목록은 앞선 메일에 이미 있음).
    once_daily: 예약 실행용. 오늘(KST) 정기 메일을 이미 보냈으면 보충 실행처럼 새 공고가 있을 때만
    '보충' 메일을 보낸다 (새벽 실행과 예비 실행이 둘 다 돌아도 전체 메일은 하루 한 번)."""
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
        already_mailed = once_daily and store.mailed_on(today)
        supplement = catch_up or already_mailed

        pending = store.unreported()
        statuses = ("모집중", "결과공고") if rules.keep_result_notices else ("모집중",)
        new = sorted(
            (p for p in pending if p.status in statuses and is_fresh(p, today, rules)), key=lambda p: sort_key(p, today)
        )
        active = sorted(store.active(today, rules.active_max_age_days, statuses), key=lambda p: sort_key(p, today))
        closing_soon = [
            p for p in active if p.deadline is not None and 0 <= (p.deadline - today).days <= rules.closing_soon_days
        ]
        suffix = "_보충" if supplement else ""
        # 수집현황까지 담은 보고서는 Actions 보관용, 메일에는 강사잇다 올리기 양식을 붙인다
        report_path = build_report(
            out_dir / f"울산_강사구인_{today.isoformat()}{suffix}.xlsx",
            today, new, closing_soon, active, results, source_names,
        )
        upload_path = build_gangsaitda(
            out_dir / f"강사잇다_울산_{today.isoformat()}{suffix}.xlsx", active, source_names, _template(config_dir)
        )
        outcome = RunOutcome(
            today, results, new, closing_soon, active, report_path, source_names=source_names, upload_path=upload_path,
            already_mailed=already_mailed,
        )

        if send_mail and (new or not supplement):
            held = sum(1 for p in active if p.deadline is None)
            note = f"첨부 파일은 강사잇다 올리기 양식입니다 (마감 전 공고 {len(active)}건" + (
                f", 그중 마감일을 찾지 못한 {held}건은 '보류' 표시)." if held else ")."
            )
            cfg = MailConfig.from_env()
            msg = build_message(
                cfg,
                subject_line(today, len(new), len(closing_soon), catch_up=supplement),
                html_body(
                    today, new, [] if supplement else closing_soon, len(active), results,
                    catch_up=catch_up, backup=already_mailed and not catch_up, attachment_note=note,
                ),
                text_body(today, new, note),
                upload_path,
            )
            send(cfg, msg)
            # 오래돼서 신규에서 뺀 글도 함께 '보냄' 처리해서 다음 날 다시 거르지 않게 한다
            store.mark_reported([p.uid for p in pending], now_kst())
            store.log_mail(now, "보충" if supplement else "정기", len(new))
            store.commit()
            outcome.mailed = True
        return outcome
    finally:
        store.close()
