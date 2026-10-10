"""오늘의 브리핑: 지역별 강사 공고 메일과 캠프 수주 메일을 한 통으로 묶어 보낸다.

읽는 곳 (config/briefing.yaml)
    ulsan_db   울산(또는 같은 구조의 부산) 수집 기록 DB — 오늘 메일로 알린 신규, 마감임박, 수집 이상
               이 저장소는 state 브랜치에서 복원한 로컬 DB(--db), 다른 저장소는 raw 주소로 내려받는다.
    md_report  경남·대학평생교육원 저장소가 매일 커밋하는 reports/YYYY-MM-DD.md
    (캠프 수주는 이 저장소 DB 의 camp_bids / camp_runs)

강사잇다 운영 (config/briefing.yaml 의 gangsaitda, 9/29 추가)
    합본 엑셀  울산 DB 의 마감 전 공고 + 경남·평생교육원 저장소가 매일 커밋하는 reports/gangsaitda/latest.xlsx
               (그날 보고서가 도착한 곳만) → 강사잇다 양식 한 파일로 합쳐 메일에 첨부. 제목·기관이 같은 줄은 하나만.
    공유 글    오늘 새 공고 제목·지역·D-day 로 강사방에 붙여 넣을 요약 글을 메일 맨 아래에 넣는다.

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
    upload: "UploadFile | None" = None  # 강사잇다 합본 엑셀
    imported: "ImportResult | None" = None  # 사이트에 자동 등록한 결과 (열쇠가 있을 때만)
    share_text: str = ""  # 강사방 공유용 요약 글

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


# ---------------------------------------------------------------- 강사잇다 운영: 합본 엑셀·공유 글


@dataclass
class UploadFile:
    path: Path | None = None
    rows: int = 0
    held: int = 0  # '보류' 줄 (마감일 확인 필요)
    by_region: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)  # 이번 파일에 못 넣은 곳 (도착 전·읽기 실패)
    data: list[dict] = field(default_factory=list)  # 합본 줄 (칸 이름 → 값). 사이트로 보낼 때 쓴다


@dataclass
class ImportResult:
    """강사잇다 사이트 '받는 문'(/api/jobs/import)에 보낸 결과."""

    sent: int = 0  # 보낸 줄 수
    created: int = 0  # 사이트에 새로 올라간 공고 수
    skipped: int = 0  # 건너뛴 줄 (이미 있음·보류)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (제목, 이유) — 사이트가 넣지 못한 줄
    error: str = ""  # 보내기 자체가 실패한 이유 (열쇠 틀림, 접속 실패 등)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.search(r"(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})", str(value or ""))
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def _ulsan_rows(db: Path, today: date, config_dir: Path) -> list[dict]:
    """울산 수집 DB 의 마감 전 공고 → 강사잇다 양식 줄 (지역 메일에 붙는 파일과 같은 기준)."""
    from .config import load_rules, load_settings
    from .gangsaitda import row_values
    from .report_excel import sort_key
    from .storage import Store

    rules = load_rules(config_dir)
    names = {s.id: s.name for s in load_settings(config_dir).sources}
    statuses = ("모집중", "결과공고") if rules.keep_result_notices else ("모집중",)
    store = Store(db)
    try:
        active = sorted(store.active(today, rules.active_max_age_days, statuses), key=lambda p: sort_key(p, today))
    finally:
        store.close()
    return [row_values(p, names.get(p.source_id, "")) for p in active]


def _clean_headcount(value) -> int | None:
    """'2', '2명', 2.0 → 2. 1~999 가 아니거나 숫자가 아니면 None (사이트 규칙과 같다)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value) if float(value).is_integer() else None
    else:
        m = re.fullmatch(r"\s*(\d{1,3})\s*명?\s*", str(value))
        n = int(m.group(1)) if m else None
    return n if n is not None and 1 <= n <= 999 else None


def _key(row: dict) -> str:
    return re.sub(r"\s+", "", str(row.get("제목", ""))) + "|" + re.sub(r"\s+", "", str(row.get("기관명", "")))


def build_upload(cfg: dict, b: Briefing, local_db: Path | None, fetch, out_dir: Path,
                 config_dir: Path = DEFAULT_CONFIG_DIR) -> UploadFile:
    """지역별 강사잇다 양식을 한 파일로. 오늘 자료가 도착한 곳만 넣는다 (어제 파일이 섞이지 않게)."""
    from .gangsaitda import HOLD, SCHEDULE_FALLBACK, TEMPLATE_NAME, read_rows, write_rows

    out = UploadFile()
    rows: list[dict] = []
    by_id = {s.id: s for s in b.sections}
    for src in cfg.get("sources", []):
        if src.get("enabled", True) is False:
            continue
        name = src["name"]
        sec = by_id.get(src["id"])
        try:
            if src["type"] == "ulsan_db" and not src.get("repo"):
                if local_db is None or not local_db.exists():
                    out.missing.append(name)
                    continue
                got = _ulsan_rows(local_db, b.today, config_dir)
            elif src.get("upload_url"):
                if sec is None or not sec.ready or sec.error:
                    out.missing.append(name)
                    continue
                body = fetch(src["upload_url"])
                if body is None:
                    out.missing.append(name)
                    continue
                got = read_rows(body)
                for r in got:
                    r["메모"] = " · ".join(x for x in (f"{name} 수집", str(r.get("메모") or "")) if x)
            else:
                continue
        except Exception as exc:  # noqa: BLE001 — 한 곳이 깨져도 나머지로 만든다
            out.missing.append(f"{name}(읽기 실패: {type(exc).__name__})")
            continue
        # 캐시 등으로 지난 파일이 섞여도 마감이 지난 줄은 올라가지 않으니 뺀다
        got = [r for r in got if not (_as_date(r.get("마감일")) and _as_date(r.get("마감일")) < b.today)]
        out.by_region[name] = len(got)
        rows.extend(got)
    seen: set[str] = set()
    merged = []
    for r in rows:
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        # '수업 일정'은 사이트 필수 칸. 다른 저장소 양식에 비어 있으면 울산·부산 양식과 같은 안내로 채운다
        if not str(r.get("수업 일정") or "").strip():
            r["수업 일정"] = SCHEDULE_FALLBACK
        # '모집 인원'은 사이트가 1~999 숫자만 받는다. 전화번호 조각 등 엉뚱한 값이면 비우고 메모에 남긴다
        headcount = _clean_headcount(r.get("모집 인원"))
        if headcount != r.get("모집 인원"):
            r["모집 인원"] = headcount
            if headcount is None:
                r["메모"] = " · ".join(x for x in ("모집 인원 확인 필요", str(r.get("메모") or "")) if x)
        merged.append(r)
    merged.sort(key=lambda r: (r.get("처리") == HOLD, _as_date(r.get("마감일")) or date.max, str(r.get("지역", ""))))
    out.rows = len(merged)
    out.held = sum(1 for r in merged if r.get("처리") == HOLD)
    out.data = merged
    if merged:
        out.path = write_rows(out_dir / f"강사잇다_부울경_{b.today.isoformat()}.xlsx", merged,
                              config_dir / TEMPLATE_NAME)
    return out


IMPORT_BATCH = 200  # 사이트가 한 번에 받는 최대 줄 수


def _json_value(value):
    """엑셀 칸 값을 JSON 으로 보낼 수 있는 모양으로 (날짜는 '2026-10-15')."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def push_upload(rows: list[dict], url: str, token: str, post=None) -> ImportResult:
    """합본 줄을 강사잇다 사이트의 받는 문으로 보낸다. 사이트가 엑셀 올리기와 같은 규칙으로 확인해 올린다.

    같은 제목은 사이트가 건너뛰므로 하루에 여러 번 보내도 공고가 두 번 올라가지 않는다.
    '보류' 줄도 그대로 보내고 사이트가 건너뛴다 (돌아온 결과에 남게).
    """
    import requests

    post = post or requests.post
    result = ImportResult(sent=len(rows))
    for start in range(0, len(rows), IMPORT_BATCH):
        batch = [{k: _json_value(v) for k, v in r.items()} for r in rows[start:start + IMPORT_BATCH]]
        try:
            resp = post(url, json={"rows": batch}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        except requests.exceptions.RequestException as exc:
            result.error = f"사이트 접속 실패: {type(exc).__name__}"
            return result
        if resp.status_code != 200:
            try:
                detail = str(resp.json().get("error", ""))
            except ValueError:
                detail = ""
            result.error = f"사이트가 거절함 (HTTP {resp.status_code}) {detail}".strip()
            return result
        body = resp.json()
        result.created += int(body.get("created", 0))
        result.skipped += len(body.get("skipped", []))
        result.failed += [(str(f.get("title", "")), str(f.get("reason", ""))) for f in body.get("failed", [])]
    return result


def _place(it: Item, sec: Section) -> str:
    if sec.id == "ulsan":
        return "울산"
    first = re.split(r"[,·]", it.region or "")[0].strip()
    return first or sec.name


def share_text(b: Briefing, cfg: dict) -> str:
    """강사방에 그대로 붙여 넣을 오늘의 공고 요약 (제목·지역·D-day 만. 기관·지원 방법은 사이트에서)."""
    from .gangsaitda import clean_title

    g = cfg.get("gangsaitda") or {}
    site = str(g.get("site_url") or "").strip() or "[사이트 주소]"
    limit = int(g.get("share_max", 10))
    d = b.today
    day = f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"
    ready = [s for s in b.sections if s.ready and not s.error]

    def pick(attr: str) -> list[tuple[Item, Section]]:
        seen: set[str] = set()
        out = []
        pairs = [(i, s) for s in ready for i in getattr(s, attr)]
        for i, s in sorted(pairs, key=lambda x: (x[0].deadline or date.max, x[0].title)):
            k = re.sub(r"\s+", "", i.title)
            if k not in seen:
                seen.add(k)
                out.append((i, s))
        return out

    new = pick("new")
    today_close = {re.sub(r"\s+", "", i.title) for s in ready for i in s.new + s.closing if i.deadline == d}
    if new:
        head = f"[오늘의 부울경 강사 공고] {day} 새 공고 {len(new)}건"
        items = new
    else:
        items = pick("closing")
        head = f"[오늘의 부울경 강사 공고] {day} 새 공고는 없고 마감 임박 {len(items)}건"
    if new and today_close:
        head += f" · 오늘 마감 {len(today_close)}건"
    lines = [head]
    for i, s in items[:limit]:
        dd = dday_label(i.deadline, d) if i.deadline else "마감 확인"
        lines.append(f"- {clean_title(i.title)} ({_place(i, s)} · {dd})")
    if len(items) > limit:
        lines.append(f"외 {len(items) - limit}건")
    lines.append(f"전체 보기: {site}")
    return "\n".join(lines)


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
    # 3. 강사잇다 운영: 합본 엑셀 안내 + 강사방 공유 글
    parts.append("<h3 style='margin:18px 0 4px;border-bottom:2px solid #1f4e78'>📋 강사잇다 올리기 · 강사방 공유</h3>")
    up = b.upload
    imp = b.imported
    if imp is not None:
        if imp.error:
            parts.append(f"<p style='margin:4px 0;color:#b45309'>⚠ 사이트 자동 등록 실패: {escape(imp.error)}"
                         " — 첨부 엑셀을 관리자 화면 <b>/admin/jobs/upload</b> 에 직접 올려 주세요.</p>")
        else:
            parts.append(f"<p style='margin:4px 0'>✅ 사이트 자동 등록: 새로 <b>{imp.created}</b>건 올라감"
                         f" (보낸 {imp.sent}줄 중 이미 있거나 보류라 건너뜀 {imp.skipped}줄"
                         + (f", 넣지 못함 {len(imp.failed)}줄" if imp.failed else "") + ")</p>")
            if imp.failed:
                items = "".join(f"<li>{escape(t)} — {escape(r)}</li>" for t, r in imp.failed[:10])
                parts.append(f"<ul style='margin:0 0 6px;font-size:13px;color:#b45309'>{items}</ul>")
    if up is not None and up.path is not None:
        regions = ", ".join(f"{k} {v}" for k, v in up.by_region.items())
        how = ("확인용으로 붙였습니다 (사이트에 이미 올라갔으니 다시 올리지 않아도 됩니다)"
               if imp is not None and not imp.error
               else "강사잇다 관리자 화면 <b>/admin/jobs/upload</b> 에 이 파일 하나만 올리면 됩니다")
        parts.append(
            f"<p style='margin:4px 0'>📎 첨부 <b>{escape(up.path.name)}</b> — 부울경 마감 전 공고 <b>{up.rows}</b>줄 ({escape(regions)})"
            + (f", 그중 마감일을 못 찾은 {up.held}줄은 '처리' 칸에 '보류'" if up.held else "")
            + f". {how}.</p>")
    if up is not None and up.missing:
        parts.append(f"<p style='margin:4px 0;color:#b45309;font-size:13px'>합본에 빠진 곳: {escape(', '.join(up.missing))}"
                     " — 그 지역 메일의 엑셀을 따로 올려 주세요.</p>")
    if b.share_text:
        parts.append("<p style='margin:10px 0 4px'><b>강사방에 붙여 넣을 글</b> (그대로 복사)</p>"
                     "<pre style=\"white-space:pre-wrap;font-family:'Malgun Gothic',sans-serif;font-size:14px;"
                     "background:#f6f8fa;border:1px solid #ddd;padding:10px 12px;margin:0\">"
                     f"{escape(b.share_text)}</pre>")

    parts.append("<p style='color:#888;font-size:12px;margin-top:16px'>지역별 메일은 그대로 따로 나갑니다. "
                 "이 브리핑은 GitHub Actions 에서 매일 아침 자동으로 보냅니다.</p></div>")
    return "".join(parts)


def text_body(b: Briefing) -> str:
    lines = [subject_line(b), ""]
    if b.share_text:
        lines += ["[강사방에 붙여 넣을 글]", b.share_text, ""]
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
    out_dir: Path | None = None,
    post=None,
) -> BriefingOutcome:
    cfg = load_config(config_dir)
    now = now or now_kst()
    today = now.date()
    fetch = fetch or http_fetch
    b = gather(cfg, today, db_path, fetch)
    if cfg.get("gangsaitda", {}).get("enabled", True):
        b.share_text = share_text(b, cfg)
        try:
            out_dir = out_dir or (out_html.parent if out_html else Path(tempfile.mkdtemp()))
            b.upload = build_upload(cfg, b, db_path, fetch, out_dir, config_dir)
        except Exception as exc:  # noqa: BLE001 — 엑셀이 실패해도 브리핑은 보낸다
            b.upload = UploadFile(missing=[f"합본 엑셀 실패: {type(exc).__name__}: {exc}"[:200]])
        # 사이트 자동 등록: 받는 문 주소(설정)와 열쇠(GANGSAITDA_IMPORT_TOKEN)가 둘 다 있을 때만.
        # 메일을 안 보내는 시험 실행(--no-mail)에서는 사이트도 건드리지 않는다.
        import_url = str(cfg.get("gangsaitda", {}).get("import_url") or "").strip()
        token = os.environ.get("GANGSAITDA_IMPORT_TOKEN", "").strip()
        if send_mail and import_url and token and b.upload is not None and b.upload.data:
            try:
                b.imported = push_upload(b.upload.data, import_url, token, post=post)
            except Exception as exc:  # noqa: BLE001 — 등록이 실패해도 브리핑은 보낸다 (엑셀을 직접 올리면 됨)
                b.imported = ImportResult(sent=len(b.upload.data), error=f"{type(exc).__name__}: {exc}"[:200])
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
    attachment = b.upload.path if b.upload is not None else None
    send(cfg_mail, build_message(cfg_mail, subject_line(b), html, text_body(b), attachment))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(today.isoformat(), encoding="utf-8")
    outcome.sent = True
    return outcome
