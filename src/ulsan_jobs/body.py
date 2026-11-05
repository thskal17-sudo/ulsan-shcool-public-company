"""공고 상세 페이지에서 본문 글만 뽑는다 (강사잇다 '상세 내용'에 넣는다).

게시판마다 본문 칸 이름이 달라서 칸 이름을 하나하나 적지 않고, '본문다운 글 조각'(접수·모집·자격·기간 같은 말이 든 긴 글)을
70% 넘게 품은 가장 작은 칸을 본문으로 본다. 메뉴·머리·꼬리 칸과 작성자·조회수·첨부파일 같은 글 정보 줄은 뺀다.
본문 없이 첨부 공고문만 올린 글이면 빈 글을 돌려주고, 부르는 쪽이 첨부 공고문 글(notice_excerpt)을 대신 쓴다.
(2026-11-05 부산·경북·울산 공고 페이지 78개로 맞춤. 표는 강사잇다가 화면에서 빼고 '원문 공고에서 확인' 안내로 바꾼다.)
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

BODY_MAX = 1500
MIN_BODY = 60  # 이보다 짧으면 본문이 없는 글로 본다

_CHROME_EXACT = {"gnb", "lnb", "snb", "menu", "nav", "navbar", "footer", "header", "quick", "sitemap", "breadcrumb", "location",
                 "util", "banner", "pagination", "paging", "sns", "share", "search", "satisfaction", "survey", "copyright",
                 "popup", "dropdown-menu", "skip", "skipnav", "top_menu", "topmenu", "side", "sidebar", "aside"}
_CHROME_PREFIX = ("gnb", "lnb", "snb", "footer", "sitemap", "skipnav", "quick_", "header_", "top_menu", "main_menu", "sub_menu",
                  "menu_", "nav-", "navbar")
_BODY_WORD = re.compile(r"접수|모집|채용|자격|기간|인원|강사|서류|제출|문의|계약|근무|보수|대상|방법|일정|응시|선발|면접|위촉|공고")
_BLOCK = ["div", "td", "article", "section", "dd", "main", "table", "tbody", "tr", "ul", "li", "form", "body"]
_META = re.compile(r"^(작성자|작성일|등록일|게시일|수정일|조회수?|담당부서|담당자|부서|전화번호|첨부파일|첨부|파일|"
                   r"이전글|다음글|목록|글쓰기|공유하기|인쇄|미리보기|다운로드|바로보기|내려받기|등록자연락처|출처|"
                   r"공공누리|만족도|작성기관|분류|구분|번호|제목|내용|상세보기|글쓴이|날짜|추천|스크랩|"
                   r"다운로드 정보|다운로드 횟수|바로듣기|주소 복사|링크 복사|크게|작게|\(\s*\d+\s*회\s*\))\s*[:：]?\s*$")
# 글 정보 칸 이름과 값이 붙은 줄 ('작성자노동자종합복지회관', '작성일2026-11-02', '조회수176')
_META_VALUE = re.compile(r"^(작성자|작성일|등록일|게시일|수정일|조회수|조회)\s*[:：]?\s*\S.{0,30}$")
_SENTENCE = re.compile(r"(다|요|니다)\.?\s*$")
_FILE = re.compile(r"\.(hwp|hwpx|pdf|docx?|xlsx?|zip|jpe?g|png|gif)\b", re.I)


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _is_chrome(tag) -> bool:
    tokens = [t.lower() for t in [tag.get("id") or "", *(tag.get("class") or [])] if t]
    return any(t in _CHROME_EXACT or t.startswith(_CHROME_PREFIX) for t in tokens)


def _content_block(soup: BeautifulSoup, title: str):
    tkey = _sq(title)
    nodes = []
    for s in soup.find_all(string=True):
        t = s.strip()
        if len(t) < 12 or s.find_parent("a") or not _BODY_WORD.search(t):
            continue
        if tkey and _sq(t) in tkey:  # 제목 조각
            continue
        nodes.append(s)
    if len(nodes) < 2:
        return None
    need = max(2, int(len(nodes) * 0.7 + 0.5))
    best = None
    for el in soup.find_all(_BLOCK):
        if sum(1 for s in nodes if el in s.parents) >= need:
            size = len(_sq(el.get_text(" ")))
            if best is None or size < best[0]:
                best = (size, el)
    return best[1] if best else None


def body_text(page: BeautifulSoup | bytes | str, title: str = "", limit: int = BODY_MAX) -> str:
    """상세 페이지의 본문 글. 본문이 없으면(첨부만 올린 글 등) 빈 글. 넘겨받은 soup 은 바꾸지 않는다."""
    soup = BeautifulSoup(str(page) if isinstance(page, BeautifulSoup) else page, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe", "select", "button", "title", "head"]):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.attrs is not None and tag.name not in ("body", "html") and _is_chrome(tag):
            tag.decompose()
    el = _content_block(soup, title)
    if el is None:
        return ""
    for br in el.find_all("br"):
        br.replace_with("\n")
    for blk in el.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "dt", "dd"]):
        blk.insert_after(NavigableString("\n"))
    tkey = _sq(title)
    lines: list[str] = []
    for raw in el.get_text("").split("\n"):
        line = re.sub(r"[ \t 　]+", " ", raw).strip()
        if not line or _META.match(line) or _META_VALUE.match(line) or (_FILE.search(line) and len(line) < 120):
            continue
        sq = _sq(line)
        if tkey and len(tkey) >= 6 and (sq == tkey or (tkey in sq and len(sq) <= len(tkey) + 8 and not _SENTENCE.search(line))):
            continue  # 제목 되풀이 ('2026학년도 …' 처럼 앞뒤가 조금 붙은 것까지. '… 공고입니다.' 같은 문장은 남긴다)
        if re.fullmatch(r"[\d\-.: /()]+", line):
            continue  # 날짜·숫자만 있는 줄 (글 정보 값)
        if lines and lines[-1] == line:
            continue  # 같은 줄 되풀이 (칸 이름과 값이 같은 글 등)
        lines.append(line)
    text = "\n".join(lines).strip()
    return _cut(text, limit) if len(text) >= MIN_BODY else ""


def notice_excerpt(notice: str | None, limit: int = BODY_MAX) -> str:
    """첨부 공고문 글을 상세 내용으로 쓸 만큼 (빈 줄·연속 빈칸 정리, limit 자까지)."""
    lines = [re.sub(r"[ \t 　]+", " ", ln).strip() for ln in (notice or "").splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return _cut(text, limit) if len(text) >= MIN_BODY else ""


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip()
