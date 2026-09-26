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


def full_title(soup: BeautifulSoup, short: str, max_len: int = 200) -> str | None:
    """상세 페이지에서 목록 제목(short)으로 시작하는 가장 짧은 글 덩어리 = 전체 제목."""
    prefix = _squash(_ELLIPSIS.sub("", short or ""))
    if len(prefix) < 6:
        return None
    best: str | None = None
    for el in soup.find_all(_TITLE_TAGS):
        text = re.sub(r"\s+", " ", el.get_text(" ")).strip()
        if len(text) > max_len:
            continue
        squashed = _squash(text)
        if squashed.startswith(prefix) and len(squashed) > len(prefix):
            if best is None or len(text) < len(best):
                best = text
    return best
