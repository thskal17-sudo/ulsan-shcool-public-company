"""표(table) 형태 게시판 공통 수집기.

공공기관 게시판은 대부분 '번호 | 제목 | 작성자 | 등록일 | 조회수' 형태의 표라서, 셀렉터를 일일이
적지 않아도 머리글(th)로 열을 찾아 읽을 수 있다. 사이트별 차이는 sources.yaml 의 옵션으로 흡수한다.

options (모두 선택)
    page_param      목록 페이지 번호 파라미터 이름 (예: pageIndex). 없으면 1페이지만 본다
    row_selector    행 CSS 셀렉터 (자동 판별이 틀릴 때만)
    link_template   제목 링크가 javascript 일 때 onclick 인자로 상세 주소를 만드는 틀 ({0}, {1} …)
    key_param       상세 주소에서 게시글 번호로 쓸 쿼리 파라미터 (예: q_bbsDocNo)
    form_link       true 면 onclick 으로 제출하는 <form> 의 action + hidden 값으로 상세 주소를 만든다
    detail_get      상세 페이지를 GET 으로 열 수 있으면 true (마감일 추출에 사용, 기본 true)
    org_name        기관명 기본값 (작성자 열이 '관리자' 등일 때)
    link_base       상대 링크를 풀 기준 주소 (페이지 주소와 다를 때. <base href> 가 있으면 자동 적용)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..classify import infer_district
from ..dates import extract_deadline, has_full_date, parse_date
from ..models import Posting
from .base import Collector

_HEADER_MAP = {
    "title": r"제목|공고명|모집명|프로그램명|강좌명",
    "posted": r"등록일|작성일|게시일|공고일|날짜|일자",
    "deadline": r"마감|접수기간|모집기간|신청기간|접수일|기간",
    "org": r"작성자|부서|기관|학교|담당|등록자|글쓴이",
    "label": r"^(구분|분류|분야|직종)$",
    "district": r"^(지역|구군|구·군)$",
}
# 게시글 식별값에서 빼는 쿼리 파라미터 (페이지 번호·검색어·세션 등은 같은 글이라도 달라진다)
_VOLATILE_PARAMS = re.compile(r"page|currpage|rowperpage|search|sort|_csrf|jsessionid", re.I)
_NOT_ORG = re.compile(r"^(관리자|담당자|admin|운영자|홈페이지|-)?$", re.I)
_JS_CALL = re.compile(r"([A-Za-z_$][\w$.]*)\s*\(([^)]*)\)")
_JS_ARG = re.compile(r"""['"]([^'"]*)['"]|(-?\d+)""")


@dataclass
class BoardRow:
    title: str
    url: str
    key: str
    posted: date | None
    deadline: date | None
    org: str
    detail_ok: bool
    label: str = ""
    district: str = ""


class BoardCollector(Collector):
    def collect(self) -> list[Posting]:
        opts = self.source.options
        postings: list[Posting] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.source.pages) + 1):
            url = self.page_url(page)
            if url is None:
                break
            resp = self.http.get(url)
            rows = parse_board(resp.content, resp.url, opts, self.today)
            fresh = [r for r in rows if r.key not in seen]
            for r in fresh:
                seen.add(r.key)
                postings.append(
                    Posting(
                        source_id=self.source.id,
                        title=r.title,
                        url=r.url,
                        post_key=r.key,
                        org_name=r.org or opts.get("org_name", ""),
                        org_type=self.source.org_type,
                        district=infer_district(f"{r.district} {r.org}", self.source.district),
                        posted_date=r.posted,
                        deadline=r.deadline,
                        label=r.label,
                        detail_url=r.url if r.detail_ok and opts.get("detail_get", True) else None,
                    )
                )
            if not fresh:
                break
        return postings

    def page_url(self, page: int) -> str | None:
        url = self.source.url or ""
        if page == 1:
            return url
        param = self.source.options.get("page_param")
        if not param:
            return None
        return set_query(url, **{param: page})


def set_query(url: str, **params) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urlunsplit(parts._replace(query=urlencode(query)))


def stable_key(url: str) -> str:
    """같은 글이면 목록 페이지가 달라도 같은 값이 되도록 URL 을 정리한다."""
    parts = urlsplit(url)
    path = re.sub(r";jsessionid=[^/?#]*", "", parts.path, flags=re.I)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _VOLATILE_PARAMS.search(k) and v]
    return urlunsplit(parts._replace(path=path, query=urlencode(sorted(query)), fragment=""))


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pick_rows(soup: BeautifulSoup, row_selector: str | None) -> tuple[list[Tag], list[str]]:
    if row_selector:
        rows = soup.select(row_selector)
        table = rows[0].find_parent("table") if rows else None
    else:
        best, best_rows = None, []
        for table in soup.find_all("table"):
            if table.find("table"):  # 레이아웃용 바깥 표는 건너뜀
                continue
            rows = [tr for tr in table.find_all("tr") if tr.find("td") and (tr.find("a") or _submit_title(tr))]
            if len(rows) > len(best_rows):
                best, best_rows = table, rows
        table, rows = best, best_rows
    headers: list[str] = []
    if table is not None:
        # 머리글은 th 만 있는 첫 행 ('전체게시물: n개' 같은 요약 행은 건너뜀)
        head_row = next((tr for tr in table.find_all("tr") if tr.find("th") and not tr.find("td")), None)
        if head_row is not None:
            headers = [_clean(th.get_text()) for th in head_row.find_all(["th", "td"])]
    return rows, headers


def _submit_title(tag: Tag) -> Tag | None:
    """제목이 링크 대신 폼 제출 버튼(<input type=submit value="제목">)인 게시판."""
    for inp in tag.find_all("input"):
        if (inp.get("type") or "").lower() == "submit" and len(_clean(inp.get("value"))) >= 2:
            return inp
    return None


def _form_get_url(form: Tag, base_url: str) -> str:
    params = {
        i.get("name"): i.get("value", "")
        for i in form.find_all("input")
        if i.get("name") and i.get("name") != "_csrf" and (i.get("type") or "").lower() != "submit"
    }
    action = urljoin(base_url, form.get("action") or base_url)
    return f"{stable_key(action) if ';jsessionid' in action.lower() else action}?{urlencode(params)}"


def _column_index(headers: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    for role, pattern in _HEADER_MAP.items():
        for i, h in enumerate(headers):
            if re.search(pattern, h) and i not in index.values():
                index[role] = i
                break
    return index


def _js_args(code: str) -> list[str]:
    m = _JS_CALL.search(code or "")
    if not m:
        return []
    return [a if a else n for a, n in _JS_ARG.findall(m.group(2))]


def _pick_key(args: list[str]) -> str:
    """JS 인자 중 게시글 번호로 보이는 값 (boardView('employ','207','') → '207')."""
    for a in args:
        if re.fullmatch(r"\d+", a):
            return a
    for a in args:
        if re.search(r"\d{3,}", a):
            return a
    return next((a for a in args if a), "")


def _form_url(soup: BeautifulSoup, onclick: str, base_url: str) -> tuple[str, str] | None:
    """onclick="document.getElementById('X').submit()" → 폼 action + hidden 값으로 GET 주소."""
    m = re.search(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)\s*\.submit""", onclick or "")
    if not m:
        m = re.search(r"""document\.(\w+)\.submit""", onclick or "")
    if not m:
        return None
    form = soup.find("form", id=m.group(1)) or soup.find("form", attrs={"name": m.group(1)})
    if form is None:
        return None
    params = {
        i.get("name"): i.get("value", "")
        for i in form.find_all("input")
        if i.get("name") and i.get("name") != "_csrf" and i.get("value")
    }
    action = urljoin(base_url, form.get("action") or base_url)
    return f"{action}?{urlencode(params)}", m.group(1)


def parse_board(html: bytes | str, base_url: str, opts: dict, today: date) -> list[BoardRow]:
    soup = BeautifulSoup(html, "lxml")
    base_tag = soup.find("base", href=True)
    if opts.get("link_base"):
        base_url = opts["link_base"]
    elif base_tag is not None:
        base_url = urljoin(base_url, base_tag["href"])
    rows, headers = _pick_rows(soup, opts.get("row_selector"))
    col = _column_index(headers)
    out: list[BoardRow] = []
    for tr in rows:
        tds = tr.find_all("td")
        aligned = bool(headers) and len(tds) == len(headers)
        title_td = tds[col["title"]] if aligned and "title" in col else None
        anchors = (title_td or tr).find_all("a")
        anchor = max(anchors, key=lambda a: len(_clean(a.get_text())), default=None)
        submit = _submit_title(title_td or tr) if anchor is None or len(_clean(anchor.get_text())) < 2 else None
        if submit is not None and submit.find_parent("form") is not None:
            title = _clean(submit.get("value"))
            url = _form_get_url(submit.find_parent("form"), base_url)
            key_param = opts.get("key_param")
            values = parse_qs(urlsplit(url).query).get(key_param) if key_param else None
            key, detail_ok = (values[0] if values else url), True
        elif anchor is not None:
            title = _clean(anchor.get("title") if len(_clean(anchor.get_text())) < 2 else anchor.get_text())
            url, key, detail_ok = _resolve_link(soup, anchor, tr, base_url, opts)
        else:
            continue
        title = re.sub(r"\s*(새글|NEW|new|첨부파일|파일첨부)$", "", title)
        if len(title) < 2:
            continue

        def cell(role: str) -> str:
            return _clean(tds[col[role]].get_text(" ")) if aligned and role in col else ""

        posted = parse_date(cell("posted"), today)
        if posted is None:
            for td in tds:
                if td is not title_td and has_full_date(td.get_text()):
                    posted = parse_date(td.get_text(), today)
                    break
        deadline_text = cell("deadline")
        deadline = extract_deadline(deadline_text, today) or (
            parse_date(deadline_text, today) if deadline_text else None
        )
        deadline = deadline or extract_deadline(title, today)
        org = cell("org")
        if _NOT_ORG.match(org):
            org = ""
        out.append(BoardRow(title, url, key, posted, deadline, org, detail_ok, cell("label"), cell("district")))
    return out


def _resolve_link(soup, anchor: Tag, tr: Tag, base_url: str, opts: dict) -> tuple[str, str, bool]:
    href = (anchor.get("href") or "").strip()
    onclick = anchor.get("onclick") or tr.get("onclick") or ""
    key_param = opts.get("key_param")

    if href and not href.lower().startswith("javascript") and not href.startswith("#"):
        url = urljoin(base_url, href)
        key = stable_key(url)
        if key_param:
            values = parse_qs(urlsplit(url).query).get(key_param)
            key = values[0] if values else url
        return url, key, True

    code = onclick if onclick else href
    if opts.get("form_link", True):
        form = _form_url(soup, code, base_url)
        if form:
            url, form_id = form
            key = form_id
            if key_param:
                values = parse_qs(urlsplit(url).query).get(key_param)
                key = values[0] if values else form_id
            return url, key, True

    args = _js_args(code)
    template = opts.get("link_template")
    if template and args:
        try:
            url = template.format(*args)
            return url, _pick_key(args), True
        except (IndexError, KeyError):
            pass
    title = _clean(anchor.get_text())
    return base_url, (_pick_key(args) or title), False
