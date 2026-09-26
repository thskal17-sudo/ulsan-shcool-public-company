"""게시판마다 제각각인 날짜 표기를 date 로 바꾸고, 본문에서 접수 마감일을 찾는다."""
from __future__ import annotations

import re
from datetime import date, timedelta

# 2026-09-25, 2026.09.25, 2026. 9. 25., 2026/9/25, 2026년 9월 25일
_FULL = re.compile(r"(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*일?")
# 26-09-25, 26.09.25 (연도 두 자리)
_SHORT_YEAR = re.compile(r"(?<!\d)(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)")
# 9. 25., 9.25, 9/25, 9월 25일 (연도 없음)
_NO_YEAR = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:[./]|월)\s*(\d{1,2})\s*(?:일|\.)?(?![\d:])")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str | None, today: date | None = None) -> date | None:
    """문자열 안의 첫 날짜를 읽는다. 연도가 없으면 today 기준으로 가장 가까운 해를 고른다."""
    if not text:
        return None
    m = _FULL.search(text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _SHORT_YEAR.search(text)
    if m:
        return _safe_date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _NO_YEAR.search(text)
    if m and today is not None:
        return _nearest_year(int(m.group(1)), int(m.group(2)), today)
    return None


def _nearest_year(month: int, day: int, today: date) -> date | None:
    candidates = [_safe_date(today.year + dy, month, day) for dy in (-1, 0, 1)]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs((c - today).days))


def find_all_dates(text: str, today: date) -> list[tuple[int, date]]:
    """(위치, 날짜) 목록. 연도 없는 날짜는 앞에 나온 날짜의 연도를 따른다."""
    found: list[tuple[int, int, date]] = []
    taken: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < te and ts < e for ts, te in taken)

    for rx, kind in ((_FULL, "full"), (_SHORT_YEAR, "short"), (_NO_YEAR, "noyear")):
        for m in rx.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            if kind == "full":
                d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif kind == "short":
                d = _safe_date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                prev = [f for f in found if f[0] < m.start()]
                base_year = max(prev, key=lambda f: f[0])[2].year if prev else None
                if base_year is not None:
                    d = _safe_date(base_year, int(m.group(1)), int(m.group(2)))
                    if d and prev and d < max(prev, key=lambda f: f[0])[2]:
                        d = _safe_date(base_year + 1, int(m.group(1)), int(m.group(2)))
                else:
                    d = _nearest_year(int(m.group(1)), int(m.group(2)), today)
            if d:
                found.append((m.start(), m.end(), d))
                taken.append((m.start(), m.end()))
        found.sort()
    return [(s, d) for s, _, d in found]


_PERIOD_LABEL = re.compile(r"(접수|모집|신청|제출|공고)\s*(기간|기한|마감|일시|일정)")


def extract_deadline(text: str | None, today: date) -> date | None:
    """본문·제목에서 접수 마감일을 찾는다.

    1순위: '접수기간/모집기간 …' 뒤 200자 안의 '~' 다음 날짜 (없으면 '까지' 앞 날짜)
    2순위: 문서 어디든 '~ 날짜' 또는 '날짜 까지'
    """
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    windows = [text[m.end(): m.end() + 200] for m in _PERIOD_LABEL.finditer(text)]
    for window in windows + [text]:
        d = _deadline_in(window, today)
        if d:
            return d
    return None


def _deadline_in(text: str, today: date) -> date | None:
    dates = find_all_dates(text, today)
    if not dates:
        return None
    tilde = re.search(r"[~∼～〜]", text)
    if tilde:
        after = [d for pos, d in dates if pos > tilde.start()]
        if after:
            return after[0]
        before = [d for pos, d in dates if pos < tilde.start()]
        if before:
            return before[-1]  # '9. 26. 09:00 ~ 18:00' 처럼 같은 날 마감
    until = re.search(r"까지", text)
    if until:
        before = [d for pos, d in dates if pos < until.start()]
        if before:
            return before[-1]
    return None


def days_left(deadline: date | None, today: date) -> int | None:
    return None if deadline is None else (deadline - today).days


def dday_label(deadline: date | None, today: date) -> str:
    n = days_left(deadline, today)
    if n is None:
        return "원문확인"
    if n < 0:
        return "마감"
    if n == 0:
        return "D-day"
    return f"D-{n}"


def within_days(d: date | None, today: date, days: int) -> bool:
    return d is not None and d >= today - timedelta(days=days)
