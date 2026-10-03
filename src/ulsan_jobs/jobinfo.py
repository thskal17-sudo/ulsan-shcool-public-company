"""공고 본문·첨부 공고문에서 강사잇다 양식에 넣을 칸(수업 일정·대상·인원·자격·서류·이메일)을 찾는다.

공고문은 대개 '라벨: 값' 줄이거나, 표를 글자로 풀면 라벨 칸과 값 칸이 차례로 한 줄씩 나온다.
    ○ 위촉기간: 2026. 10. 1. ~ 12. 31.        라벨과 값이 한 줄
    2. 지원자격                                라벨만 있고 아래 줄에 항목들
       - 관련 자격증 소지자
    모집분야 / 모집인원 / 위촉기간             표 머리 칸이 연달아 나온 뒤
    수영(초급반) / 2명 / 2026. 10. 1. ~        같은 순서로 값 칸
라벨은 줄 앞(번호·기호 뒤)에 있을 때만 인정하고, 칸마다 값 모양을 확인한다 (일정은 날짜가 있어야 하는 등).
찾지 못한 칸은 비워 두고, 강사잇다 양식을 만들 때 안내 문구로 채운다.
"""
from __future__ import annotations

import re


# 찾는 방법을 고치면 올린다. 저장된 정보의 버전이 다르면 다음 실행 때 공고문을 다시 읽는다
INFO_VERSION = 5


def _words(*words: str) -> str:
    """'위촉기간' → '위\\s*촉\\s*기\\s*간' (공고문은 글자 사이를 띄워 쓰기도 한다)."""
    return "|".join(r"\s*".join(re.escape(ch) for ch in w.replace(" ", "")) for w in words)


LABELS = {
    "schedule": _words(
        "운영기간", "교육기간", "수업기간", "강의기간", "강좌기간", "위촉기간", "위탁기간", "계약기간",
        "근무기간", "활동기간", "사업기간", "채용기간", "임용기간", "근무예정기간", "계약예정기간",
        "운영일시", "수업일시", "교육일시", "강의일시", "운영일정", "수업일정", "교육일정",
    ),
    "hours": _words("수업시간", "운영시간", "교육시간", "강의시간", "근무시간", "수업요일"),
    "target": _words("교육대상", "수업대상", "수강대상", "운영대상", "참여대상", "대상학년", "대상학생"),
    "headcount": _words("모집인원", "선발인원", "채용인원", "위촉인원", "모집예정인원"),
    "qualification": _words("지원자격", "응시자격", "자격요건", "신청자격", "응모자격", "자격기준", "지원조건"),
    "documents": _words("제출서류", "구비서류", "접수서류", "응시서류", "신청서류"),
    # 모집 분야는 값이 '강사'·'운영 조건'처럼 엉뚱한 경우가 많아 쓰지 않고, 표 머리 칸을 맞추는 데만 쓴다
    "field": _words("모집분야", "모집과목", "모집종목", "모집강좌", "강좌명", "프로그램명", "강의과목", "채용분야"),
}
# 값을 모으다가 여기서 멈추는 다른 항목 이름들
_OTHER = _words(
    "접수기간", "접수방법", "접수처", "전형방법", "선발방법", "심사방법", "전형일정", "문의처", "문의",
    "보수", "강사료", "근무조건", "근무장소", "기타사항", "유의사항", "합격자발표", "결과발표",
)
_BULLET = r"(?:[○◦●■□▶▷◆◇※·ㆍ•\-*▪]|\(?\d{1,2}[).]|\(?[가나다라마바사아자차카타파하][).]|[①-⑳])"
_PATTERNS = {
    key: re.compile(rf"^(?:{_BULLET}\s*)*(?:{label})(?![가-힣])\s*[:：]?\s*(.*)$")
    for key, label in {**LABELS, "other": _OTHER}.items()
}
_ITEM = re.compile(r"^(?:[-·ㆍ•*◦○▪]|[①-⑳]|\(?\d{1,2}\)|[가나다라마바사아자차카타파하]\.)\s*")
_HEADING_TAIL = re.compile(r"^(?:및|과|와|등|의|에\s)")  # '1. 모집분야 및 인원' 같은 제목 줄
_MULTI = {"qualification", "documents"}

_DATED = re.compile(r"(?:19|20)\d{2}\s*[.년]\s*\d{1,2}\s*[.월]\s*\d{1,2}")
# 일정 값이 날짜 모양인지: '10. 14'·'10월 14일'·'2026년 10월'·'10월~12월' (숫자 하나뿐인 '1' 은 아님)
_DATE_LIKE = re.compile(r"\d{1,2}\s*[./월]\s*\d{1,2}|(?:19|20)\d{2}\s*[.년]\s*\d{1,2}|\d{1,2}\s*월")
_SECTION = re.compile(r"^\d{1,2}\.\s*\S")  # '2. 지원 자격' 같은 큰 번호 줄
_RECRUIT_SECTION = re.compile(r"^\d{1,2}\.\s*.*(?:모집|채용)")  # '1. 모집내용', '1. 채용 과목 및 기간'
_BARE_PERIOD = re.compile(r"^기\s*간$")  # 모집 표의 칸 이름이 '기간'뿐인 경우
_HEAD_CELL_MAX = 10  # 라벨 아래 표 머리 칸('구분', '내용', '1차 전형')으로 보는 짧은 줄 길이

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_APPLY_WORDS = re.compile(r"접수|제출|지원|응모|신청")
_MAIL_WORDS = re.compile(r"이메일|전자우편|e-?mail|메일", re.I)
_JUNK_EMAIL = re.compile(r"^(?:webmaster|admin|master|root|privacy|noreply|no-reply|help)@", re.I)


def _lines(text: str | None) -> list[str]:
    return [ln for ln in (re.sub(r"\s+", " ", raw).strip() for raw in (text or "").splitlines()) if ln]


def _label_of(line: str) -> tuple[str, str] | None:
    """줄 앞의 항목 이름과 나머지 글자. 항목 이름이 아니면 None."""
    for key, pattern in _PATTERNS.items():
        m = pattern.match(line)
        if m:
            value = m.group(1).strip(" :：-–")
            if _HEADING_TAIL.match(value):
                return None
            return key, value
    return None


def _is_label_only(line: str) -> bool:
    found = _label_of(line)
    return found is not None and not found[1]


def _schedule_below(lines: list[str], start: int, window: int = 12) -> str | None:
    """표 머리의 '채용기간' 아래 몇 칸 뒤에 오는 첫 날짜 줄 (다음 줄이 '~ 끝날짜'면 이어 붙임).

        교과 / 인원 / 채용기간 / 비고 / 국어 / 1명 / 2026.10.19.(월) ~ 10.23.(금)
        채용기간 / 생물 / 1 / 2026. 10. 23.(금) / ~ 2026. 10. 26.(월) (4일)
    """
    for j in range(start, min(start + window, len(lines))):
        line = lines[j]
        found = _label_of(line)
        if _SECTION.match(line) or (found and found[0] not in ("schedule", "hours", "headcount", "field")):
            return None  # 다른 항목으로 넘어감
        if _DATED.search(line):
            if "~" not in line and j + 1 < len(lines) and lines[j + 1].startswith("~"):
                return f"{line} {lines[j + 1]}"
            return line
    return None


def _usable(key: str, value: str) -> bool:
    """일정 칸은 날짜 모양일 때만 받는다. 표 칸이 어긋나 인원 '1' 같은 값이 오면 버리고 뒤에서 다시 찾는다."""
    return key != "schedule" or bool(_DATE_LIKE.search(value))


def _items_below(lines: list[str], start: int, key: str, window: int = 8) -> list[str] | None:
    """라벨 아래 표 머리 칸('구분', '내용', '1차 전형', 같은 라벨)을 건너뛰고 나오는 항목 줄들 (4개까지).

        □ 제출 서류 / 구분 / 내용 / 1차 전형 / 제출 서류 / (지원자 공통) / ① 강사 지원 신청서 1부 / ② …
    """
    for j in range(start, min(start + window, len(lines))):
        line = lines[j]
        if _ITEM.match(line) and _label_of(line) is None:
            items: list[str] = []
            while j < len(lines) and len(items) < 4 and _ITEM.match(lines[j]) and _label_of(lines[j]) is None:
                items.append(_ITEM.sub("", lines[j]))
                j += 1
            return items
        found = _label_of(line)
        if _SECTION.match(line) or (found and (found[0] != key or found[1])) or (
            not found and len(line) > _HEAD_CELL_MAX
        ):
            return None
    return None


def _raw_fields(lines: list[str]) -> dict[str, str]:
    """항목 이름 → 값 글자 (칸마다 처음 찾은 쓸 만한 것)."""
    out: dict[str, str] = {}
    in_recruit = False
    i = 0
    while i < len(lines):
        if _SECTION.match(lines[i]):
            in_recruit = bool(_RECRUIT_SECTION.match(lines[i]))
        if in_recruit and "schedule" not in out and _BARE_PERIOD.match(lines[i]):
            # 무룡고 공고문: 과목 / 채용 / 인원(명) / 기간 / 비고 / 일반사회 / … / 2026. 10. 22. ~ 10. 30.
            below = _schedule_below(lines, i + 1)
            if below:
                out["schedule"] = below
            i += 1
            continue
        found = _label_of(lines[i])
        if found is None:
            i += 1
            continue
        key, value = found
        if key == "schedule" and not value and "schedule" not in out:
            below = _schedule_below(lines, i + 1)
            if below:
                out["schedule"] = below
                i += 1
                continue
        if not value:
            # 라벨만 있는 줄이 연달아 나오면 표 머리 칸: 뒤따르는 같은 수의 줄이 차례로 값
            run = [key]
            j = i + 1
            while j < len(lines) and _is_label_only(lines[j]):
                run.append(_label_of(lines[j])[0])
                j += 1
            if len(run) >= 2:
                values = lines[j: j + len(run)]
                if len(values) == len(run) and not any(_label_of(v) for v in values):
                    for k, v in zip(run, values):
                        if _usable(k, v):
                            out.setdefault(k, v)
                    i = j + len(run)
                    continue
                i = j
                continue
        if key in _MULTI and not value and key not in out and i + 1 < len(lines):
            nxt = lines[i + 1]
            if not _ITEM.match(nxt) and _label_of(nxt) is None and len(nxt) <= _HEAD_CELL_MAX:
                below = _items_below(lines, i + 1, key)
                if below:
                    out[key] = "\n".join(below)
                    i += 1
                    continue
        items: list[str] = [value] if value else []
        j = i + 1
        while j < len(lines) and _label_of(lines[j]) is None:
            nxt = lines[j]
            if key in _MULTI and len(items) < 4 and (_ITEM.match(nxt) or not items):
                items.append(_ITEM.sub("", nxt))
            elif not items:
                items.append(nxt)
            else:
                break
            j += 1
        if items and key != "other" and _usable(key, items[0]):
            out.setdefault(key, "\n".join(items) if key in _MULTI else items[0])
        i = j if items else i + 1
    return out


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clean(raw: dict[str, str]) -> dict:
    info: dict = {}
    schedule = raw.get("schedule", "")
    if schedule:
        hours = raw.get("hours", "")
        if hours and re.search(r"\d|[월화수목금토일]요일|매주", hours) and hours not in schedule:
            schedule = f"{schedule} / {hours}"
        info["schedule"] = _cap(schedule, 120)
    if raw.get("target"):
        info["target"] = _cap(raw["target"], 60)
    headcount = raw.get("headcount", "")
    m = re.search(r"(\d{1,3})\s*명", headcount)
    if m and "각" not in headcount and int(m.group(1)) > 0:
        info["headcount"] = int(m.group(1))
    for key, limit in (("qualification", 100), ("documents", 100)):
        if raw.get(key):
            info[key] = "\n".join(_cap(part, limit) for part in raw[key].split("\n") if part)
    return info


def _email(lines: list[str], strict: bool) -> str | None:
    """접수 안내 줄에 있는 이메일. strict 면 '접수·제출·지원' 같은 말이 같은 줄(또는 윗줄)에 있어야 한다."""
    for i, line in enumerate(lines):
        for m in _EMAIL.finditer(line):
            addr = m.group(0).rstrip(".")
            if _JUNK_EMAIL.match(addr):
                continue
            context = line + " " + (lines[i - 1] if i else "")
            if _APPLY_WORDS.search(context) or (not strict and _MAIL_WORDS.search(context)):
                return addr
    return None


def extract_info(notice: str | None, page: str | None = None) -> dict:
    """첨부 공고문(notice)을 먼저 보고, 못 찾은 칸만 상세 페이지 글자(page)에서 찾는다.

    돌려주는 칸: schedule, target, headcount(int), qualification, documents, email (찾은 것만).
    """
    notice_lines, page_lines = _lines(notice), _lines(page)
    info = _clean(_raw_fields(page_lines))
    info.update(_clean(_raw_fields(notice_lines)))
    email = _email(notice_lines, strict=False) or _email(page_lines, strict=True)
    if email:
        info["email"] = email
    return info
