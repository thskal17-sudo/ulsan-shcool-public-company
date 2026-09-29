"""명령줄 실행.

    python -m ulsan_jobs run [--no-mail] [--db data/postings.db] [--out out] [--source ID ...]
                             [--retry-list FILE] [--catch-up]
    python -m ulsan_jobs check-source ID [ID ...]   # 소스만 시험 수집 (DB·메일 없음)
    python -m ulsan_jobs send-test-mail             # 메일 설정 확인
    python -m ulsan_jobs camp [--no-mail] [--days N] # 나라장터 캠프 수주 공고 (G2B_API_KEY 필요)
    python -m ulsan_jobs briefing [--no-mail] [--force] # 오늘의 브리핑 (BRIEFING_TO 필요)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .classify import judge
from .collectors import COLLECTORS, NotConfigured
from .config import DEFAULT_CONFIG_DIR, load_rules, load_settings
from .dates import dday_label
from .http import Http
from .models import now_kst, today_kst


def cmd_run(args) -> int:
    from .pipeline import run

    outcome = run(
        db_path=Path(args.db),
        out_dir=Path(args.out),
        send_mail=not args.no_mail,
        config_dir=Path(args.config),
        only=args.source or None,
        max_phase=args.max_phase,
        catch_up=args.catch_up,
    )
    if args.retry_list:
        retry = [r.source_id for r in outcome.results if r.needs_other_server]
        Path(args.retry_list).write_text(" ".join(retry), encoding="utf-8")
    print(f"\n=== 수집 결과 {outcome.today} ===")
    for r in outcome.results:
        print(f"[{r.state:^5}] {r.name:<40} 목록 {r.fetched:>3} · 강사 {r.matched:>3} · 신규 {r.new:>3} {r.error}")
        for s in r.samples:
            print(f"          예) {s}")
    print(f"\n신규 {len(outcome.new)}건 · 마감임박 {len(outcome.closing_soon)}건 · 진행중 {len(outcome.active)}건")
    for p in outcome.new:
        print(f"  [{dday_label(p.deadline, outcome.today):>6}] {p.category:<8} {p.org_name or '-':<20} {p.title}")
        print(f"           {p.url}")
    print(f"\n엑셀: {outcome.report_path}")
    print(f"강사잇다 양식: {outcome.upload_path} (마감 전 공고 {len(outcome.active)}건)")
    if outcome.mailed:
        print("메일: 발송함")
    elif args.no_mail:
        print("메일: 발송 안 함 (--no-mail)")
    else:
        print("메일: 새 공고가 없어 보내지 않음 (보충 수집)")
    return 0


def cmd_check_source(args) -> int:
    settings = load_settings(Path(args.config))
    rules = load_rules(Path(args.config))
    by_id = {s.id: s for s in settings.sources}
    http = Http()
    today = today_kst()
    status_code = 0
    for source_id in args.ids:
        src = by_id.get(source_id)
        if src is None:
            print(f"알 수 없는 소스: {source_id}")
            status_code = 1
            continue
        print(f"\n=== {src.id} · {src.name}\n    {src.url}")
        cls = COLLECTORS.get(src.collector)
        if cls is None:
            print(f"    수집기 '{src.collector}' 미구현")
            continue
        if src.options.get("legacy_tls") and src.url:
            http.allow_legacy_tls(urlsplit(src.url).hostname or "")
        collector = cls(src, http, today)
        try:
            items = collector.collect()
        except NotConfigured as exc:
            print(f"    설정필요: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"    오류: {type(exc).__name__}: {exc}")
            status_code = 1
            continue
        print(f"    목록 {len(items)}건")
        for p in items:
            verdict = judge(p.title, rules, src.keyword_filter, p.label) or "제외"
            print(f"    [{verdict:^4}] {p.posted_date or '-'} ~{p.deadline or '?'} | {p.label or '-'} | {p.org_name or '-'} | {p.title}")
            print(f"             {p.url}")
    return status_code


def cmd_camp(args) -> int:
    from .camp import G2BError, run_camp

    try:
        out = run_camp(
            db_path=Path(args.db),
            send_mail=not args.no_mail,
            config_dir=Path(args.config),
            lookback_days=args.days,
            recheck=args.recheck,
        )
    except G2BError as exc:
        print(f"캠프 수주 공고: {exc}")
        return 1
    print(f"\n=== 캠프 수주 공고 {out.now:%Y-%m-%d %H:%M} ===")
    print(f"용역 공고 {out.scanned}건 · 키워드 일치 {out.matched}건 · 새 공고 {len(out.new)}건 · API 호출 {out.calls}회")
    if out.methods:
        print("키워드 공고의 계약방법: " + ", ".join(f"{k} {v}" for k, v in sorted(out.methods.items())))
    for b in out.new:
        close = f"{b.close_at:%m/%d %H:%M}" if b.close_at else "원문확인"
        price = f"{b.price:,}원({b.price_label})" if b.price else "-"
        print(f"  [{b.tier}] ~{close} | {b.demand_org or b.org} | {b.title}")
        print(f"          {b.method or '-'} | {price} | 참가지역 {b.region_text} | {b.url}")
    if out.closing_soon:
        print(f"마감임박 {len(out.closing_soon)}건")
    if out.region_errors:
        print(f"참가가능지역 조회 실패 {out.region_errors}건 ('검토'로 분류)")
    if out.mailed:
        print("메일: 발송함")
    elif args.no_mail:
        print("메일: 발송 안 함 (--no-mail)")
    else:
        print("메일: 새 공고가 없어 보내지 않음")
    return 0


def cmd_briefing(args) -> int:
    from .briefing import run_briefing

    out = run_briefing(
        db_path=Path(args.db),
        state_path=Path(args.state),
        send_mail=not args.no_mail,
        force=args.force,
        config_dir=Path(args.config),
        out_html=Path(args.html) if args.html else None,
    )
    b = out.briefing
    print(f"\n=== 오늘의 브리핑 {b.today} ===")
    for s in b.sections:
        state = "도착" if s.ready else "미도착"
        print(f"  {s.name:<10} {state} · 신규 {len(s.new)} · 마감임박 {len(s.closing)} · 실패 {len(s.problems)} {s.error}")
    c = b.camp
    print(f"  캠프 수주   {'도착' if c.ready else '미도착'} · 바로지원 {len(c.go)} · 검토 {len(c.review)} · 참고 {len(c.ref)}")
    print(f"판단: {out.reason}")
    print("메일: 발송함" if out.sent else "메일: 보내지 않음")
    return 0


def cmd_send_test_mail(args) -> int:
    from .mailer import MailConfig, build_message, send

    cfg = MailConfig.from_env()
    now = now_kst()
    msg = build_message(
        cfg,
        "[울산 강사구인] 메일 설정 확인",
        f"<p>메일 설정이 정상입니다. ({now:%Y-%m-%d %H:%M} KST)</p>",
        f"메일 설정이 정상입니다. ({now:%Y-%m-%d %H:%M} KST)",
        None,
    )
    send(cfg, msg)
    print(f"테스트 메일 발송: {', '.join(cfg.to)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ulsan_jobs", description="울산 공공기관 강사 구인공고 수집")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_DIR), help="설정 폴더 (sources.yaml, keywords.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="수집 → 엑셀 → 메일")
    p_run.add_argument("--db", default="data/postings.db")
    p_run.add_argument("--out", default="out")
    p_run.add_argument("--no-mail", action="store_true", help="메일을 보내지 않음 (신규 표시도 유지)")
    p_run.add_argument("--source", nargs="*", help="이 소스만 수집")
    p_run.add_argument("--max-phase", type=int, default=None, help="이 단계까지의 소스 수집 (기본: sources.yaml 의 max_phase)")
    p_run.add_argument(
        "--retry-list", default=None, help="다른 서버에서 다시 수집할 소스 id(접속불가·시간초과)를 이 파일에 공백으로 적음"
    )
    p_run.add_argument(
        "--catch-up", action="store_true", help="보충 수집: --source 소스만 다시 수집하고 새 공고가 있을 때만 메일"
    )
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check-source", help="소스 시험 수집")
    p_check.add_argument("ids", nargs="+")
    p_check.set_defaults(func=cmd_check_source)

    p_camp = sub.add_parser("camp", help="나라장터 캠프 수주 공고 수집 → 메일")
    p_camp.add_argument("--db", default="data/postings.db")
    p_camp.add_argument("--no-mail", action="store_true", help="메일을 보내지 않음 (새 공고 표시도 유지)")
    p_camp.add_argument("--days", type=int, default=None, help="최근 며칠치 공고를 볼지 (기본: config/camp.yaml)")
    p_camp.add_argument("--recheck", action="store_true", help="아직 안 알린 공고의 참가가능지역을 다시 조회")
    p_camp.set_defaults(func=cmd_camp)

    p_brief = sub.add_parser("briefing", help="오늘의 브리핑 (지역별 강사 공고 + 캠프 수주를 한 통으로)")
    p_brief.add_argument("--db", default="data/postings.db")
    p_brief.add_argument("--state", default="data/briefing_last_sent.txt", help="마지막으로 보낸 날짜를 적는 파일")
    p_brief.add_argument("--no-mail", action="store_true")
    p_brief.add_argument("--force", action="store_true", help="오늘 이미 보냈거나 자료가 덜 와도 보냄")
    p_brief.add_argument("--html", default=None, help="메일 본문을 이 파일로도 저장")
    p_brief.set_defaults(func=cmd_briefing)

    p_mail = sub.add_parser("send-test-mail", help="메일 설정 확인")
    p_mail.set_defaults(func=cmd_send_test_mail)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
