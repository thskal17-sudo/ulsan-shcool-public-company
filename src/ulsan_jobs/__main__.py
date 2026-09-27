"""명령줄 실행.

    python -m ulsan_jobs run [--no-mail] [--db data/postings.db] [--out out] [--source ID ...]
    python -m ulsan_jobs check-source ID [ID ...]   # 소스만 시험 수집 (DB·메일 없음)
    python -m ulsan_jobs send-test-mail             # 메일 설정 확인
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
    )
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
    print("메일: " + ("발송함" if outcome.mailed else "발송 안 함 (--no-mail)"))
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
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser("check-source", help="소스 시험 수집")
    p_check.add_argument("ids", nargs="+")
    p_check.set_defaults(func=cmd_check_source)

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
