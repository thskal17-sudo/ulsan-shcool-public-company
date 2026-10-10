"""고용24 채용정보 상세검색 화면을 읽는 수집기 (로그인·API 인증키 없이 열린다).

요청: GET https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do
      ?srcKeyword=방과후&region=31000&resultCnt=50&currentPageNo=1
결과 표(table#contentArea)의 줄(tr#list1, list2 …)마다
    a.cp_name                      회사명
    input[type=checkbox] value     '공고번호|정보구분|회사명|전체 제목' (화면 제목은 잘리기도 함)
    a[data-emp-detail] href        상세 (empDetailAuthView.do?wantedAuthNo=…)
    li.site                        근무지 ('울산광역시 동구 꽃바위6길')
    p.s1_r                         '마감일 : 2026-10-13', '등록일 : 2026-08-14'

방과후 위탁업체처럼 공공기관이 아닌 곳의 강사 공고를 받으려고 쓴다. 고용24의 '방과후' 검색에는
발달장애인 방과후활동서비스·요양 같은 돌봄 인력 공고도 섞여 있어 title_exclude 로 뺀다.

options
    keywords         검색어 목록 (예: [방과후, 늘봄])
    region           근무지역 코드 (기본 31000 = 울산광역시 전체)
    company_exclude  회사명이 맞으면 뺀다 (정규식, 예: 학원|교습소)
    title_exclude    제목이 맞으면 뺀다 (정규식, 예: 발달장애|활동서비스)
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..classify import infer_district
from ..dates import parse_date
from ..models import Posting
from .base import Collector

SEARCH_URL = "https://www.work24.go.kr/wk/a/b/1200/retriveDtlEmpSrchList.do"


def _text(tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip() if tag else ""


def parse_work24_search(html: bytes | str, base_url: str = SEARCH_URL) -> list[dict[str, str]]:
    """검색 결과 표의 줄마다 key·company·title·url·site·closing·registered."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for tr in soup.select("table#contentArea tr[id^=list]"):
        link = tr.select_one("a[data-emp-detail]") or tr.select_one("a[href*='empDetailAuthView']")
        if link is None:
            continue
        url = urljoin(base_url, link.get("href", ""))
        key = (parse_qs(urlsplit(url).query).get("wantedAuthNo") or [""])[0]
        company = _text(tr.select_one("a.cp_name"))
        title = _text(link)
        box = tr.select_one("input[type=checkbox][value]")
        if box is not None:
            parts = box["value"].split("|")
            if len(parts) >= 4 and parts[3].strip():
                title = "|".join(parts[3:]).strip()  # 화면 제목은 '…'로 잘리기도 해서 전체 제목을 쓴다
            if not company and len(parts) >= 3:
                company = parts[2].strip()
        dates = {m.group(1): m.group(2) for m in re.finditer(r"(마감일|등록일)\s*:\s*([\d-]+)", _text(tr))}
        if not key or not title:
            continue
        items.append(
            {
                "key": key,
                "company": company,
                "title": re.sub(r"\s+", " ", title),
                "url": url,
                "site": _text(tr.select_one("li.site")),
                "closing": dates.get("마감일", ""),
                "registered": dates.get("등록일", ""),
            }
        )
    return items


class Work24WebCollector(Collector):
    def collect(self) -> list[Posting]:
        opts = self.source.options
        keywords = opts.get("keywords") or ["방과후"]
        company_exclude = re.compile(opts["company_exclude"]) if opts.get("company_exclude") else None
        title_exclude = re.compile(opts["title_exclude"]) if opts.get("title_exclude") else None
        postings: list[Posting] = []
        seen: set[str] = set()
        for keyword in keywords:
            params = {"srcKeyword": keyword, "region": str(opts.get("region", "31000")), "resultCnt": 50, "currentPageNo": 1}
            resp = self.http.get(SEARCH_URL, params=params)
            for item in parse_work24_search(resp.content, getattr(resp, "url", None) or SEARCH_URL):
                if item["key"] in seen:
                    continue
                seen.add(item["key"])
                if company_exclude and company_exclude.search(item["company"]):
                    continue
                if title_exclude and title_exclude.search(item["title"]):
                    continue
                postings.append(
                    Posting(
                        source_id=self.source.id,
                        title=item["title"],
                        url=item["url"],
                        post_key=item["key"],
                        org_name=item["company"],
                        org_type=self.source.org_type,
                        district=infer_district(item["site"], self.source.district),
                        posted_date=parse_date(item["registered"], self.today),
                        deadline=parse_date(item["closing"], self.today),
                        detail_url=item["url"],
                    )
                )
        return postings
