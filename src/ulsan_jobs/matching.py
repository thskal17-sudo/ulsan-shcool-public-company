"""결과공고로 모집이 끝난 공고 찾기.

공단·수련관 공고는 마감일이 첨부파일에만 있는 경우가 많아서, 같은 게시판에 나중에
'서류심사 결과'·'최종합격자' 같은 결과공고가 올라오면 그 모집은 끝났다고 본다.

같은 모집인지는 제목에서 연도·차수·공고/모집/결과 같은 말을 뺀 '핵심'을 비교해,
한쪽 핵심이 다른 쪽을 그대로 포함할 때만 같다고 본다 (과목·시설 이름이 다르면 다른 모집).
    '[남부청소년수련관] 2026년 주말수영 강사(긴급) 위·수탁 모집 공고'  → 남부청소년수련관주말수영강사
    '[남부청소년수련관] 2026년 주말수영 강사 긴급 위·수탁 서류심사 결과' → 남부청소년수련관주말수영강사
"""
from __future__ import annotations

import re

from .models import Posting

_ELLIPSIS = re.compile(r"(\.{2,}|…)\s*$")
_PATTERNS = [
    r"(19|20)\d{2}(학년도|년도|년)?",
    r"제?\d+(회|차)",
    r"\(안\)",
]
# 길이가 긴 것부터 지운다 ('위수탁' 을 '수탁' 보다 먼저)
_WORDS = sorted(
    [
        "재공고", "공고문", "공고", "모집", "채용", "위촉", "위수탁", "수탁자", "수탁", "긴급", "결과", "최종",
        "합격자", "합격", "서류", "심사", "면접", "발표", "대상자", "대상", "적격", "선정", "예정자", "예정",
        "시행", "계획", "안내", "결정", "명단", "전형", "공개경쟁", "알림", "프리랜서", "및",
    ],
    key=len,
    reverse=True,
)
MIN_CORE = 6


def core(title: str) -> str:
    text = title or ""
    if _ELLIPSIS.search(text):
        # 목록에서 잘린 제목: 말줄임과 잘린 마지막 낱말을 버린다 ('… 시행 공...' → '… 시행')
        text = _ELLIPSIS.sub("", text).rsplit(None, 1)[0] if " " in text.strip() else _ELLIPSIS.sub("", text)
    text = re.sub(r"\s+", "", text)
    for pattern in _PATTERNS:
        text = re.sub(pattern, "", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    for word in _WORDS:
        text = text.replace(word, "")
    return text


def same_program(recruit_title: str, result_title: str) -> bool:
    a, b = core(recruit_title), core(result_title)
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_CORE and short in long


def _num(key: str | None) -> int | None:
    return int(key) if key and key.isdigit() else None


def is_later(result: Posting, recruit: Posting) -> bool:
    """결과공고가 모집공고보다 나중에 올라왔는가 (같은 날이면 글 번호로 비교)."""
    if result.posted_date is None or recruit.posted_date is None:
        return False
    if result.posted_date != recruit.posted_date:
        return result.posted_date > recruit.posted_date
    a, b = _num(result.post_key), _num(recruit.post_key)
    return a is not None and b is not None and a > b


def finished_by(recruit: Posting, results: list[Posting]) -> Posting | None:
    """이 모집공고를 끝낸 결과공고 (없으면 None)."""
    for res in results:
        if res.uid == recruit.uid:
            continue
        if recruit.org_name and res.org_name and recruit.org_name != res.org_name:
            continue
        if is_later(res, recruit) and same_program(recruit.title, res.title):
            return res
    return None
