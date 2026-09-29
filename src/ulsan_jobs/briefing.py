"""오늘의 브리핑: 지역별 강사 공고 메일과 캠프 수주 메일을 한 통으로 묶어 보낸다.

읽는 곳 (config/briefing.yaml)
    ulsan_db   울산(또는 같은 구조의 부산) 수집 기록 DB — 오늘 메일로 알린 신규, 마감임박, 수집 이상
               이 저장소는 state 브랜치에서 복원한 로컬 DB(--db), 다른 저장소는 raw 주소로 내려받는다.
    md_report  경남·대학평생교육원 저장소가 매일 커밋하는 reports/YYYY-MM-DD.md
    (캠프 수주는 이 저장소 DB 의 camp_bids / camp_runs)

보내는 때
    수집 워크플로가 끝날 때마다(workflow_run)와 예비 예약 때 실행된다. 모든 곳의 오늘 자료가
    도착했으면 보내고, 아직이면 기다린다. send_anyway_after(KST)가 지나면 도착한 것만으로 보낸다.
    하루 한 번만 보낸다 — 보낸 날짜는 --state 파일에 적는다 (워크플로가 briefing-state 브랜치에 보관).

받는 사람: 환경변수 BRIEFING_TO (쉼표로 구분). 없으면 보내지 않고 실패한다.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import yaml

from .config import DEFAULT_CONFIG_DIR
from .dates import dday_label
from .models import now_kst

WEEKDAYS = "월화수목금토일"


# ---------------------------------------------------------------- 자료 모양


@dataclass
class Item:
    title: str
    org: str = ""
    url: str = ""
    deadline: date | None = None
    deadline_text: str = ""  # 보고서에 적힌 마감 표기 그대로 (시각 포함)
    region: str = ""
    category: str = ""
    note: str = ""


@dataclass
class Section:
    """한 지역(또는 캠프)의 오늘 자료."""

    id: str
    name: str
    ready: bool = False  # 오늘 자료가 도착했는가
    new: list[Item] = field(default_factory=list)
    closing: list[Item] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    link: str = ""
    error: str = ""


@dataclass
class CampSummary:
    ready: bool = False
    go: list[Item] = field(default_factory=list)
    review: list[Item] = field(default_factory=list)
    ref: list[Item] = field(default_factory=list)
    closing: list[Item] = field(default_factory=list)


@dataclass
class Briefing:
    today: date
    sections: list[Section]
    camp: CampSummary
    links: list[dict] = field(default_factory=list)

    @property
    def all_ready(self) -> bool:
        return self.camp.ready and all(s.ready for s in self.sections)

    @property
    def missing(self) -> list[str]:
        names = [s.name for s in self.sections if not s.ready]
        if not self.camp.ready:
            names.append("캠프 수주")
        return names


# ---------------------------------------------------------------- 경남·평생교육원 보고서


_ITEM = re.compile(r"^\d+\.\s+\*\*(?:\[(?P<cat>[^\]]+)\]\s*)?(?P<body>.+?)\*\*(?:\s+—\s+(?P<dday>\S+))?\s*$")
_DEADLINE = re.compile(r"마감\s+(?P<md>\d{1,2})\.(?P<d>\d{1,2})(?:\([^)]*\))?(?P<time>\s+\d{1,2}:\d{2})?")


def parse_md_report(text: str, today: date) -> tuple[list[Item], list[Item], list[str]]:
    """경남·평생교육원 일일 보고서 → (신규, 마감임박, 실패 소스)."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:]
            sections[current] = []
        elif current:
            sections[current].append(line)

    def items_of(keyword: str) -> list[Item]:
        lines = next((v for k, v in sections.items() if keyword in k), [])
        out: list[Item] = []
        for line in lines:
            m = _ITEM.match(line.strip())
            if m:
                body = m.group("body")
                org, _, title = body.partition(" · ")
                if not title:
                    org, title = "", body
                out.append(Item(title=title.strip(), org=org.strip(), category=m.group("cat") or ""))
            elif out and line.startswith("   "):
                it = out[-1]
                url = re.search(r"→\s+(\S+)", line)
                if url:
                    it.url = url.group(1)
                dl = _DEADLINE.search(line)
                if dl:
                    it.deadline = _month_day(int(dl.group("md")), int(dl.group("d")), today)
                    it.deadline_text = f"{int(dl.group('md'))}/{int(dl.group('d'))}" + (dl.group("time") or "")
                parts = [p.strip() for p in line.split("→")[0].split(" · ")]
                if len(parts) >= 2:
                    it.region = parts[1]
                if "확인 필요" in line:
                    it.note = "강사 공고 여부 확인 필요"
        return out

    problems: list[str] = []
    for line in next((v for k, v in sections.items() if "부록" in k), []):
        if line.startswith("| 실패 소스"):
            problems = re.findall(r"`([^`]+)`", line)
    return items_of("신규"), items_of("마감 임박"), problems


def _month_day(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if abs((d - today).days) <= 200:
            return d
    return None


# ---------------------------------------------------------------- 울산(부산) 수집 DB


def _kst_date(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def read_ulsan_db(path: Path, today: date, soon_days: int) -> tuple[bool, list[Item], list[Item], list[str]]:
    """→ (오늘 수집했는가, 오늘 알린 신규, 마감임박, 오늘 문제난 소스)."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        runs = [r for r in conn.execute("SELECT run_at, source_id, state FROM source_runs")
                if _kst_date(r["run_at"]) == today]
        ready = bool(runs)
        problems = sorted({r["source_id"] for r in runs if r["state"] in ("오류", "시간초과")})
        new, closing = [], []
        for r in conn.execute("SELECT * FROM postings WHERE status = '모집중'"):
            dl = date.fromisoformat(r["deadline"]) if r["deadline"] else None
            item = Item(title=r["title"], org=r["org_name"] or "", url=r["url"], deadline=dl,
                        region=r["district"] or "", category=r["category"] or "")
            if _kst_date(r["reported_at"]) == today:
                new.append(item)
            if dl and 0 <= (dl - today).days <= soon_days:
                closing.append(item)
        return ready, new, closing, problems
    finally:
        conn.close()


def read_camp_db(path: Path, today: date, soon_days: int) -> CampSummary:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = CampSummary()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "camp_bids" not in tables:
            return out
        if "camp_runs" in tables:
            out.ready = any(_kst_date(r[0]) == today for r in conn.execute("SELECT run_at FROM camp_runs"))
        for r in conn.execute("SELECT * FROM camp_bids WHERE tier IN ('바로지원', '검토', '참고')"):
            close = datetime.fromisoformat(r["close_at"]) if r["close_at"] else None
            price = f"{r['price'] // 10_000:,}만원" if r["price"] else ""
            keys = r.keys()
            source = (r["source"] if "source" in keys else None) or "나라장터"
            tag = "[교육] " if "topic" in keys and r["topic"] == "교육" else ""
            item = Item(title=tag + r["title"], org=r["demand_org"] or r["org"] or "", url=r["url"] or "",
                        deadline=close.date() if close else None,
                        deadline_text=f"{close:%m/%d %H:%M}" if close else "",
                        note=" · ".join(x for x in (source, price) if x), category=r["tier"])
            if _kst_date(r["reported_at"]) == today:
                {"바로지원": out.go, "검토": out.review, "참고": out.ref}[r["tier"]].append(item)
            elif (r["reported_at"] and r["tier"] in ("바로지원", "검토") and item.deadline
                  and 0 <= (item.deadline - today).days <= soon_days):
                out.closing.append(item)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- 모으기


def load_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> dict:
    return yaml.safe_load((config_dir / "briefing.yaml").read_text(encoding="utf-8")) or {}


def _by_deadline(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda i: (i.deadline or date.max, i.title))


def gather(cfg: dict, today: date, local_db: Path | None, fetch) -> Briefing:
    """fetch(url) -> bytes | None (없으면 None). 테스트에서 바꿔 끼운다."""
    soon = int(cfg.get("closing_soon_days", 3))
    sections: list[Section] = []
    for src in cfg.get("sources", []):
        if src.get("enabled", True) is False:
            continue
        sec = Section(id=src["id"], name=src["name"])
        try:
            if src["type"] == "md_report":
                ymd = today.isoformat()
                sec.link = src.get("link", "").format(date=ymd)
                body = fetch(src["url"].format(date=ymd))
                if body is not None:
                    sec.ready = True
                    sec.new, sec.closing, sec.problems = parse_md_report(body.decode("utf-8"), today)
            elif src["type"] == "ulsan_db":
                path = local_db if not src.get("repo") else None
                if path is None:
                    raw = src.get("db_url") or src["repo"].replace(
                        "https://github.com/", "https://raw.githubusercontent.com/") + "/state/postings.db"
                    body = fetch(raw)
                    if body is not None:
                        tmp = Path(tempfile.mkdtemp()) / "postings.db"
                        tmp.write_bytes(body)
                        path = tmp
                if path is not None and path.exists():
                    sec.ready, sec.new, sec.closing, sec.problems = read_ulsan_db(path, today, soon)
        except Exception as exc:  # noqa: BLE001 — 한 곳이 깨져도 나머지는 보낸다
            sec.error = f"{type(exc).__name__}: {exc}"[:200]
            sec.ready = True  # 오류도 '도착'으로 보고 브리핑에 표시한다
        sec.new, sec.closing = _by_deadline(sec.new), _by_deadline(sec.closing)
        sections.append(sec)
    camp = read_camp_db(local_db, today, soon) if local_db and local_db.exists() else CampSummary()
    links = cfg.get("weekly_links", []) if today.weekday() == 0 else []
    return Briefing(today=today, sections=sections, camp=camp, links=links)


def http_fetch(url: str) -> bytes | None:
    from .http import Http

    import requests

    try:
        return Http(min_interval=0.2).get(url).content
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None  # 오늘 보고서가 아직 없음
        raise


# ---------------------------------------------------------------- 메일


def subject_line(b: Briefing) -> str:
    d = b.today
    new = sum(len(s.new) for s in b.sections)
    soon = sum(len(s.closing) for s in b.sections)
    return (f"[오늘의 브리핑] {d.month}/{d.day}({WEEKDAYS[d.weekday()]}) 강사 신규 {new}건 · 마감임박 {soon}건"
            f" · 캠프 바로지원 {len(b.camp.go)}건")


def _row(it: Item, today: date, show_region: bool = True) -> str:
    dd = dday_label(it.deadline, today) if it.deadline else "원문확인"
    color = "#c0392b" if it.deadline and (it.deadline - today).days <= 1 else "#555"
    title = f"<a href='{escape(it.url, quote=True)}'>{escape(it.title)}</a>" if it.url else escape(it.title)
    meta = " · ".join(x for x in [it.region if show_region else "", it.org, it.note] if x)
    return (
        "<tr>"
        f"<td style='padding:4px 8px;white-space:nowrap;color:{color};vertical-align:top'>{escape(dd)}</td>"
        f"<td style='padding:4px 8px'>{title}"
        + (f"<br><span style='color:#888;font-size:12px'>{escape(meta)}</span>" if meta else "")
        + "</td></tr>"
    )


def _list(title: str, items: list[Item], today: date, limit: int, empty: str = "없음") -> str:
    if not items:
        return f"<p style='margin:4px 0 10px;color:#888'><b style='color:#222'>{escape(title)}</b> · {empty}</p>"
    rows = "".join(_row(i, today) for i in items[:limit])
    more = f"<p style='margin:2px 8px;color:#888;font-size:12px'>…외 {len(items) - limit}건</p>" if len(items) > limit else ""
    return (f"<p style='margin:10px 0 2px'><b>{escape(title)}</b> ({len(items)})</p>"
            f"<table cellspacing='0' style='border-collapse:collapse'>{rows}</table>{more}")


def html_body(b: Briefing, limit: int = 15) -> str:
    t = b.today
    new = sum(len(s.new) for s in b.sections)
    soon = sum(len(s.closing) for s in b.sections)
    per_region = " · ".join(f"{s.name} {len(s.new)}" for s in b.sections)
    parts = [
        "<div style=\"font-family:'Malgun Gothic',sans-serif;font-size:14px;color:#222;max-width:760px\">",
        f"<h2 style='margin:0 0 6px'>오늘의 브리핑 {t.isoformat()} ({WEEKDAYS[t.weekday()]})</h2>",
        f"<p style='margin:0 0 12px'>강사 공고 신규 <b>{new}</b>건 ({escape(per_region)}) · 마감임박 <b>{soon}</b>건"
        f" · 캠프 수주 바로지원 <b>{len(b.camp.go)}</b>건 · 검토 <b>{len(b.camp.review)}</b>건</p>",
    ]
    if b.missing:
        parts.append(
            "<div style='background:#fff7e6;border:1px solid #f5d49a;padding:8px 12px;margin:8px 0'>"
            f"아직 오늘 자료가 도착하지 않은 곳: <b>{escape(', '.join(b.missing))}</b> — 도착하면 지역별 메일로 따로 옵니다.</div>"
        )
    for link in b.links:
        parts.append(
            "<div style='background:#eef4ff;border:1px solid #c7d7f5;padding:8px 12px;margin:8px 0'>"
            f"📌 <a href='{escape(link['url'], quote=True)}'>{escape(link['label'])}</a></div>"
        )

    # 1. 캠프 수주
    c = b.camp
    parts.append("<h3 style='margin:18px 0 4px;border-bottom:2px solid #1f4e78'>🏫 캠프·교육 수주 (나라장터·S2B)</h3>")
    if not c.ready and not (c.go or c.review or c.ref):
        parts.append("<p style='color:#888'>오늘 수집 전</p>")
    else:
        parts.append(_list("★ 바로지원 (부산 업체가 낼 수 있는 소액·수의 견적)", c.go, t, limit))
        parts.append(_list("검토 (금액이 큰 입찰 등)", c.review, t, min(limit, 5)))
        if c.ref:
            parts.append(f"<p style='margin:4px 0;color:#888'>참고(다른 지역·지정 업체 한정) {len(c.ref)}건은"
                         " 캠프·교육 수주 메일에 있습니다.</p>")
        if c.closing:
            parts.append(_list("마감임박 (이미 알린 공고)", _by_deadline(c.closing), t, limit))

    # 2. 강사 공고: 전 지역 마감임박을 먼저, 그다음 지역별 신규
    all_soon = _by_deadline([
        Item(**{**i.__dict__, "region": f"{s.name}({i.region})" if i.region and i.region != s.name else s.name})
        for s in b.sections for i in s.closing
    ])
    parts.append("<h3 style='margin:18px 0 4px;border-bottom:2px solid #1f4e78'>⏰ 강사 공고 마감임박 (3일 이내, 전 지역)</h3>")
    parts.append(_list("마감 빠른 순", all_soon, t, limit * 2))

    parts.append("<h3 style='margin:18px 0 4px;border-bottom:2px solid #1f4e78'>🆕 강사 공고 신규 (지역별)</h3>")
    for s in b.sections:
        if not s.ready:
            parts.append(f"<p style='margin:6px 0;color:#888'><b style='color:#222'>{escape(s.name)}</b> · 아직 도착 전</p>")
            continue
        if s.error:
            parts.append(f"<p style='margin:6px 0;color:#c0392b'><b>{escape(s.name)}</b> · 읽기 실패: {escape(s.error)}</p>")
            continue
        title = s.name
        parts.append(_list(title, s.new, t, limit))
        if s.link:
            parts.append(f"<p style='margin:0 8px 6px;font-size:12px'><a href='{escape(s.link, quote=True)}'>"
                         f"{escape(s.name)} 전체 보고서</a></p>")

    problems = [(s.name, p) for s in b.sections for p in s.problems]
    if problems:
        items = ", ".join(f"{n} {p}" for n, p in problems)
        parts.append(f"<p style='color:#b45309;font-size:13px;margin-top:14px'>⚠ 수집이 실패한 곳: {escape(items)}"
                     " — 지역별 메일 아래쪽에 자세한 이유가 있습니다.</p>")
    parts.append("<p style='color:#888;font-size:12px;margin-top:16px'>지역별 메일은 그대로 따로 나갑니다. "
                 "이 브리핑은 GitHub Actions 에서 매일 아침 자동으로 보냅니다.</p></div>")
    return "".join(parts)


def text_body(b: Briefing) -> str:
    lines = [subject_line(b), ""]
    for label, items in (("캠프 바로지원", b.camp.go), ("캠프 검토", b.camp.review)):
        for i in items:
            lines.append(f"[{label}] {i.deadline_text or '-'} | {i.org} | {i.title}\n  {i.url}")
    for s in b.sections:
        lines.append(f"\n[{s.name}] 신규 {len(s.new)}건" + ("" if s.ready else " (도착 전)"))
        for i in s.new:
            lines.append(f"- {dday_label(i.deadline, b.today) if i.deadline else '원문확인'} | {i.org} | {i.title}\n  {i.url}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 실행


@dataclass
class BriefingOutcome:
    briefing: Briefing
    sent: bool = False
    reason: str = ""


def should_send(b: Briefing, now: datetime, cfg: dict, already_sent: bool, force: bool) -> tuple[bool, str]:
    if force:
        return True, "강제 발송"
    if already_sent:
        return False, "오늘 이미 보냄"
    if b.all_ready:
        return True, "모든 자료 도착"
    hh, mm = (int(x) for x in str(cfg.get("send_anyway_after", "09:00")).split(":"))
    if (now.hour, now.minute) >= (hh, mm):
        return True, f"{hh:02d}:{mm:02d} 지남 — 도착한 것만으로 발송 (미도착: {', '.join(b.missing)})"
    return False, f"기다리는 중 (미도착: {', '.join(b.missing)})"


def run_briefing(
    *,
    db_path: Path | None,
    state_path: Path,
    send_mail: bool,
    force: bool = False,
    config_dir: Path = DEFAULT_CONFIG_DIR,
    now: datetime | None = None,
    fetch=None,
    out_html: Path | None = None,
) -> BriefingOutcome:
    cfg = load_config(config_dir)
    now = now or now_kst()
    today = now.date()
    b = gather(cfg, today, db_path, fetch or http_fetch)
    already = state_path.exists() and state_path.read_text(encoding="utf-8").strip() == today.isoformat()
    go, reason = should_send(b, now, cfg, already, force)
    html = html_body(b, int(cfg.get("max_items", 15)))
    if out_html:
        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_html.write_text(f"<meta charset='utf-8'>{html}", encoding="utf-8")
    outcome = BriefingOutcome(briefing=b, reason=reason)
    if not go or not send_mail:
        return outcome
    from .mailer import MailConfig, build_message, send

    to = [a.strip() for a in os.environ.get("BRIEFING_TO", "").split(",") if a.strip()]
    if not to:
        raise RuntimeError("BRIEFING_TO 미설정: 브리핑 받을 주소를 GitHub Secret 에 등록하세요.")
    cfg_mail = MailConfig.from_env()
    cfg_mail.to = to
    send(cfg_mail, build_message(cfg_mail, subject_line(b), html, text_body(b), None))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(today.isoformat(), encoding="utf-8")
    outcome.sent = True
    return outcome
