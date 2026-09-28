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
import re
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


def subject_line(today: date, new_count: int, soon_count: int, *, catch_up: bool = False) -> str:
    day = f"{today.month}/{today.day}({WEEKDAYS[today.weekday()]})"
    if catch_up:
        return f"[울산 강사구인] {day} 보충 신규 {new_count}건"
    return f"[울산 강사구인] {day} 신규 {new_count}건 · 마감임박 {soon_count}건"


def short_name(name: str) -> str:
    """'나라일터 모집공고 (기관명에 '울산' 포함)' → '나라일터 모집공고' (끝의 괄호 설명을 뺌)."""
    return re.sub(r"\s+\([^()]*\)$", "", name)


def html_body(
    today: date,
    new: list[Posting],
    closing_soon: list[Posting],
    active_count: int,
    results: list[SourceResult],
    limit: int = 30,
    *,
    catch_up: bool = False,
) -> str:
    problems = [r for r in results if r.is_problem]
    unreachable = [r for r in results if r.is_unreachable]
    parts = ["<div style=\"font-family:'Malgun Gothic',sans-serif;font-size:14px;color:#222\">"]
    if catch_up:
        parts += [
            f"<h2 style='margin:0 0 8px'>울산 강사 구인공고 {today.isoformat()} · 보충</h2>",
            f"<p>앞선 수집 때 접속되지 않은 사이트 {len(results)}곳을 다른 수집 서버에서 다시 읽어"
            f" 새로 찾은 공고 <b>{len(new)}</b>건입니다.</p>",
        ]
    else:
        parts += [
            f"<h2 style='margin:0 0 8px'>울산 강사 구인공고 {today.isoformat()}</h2>",
            f"<p>신규 <b>{len(new)}</b>건 · 마감임박(D-3) <b>{len(closing_soon)}</b>건 · 진행중 전체 <b>{active_count}</b>건"
            " — 전체 목록은 첨부 엑셀을 확인하세요.</p>",
        ]
    if problems:
        items = "".join(f"<li>{escape(short_name(r.name))}: {escape(r.error[:200])}</li>" for r in problems)
        parts.append(
            "<div style='background:#fdecea;border:1px solid #f5c2c0;padding:8px 12px;margin:8px 0'>"
            f"<b>⚠ 확인이 필요한 사이트 {len(problems)}곳</b> (사이트 개편 등으로 공고를 놓쳤을 수 있음)"
            f"<ul style='margin:4px 0'>{items}</ul></div>"
        )
    if unreachable:
        names = " · ".join(escape(short_name(r.name)) for r in unreachable)
        parts.append(
            "<div style='background:#f3f4f6;border:1px solid #d1d5db;padding:8px 12px;margin:8px 0;color:#444'>"
            f"<b>접속 안 된 사이트 {len(unreachable)}곳</b> — 수집 서버(해외)의 접속을 막거나 일시 장애인 곳입니다."
            " 다른 서버와 다음 실행에서 다시 수집하므로 따로 할 일은 없습니다."
            f"<div style='font-size:13px;margin-top:4px'>{names}</div></div>"
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
