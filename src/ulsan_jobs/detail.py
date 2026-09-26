"""상세 페이지에서 보조 정보 꺼내기: 본문 글자, 목록에서 잘린 제목의 전체.

일부 게시판은 목록에 제목을 앞부분만 보여 준다 (예: 울주군시설관리공단 '…강사 긴급 위·수탁 서류',
새올·중구도시관리공단 '…면접심사 시행 공...'). 잘린 뒷부분에 '결과'·'합격자' 같은 말이 있으면
결과공고를 모집 공고로 잘못 보내게 되므로, 상세 페이지에서 전체 제목을 찾아 다시 판별한다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

_ELLIPSIS = re.compile(r"(\.{2,}|…)\s*$")
_TITLE_TAGS = ["h1", "h2", "h3", "h4", "h5", "th", "td", "dt", "dd", "p", "strong", "b", "span", "div", "li", "caption"]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def looks_truncated(title: str) -> bool:
    return bool(_ELLIPSIS.search(title or ""))


def extends(full: str, short: str) -> bool:
    """full 이 잘린 제목 short 의 전체인가 (공백·말줄임 무시)."""
    return _squash(full).startswith(_squash(_ELLIPSIS.sub("", short or "")))


def page_soup(content: bytes) -> BeautifulSoup:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup


def page_text(soup: BeautifulSoup) -> str:
    return soup.get_text(" ")


# 제목과 같은 칸에 붙어 나오는 글 정보 (예: '… 모집 공고 작성자 관리자 작성일 2026-09-16 조회수 133')
_META_TAIL = re.compile(r"\s+(작성자|작성일|등록일|게시일|조회수?|첨부(파일)?|글쓴이|담당부서)\s*[:：]?\s.*$")


def full_title(soup: BeautifulSoup, short: str, max_len: int = 200) -> str | None:
    """상세 페이지에서 목록 제목(short)으로 시작하는 가장 짧은 글 덩어리 = 전체 제목."""
    prefix = _squash(_ELLIPSIS.sub("", short or ""))
    if len(prefix) < 6:
        return None
    candidates = [el.get_text(" ") for el in soup.find_all(_TITLE_TAGS)]
    candidates += [str(s) for s in soup.find_all(string=True)]  # 제목이 다른 요소와 한 칸에 섞인 경우
    best: str | None = None
    for raw in candidates:
        text = re.sub(r"\s+", " ", raw).strip()
        if len(text) > max_len * 2:
            continue
        cut = _META_TAIL.sub("", text)
        if _squash(cut).startswith(prefix):
            text = cut
        squashed = _squash(text)
        if len(text) <= max_len and squashed.startswith(prefix) and len(squashed) > len(prefix):
            if best is None or len(text) < len(best):
                best = text
    return best
