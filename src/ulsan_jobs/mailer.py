"""메일 발송 (Gmail SMTP + 앱 비밀번호 기본).

환경변수
    SMTP_USER          보내는 계정 (예: someone@gmail.com)
    SMTP_APP_PASSWORD  앱 비밀번호 (Gmail: 2단계 인증 후 발급)
    MAIL_TO            받는 주소, 여러 명이면 쉼표로 구분 (없으면 SMTP_USER)
    SMTP_HOST          기본 smtp.gmail.com
    SMTP_PORT          기본 465 (SSL). 587 이면 STARTTLS
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path

from .dates import dday_label
from .models import Posting, SourceResult

WEEKDAYS = "월화수목금토일"
XLSX_MIME = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@dataclass
class MailConfig:
    user: str
    password: str
    to: list[str]
    host: str = "smtp.gmail.com"
    port: int = 465

    @classmethod
    def from_env(cls) -> "MailConfig":
        user = os.environ.get("SMTP_USER", "").strip()
        password = os.environ.get("SMTP_APP_PASSWORD", "").replace(" ", "")
        if not user or not password:
            raise RuntimeError("메일 설정이 없습니다: SMTP_USER, SMTP_APP_PASSWORD 환경변수(GitHub Secrets)를 등록하세요.")
        to = [a.strip() for a in os.environ.get("MAIL_TO", user).split(",") if a.strip()]
        return cls(
            user=user,
            password=password,
            to=to,
            host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.environ.get("SMTP_PORT", "465")),
        )


def subject_line(today: date, new_count: int, soon_count: int) -> str:
    return f"[울산 강사구인] {today.month}/{today.day}({WEEKDAYS[today.weekday()]}) 신규 {new_count}건 · 마감임박 {soon_count}건"


def html_body(
    today: date,
    new: list[Posting],
    closing_soon: list[Posting],
    active_count: int,
    results: list[SourceResult],
    limit: int = 30,
) -> str:
    problems = [r for r in results if r.is_problem]
    parts = [
        "<div style=\"font-family:'Malgun Gothic',sans-serif;font-size:14px;color:#222\">",
        f"<h2 style='margin:0 0 8px'>울산 강사 구인공고 {today.isoformat()}</h2>",
        f"<p>신규 <b>{len(new)}</b>건 · 마감임박(D-3) <b>{len(closing_soon)}</b>건 · 진행중 전체 <b>{active_count}</b>건"
        " — 전체 목록은 첨부 엑셀을 확인하세요.</p>",
    ]
    if problems:
        items = "".join(
            f"<li>{escape(r.name)}: {escape(r.state)} {escape(r.error[:200])}</li>" for r in problems
        )
        parts.append(
            "<div style='background:#fdecea;border:1px solid #f5c2c0;padding:8px 12px;margin:8px 0'>"
            f"<b>⚠ 수집에 문제가 있는 소스 {len(problems)}곳</b> (사이트 개편 등으로 공고를 놓쳤을 수 있음)"
            f"<ul style='margin:4px 0'>{items}</ul></div>"
        )
    parts.append(_table("신규 공고", new, today, limit))
    if closing_soon:
        parts.append(_table("마감임박", closing_soon, today, limit))
    parts.append("<p style='color:#888;font-size:12px'>이 메일은 GitHub Actions 에서 매일 자동 발송됩니다.</p></div>")
    return "".join(parts)


def _table(title: str, postings: list[Posting], today: date, limit: int) -> str:
    if not postings:
        return f"<h3>{escape(title)}</h3><p>없음</p>"
    rows = []
    for p in postings[:limit]:
        rows.append(
            "<tr>"
            f"<td style='white-space:nowrap'>{escape(dday_label(p.deadline, today))}</td>"
            f"<td>{escape(p.category)}</td>"
            f"<td>{escape(p.org_name or p.org_type)}</td>"
            f"<td><a href='{escape(p.url, quote=True)}'>{escape(p.title)}</a></td>"
            "</tr>"
        )
    more = f"<p>…외 {len(postings) - limit}건 (첨부 엑셀 참고)</p>" if len(postings) > limit else ""
    th = "style='background:#1f4e78;color:#fff;padding:4px 8px;text-align:left'"
    return (
        f"<h3>{escape(title)} ({len(postings)})</h3>"
        "<table cellspacing='0' cellpadding='4' style='border-collapse:collapse;border:1px solid #ddd'>"
        f"<tr><th {th}>마감</th><th {th}>분야</th><th {th}>기관</th><th {th}>제목</th></tr>"
        + "".join(rows)
        + "</table>"
        + more
    )


def text_body(today: date, new: list[Posting]) -> str:
    lines = [f"울산 강사 구인공고 {today.isoformat()} - 신규 {len(new)}건", ""]
    for p in new:
        lines.append(f"- [{dday_label(p.deadline, today)}] {p.org_name} | {p.title}\n  {p.url}")
    lines.append("\n전체 목록은 첨부 엑셀을 확인하세요.")
    return "\n".join(lines)


def build_message(cfg: MailConfig, subject: str, html: str, text: str, attachment: Path | None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.user
    msg["To"] = ", ".join(cfg.to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if attachment is not None:
        msg.add_attachment(
            attachment.read_bytes(), maintype=XLSX_MIME[0], subtype=XLSX_MIME[1], filename=attachment.name
        )
    return msg


def send(cfg: MailConfig, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if cfg.port == 465:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=60) as smtp:
            smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=60) as smtp:
            smtp.starttls(context=context)
            smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
