"""SQLite 저장소: 한 번 본 공고를 기억해서 '신규'를 판정하고, 수집 이력을 남긴다.

신규 판정은 '처음 본 시각'이 아니라 '아직 메일로 보내지 않았는가(reported_at IS NULL)'로 한다.
메일 발송이 실패하면 다음 실행 때 다시 신규로 잡혀서 공고를 놓치지 않는다.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Posting, SourceResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    uid           TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    post_key      TEXT,
    org_name      TEXT,
    org_type      TEXT,
    district      TEXT,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    posted_date   TEXT,
    deadline      TEXT,
    category      TEXT,
    status        TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    reported_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_postings_reported ON postings(reported_at);
CREATE TABLE IF NOT EXISTS source_runs (
    run_at    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    state     TEXT NOT NULL,
    fetched   INTEGER NOT NULL,
    matched   INTEGER NOT NULL,
    new       INTEGER NOT NULL,
    error     TEXT
);
"""


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def title_of(self, uid: str) -> str | None:
        row = self.conn.execute("SELECT title FROM postings WHERE uid = ?", (uid,)).fetchone()
        return row[0] if row else None

    def upsert(self, p: Posting, now: datetime) -> bool:
        """저장하고, 처음 보는 공고면 True."""
        ts = now.isoformat(timespec="seconds")
        row = self.conn.execute("SELECT uid FROM postings WHERE uid = ?", (p.uid,)).fetchone()
        if row is None:
            self.conn.execute(
                """INSERT INTO postings (uid, source_id, post_key, org_name, org_type, district, title, url,
                       posted_date, deadline, category, status, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p.uid, p.source_id, p.post_key, p.org_name, p.org_type, p.district, p.title, p.url,
                 _iso(p.posted_date), _iso(p.deadline), p.category, p.status, ts, ts),
            )
            return True
        self.conn.execute(
            """UPDATE postings SET title = ?, url = ?, status = ?, category = ?, last_seen_at = ?,
                   org_name = COALESCE(NULLIF(?, ''), org_name),
                   posted_date = COALESCE(posted_date, ?),
                   deadline = COALESCE(?, deadline)
               WHERE uid = ?""",
            (p.title, p.url, p.status, p.category, ts, p.org_name, _iso(p.posted_date), _iso(p.deadline), p.uid),
        )
        return False

    def set_deadline(self, uid: str, deadline: date) -> None:
        self.conn.execute("UPDATE postings SET deadline = ? WHERE uid = ?", (_iso(deadline), uid))

    def unreported(self) -> list[Posting]:
        rows = self.conn.execute("SELECT * FROM postings WHERE reported_at IS NULL ORDER BY first_seen_at")
        return [_to_posting(r) for r in rows]

    def mark_reported(self, uids: list[str], now: datetime) -> None:
        ts = now.isoformat(timespec="seconds")
        self.conn.executemany("UPDATE postings SET reported_at = ? WHERE uid = ?", [(ts, u) for u in uids])

    def active(self, today: date, max_age_days: int, statuses: tuple[str, ...] = ("모집중",)) -> list[Posting]:
        """마감 전이거나, 마감일을 모르지만 최근 게시된 공고."""
        cutoff = (today - timedelta(days=max_age_days)).isoformat()
        marks = ",".join("?" for _ in statuses)
        rows = self.conn.execute(
            f"""SELECT * FROM postings
                WHERE status IN ({marks})
                  AND (deadline >= ?
                       OR (deadline IS NULL AND COALESCE(posted_date, substr(first_seen_at, 1, 10)) >= ?))""",
            (*statuses, today.isoformat(), cutoff),
        )
        return [_to_posting(r) for r in rows]

    def log_runs(self, results: list[SourceResult], now: datetime) -> None:
        ts = now.isoformat(timespec="seconds")
        self.conn.executemany(
            "INSERT INTO source_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(ts, r.source_id, r.state, r.fetched, r.matched, r.new, r.error) for r in results],
        )

    def commit(self) -> None:
        self.conn.commit()


def _to_posting(r: sqlite3.Row) -> Posting:
    return Posting(
        source_id=r["source_id"],
        title=r["title"],
        url=r["url"],
        post_key=r["post_key"] or "",
        org_name=r["org_name"] or "",
        org_type=r["org_type"] or "",
        district=r["district"] or "",
        posted_date=_d(r["posted_date"]),
        deadline=_d(r["deadline"]),
        category=r["category"] or "",
        status=r["status"] or "",
    )
