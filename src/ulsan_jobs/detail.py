"""상세 페이지에서 보조 정보 꺼내기: 본문 글자, 목록에서 잘린 제목의 전체.

일부 게시판은 목록에 제목을 앞부분만 보여 준다 (예: 울주군시설관리공단 '…강사 긴급 위·수탁 서류',
새올·중구도시관리공단 '…면접심사 시행 공...'). 잘린 뒷부분에 '결과'·'합격자' 같은 말이 있으면
결과공고를 모집 공고로 잘못 보내게 되므로, 상세 페이지에서 전체 제목을 찾아 다시 판별한다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, CData, NavigableString, Tag

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


# 줄을 나누는 요소. 나머지(span·b·a·font 같은 글자 꾸밈)는 앞뒤 글자와 한 줄로 잇는다
_BLOCK_TAGS = frozenset(
    "address article aside blockquote br caption dd div dl dt fieldset figcaption figure form h1 h2 h3 h4 h5 h6 "
    "hr li main ol p pre section table tbody td tfoot th thead tr ul".split()
)


def block_text(soup: BeautifulSoup) -> str:
    """문단·표 칸·줄바꿈마다 한 줄씩 나눈 본문 글자 (화면에 보이는 줄과 같게).

    get_text("\n") 은 태그마다 줄을 나눠서, 글자 조각마다 <span> 을 씌운 공고(천상고)가
        <p><span>가</span><span>. </span><span>해당과목 교원자격증 소지자</span></p>
    '가' / '.' / '해당과목 교원자격증 소지자' 세 줄로 갈라진다.
    """
    out: list[str] = []
    stack = [iter(soup.children)]
    closes = [False]  # 그 단계를 다 읽은 뒤 줄을 바꿀지
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            if closes.pop():
                out.append("\n")
        elif isinstance(child, Tag):
            block = child.name in _BLOCK_TAGS
            if block:
                out.append("\n")
            stack.append(iter(child.children))
            closes.append(block)
        elif type(child) in (NavigableString, CData):  # get_text 처럼 주석·doctype 은 뺀다
            out.append(re.sub(r"\s+", " ", str(child)))  # HTML 소스의 줄바꿈은 화면에서 빈칸
    return "".join(out)


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
